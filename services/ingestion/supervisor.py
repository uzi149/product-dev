"""Keeps one camera worker running per enabled RTSP camera (ADR 0014).

The camera list is read from PostgreSQL every `camera_reload_seconds`: new or re-enabled
cameras get a worker, disabled ones lose theirs, and a worker whose source, frame rate or
detection settings changed is restarted.
"""

import logging
from collections.abc import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.ingestion.camera_worker import CameraConfig, CameraWorker, utc_now
from services.ingestion.outbox import Outbox
from services.ingestion.status import StatusBoard
from shared.camera_status import LiveState
from shared.config import Settings
from shared.crypto import CryptoError, decrypt_text
from shared.database.models import Camera, SourceType

log = logging.getLogger(__name__)

# A worker that doesn't stop within this time is left behind (it is a daemon thread).
STOP_TIMEOUT_SECONDS = 15


def plan_changes(
    running: dict[UUID, CameraConfig], wanted: dict[UUID, CameraConfig]
) -> tuple[list[UUID], list[UUID]]:
    """(cameras to stop, cameras to start). A changed camera is in both."""
    stop = [i for i, config in running.items() if wanted.get(i) != config]
    start = [i for i, config in wanted.items() if running.get(i) != config]
    return stop, start


class Supervisor:
    def __init__(
        self,
        settings: Settings,
        session_factory: Callable[[], Session],
        outbox: Outbox,
        board: StatusBoard,
        worker_factory=CameraWorker,
    ):
        self.settings = settings
        self.session_factory = session_factory
        self.outbox = outbox
        self.board = board
        self.worker_factory = worker_factory
        self.workers: dict[UUID, CameraWorker] = {}
        self._undecryptable: set[UUID] = set()

    def load(self) -> dict[UUID, CameraConfig]:
        """The enabled RTSP cameras, with their source URIs decrypted."""
        with self.session_factory() as session:
            cameras = session.scalars(
                select(Camera).where(
                    Camera.source_type == SourceType.RTSP.value,
                    Camera.enabled.is_(True),
                    Camera.source_uri_encrypted.is_not(None),
                )
            ).all()
        wanted, undecryptable = {}, set()
        for camera in cameras:
            try:
                source = decrypt_text(camera.source_uri_encrypted)
            except CryptoError as error:
                if camera.id not in self._undecryptable:
                    log.error("%s: %s; the camera is not read", camera.display_name, error)
                    self.board.set_state(camera.id, LiveState.OFFLINE, utc_now(), str(error))
                undecryptable.add(camera.id)
                continue
            wanted[camera.id] = CameraConfig(
                id=camera.id,
                name=camera.display_name,
                source=source,
                analysis_fps=camera.analysis_fps,
                detection_config=dict(camera.detection_config or {}),
            )
        # A camera that could be decrypted before (or is gone now) loses its error status.
        for camera_id in self._undecryptable - undecryptable:
            if camera_id not in wanted:
                self.board.remove(camera_id)
        self._undecryptable = undecryptable
        return wanted

    def sync(self, wanted: dict[UUID, CameraConfig]) -> None:
        running = {i: w.camera for i, w in self.workers.items()}
        stop, start = plan_changes(running, wanted)
        for camera_id in stop:
            self._stop(camera_id)
            if camera_id not in wanted:
                self.board.remove(camera_id)
        for camera_id in start:
            worker = self.worker_factory(wanted[camera_id], self.settings, self.outbox, self.board)
            self.workers[camera_id] = worker
            worker.start()

    def reload(self) -> None:
        try:
            wanted = self.load()
        except Exception as error:  # noqa: BLE001 - keep the cameras running while the DB is down
            log.warning("could not load the camera list, keeping the current one: %s", error)
            return
        self.sync(wanted)

    def stop_all(self) -> None:
        for worker in self.workers.values():
            worker.stop()
        for camera_id in list(self.workers):
            self._stop(camera_id)

    def _stop(self, camera_id: UUID) -> None:
        worker = self.workers.pop(camera_id)
        worker.stop()
        worker.join(STOP_TIMEOUT_SECONDS)
        if worker.is_alive():
            log.warning("%s did not stop within %d s", worker.camera.label, STOP_TIMEOUT_SECONDS)
