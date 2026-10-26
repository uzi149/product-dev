from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.api.deps import get_session, get_storage_client
from shared.database.models import StaticPeriod
from shared.schemas.static_period import StaticPeriodRead
from shared.storage import StorageClient

router = APIRouter(prefix="/static-periods", tags=["static periods"])


@router.get("", response_model=list[StaticPeriodRead])
def list_static_periods(
    camera_id: UUID | None = None,
    start: datetime | None = Query(None, description="Only periods that end after this time"),
    end: datetime | None = Query(None, description="Only periods that start before this time"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> list[StaticPeriod]:
    """Newest first. A period matches when it overlaps the [start, end] range."""
    for name, value in (("start", start), ("end", end)):
        if value is not None and value.tzinfo is None:
            raise HTTPException(422, f"{name} must include a timezone")
    query = select(StaticPeriod).order_by(StaticPeriod.start_time.desc(), StaticPeriod.id)
    if camera_id is not None:
        query = query.where(StaticPeriod.camera_id == camera_id)
    if start is not None:
        query = query.where(StaticPeriod.end_time > start)
    if end is not None:
        query = query.where(StaticPeriod.start_time < end)
    return list(session.scalars(query.limit(limit).offset(offset)))


@router.get("/{static_period_id}/reference-frame")
def reference_frame(
    static_period_id: UUID,
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
) -> RedirectResponse:
    """Redirect to a time-limited MinIO URL of the period's reference image."""
    period = session.get(StaticPeriod, static_period_id)
    if period is None:
        raise HTTPException(404, "static period not found")
    if period.reference_frame_uri is None:
        raise HTTPException(404, "this static period has no reference frame")
    url = storage.presigned_get_url(storage.key_from_uri(period.reference_frame_uri))
    # The presigned URL expires: never let the browser reuse this redirect.
    return RedirectResponse(url, status_code=307, headers={"Cache-Control": "no-store"})
