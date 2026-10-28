"""Live ingestion of the enabled RTSP cameras (steps 2.3 + 2.4, ADR 0014).

    python -m services.ingestion

Needs PostgreSQL, MinIO and Redis (`docker compose up -d`) and FFmpeg on PATH. Stop it with
Ctrl+C (or Ctrl+Break / SIGTERM): open segments are stored before it exits.
"""

import logging
import shutil
import signal
import sys
import threading
import time

import redis

from services.ingestion.outbox import Outbox, Uploader
from services.ingestion.status import StatusBoard, StatusPublisher
from services.ingestion.supervisor import Supervisor
from shared.camera_status import HeartbeatLock, get_redis
from shared.config import get_settings
from shared.crypto import CryptoError, check_key
from shared.database.session import get_sessionmaker
from shared.jobs.celery_app import request_analysis
from shared.storage import get_storage
from shared.video.ffmpeg import FFmpegNotInstalled, _require

log = logging.getLogger("services.ingestion")

# At shutdown, wait this long for the outbox to empty; the rest is stored at the next start.
DRAIN_SECONDS = 30


def _stop_on_signals() -> None:
    """Ctrl+Break (Windows) and SIGTERM (a service manager) stop it like Ctrl+C does."""

    def stop(signum, frame):
        raise KeyboardInterrupt

    for name in ("SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), stop)


def main() -> int:
    _stop_on_signals()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    settings = get_settings()
    try:
        check_key()
        _require("ffmpeg")
        _require("ffprobe")
    except (CryptoError, FFmpegNotInstalled) as error:
        log.error("%s", error)
        return 1

    client = get_redis()
    lock = HeartbeatLock(client, settings.camera_status_ttl_seconds)
    try:
        if not lock.acquire():
            log.error(
                "another ingestion process is running (%s). If it crashed, try again in %d s.",
                lock.holder(),
                settings.camera_status_ttl_seconds,
            )
            return 1
    except redis.RedisError as error:
        log.error(
            "Redis is not reachable at %s (run `docker compose up -d`): %s",
            settings.redis_url,
            error,
        )
        return 1

    # Fragments of a run that didn't finish are useless without their planner; the outbox
    # holds everything that was kept, and is stored first.
    shutil.rmtree(settings.buffer_dir, ignore_errors=True)
    outbox = Outbox(settings.outbox_dir)
    outbox.remove_unfinished()
    leftover = len(outbox.pending())
    if leftover:
        log.info("%d item(s) from the last run are waiting in %s", leftover, settings.outbox_dir)

    board = StatusBoard()
    storage = get_storage()
    uploader = Uploader(
        outbox,
        storage,
        get_sessionmaker(),
        on_segment_stored=lambda segment_id: request_analysis([segment_id], settings),
    )
    log.info("automatic analysis of new segments: %s", "on" if settings.analysis_enabled else "off")
    publisher = StatusPublisher(board, client, storage, lock, settings.camera_status_ttl_seconds)
    stop = threading.Event()
    threads = [
        threading.Thread(target=uploader.run_forever, args=(stop,), name="uploader", daemon=True),
        threading.Thread(target=publisher.run_forever, args=(stop,), name="status", daemon=True),
    ]
    for thread in threads:
        thread.start()

    supervisor = Supervisor(settings, get_sessionmaker(), outbox, board)
    log.info(
        "live ingestion started; cameras are reloaded every %d s", settings.camera_reload_seconds
    )
    try:
        while True:
            supervisor.reload()
            deadline = time.monotonic() + settings.camera_reload_seconds
            while time.monotonic() < deadline:
                time.sleep(0.5)  # short sleeps: Ctrl+C is only seen between them on Windows
    except KeyboardInterrupt:
        log.info("stopping: storing the open segments…")
    finally:
        supervisor.stop_all()
        stop.set()
        for thread in threads:
            thread.join(15)
        if threads[0].is_alive():
            log.warning(
                "the uploader is still busy; the rest of the outbox is stored at the next start"
            )
        elif not uploader.drain(DRAIN_SECONDS):
            log.warning(
                "%d item(s) not stored yet; they are stored at the next start",
                len(outbox.pending()),
            )
        try:
            lock.release()
        except redis.RedisError:
            pass
        log.info("live ingestion stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
