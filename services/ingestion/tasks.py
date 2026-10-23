"""Celery tasks for file ingestion (ADR 0011-0013)."""

import logging
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from services.ingestion.file_ingest import ingest_file, remove_objects
from shared.config import Settings, get_settings
from shared.database.models import Camera, ProcessingStatus, Upload
from shared.database.session import get_sessionmaker
from shared.jobs.celery_app import INGEST_UPLOAD, celery_app
from shared.storage import NotFound, StorageClient, StorageError, get_storage
from shared.storage.keys import upload_key
from shared.video.ffmpeg import FFmpegError
from shared.video.frames import FrameReadError

log = logging.getLogger(__name__)

MAX_RETRIES = 2


class TransientError(Exception):
    """Postgres or MinIO was unreachable; the job can be tried again."""


def _is_transient(error: Exception) -> bool:
    return isinstance(error, OperationalError) or (
        isinstance(error, StorageError) and not isinstance(error, NotFound)
    )


def _message(error: Exception) -> str:
    if isinstance(error, NotFound):
        return "the uploaded file is no longer in storage"
    if isinstance(error, (FFmpegError, FrameReadError, ValueError)):
        return str(error)
    return f"unexpected error: {error}"


def process_upload(
    upload_id: UUID,
    session_factory: Callable[[], Session] | None = None,
    storage: StorageClient | None = None,
    settings: Settings | None = None,
) -> None:
    """Ingest one staged upload and record the outcome on its `uploads` row.

    Permanent failures (a bad video) mark the row `failed`. Transient ones (Postgres or MinIO
    unreachable) raise TransientError and keep the staged file, so the job can run again.
    """
    session_factory = session_factory or get_sessionmaker()
    storage = storage or get_storage()
    settings = settings or get_settings()

    with session_factory() as session:
        upload = session.get(Upload, upload_id)
        if upload is None:
            log.warning("upload %s does not exist; nothing to do", upload_id)
            return
        if upload.status == ProcessingStatus.DONE:
            return  # delivered twice (late acks): already finished
        upload.status = ProcessingStatus.PROCESSING
        upload.error = None
        session.commit()
        camera = session.get(Camera, upload.camera_id)

        keys: list[str] = []
        try:
            with tempfile.TemporaryDirectory(prefix="sentinel-upload-") as tmp:
                source = Path(tmp, "source")
                storage.download_file(upload_key(upload_id), source)
                result = ingest_file(session, storage, source, camera, upload.start_time, settings)
                keys = result.keys
                upload.status = ProcessingStatus.DONE
                upload.segments_created = len(result.segments)
                upload.static_periods_created = len(result.static_periods)
                upload.finished_at = datetime.now(UTC)
                session.commit()
            log.info(
                "upload %s: %d segment(s), %d static period(s)",
                upload_id,
                len(result.segments),
                len(result.static_periods),
            )
        except Exception as error:
            session.rollback()
            remove_objects(storage, keys)
            if _is_transient(error):
                raise TransientError(str(error)) from error
            log.exception("upload %s failed", upload_id)
            _mark_failed(session, storage, upload_id, _message(error))
            return
    remove_objects(storage, [upload_key(upload_id)])


def mark_failed(upload_id: UUID, message: str) -> None:
    """Give up on an upload: record the error and remove the staged file."""
    with get_sessionmaker()() as session:
        _mark_failed(session, get_storage(), upload_id, message)


def _mark_failed(session: Session, storage: StorageClient, upload_id: UUID, message: str) -> None:
    upload = session.get(Upload, upload_id)
    if upload is not None:
        upload.status = ProcessingStatus.FAILED
        upload.error = message[:1000]
        upload.finished_at = datetime.now(UTC)
        session.commit()
    remove_objects(storage, [upload_key(upload_id)])


@celery_app.task(name=INGEST_UPLOAD, bind=True, max_retries=MAX_RETRIES)
def ingest_upload(self, upload_id: str) -> None:
    try:
        process_upload(UUID(upload_id))
    except TransientError as error:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=error, countdown=10 * 2**self.request.retries) from error
        mark_failed(UUID(upload_id), f"gave up after {self.max_retries} retries: {error}")
