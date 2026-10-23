import logging
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.api.deps import get_session, get_storage_client, get_upload_queue
from shared.config import get_settings
from shared.database.models import Camera, ProcessingStatus, Upload
from shared.schemas.upload import UploadRead
from shared.storage import StorageClient
from shared.storage.keys import upload_key
from shared.video.ffmpeg import FFmpegError, FFmpegNotInstalled, probe

log = logging.getLogger(__name__)

router = APIRouter(prefix="/videos", tags=["videos"])

_CHUNK = 1024 * 1024


def _save_upload(upload: UploadFile, dest: Path, max_bytes: int) -> None:
    written = 0
    with dest.open("wb") as out:
        while chunk := upload.file.read(_CHUNK):
            written += len(chunk)
            if written > max_bytes:
                raise HTTPException(413, f"file is larger than {max_bytes // (1024 * 1024)} MB")
            out.write(chunk)
    if written == 0:
        raise HTTPException(422, "the file is empty")


@router.post("/upload", response_model=UploadRead, status_code=202)
def upload_video(
    file: UploadFile = File(...),
    camera_id: UUID = Form(...),
    start_time: datetime | None = Form(None),
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
    queue: Callable[[UUID], None] = Depends(get_upload_queue),
) -> Upload:
    """Accept a video for background ingestion: only its active parts will be stored.

    The file is checked straight away (a non-video is rejected here), staged in MinIO and
    queued. Follow the result with GET /videos/uploads/{id}.
    """
    if session.get(Camera, camera_id) is None:
        raise HTTPException(404, "camera not found")
    if start_time is None:
        start_time = datetime.now(UTC)
    elif start_time.tzinfo is None:
        raise HTTPException(422, "start_time must include a timezone, e.g. 2026-01-01T12:00:00Z")

    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    upload_id = uuid4()
    key = upload_key(upload_id)
    with tempfile.TemporaryDirectory(prefix="sentinel-upload-") as tmp:
        raw = Path(tmp, "upload")
        _save_upload(file, raw, max_bytes)
        try:
            info = probe(raw)
        except FFmpegNotInstalled as error:
            raise HTTPException(500, str(error)) from error
        except FFmpegError as error:
            raise HTTPException(422, f"not a readable video: {error}") from error
        if info.duration_ms <= 0:
            raise HTTPException(422, "the video has no duration")
        storage.upload_file(key, raw, content_type=file.content_type or "application/octet-stream")

    upload = Upload(
        id=upload_id,
        camera_id=camera_id,
        filename=(file.filename or "upload")[:255],
        start_time=start_time,
        status=ProcessingStatus.PENDING,
    )
    try:
        session.add(upload)
        session.commit()
    except Exception:
        session.rollback()
        storage.delete(key)  # don't leave a staged file without a row
        raise

    try:
        queue(upload_id)
    except Exception as error:
        log.exception("could not queue upload %s", upload_id)
        storage.delete(key)
        upload.status = ProcessingStatus.FAILED
        upload.error = "the job queue (Redis) is not reachable"
        upload.finished_at = datetime.now(UTC)
        session.commit()
        raise HTTPException(503, "the job queue is not reachable; try again later") from error
    session.refresh(upload)
    return upload


@router.get("/uploads", response_model=list[UploadRead])
def list_uploads(
    camera_id: UUID | None = None,
    limit: int = Query(20, ge=1, le=200),
    session: Session = Depends(get_session),
) -> list[Upload]:
    """Most recent first."""
    query = select(Upload).order_by(Upload.created_at.desc(), Upload.id)
    if camera_id is not None:
        query = query.where(Upload.camera_id == camera_id)
    return list(session.scalars(query.limit(limit)))


@router.get("/uploads/{upload_id}", response_model=UploadRead)
def get_upload(upload_id: UUID, session: Session = Depends(get_session)) -> Upload:
    upload = session.get(Upload, upload_id)
    if upload is None:
        raise HTTPException(404, "upload not found")
    return upload
