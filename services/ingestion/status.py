"""Camera status and snapshots, from the camera threads to Redis and MinIO (ADR 0014).

Camera workers only update the in-memory board. One publisher thread does the network I/O:
it uploads the newest snapshot of each camera, writes every status to Redis (refreshed before it
expires), and keeps the ingestion heartbeat alive.
"""

import logging
import threading
import time
from dataclasses import replace
from datetime import datetime
from uuid import UUID

import redis

from shared.camera_status import (
    CameraStatus,
    HeartbeatLock,
    LiveState,
    delete_status,
    write_status,
)
from shared.storage import StorageClient, StorageError
from shared.storage.keys import snapshot_key

log = logging.getLogger(__name__)


class StatusBoard:
    """Thread-safe: written by the camera workers, read by the publisher."""

    def __init__(self):
        self._lock = threading.Lock()
        self._statuses: dict[UUID, CameraStatus] = {}
        self._dirty: set[UUID] = set()
        self._removed: set[UUID] = set()
        self._snapshots: dict[UUID, tuple[bytes, datetime]] = {}

    def set_state(
        self, camera_id: UUID, state: LiveState, since: datetime, error: str | None = None
    ) -> None:
        with self._lock:
            current = self._statuses.get(camera_id) or CameraStatus(state)
            activity = current.activity and state == LiveState.ONLINE
            self._statuses[camera_id] = replace(
                current, state=state, since=since, error=error, activity=activity
            )
            self._dirty.add(camera_id)
            self._removed.discard(camera_id)

    def set_activity(self, camera_id: UUID, active: bool) -> None:
        with self._lock:
            current = self._statuses.get(camera_id)
            if current is not None and current.activity != active:
                self._statuses[camera_id] = replace(current, activity=active)
                self._dirty.add(camera_id)

    def frame(self, camera_id: UUID, at: datetime) -> None:
        with self._lock:
            current = self._statuses.get(camera_id)
            if current is not None:
                self._statuses[camera_id] = replace(current, last_frame_at=at)

    def snapshot(self, camera_id: UUID, jpeg: bytes, at: datetime) -> None:
        with self._lock:
            self._snapshots[camera_id] = (jpeg, at)

    def remove(self, camera_id: UUID) -> None:
        with self._lock:
            self._statuses.pop(camera_id, None)
            self._snapshots.pop(camera_id, None)
            self._dirty.discard(camera_id)
            self._removed.add(camera_id)

    def get(self, camera_id: UUID) -> CameraStatus | None:
        with self._lock:
            status = self._statuses.get(camera_id)
            return None if status is None else replace(status)

    def take_snapshots(self) -> dict[UUID, tuple[bytes, datetime]]:
        """The newest snapshot of each camera since the last call."""
        with self._lock:
            snapshots, self._snapshots = self._snapshots, {}
            return snapshots

    def take(self) -> tuple[dict[UUID, CameraStatus], set[UUID], set[UUID]]:
        """(all statuses, changed ids, removed ids) for one publish round."""
        with self._lock:
            result = (dict(self._statuses), self._dirty, self._removed)
            self._dirty, self._removed = set(), set()
            return result

    def snapshot_stored(self, camera_id: UUID, at: datetime) -> None:
        with self._lock:
            current = self._statuses.get(camera_id)
            if current is not None:
                self._statuses[camera_id] = replace(current, last_snapshot_at=at)
                self._dirty.add(camera_id)


class StatusPublisher:
    def __init__(
        self,
        board: StatusBoard,
        client: redis.Redis,
        storage: StorageClient,
        lock: HeartbeatLock,
        ttl: int,
        refresh_seconds: float = 5,
    ):
        self.board = board
        self.client = client
        self.storage = storage
        self.lock = lock
        self.ttl = ttl
        self.refresh_seconds = refresh_seconds
        self._last_full = 0.0
        self._failing: set[str] = set()

    def publish_once(self) -> None:
        for camera_id, (jpeg, at) in self.board.take_snapshots().items():
            key = snapshot_key(camera_id)
            if self._ok(
                "storage", lambda k=key, j=jpeg: self.storage.upload_bytes(k, j, "image/jpeg")
            ):
                self.board.snapshot_stored(camera_id, at)
        statuses, dirty, removed = self.board.take()
        full = time.monotonic() - self._last_full >= self.refresh_seconds
        targets = statuses if full else {i: s for i, s in statuses.items() if i in dirty}

        def write() -> None:
            for camera_id, status in targets.items():
                write_status(self.client, camera_id, status, self.ttl)
            for camera_id in removed:
                delete_status(self.client, camera_id)
            # After a long Redis outage the heartbeat may have expired: take it back if free.
            if full and not self.lock.refresh() and not self.lock.acquire():
                log.error("the ingestion heartbeat belongs to %s now", self.lock.holder())

        if self._ok("redis", write) and full:
            self._last_full = time.monotonic()

    def run_forever(self, stop: threading.Event, interval: float = 1) -> None:
        while not stop.is_set():
            try:
                self.publish_once()
            except Exception:
                log.exception("publishing camera status failed")
            stop.wait(interval)

    def _ok(self, what: str, call) -> bool:
        """Run a network call; log once per outage instead of every second."""
        try:
            call()
        except (redis.RedisError, StorageError) as error:
            if what not in self._failing:
                log.warning("%s unreachable, camera status not published: %s", what, error)
                self._failing.add(what)
            return False
        if what in self._failing:
            log.info("%s reachable again", what)
            self._failing.discard(what)
        return True
