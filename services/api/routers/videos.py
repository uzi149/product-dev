import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from services.api.deps import get_session, get_storage_client
from shared.config import get_settings
from shared.database.models import Camera, VideoSegment
from shared.schemas.segment import SegmentRead
from shared.storage import StorageClient
from shared.storage.keys import segment_key
from shared.video.ffmpeg import FFmpegError, FFmpegNotInstalled, probe, to_h264_mp4

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


@router.post("/upload", response_model=SegmentRead, status_code=201)
def upload_video(
    file: UploadFile = File(...),
    camera_id: UUID = Form(...),
    start_time: datetime | None = Form(None),
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
) -> VideoSegment:
    """Phase 1: the whole file becomes one segment (activity cutting arrives in Phase 2)."""
    if session.get(Camera, camera_id) is None:
        raise HTTPException(404, "camera not found")
    if start_time is None:
        start_time = datetime.now(UTC)
    elif start_time.tzinfo is None:
        raise HTTPException(422, "start_time must include a timezone, e.g. 2026-01-01T12:00:00Z")

    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    with tempfile.TemporaryDirectory(prefix="sentinel-upload-") as tmp:
        raw, converted = Path(tmp, "upload"), Path(tmp, "segment.mp4")
        _save_upload(file, raw, max_bytes)
        try:
            to_h264_mp4(raw, converted)
            info = probe(converted)
        except FFmpegNotInstalled as error:
            raise HTTPException(500, str(error)) from error
        except FFmpegError as error:
            raise HTTPException(422, f"not a readable video: {error}") from error
        if info.duration_ms <= 0:
            raise HTTPException(422, "the video has no duration")

        segment_id = uuid4()
        key = segment_key(camera_id, start_time, segment_id)
        uri = storage.upload_file(key, converted, content_type="video/mp4")
        try:
            segment = VideoSegment(
                id=segment_id,
                camera_id=camera_id,
                start_time=start_time,
                end_time=start_time + timedelta(milliseconds=info.duration_ms),
                duration_ms=info.duration_ms,
                storage_uri=uri,
                codec=info.codec,
                width=info.width,
                height=info.height,
            )
            session.add(segment)
            session.commit()
        except Exception:
            session.rollback()
            storage.delete(key)  # don't leave an object without a row
            raise
        session.refresh(segment)
        return segment
