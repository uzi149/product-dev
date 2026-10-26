from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.api.deps import get_upload_queue
from services.ingestion.tasks import process_upload
from shared.config import Settings
from shared.database.models import Upload, VideoSegment
from shared.storage.keys import upload_key
from shared.video.ffmpeg import probe
from tests.api.conftest import FFMPEG
from tests.ingestion.videos import activity_video

START = "2026-03-07T09:05:00+00:00"

needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")


@pytest.fixture
def camera_id(client):
    body = {"display_name": "Upload cam", "source_type": "file"}
    return client.post("/cameras", json=body).json()["id"]


@pytest.fixture
def burst_video(tmp_path):
    """40 s with activity at 20-25 s."""
    return activity_video(tmp_path / "burst.avi", 40, [(20, 25)])


def upload(client, path, camera_id, start_time=START, name=None):
    data = {"camera_id": camera_id}
    if start_time:
        data["start_time"] = start_time
    with open(path, "rb") as handle:
        return client.post(
            "/videos/upload", data=data, files={"file": (name or path.name, handle, "video/mp4")}
        )


def count(engine, model) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def run_worker(engine, storage, upload_id):
    """What the Celery worker does with a queued upload."""
    process_upload(
        UUID(upload_id),
        session_factory=lambda: Session(engine),
        storage=storage,
        settings=Settings(_env_file=None),
    )


@needs_ffmpeg
def test_upload_is_staged_and_queued(client, camera_id, burst_video, storage, queued):
    response = upload(client, burst_video, camera_id)
    assert response.status_code == 202
    body = response.json()
    assert body["camera_id"] == camera_id
    assert body["filename"] == "burst.avi"
    assert body["status"] == "pending"
    assert datetime.fromisoformat(body["start_time"]) == datetime(2026, 3, 7, 9, 5, tzinfo=UTC)
    assert body["segments_created"] is None
    assert queued == [UUID(body["id"])]
    assert storage.exists(upload_key(UUID(body["id"])))
    assert client.get(f"/videos/uploads/{body['id']}").json() == body


@needs_ffmpeg
def test_worker_stores_only_the_active_part_and_it_plays_back(
    client, camera_id, burst_video, storage, engine, tmp_path
):
    upload_id = upload(client, burst_video, camera_id).json()["id"]
    run_worker(engine, storage, upload_id)

    status = client.get(f"/videos/uploads/{upload_id}").json()
    assert status["status"] == "done"
    assert (status["segments_created"], status["static_periods_created"]) == (1, 2)
    assert not storage.exists(upload_key(UUID(upload_id)))

    segments = client.get("/segments", params={"camera_id": camera_id}).json()
    assert len(segments) == 1
    segment = segments[0]
    # Activity 20-25 s plus 10 s pre/post roll, from the upload start 09:05:00.
    start = datetime.fromisoformat(segment["start_time"])
    assert start == datetime(2026, 3, 7, 9, 5, 10, tzinfo=UTC)
    assert 24_500 <= segment["duration_ms"] <= 25_500
    assert segment["codec"] == "h264"  # the MJPEG source was re-encoded
    assert 0 < segment["activity_score"] <= 1
    assert client.get(f"/cameras/{camera_id}").json()["segment_count"] == 1

    playback = client.get(f"/segments/{segment['id']}/playback", follow_redirects=False)
    assert playback.status_code == 307
    assert playback.headers["cache-control"] == "no-store"
    served = httpx.get(playback.headers["location"])
    assert served.status_code == 200
    downloaded = tmp_path / "played.mp4"
    downloaded.write_bytes(served.content)
    assert probe(downloaded).codec == "h264"


@needs_ffmpeg
def test_start_time_defaults_to_now(client, camera_id, burst_video):
    body = upload(client, burst_video, camera_id, start_time=None).json()
    started = datetime.fromisoformat(body["start_time"])
    assert abs(datetime.now(UTC) - started) < timedelta(minutes=1)


def test_naive_start_time_is_rejected(client, camera_id, burst_video, engine, queued):
    response = upload(client, burst_video, camera_id, start_time="2026-03-07T09:05:00")
    assert response.status_code == 422
    assert count(engine, Upload) == 0
    assert queued == []


def test_unknown_camera_is_rejected(client, burst_video, engine):
    response = upload(client, burst_video, "00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert count(engine, Upload) == 0


@needs_ffmpeg
def test_text_file_is_rejected_straight_away(client, camera_id, tmp_path, engine, queued):
    bogus = tmp_path / "notes.mp4"
    bogus.write_text("this is not a video")
    response = upload(client, bogus, camera_id)
    assert response.status_code == 422
    assert "not a readable video" in response.json()["detail"]
    assert count(engine, Upload) == 0
    assert queued == []


@needs_ffmpeg
def test_truncated_video_is_rejected(client, camera_id, burst_video, tmp_path, engine):
    truncated = tmp_path / "cut.avi"
    truncated.write_bytes(burst_video.read_bytes()[:300])
    assert upload(client, truncated, camera_id).status_code == 422
    assert count(engine, Upload) == 0


def test_empty_file_is_rejected(client, camera_id, tmp_path):
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    assert upload(client, empty, camera_id).status_code == 422


def test_oversized_upload_is_rejected(client, camera_id, tmp_path, monkeypatch, engine):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    from shared.config import get_settings

    get_settings.cache_clear()
    big = tmp_path / "big.mp4"
    big.write_bytes(b"\0" * (2 * 1024 * 1024))
    assert upload(client, big, camera_id).status_code == 413
    assert count(engine, Upload) == 0


@needs_ffmpeg
def test_queue_down_fails_the_upload_and_removes_the_staged_file(
    client, camera_id, burst_video, storage, engine
):
    def unreachable(upload_id):
        raise ConnectionError("Redis is down")

    client.app.dependency_overrides[get_upload_queue] = lambda: unreachable
    response = upload(client, burst_video, camera_id)
    assert response.status_code == 503

    listed = client.get("/videos/uploads", params={"camera_id": camera_id}).json()
    assert len(listed) == 1
    assert listed[0]["status"] == "failed"
    assert "not reachable" in listed[0]["error"]
    assert not storage.exists(upload_key(UUID(listed[0]["id"])))
    assert count(engine, VideoSegment) == 0


@needs_ffmpeg
def test_list_uploads_newest_first_and_by_camera(client, camera_id, burst_video):
    other = client.post("/cameras", json={"display_name": "Other", "source_type": "file"}).json()
    first = upload(client, burst_video, camera_id, name="first.avi").json()
    second = upload(client, burst_video, camera_id, name="second.avi").json()
    upload(client, burst_video, other["id"], name="elsewhere.avi")

    listed = client.get("/videos/uploads", params={"camera_id": camera_id}).json()
    assert [u["id"] for u in listed] == [second["id"], first["id"]]
    assert len(client.get("/videos/uploads").json()) == 3
    assert len(client.get("/videos/uploads", params={"limit": 1}).json()) == 1


def test_unknown_upload_is_404(client):
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/videos/uploads/{missing}").status_code == 404
