"""Camera worker: reconnect waits, offline status and offline periods (no RTSP server needed)."""

import json
import shutil
import time
from uuid import uuid4

import pytest

from services.ingestion.camera_worker import CameraConfig, CameraWorker, backoff_seconds
from services.ingestion.outbox import ITEM_FILE, Outbox
from services.ingestion.status import StatusBoard
from shared.camera_status import LiveState
from shared.config import Settings
from tests.api.conftest import FFMPEG
from tests.ingestion.videos import camera_video


def test_reconnect_waits_grow_and_stop_at_the_maximum():
    assert [backoff_seconds(n, 30) for n in range(1, 8)] == [1, 2, 5, 10, 30, 30, 30]
    assert backoff_seconds(4, 3) == 3
    assert backoff_seconds(0, 30) == 0


def wait_for(condition, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    return False


def state(board: StatusBoard, camera: CameraConfig) -> LiveState | None:
    status = board.get(camera.id)
    return None if status is None else status.state


def items(outbox: Outbox) -> list[dict]:
    return [json.loads((p / ITEM_FILE).read_text()) for p in outbox.pending()]


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")
def test_an_unreachable_camera_is_offline_and_recovers_with_an_offline_period(tmp_path):
    settings = Settings(_env_file=None, ingestion_data_dir=tmp_path / "data")
    outbox, board = Outbox(settings.outbox_dir), StatusBoard()
    source = tmp_path / "not-there-yet.mp4"
    camera = CameraConfig(id=uuid4(), name="Gate", source=str(source), analysis_fps=1)
    worker = CameraWorker(camera, settings, outbox, board)
    worker.start()
    try:
        assert wait_for(lambda: state(board, camera) == LiveState.OFFLINE)
        assert board.get(camera.id).error

        time.sleep(1.5)  # offline for a while, then the "stream" comes back
        clip = camera_video(tmp_path / "clip.mp4", 12, [])
        shutil.copy(clip, source)
        assert wait_for(lambda: any(i.get("health_status") == "ok" for i in items(outbox)))
    finally:
        worker.stop()
        worker.join(30)
    assert not worker.is_alive()

    offline = [i for i in items(outbox) if i.get("health_status") == "offline"]
    assert offline, "the time the camera was unreachable is recorded"
    assert offline[0]["start_time"] < offline[0]["end_time"]
    assert offline[0]["reference_frame"] is None


def anchor_after(source: str, arrivals: list[float]):
    """Feed frames at stream times 0, 1, 2... arriving at these clock times (s); the anchor."""
    from datetime import UTC, datetime, timedelta

    import numpy as np

    from services.ingestion.camera_worker import _Run
    from services.ingestion.planner import PlannerConfig, SegmentPlanner
    from shared.video.change_detector import ChangeDetector, resolve_params

    base = datetime(2026, 3, 7, 9, 0, tzinfo=UTC)
    clock = iter(base + timedelta(seconds=a) for a in arrivals)
    settings = Settings(_env_file=None)
    camera = CameraConfig(id=uuid4(), name="cam", source=source, analysis_fps=1)
    worker = CameraWorker(
        camera, settings, Outbox(settings.outbox_dir), StatusBoard(), clock=lambda: next(clock)
    )
    run = _Run(worker, SegmentPlanner(PlannerConfig.from_settings(settings)))
    detector = ChangeDetector(resolve_params({}, settings))
    frame = np.zeros((90, 160, 3), np.uint8)
    for t in range(len(arrivals)):
        run.frame(float(t), frame, detector)
    return (run.anchor - base).total_seconds()


def test_a_live_stream_is_anchored_by_the_frame_with_the_least_delay():
    # FFmpeg probes for a few seconds, then hands over the first frames at once.
    burst = [5.8, 5.8, 5.8, 5.8, 6.4, 7.4, 8.4]
    assert anchor_after("rtsp://cam/1", burst) == pytest.approx(2.4)
    # A file is read faster than real time: the first frame's anchor is kept.
    assert anchor_after("clip.mp4", [0.0, 0.01, 0.02]) == pytest.approx(0.0)


def test_the_anchor_is_fixed_once_footage_is_kept_so_rows_never_overlap(tmp_path):
    from datetime import UTC, datetime, timedelta

    import numpy as np

    from services.ingestion.camera_worker import _Run
    from services.ingestion.planner import KeepStatic, PlannerConfig, SegmentPlanner
    from shared.video.change_detector import ChangeDetector, resolve_params

    base = datetime(2026, 3, 7, 9, 0, tzinfo=UTC)
    arrivals = iter(base + timedelta(seconds=a) for a in [5.8, 5.8, 5.8, 6.4, 7.4])
    settings = Settings(_env_file=None, ingestion_data_dir=tmp_path / "data")
    outbox = Outbox(settings.outbox_dir)
    camera = CameraConfig(id=uuid4(), name="cam", source="rtsp://cam/1", analysis_fps=1)
    worker = CameraWorker(camera, settings, outbox, StatusBoard(), clock=lambda: next(arrivals))
    worker._offline_since = base - timedelta(seconds=20)  # the camera was away before this run
    run = _Run(worker, SegmentPlanner(PlannerConfig.from_settings(settings)))
    detector = ChangeDetector(resolve_params({}, settings))
    frame = np.zeros((90, 160, 3), np.uint8)

    for t in range(3):
        run.frame(float(t), frame, detector)
    assert items(outbox) == [], "the offline period waits until the anchor is fixed"
    run.apply([KeepStatic(0.0, 2.0)])  # footage handed over with the anchor at 9:00:03.8
    run.frame(3.0, frame, detector)  # arrives with less delay, but too late to move the anchor
    run.frame(4.0, frame, detector)
    assert (run.anchor - base).total_seconds() == pytest.approx(3.8)

    offline, static = items(outbox)
    assert offline["health_status"] == "offline" and static["health_status"] == "ok"
    # The offline period ends where the run's first footage starts, on the same anchor.
    assert offline["end_time"] == static["start_time"]
