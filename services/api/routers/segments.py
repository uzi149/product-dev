from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.api.deps import get_session, get_storage_client
from shared.database.models import VideoSegment
from shared.schemas.segment import SegmentRead
from shared.storage import StorageClient

router = APIRouter(prefix="/segments", tags=["segments"])


def _get_segment(session: Session, segment_id: UUID) -> VideoSegment:
    segment = session.get(VideoSegment, segment_id)
    if segment is None:
        raise HTTPException(404, "segment not found")
    return segment


@router.get("", response_model=list[SegmentRead])
def list_segments(
    camera_id: UUID | None = None,
    start: datetime | None = Query(None, description="Only segments that end after this time"),
    end: datetime | None = Query(None, description="Only segments that start before this time"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> list[VideoSegment]:
    """Newest first. A segment matches when it overlaps the [start, end] range."""
    for name, value in (("start", start), ("end", end)):
        if value is not None and value.tzinfo is None:
            raise HTTPException(422, f"{name} must include a timezone")
    query = select(VideoSegment).order_by(VideoSegment.start_time.desc(), VideoSegment.id)
    if camera_id is not None:
        query = query.where(VideoSegment.camera_id == camera_id)
    if start is not None:
        query = query.where(VideoSegment.end_time > start)
    if end is not None:
        query = query.where(VideoSegment.start_time < end)
    return list(session.scalars(query.limit(limit).offset(offset)))


@router.get("/{segment_id}", response_model=SegmentRead)
def get_segment(segment_id: UUID, session: Session = Depends(get_session)) -> VideoSegment:
    return _get_segment(session, segment_id)


@router.get("/{segment_id}/playback")
def playback(
    segment_id: UUID,
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
) -> RedirectResponse:
    """Redirect to a time-limited MinIO URL; the browser then streams it with range requests."""
    segment = _get_segment(session, segment_id)
    url = storage.presigned_get_url(storage.key_from_uri(segment.storage_uri))
    # The presigned URL expires: never let the browser reuse this redirect.
    return RedirectResponse(url, status_code=307, headers={"Cache-Control": "no-store"})
