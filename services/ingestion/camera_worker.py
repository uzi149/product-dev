"""One live camera: FFmpeg run -> change detector -> planner -> outbox, with reconnects (ADR 0014).

Inside a run everything is on stream time. The machine clock anchors stream time to UTC: anchor =
arrival time - stream time of a frame. FFmpeg probes a new stream for a few seconds and then hands
over the first frames in a burst, so for a live source the frame with the least delay in the
first seconds sets the anchor. Stored times are accurate to about 1-2 s.

The anchor is fixed once the first footage of a run goes to the outbox (or the settling time is
over), and the offline period before the run is written then, so every row of a run is placed
with the same anchor and none of them overlap.
"""

import logging
import queue
import shutil
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import cv2

from services.ingestion.outbox import Outbox
from services.ingestion.planner import (
    Action,
    DropFragments,
    KeepSegment,
    KeepStatic,
    PlannerConfig,
    SegmentPlanner,
)
from services.ingestion.status import StatusBoard
from shared.camera_status import LiveState
from shared.config import Settings
from shared.database.models import HealthStatus
from shared.schemas.camera import mask_uri
from shared.video.change_detector import ChangeDetector, State, resolve_params
from shared.video.stream import StreamProcess, is_live, mask_credentials

log = logging.getLogger(__name__)

# Waits between reconnect attempts; after these, `reconnect_max_backoff_seconds`.
BACKOFF_SECONDS = (1, 2, 5, 10)
# A run that lasted this long counts as a recovery: the waits start again from the first one.
STABLE_RUN_SECONDS = 60
_JPEG_QUALITY = 80
# For live sources the anchor is refined over this much stream time (the start-up burst).
ANCHOR_SETTLE_SECONDS = 30


def backoff_seconds(failures: int, maximum: float) -> float:
    """Wait before attempt `failures + 1`: 1, 2, 5, 10, then `maximum` seconds."""
    if failures <= 0:
        return 0.0
    wait = BACKOFF_SECONDS[failures - 1] if failures <= len(BACKOFF_SECONDS) else maximum
    return min(wait, maximum)


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class CameraConfig:
    """What a worker needs from the camera row. A change restarts the worker."""

    id: UUID
    name: str
    source: str  # decrypted: never log it, use `label`
    analysis_fps: int
    detection_config: dict[str, Any] = field(default_factory=dict, hash=False)

    @property
    def label(self) -> str:
        return f"{self.name} ({mask_uri(self.source)})"


@dataclass
class RunResult:
    started_at: datetime
    frames: int = 0
    last_frame_at: datetime | None = None
    error: str | None = None
    lasted: float = 0.0


class CameraWorker(threading.Thread):
    def __init__(
        self,
        camera: CameraConfig,
        settings: Settings,
        outbox: Outbox,
        board: StatusBoard,
        clock: Callable[[], datetime] = utc_now,
    ):
        super().__init__(name=f"camera-{camera.name}", daemon=True)
        self.camera = camera
        self.settings = settings
        self.outbox = outbox
        self.board = board
        self.clock = clock
        self._stopping = threading.Event()
        self._offline_since: datetime | None = None

    def stop(self) -> None:
        self._stopping.set()

    @property
    def stopping(self) -> bool:
        return self._stopping.is_set()

    def run(self) -> None:
        log.info("%s: starting", self.camera.label)
        self.board.set_state(self.camera.id, LiveState.CONNECTING, self.clock())
        failures = 0
        while not self.stopping:
            result = self.run_once()
            if self.stopping:
                break
            failures = 1 if result.lasted >= STABLE_RUN_SECONDS else failures + 1
            if self._offline_since is None:
                self._offline_since = result.last_frame_at or result.started_at
            error = result.error or "the stream ended"
            self.board.set_state(self.camera.id, LiveState.OFFLINE, self._offline_since, error)
            wait = backoff_seconds(failures, self.settings.reconnect_max_backoff_seconds)
            log.warning("%s: %s; reconnecting in %.0f s", self.camera.label, error, wait)
            self._stopping.wait(wait)
        if self._offline_since is not None:
            self._keep_offline(self._offline_since, self.clock())
        log.info("%s: stopped", self.camera.label)

    def run_once(self) -> RunResult:
        """One FFmpeg run, until the stream ends, stalls, or the worker is stopped."""
        result = RunResult(started_at=self.clock())
        started = time.monotonic()
        folder = self.settings.buffer_dir / str(self.camera.id) / uuid4().hex[:12]
        stream = StreamProcess(
            self.camera.source,
            folder,
            fps=self.camera.analysis_fps,
            fragment_seconds=self.settings.fragment_seconds,
            width=self.settings.snapshot_width,
            label=self.camera.name,
        )
        planner = SegmentPlanner(PlannerConfig.from_settings(self.settings))
        run = _Run(self, planner)
        try:
            detector = ChangeDetector(resolve_params(self.camera.detection_config, self.settings))
            stream.start()
            last_frame = time.monotonic()
            while not self.stopping:
                try:
                    item = stream.frames.get(timeout=1)
                except queue.Empty:
                    if time.monotonic() - last_frame > self.settings.stream_stall_seconds:
                        result.error = f"no video for {self.settings.stream_stall_seconds} s"
                        break
                    run.apply(planner.on_fragments(stream.new_fragments()))
                    continue
                if item is None:
                    break  # FFmpeg stopped
                last_frame = time.monotonic()
                t, frame = item
                run.frame(t, frame, detector)
                run.apply(planner.on_fragments(stream.new_fragments()))
        except Exception as error:
            log.exception("%s: the run failed", self.camera.label)
            result.error = mask_credentials(str(error)) or type(error).__name__
        finally:
            stream.stop()
            try:
                run.apply(planner.on_fragments(stream.new_fragments()))
                run.apply(planner.finish(stream.partial_fragment()))
                run.fix_anchor()  # a short run with nothing kept still ends the offline period
            except Exception:
                log.exception("%s: could not keep the last footage of the run", self.camera.label)
            shutil.rmtree(folder, ignore_errors=True)
        if result.error is None and stream.returncode not in (None, 0) and not self.stopping:
            result.error = stream.last_error() or f"ffmpeg exited with code {stream.returncode}"
        if result.error is None and run.frames == 0 and not self.stopping:
            result.error = stream.last_error() or "no video received"
        result.frames = run.frames
        result.last_frame_at = run.wall(run.last_t) if run.frames else None
        result.lasted = time.monotonic() - started
        return result

    # --- called by _Run ---------------------------------------------------------------------

    def came_online(self, at: datetime) -> None:
        self.board.set_state(self.camera.id, LiveState.ONLINE, at)
        log.info("%s: online", self.camera.label)

    def end_offline(self, at: datetime) -> None:
        """The offline period before this run ends at the run's first frame."""
        if self._offline_since is not None:
            self._keep_offline(self._offline_since, max(at, self._offline_since))
            self._offline_since = None

    def _keep_offline(self, start: datetime, end: datetime) -> None:
        if (end - start).total_seconds() >= 1:
            self.outbox.add_static(self.camera.id, start, end, HealthStatus.OFFLINE, None)


class _Run:
    """State of one FFmpeg run: anchor, snapshots, and carrying out the planner's actions."""

    def __init__(self, worker: CameraWorker, planner: SegmentPlanner):
        self.worker = worker
        self.planner = planner
        self.anchor: datetime | None = None
        self._refine_anchor = is_live(worker.camera.source)
        self._anchor_fixed = False
        self.frames = 0
        self.first_t = 0.0
        self.last_t = 0.0
        self._last_snapshot_t: float | None = None
        settings = worker.settings
        # Enough history to find the reference frame of a static period that ended up to a
        # rolling buffer ago (see SegmentPlanner._health_chunk).
        keep = settings.rolling_buffer_seconds // settings.snapshot_interval_seconds + 3
        self._snapshots: deque[tuple[float, bytes]] = deque(maxlen=keep)

    def wall(self, t: float) -> datetime:
        return self.anchor + timedelta(seconds=t)

    def fix_anchor(self) -> None:
        """No more refining: footage placed in time from now on must all use this anchor."""
        if self._anchor_fixed or self.anchor is None:
            return
        self._anchor_fixed = True
        self.worker.end_offline(self.wall(self.first_t))

    def frame(self, t: float, frame, detector: ChangeDetector) -> None:
        camera, board = self.worker.camera, self.worker.board
        now = self.worker.clock()
        estimate = now - timedelta(seconds=t)
        if self.anchor is None:
            self.anchor = estimate
            self.first_t = t
            self.worker.came_online(now)
        elif t > ANCHOR_SETTLE_SECONDS:
            self.fix_anchor()
        elif self._refine_anchor and not self._anchor_fixed and estimate < self.anchor:
            # A frame that arrived with less delay than the first ones (see the module docstring).
            self.anchor = estimate
        self.frames += 1
        self.last_t = t
        sample = detector.update(frame, t)
        if sample.event is not None:
            log.info("%s: %s at %.1f s", camera.name, type(sample.event).__name__, t)
        board.frame(camera.id, self.wall(t))
        board.set_activity(camera.id, detector.state == State.ACTIVE)
        interval = self.worker.settings.snapshot_interval_seconds
        if self._last_snapshot_t is None or t - self._last_snapshot_t >= interval:
            ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
            if ok:
                jpeg = encoded.tobytes()
                self._snapshots.append((t, jpeg))
                board.snapshot(camera.id, jpeg, self.wall(t))
                self._last_snapshot_t = t
        self.apply(self.planner.on_sample(sample))

    def apply(self, actions: list[Action]) -> None:
        camera, outbox = self.worker.camera, self.worker.outbox
        for action in actions:
            if isinstance(action, DropFragments):
                for fragment in action.fragments:
                    Path(fragment.path).unlink(missing_ok=True)
                continue
            if self.anchor is None:
                # Footage without a single frame (FFmpeg died at once): nothing to place in time.
                continue
            self.fix_anchor()
            if isinstance(action, KeepSegment):
                outbox.add_segment(
                    camera.id,
                    [f.path for f in action.fragments],
                    self.wall(action.start),
                    action.score,
                )
            elif isinstance(action, KeepStatic):
                outbox.add_static(
                    camera.id,
                    self.wall(action.start),
                    self.wall(action.end),
                    HealthStatus.OK,
                    self._snapshot_before(action.end),
                )

    def _snapshot_before(self, t: float) -> bytes | None:
        """The newest snapshot taken at or before `t` (the static scene, not the activity)."""
        best = None
        for taken, jpeg in self._snapshots:
            if taken <= t:
                best = jpeg
        return best or (self._snapshots[0][1] if self._snapshots else None)
