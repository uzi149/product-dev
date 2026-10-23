from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from services.ingestion import file_ingest
from services.ingestion.file_ingest import ingest_file
from shared.config import Settings
from shared.database.models import Camera, StaticPeriod, VideoSegment
from shared.storage import StorageError
from shared.video.ffmpeg import FFmpegError, probe
from tests.api.conftest import FFMPEG
from tests.ingestion.videos import activity_video

pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")

START = datetime(2026, 3, 7, 9, 0, tzinfo=UTC)


@pytest.fixture
def defaults():
    return Settings(_env_file=None)


@pytest.fixture
def camera(session, storage):
    camera = Camera(display_name="Ingest cam", source_type="file")
    session.add(camera)
    session.commit()
    yield camera
    storage.delete_prefix(f"segments/{camera.id}/")
    storage.delete_prefix(f"static/{camera.id}/")


@pytest.fixture(scope="module")
def two_bursts(tmp_path_factory):
    """90 s: still 0-20, motion 20-25, still 25-60, motion 60-65, still to the end."""
    return activity_video(tmp_path_factory.mktemp("ingest") / "two.avi", 90, [(20, 25), (60, 65)])


def offsets(rows):
    return [
        (round((r.start_time - START).total_seconds()), round((r.end_time - START).total_seconds()))
        for r in sorted(rows, key=lambda r: r.start_time)
    ]


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_two_bursts_become_two_padded_segments_and_three_static_periods(
    session, storage, camera, defaults, two_bursts, tmp_path
):
    result = ingest_file(session, storage, two_bursts, camera, START, defaults)
    session.commit()

    segments = session.scalars(select(VideoSegment)).all()
    assert offsets(segments) == [(10, 35), (50, 75)]
    for segment in segments:
        assert segment.camera_id == camera.id
        assert segment.codec == "h264"
        assert (segment.width, segment.height) == (320, 240)
        assert 0 < segment.activity_score <= 1
        assert segment.end_time - segment.start_time == timedelta(milliseconds=segment.duration_ms)
        key = storage.key_from_uri(segment.storage_uri)
        assert key.startswith(f"segments/{camera.id}/2026/03/07/")
        storage.download_file(key, tmp_path / "s.mp4")
        assert probe(tmp_path / "s.mp4").codec == "h264"

    periods = session.scalars(select(StaticPeriod)).all()
    assert offsets(periods) == [(0, 10), (35, 50), (75, 90)]
    for period in periods:
        assert period.health_status == "ok"
        assert storage.exists(storage.key_from_uri(period.reference_frame_uri))

    assert len(result.keys) == 5
    assert all(storage.exists(key) for key in result.keys)


def test_a_still_video_stores_no_segment_and_one_static_period(
    session, storage, camera, defaults, tmp_path
):
    still = activity_video(tmp_path / "still.avi", 30, [])
    result = ingest_file(session, storage, still, camera, START, defaults)
    session.commit()
    assert result.segments == []
    assert offsets(session.scalars(select(StaticPeriod)).all()) == [(0, 30)]
    assert count(session, VideoSegment) == 0


def test_activity_throughout_is_one_segment(session, storage, camera, defaults, tmp_path):
    busy = activity_video(tmp_path / "busy.avi", 20, [(0, 20)])
    result = ingest_file(session, storage, busy, camera, START, defaults)
    session.commit()
    assert offsets(result.segments) == [(0, 20)]
    assert result.static_periods == []


def test_the_camera_detection_config_is_used(session, storage, camera, defaults, two_bursts):
    camera.detection_config = {"changed_fraction": 0.9}  # nothing changes that much
    result = ingest_file(session, storage, two_bursts, camera, START, defaults)
    assert result.segments == []
    assert offsets(result.static_periods) == [(0, 90)]


def test_a_failure_halfway_leaves_no_objects_and_no_rows(
    session, storage, camera, defaults, two_bursts, monkeypatch
):
    written = []
    real_upload = storage.upload_file

    def upload_then_fail(key, path, content_type=None):
        if key.startswith("static/"):
            raise StorageError("MinIO went away")
        written.append(key)
        return real_upload(key, path, content_type)

    monkeypatch.setattr(storage, "upload_file", upload_then_fail)
    with pytest.raises(StorageError):
        ingest_file(session, storage, two_bursts, camera, START, defaults)

    assert len(written) == 2  # both segments were uploaded before the failure...
    assert not any(storage.exists(key) for key in written)  # ...and removed again
    session.commit()
    assert count(session, VideoSegment) == 0
    assert count(session, StaticPeriod) == 0


def test_a_frame_that_cannot_be_read_is_an_error(
    session, storage, camera, defaults, two_bursts, monkeypatch
):
    def broken(*args):
        raise FFmpegError("no frame")

    monkeypatch.setattr(file_ingest, "extract_frame_jpeg", broken)
    with pytest.raises(FFmpegError):
        ingest_file(session, storage, two_bursts, camera, START, defaults)
    assert storage.delete_prefix(f"segments/{camera.id}/") == 0
