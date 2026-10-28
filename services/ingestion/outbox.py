"""Local hand-off between the camera threads and MinIO + PostgreSQL (ADR 0014).

A camera thread moves the fragments it wants to keep into a new item folder and goes on; it
never waits on the network. The uploader stores the items in order, and keeps an item on disk
while MinIO or PostgreSQL can't be reached, so a short outage loses no footage.

    outbox/{item}/item.json          what to store (kind, ids, times, score); written last
    outbox/{item}/frag-000012.ts...  a segment's fragments, joined by the uploader
    outbox/{item}/reference.jpg      a static period's reference frame

A folder without `item.json` is unfinished (being written, or left by a crash) and is ignored.
Folders are never renamed: on Windows that fails while a virus scanner still has a new file open.
"""

import itertools
import json
import logging
import os
import shutil
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from shared.database.models import Camera, HealthStatus, StaticPeriod, VideoSegment
from shared.storage import StorageClient, StorageError
from shared.storage.keys import segment_key, static_frame_key
from shared.video.ffmpeg import FFmpegError, join_fragments, probe

log = logging.getLogger(__name__)

ITEM_FILE = "item.json"
REFERENCE_FILE = "reference.jpg"
_JOINED = "segment.mp4"
# Renames of files that were just written can fail for a moment on Windows (a virus scanner
# reading them); they are retried this many times, 0.1 s, 0.2 s, ... apart.
_RENAME_ATTEMPTS = 6


def replace_file(src: Path, dst: Path) -> None:
    """os.replace, retried briefly when Windows says the file is in use."""
    for attempt in range(_RENAME_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _RENAME_ATTEMPTS - 1:
                raise
            time.sleep(0.1 * (attempt + 1))


class Outbox:
    def __init__(self, folder: Path):
        self.folder = folder
        self.failed_folder = folder.parent / "outbox-failed"
        self._counter = itertools.count()
        self._lock = threading.Lock()

    def add_segment(
        self, camera_id: UUID, fragments: list[Path], start_time: datetime, score: float
    ) -> Path:
        data = {
            "kind": "segment",
            "id": str(uuid4()),
            "camera_id": str(camera_id),
            "start_time": start_time.isoformat(),
            "activity_score": score,
            "fragments": [f.name for f in fragments],
        }
        return self._add(data, moves=fragments)

    def add_static(
        self,
        camera_id: UUID,
        start_time: datetime,
        end_time: datetime,
        health: HealthStatus,
        reference_jpeg: bytes | None,
    ) -> Path:
        data = {
            "kind": "static",
            "id": str(uuid4()),
            "camera_id": str(camera_id),
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "health_status": health.value,
            "reference_frame": REFERENCE_FILE if reference_jpeg else None,
        }
        return self._add(data, files={REFERENCE_FILE: reference_jpeg} if reference_jpeg else {})

    def pending(self) -> list[Path]:
        """Finished items, oldest first."""
        if not self.folder.exists():
            return []
        return sorted(p for p in self.folder.iterdir() if (p / ITEM_FILE).is_file())

    def remove_unfinished(self) -> None:
        """Folders without `item.json`: unfinished or already stored (at start, before any
        camera runs)."""
        if self.folder.exists():
            for path in self.folder.iterdir():
                if path.is_dir() and not (path / ITEM_FILE).is_file():
                    shutil.rmtree(path, ignore_errors=True)

    def done(self, item: Path) -> None:
        """Remove a stored item. `item.json` goes first, so a folder left over is ignored."""
        (item / ITEM_FILE).unlink(missing_ok=True)
        shutil.rmtree(item, ignore_errors=True)

    def _add(
        self, data: dict, moves: list[Path] = (), files: dict[str, bytes] | None = None
    ) -> Path:
        with self._lock:
            name = f"{time.time_ns():020d}-{next(self._counter):06d}-{data['kind']}"
        item = self.folder / name
        item.mkdir(parents=True)
        for path in moves:
            replace_file(path, item / path.name)  # same drive: an instant rename
        for file_name, content in (files or {}).items():
            (item / file_name).write_bytes(content)
        partial = item / f"{ITEM_FILE}.partial"
        partial.write_text(json.dumps(data), encoding="utf-8")
        replace_file(partial, item / ITEM_FILE)  # from now on the uploader sees the item
        return item


class Uploader:
    """Stores outbox items in MinIO + PostgreSQL. An item is stored once, also when retried."""

    def __init__(
        self,
        outbox: Outbox,
        storage: StorageClient,
        session_factory: sessionmaker[Session] | type[Session],
        on_segment_stored: Callable[[UUID], object] | None = None,
    ):
        self.outbox = outbox
        self.storage = storage
        self.session_factory = session_factory
        # Called with the id of each newly stored segment: queues its analysis (step 3.3).
        self.on_segment_stored = on_segment_stored
        self._waiting = False

    def run_once(self) -> bool:
        """Store the pending items in order. False when one has to wait (MinIO or DB down)."""
        for item in self.outbox.pending():
            if not self.process(item):
                return False
        return True

    def run_forever(self, stop: threading.Event, retry_seconds: float = 10) -> None:
        while not stop.is_set():
            try:
                done = self.run_once()
            except Exception:
                log.exception("the uploader failed; trying again in %.0f s", retry_seconds)
                done = False
            stop.wait(1 if done else retry_seconds)

    def drain(self, timeout: float) -> bool:
        """At shutdown: store what is pending, for at most `timeout` seconds."""
        deadline = time.monotonic() + timeout
        while self.outbox.pending():
            if time.monotonic() >= deadline:
                return False
            if not self.run_once():
                time.sleep(1)
        return True

    def process(self, item: Path) -> bool:
        """True when the item is finished with (stored, or given up); False to retry it later."""
        try:
            data = json.loads((item / ITEM_FILE).read_text(encoding="utf-8"))
            if data["kind"] == "segment":
                self._store_segment(item, data)
            else:
                self._store_static(item, data)
        except IntegrityError as error:
            # Stored by a concurrent attempt, or the camera was deleted meanwhile.
            log.warning("%s was not stored: %s", item.name, error.orig)
        except (OperationalError, StorageError) as error:
            if not self._waiting:
                log.warning(
                    "storage or database unreachable, footage waits in %s: %s",
                    self.outbox.folder,
                    error,
                )
                self._waiting = True
            return False
        except (FFmpegError, ValueError, KeyError, OSError) as error:
            # Damaged footage can't be stored; keep it aside for a look instead of retrying forever.
            log.error(
                "could not store %s, moved to %s: %s", item.name, self.outbox.failed_folder, error
            )
            shutil.copytree(item, self.outbox.failed_folder / item.name, dirs_exist_ok=True)
            self.outbox.done(item)
            return True
        if self._waiting:
            log.info("storage and database reachable again; uploading the waiting footage")
            self._waiting = False
        self.outbox.done(item)
        return True

    def _store_segment(self, item: Path, data: dict) -> None:
        segment_id, camera_id = UUID(data["id"]), UUID(data["camera_id"])
        with self.session_factory() as session:
            if session.get(VideoSegment, segment_id) is not None:
                return  # stored by an earlier attempt
            if session.get(Camera, camera_id) is None:
                log.warning("camera %s no longer exists; its segment is dropped", camera_id)
                return
            joined = item / _JOINED
            if joined.exists():
                info = probe(joined)
            else:
                # Joined under a temporary name, so a crash never leaves a half-written file.
                partial = item / "joining.mp4"
                info = join_fragments([item / name for name in data["fragments"]], partial)
                replace_file(partial, joined)
            start = datetime.fromisoformat(data["start_time"])
            key = segment_key(camera_id, start, segment_id)
            uri = self.storage.upload_file(key, joined, content_type="video/mp4")
            session.add(
                VideoSegment(
                    id=segment_id,
                    camera_id=camera_id,
                    start_time=start,
                    end_time=start + timedelta(milliseconds=info.duration_ms),
                    duration_ms=info.duration_ms,
                    storage_uri=uri,
                    activity_score=data["activity_score"],
                    codec=info.codec,
                    width=info.width,
                    height=info.height,
                )
            )
            session.commit()
            log.info(
                "camera %s: stored a %.1f s segment starting %s",
                camera_id,
                info.duration_s,
                start.isoformat(timespec="seconds"),
            )
            if self.on_segment_stored is not None:
                self.on_segment_stored(segment_id)

    def _store_static(self, item: Path, data: dict) -> None:
        period_id, camera_id = UUID(data["id"]), UUID(data["camera_id"])
        with self.session_factory() as session:
            if session.get(StaticPeriod, period_id) is not None:
                return
            if session.get(Camera, camera_id) is None:
                log.warning("camera %s no longer exists; its static period is dropped", camera_id)
                return
            uri = None
            if data.get("reference_frame"):
                key = static_frame_key(camera_id, period_id)
                uri = self.storage.upload_file(
                    key, item / data["reference_frame"], content_type="image/jpeg"
                )
            session.add(
                StaticPeriod(
                    id=period_id,
                    camera_id=camera_id,
                    start_time=datetime.fromisoformat(data["start_time"]),
                    end_time=datetime.fromisoformat(data["end_time"]),
                    reference_frame_uri=uri,
                    health_status=data["health_status"],
                )
            )
            session.commit()
