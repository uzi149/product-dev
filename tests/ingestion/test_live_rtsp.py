"""Phase 2 acceptance through the real RTSP server: loop a test video into MediaMTX, store only
the active parts, stop the stream, and recover when it comes back.

Needs `docker compose up -d` (MediaMTX, PostgreSQL, MinIO) and FFmpeg; skipped otherwise.
Takes a minute or two.
"""

import json
import socket
import subprocess
import time
from datetime import timedelta
from itertools import pairwise
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.ingestion.camera_worker import CameraConfig, CameraWorker
from services.ingestion.outbox import ITEM_FILE, Outbox, Uploader
from services.ingestion.status import StatusBoard
from shared.camera_status import LiveState
from shared.config import Settings
from shared.database.models import Camera, StaticPeriod, VideoSegment
from tests.api.conftest import FFMPEG
from tests.ingestion.videos import camera_video

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed"),
]

LOOP_SECONDS = 30
BURST = (8, 12)
# Times come from two clocks (stream time within a run, the machine clock between runs).
SLACK = timedelta(seconds=1.5)
OFFLINE_SECONDS = 5


@pytest.fixture(scope="module")
def rtsp_server():
    settings = Settings()
    try:
        socket.create_connection((settings.mediamtx_host, settings.mediamtx_rtsp_port), 2).close()
    except OSError:
        pytest.skip("MediaMTX is not reachable (run `docker compose up -d`)")
    return settings.mediamtx_rtsp_url


class Publisher:
    """FFmpeg looping a clip into MediaMTX, like the `sim` cameras do."""

    def __init__(self, clip, url):
        self.clip, self.url, self.process = clip, url, None

    def start(self):
        self.process = subprocess.Popen(
            [FFMPEG, "-v", "error", "-re", "-stream_loop", "-1", "-i", str(self.clip),
             "-c", "copy", "-f", "rtsp", "-rtsp_transport", "tcp", self.url],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )  # fmt: skip

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.kill()
            self.process.wait()


def wait_for(condition, timeout, what):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.2)
    raise AssertionError(f"timed out after {timeout} s waiting for {what}")


def kinds(outbox):
    return [json.loads((p / ITEM_FILE).read_text())["kind"] for p in outbox.pending()]


def test_a_looped_rtsp_stream_stores_activity_and_recovers_after_an_interruption(
    rtsp_server, engine, session, storage, tmp_path
):
    clip = camera_video(tmp_path / "loop.mp4", LOOP_SECONDS, [BURST])
    publisher = Publisher(clip, f"{rtsp_server}/test-{uuid4().hex[:8]}")
    camera = Camera(display_name="RTSP test", source_type="rtsp")
    session.add(camera)
    session.commit()

    settings = Settings(_env_file=None, ingestion_data_dir=tmp_path / "data")
    outbox, board = Outbox(settings.outbox_dir), StatusBoard()
    worker = CameraWorker(
        CameraConfig(camera.id, camera.display_name, publisher.url, 1), settings, outbox, board
    )

    def state():
        status = board.get(camera.id)
        return status.state if status else None

    publisher.start()
    time.sleep(1.5)
    worker.start()
    try:
        wait_for(lambda: state() == LiveState.ONLINE, 20, "the camera to come online")
        wait_for(lambda: "segment" in kinds(outbox), 2 * LOOP_SECONDS + 15, "a stored segment")

        publisher.stop()  # the stream is interrupted
        wait_for(lambda: state() == LiveState.OFFLINE, 25, "the camera to go offline")
        assert board.get(camera.id).error

        # Away for a few seconds: a blip under 1 s is not recorded, like short static periods.
        time.sleep(OFFLINE_SECONDS)
        publisher.start()  # ... and comes back
        wait_for(lambda: state() == LiveState.ONLINE, 30, "the camera to recover")
        before = kinds(outbox).count("segment")
        wait_for(
            lambda: kinds(outbox).count("segment") > before,
            2 * LOOP_SECONDS + 15,
            "a segment after recovery",
        )
    finally:
        worker.stop()
        worker.join(30)
        publisher.stop()

    assert Uploader(outbox, storage, lambda: Session(engine)).run_once()
    try:
        segments = session.scalars(select(VideoSegment).order_by(VideoSegment.start_time)).all()
        periods = session.scalars(select(StaticPeriod).order_by(StaticPeriod.start_time)).all()
        assert len(segments) >= 2
        for segment in segments:
            assert segment.codec == "h264"
            # A 4 s burst with 10-15 s before and after; never the whole 30 s loop or more.
            assert 14_000 <= segment.duration_ms <= 30_000 or segment is segments[-1]
        rows = sorted([*segments, *periods], key=lambda r: r.start_time)
        listing = "\n".join(
            f"{getattr(r, 'health_status', 'segment')}: {r.start_time:%H:%M:%S.%f} - "
            f"{r.end_time:%H:%M:%S.%f}"
            for r in rows
        )
        offline = [p for p in periods if p.health_status == "offline"]
        assert offline, f"the interruption is recorded as an offline period:\n{listing}"
        assert offline[0].end_time - offline[0].start_time >= timedelta(seconds=1)
        assert any(p.health_status == "ok" and p.reference_frame_uri for p in periods)
        # Nothing overlaps: segments and static periods follow each other in time.
        for before_row, after_row in pairwise(rows):
            assert after_row.start_time >= before_row.end_time - SLACK, listing
    finally:
        storage.delete_prefix(f"segments/{camera.id}/")
        storage.delete_prefix(f"static/{camera.id}/")
