from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session

from services.analysis.retry import (
    QueueUnavailable,
    RetryResult,
    is_retryable,
    retry_segments,
    stuck_after,
)
from services.api.deps import get_analysis_queue, get_session, get_storage_client
from shared.database.models import Camera, ProcessingStatus, VideoSegment
from shared.schemas.segment import ReanalyseRequest, ReanalyseResult, SegmentRead, StatusCounts
from shared.storage import StorageClient

router = APIRouter(prefix="/segments", tags=["segments"])


def _get_segment(session: Session, segment_id: UUID) -> VideoSegment:
    segment = session.get(VideoSegment, segment_id)
    if segment is None:
        raise HTTPException(404, "segment not found")
    return segment


def _check_timezones(**values: datetime | None) -> None:
    for name, value in values.items():
        if value is not None and value.tzinfo is None:
            raise HTTPException(422, f"{name} must include a timezone")


def _overlapping(query: Select, start: datetime | None, end: datetime | None) -> Select:
    """A segment matches when it overlaps the [start, end] range."""
    if start is not None:
        query = query.where(VideoSegment.end_time > start)
    if end is not None:
        query = query.where(VideoSegment.start_time < end)
    return query


def _read(segment: VideoSegment, now: datetime) -> SegmentRead:
    read = SegmentRead.model_validate(segment)
    read.retryable = is_retryable(segment, now, stuck_after())
    return read


def _retry(session: Session, ids: list[UUID], queue: Callable[[UUID], None]) -> RetryResult:
    try:
        return retry_segments(session, ids, queue)
    except QueueUnavailable as error:
        raise HTTPException(503, str(error)) from error


@router.get("", response_model=list[SegmentRead])
def list_segments(
    camera_id: UUID | None = None,
    start: datetime | None = Query(None, description="Only segments that end after this time"),
    end: datetime | None = Query(None, description="Only segments that start before this time"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> list[SegmentRead]:
    """Newest first. A segment matches when it overlaps the [start, end] range."""
    _check_timezones(start=start, end=end)
    query = select(VideoSegment).order_by(VideoSegment.start_time.desc(), VideoSegment.id)
    if camera_id is not None:
        query = query.where(VideoSegment.camera_id == camera_id)
    query = _overlapping(query, start, end)
    now = datetime.now(UTC)
    return [_read(s, now) for s in session.scalars(query.limit(limit).offset(offset))]


@router.get("/status-counts", response_model=StatusCounts)
def status_counts(
    camera_id: UUID,
    start: datetime | None = None,
    end: datetime | None = None,
    session: Session = Depends(get_session),
) -> StatusCounts:
    """How many of the camera's segments overlapping [start, end] are in each analysis state."""
    _check_timezones(start=start, end=end)
    stuck = (VideoSegment.status == ProcessingStatus.PROCESSING) & (
        VideoSegment.status_changed_at < datetime.now(UTC) - stuck_after()
    )
    state = case((stuck, "stuck"), else_=VideoSegment.status)
    query = _overlapping(
        select(state, func.count()).where(VideoSegment.camera_id == camera_id), start, end
    ).group_by(state)
    return StatusCounts(**dict(session.execute(query).all()))


@router.post("/reanalyse", response_model=ReanalyseResult, status_code=202)
def reanalyse_failed(
    request: ReanalyseRequest,
    session: Session = Depends(get_session),
    queue: Callable[[UUID], None] = Depends(get_analysis_queue),
) -> ReanalyseResult:
    """Analyse every failed segment of the camera within the range again (ADR 0024). Stuck
    segments are retried one at a time."""
    if session.get(Camera, request.camera_id) is None:
        raise HTTPException(404, "camera not found")
    query = _overlapping(
        select(VideoSegment.id).where(
            VideoSegment.camera_id == request.camera_id,
            VideoSegment.status == ProcessingStatus.FAILED,
        ),
        request.start,
        request.end,
    ).order_by(VideoSegment.start_time)
    result = _retry(session, list(session.scalars(query)), queue)
    return ReanalyseResult(queued=len(result.queued))


@router.get("/{segment_id}", response_model=SegmentRead)
def get_segment(segment_id: UUID, session: Session = Depends(get_session)) -> SegmentRead:
    return _read(_get_segment(session, segment_id), datetime.now(UTC))


@router.post("/{segment_id}/reanalyse", response_model=ReanalyseResult, status_code=202)
def reanalyse(
    segment_id: UUID,
    session: Session = Depends(get_session),
    queue: Callable[[UUID], None] = Depends(get_analysis_queue),
) -> ReanalyseResult:
    """Analyse a failed or stuck segment again; 409 says why any other one can't be."""
    _get_segment(session, segment_id)
    result = _retry(session, [segment_id], queue)
    if segment_id in result.skipped:
        raise HTTPException(409, f"This segment is {result.skipped[segment_id]}")
    return ReanalyseResult(queued=1)


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
