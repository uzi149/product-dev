from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.database.models import VideoSegment
from shared.video.ffmpeg import probe
from tests.api.conftest import FFMPEG, make_video

START = "2026-03-07T09:05:00+00:00"


@pytest.fixture
def camera_id(client):
    body = {"display_name": "Upload cam", "source_type": "file"}
    return client.post("/cameras", json=body).json()["id"]


def upload(client, path, camera_id, start_time=START, name=None):
    data = {"camera_id": camera_id}
    if start_time:
        data["start_time"] = start_time
    with open(path, "rb") as handle:
        return client.post(
            "/videos/upload", data=data, files={"file": (name or path.name, handle, "video/mp4")}
        )


def segment_count(engine) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(VideoSegment))


def test_upload_stores_the_video_and_plays_back(client, camera_id, h264_video, storage, tmp_path):
    response = upload(client, h264_video, camera_id)
    assert response.status_code == 201
    segment = response.json()
    assert segment["camera_id"] == camera_id
    assert segment["codec"] == "h264"
    assert (segment["width"], segment["height"]) == (320, 240)
    assert segment["status"] == "pending"
    assert 2900 <= segment["duration_ms"] <= 3100
    start = datetime.fromisoformat(segment["start_time"])
    end = datetime.fromisoformat(segment["end_time"])
    assert start == datetime(2026, 3, 7, 9, 5, tzinfo=UTC)
    assert end - start == timedelta(milliseconds=segment["duration_ms"])

    key = f"segments/{camera_id}/2026/03/07/{segment['id']}.mp4"
    assert storage.exists(key)

    listed = client.get("/segments", params={"camera_id": camera_id}).json()
    assert [s["id"] for s in listed] == [segment["id"]]
    assert client.get(f"/segments/{segment['id']}").json() == segment
    assert client.get(f"/cameras/{camera_id}").json()["segment_count"] == 1

    playback = client.get(f"/segments/{segment['id']}/playback", follow_redirects=False)
    assert playback.status_code == 307
    served = httpx.get(playback.headers["location"])
    assert served.status_code == 200
    downloaded = tmp_path / "played.mp4"
    downloaded.write_bytes(served.content)
    assert probe(downloaded).codec == "h264"


def test_non_h264_upload_is_reencoded(client, camera_id, tmp_path):
    source = make_video(tmp_path / "old.avi", codec="mpeg4")
    assert probe(source).codec == "mpeg4"
    segment = upload(client, source, camera_id, name="old.avi").json()
    assert segment["codec"] == "h264"


def test_start_time_defaults_to_now(client, camera_id, h264_video):
    segment = upload(client, h264_video, camera_id, start_time=None).json()
    started = datetime.fromisoformat(segment["start_time"])
    assert abs(datetime.now(UTC) - started) < timedelta(minutes=1)


def test_naive_start_time_is_rejected(client, camera_id, h264_video, engine):
    response = upload(client, h264_video, camera_id, start_time="2026-03-07T09:05:00")
    assert response.status_code == 422
    assert segment_count(engine) == 0


def test_unknown_camera_is_rejected(client, h264_video, engine):
    response = upload(client, h264_video, "00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert segment_count(engine) == 0


@pytest.mark.skipif(FFMPEG is None, reason="ffprobe is not installed")
def test_text_file_is_rejected_and_leaves_nothing(client, camera_id, tmp_path, engine, storage):
    bogus = tmp_path / "notes.mp4"
    bogus.write_text("this is not a video")
    response = upload(client, bogus, camera_id)
    assert response.status_code == 422
    assert "not a readable video" in response.json()["detail"]
    assert segment_count(engine) == 0
    assert storage.delete_prefix(f"segments/{camera_id}/") == 0


def test_truncated_video_is_rejected(client, camera_id, h264_video, tmp_path, engine):
    truncated = tmp_path / "cut.mp4"
    truncated.write_bytes(h264_video.read_bytes()[:300])
    assert upload(client, truncated, camera_id).status_code == 422
    assert segment_count(engine) == 0


def test_empty_file_is_rejected(client, camera_id, tmp_path):
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    assert upload(client, empty, camera_id).status_code == 422


def test_oversized_upload_is_rejected(client, camera_id, h264_video, monkeypatch, engine):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    from shared.config import get_settings

    get_settings.cache_clear()
    big = h264_video.parent / "big.mp4"
    big.write_bytes(b"\0" * (2 * 1024 * 1024))
    assert upload(client, big, camera_id).status_code == 413
    assert segment_count(engine) == 0
