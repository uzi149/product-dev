"""Live ingestion end to end, from a file read as fast as possible: FFmpeg -> detector ->
planner -> outbox -> uploader -> PostgreSQL + MinIO. No RTSP server needed."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.ingestion.camera_worker import CameraConfig, CameraWorker
from services.ingestion.outbox import Outbox, Uploader
from services.ingestion.status import StatusBoard
from shared.camera_status import LiveState
from shared.config import Settings
from shared.database.models import Camera, StaticPeriod, VideoSegment
from shared.video.ffmpeg import probe
from tests.api.conftest import FFMPEG
from tests.ingestion.videos import camera_video

pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")


@pytest.fixture(scope="module")
def two_bursts(tmp_path_factory):
    """90 s: still 0-20, motion 20-25, still 25-60, motion 60-65, still to the end."""
    return camera_video(tmp_path_factory.mktemp("live") / "two.mp4", 90, [(20, 25), (60, 65)])


@pytest.fixture
def live_settings(tmp_path):
    return Settings(_env_file=None, ingestion_data_dir=tmp_path / "data")


@pytest.fixture
def camera(session, storage):
    camera = Camera(display_name="Live cam", source_type="rtsp")
    session.add(camera)
    session.commit()
    yield camera
    storage.delete_prefix(f"segments/{camera.id}/")
    storage.delete_prefix(f"static/{camera.id}/")


def offsets(rows, origin):
    return [
        (
            round((r.start_time - origin).total_seconds()),
            round((r.end_time - origin).total_seconds()),
        )
        for r in sorted(rows, key=lambda r: r.start_time)
    ]


def test_a_live_run_stores_the_active_parts_and_the_static_periods(
    engine, storage, camera, live_settings, two_bursts, tmp_path
):
    outbox, board = Outbox(live_settings.outbox_dir), StatusBoard()
    config = CameraConfig(id=camera.id, name="Live cam", source=str(two_bursts), analysis_fps=1)
    result = CameraWorker(config, live_settings, outbox, board).run_once()

    assert result.error is None
    assert result.frames == 90
    assert board.get(camera.id).state == LiveState.ONLINE
    assert len(outbox.pending()) == 5  # 2 segments, 3 static periods
    assert not any(live_settings.buffer_dir.rglob("*.ts"))  # nothing left in the rolling buffer

    assert Uploader(outbox, storage, lambda: Session(engine)).run_once()
    assert outbox.pending() == []

    with Session(engine) as session:
        segments = session.scalars(select(VideoSegment)).all()
        periods = session.scalars(select(StaticPeriod)).all()
    origin = min(p.start_time for p in periods)
    assert offsets(segments, origin) == [(10, 35), (50, 75)]
    assert offsets(periods, origin) == [(0, 10), (35, 50), (75, 90)]
    for segment in segments:
        assert segment.codec == "h264"
        assert 0 < segment.activity_score <= 1
        assert segment.end_time - segment.start_time == timedelta(milliseconds=segment.duration_ms)
        storage.download_file(storage.key_from_uri(segment.storage_uri), tmp_path / "s.mp4")
        assert probe(tmp_path / "s.mp4").duration_s == pytest.approx(25, abs=0.3)
    for period in periods:
        assert period.health_status == "ok"
        assert storage.exists(storage.key_from_uri(period.reference_frame_uri))


def test_a_source_that_cant_be_read_reports_an_error(live_settings, tmp_path):
    outbox, board = Outbox(live_settings.outbox_dir), StatusBoard()
    config = CameraConfig(
        id=uuid4(), name="Broken", source=str(tmp_path / "nope.mp4"), analysis_fps=1
    )
    result = CameraWorker(config, live_settings, outbox, board).run_once()
    assert result.frames == 0
    assert result.error
    assert outbox.pending() == []
