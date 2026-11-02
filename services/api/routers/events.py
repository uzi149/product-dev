"""Reported events (scope §13, step 5.3): review, acknowledge, export, delete.

Only alerted events are shown: a held event waits for its post-roll footage (ADR 0033). The
evidence plays through a playback manifest like a query result's (scope §11); an export joins it
into one MP4 with "… not shown" cards between separated parts. Deleting an event removes its
evidence rows and export file; its footage is then no longer protected (ADR 0034).
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from services.api.deps import get_export_queue, get_session, get_storage_client
from services.events.playback import (
    best_keyframes,
    event_manifest,
    evidence_rows,
    export_file_name,
)
from shared.database.models import Camera, Event, EventStatus, ExportStatus, Trigger
from shared.schemas.event import (
    Acknowledge,
    EventCounts,
    EventDetail,
    EventList,
    EventRead,
    ExportState,
)
from shared.storage import StorageClient
from shared.storage.keys import export_key

router = APIRouter(prefix="/events", tags=["events"])


def _alerted() -> Select:
    return (
        select(Event, Trigger.name, Camera.display_name)
        .join(Trigger, Trigger.id == Event.trigger_id)
        .join(Camera, Camera.id == Event.camera_id)
        .where(Event.alerted_at.is_not(None))
    )


def _get(session: Session, event_id: UUID) -> tuple[Event, str, str]:
    row = session.execute(_alerted().where(Event.id == event_id)).first()
    if row is None:
        raise HTTPException(404, "event not found")
    event, trigger, camera = row
    return event, trigger, camera


def _read(event: Event, trigger: str, camera: str, keyframe: UUID | None) -> dict:
    verification = (event.details_json or {}).get("verification") or {}
    return {
        "id": event.id,
        "trigger_id": event.trigger_id,
        "trigger_name": trigger,
        "camera_id": event.camera_id,
        "camera_name": camera,
        "start_time": event.start_time,
        "end_time": event.end_time,
        "confidence": event.confidence,
        "summary": event.summary,
        "status": event.status,
        "verification": verification.get("status"),
        "verification_reason": verification.get("reason"),
        "keyframe_id": keyframe,
        "alerted_at": event.alerted_at,
        "acknowledged_at": event.acknowledged_at,
        "note": event.note,
        "export_status": event.export_status,
    }


def _stale(event: Event) -> bool:
    """The export was made before the event was last extended."""
    return event.exported_at is None or event.exported_at < event.updated_at


@router.get("", response_model=EventList)
def list_events(
    status: EventStatus | None = None,
    camera_id: UUID | None = None,
    trigger_id: UUID | None = None,
    start: datetime | None = Query(None, description="events starting at or after"),
    end: datetime | None = Query(None, description="events starting before"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> EventList:
    """The latest alerts first."""
    query = _alerted()
    if status is not None:
        query = query.where(Event.status == status)
    if camera_id is not None:
        query = query.where(Event.camera_id == camera_id)
    if trigger_id is not None:
        query = query.where(Event.trigger_id == trigger_id)
    if start is not None:
        query = query.where(Event.start_time >= start)
    if end is not None:
        query = query.where(Event.start_time < end)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.execute(
        query.order_by(Event.alerted_at.desc(), Event.id.desc()).limit(limit).offset(offset)
    ).all()
    keyframes = best_keyframes(session, [r[0] for r in rows])
    return EventList(
        items=[EventRead(**_read(e, t, c, keyframes.get(e.id))) for e, t, c in rows],
        total=total,
    )


@router.get("/counts", response_model=EventCounts)
def count_events(session: Session = Depends(get_session)) -> EventCounts:
    """For the sidebar badge (new) and the Overview page."""
    rows = session.execute(
        select(Event.status, func.count())
        .where(Event.alerted_at.is_not(None))
        .group_by(Event.status)
    ).all()
    counts = dict(rows)
    return EventCounts(
        new=counts.get(EventStatus.NEW, 0),
        confirmed=counts.get(EventStatus.CONFIRMED, 0),
        dismissed=counts.get(EventStatus.DISMISSED, 0),
        total=sum(counts.values()),
    )


@router.get("/{event_id}", response_model=EventDetail)
def get_event(event_id: UUID, session: Session = Depends(get_session)) -> EventDetail:
    event, trigger, camera = _get(session, event_id)
    details = event.details_json or {}
    keyframe = best_keyframes(session, [event]).get(event.id)
    # Alert time - action time only means something for a live camera: an uploaded file's
    # footage can be from any day.
    live = session.get(Camera, event.camera_id).source_type == "rtsp"
    return EventDetail(
        **_read(event, trigger, camera, keyframe),
        mode=details.get("mode"),
        delay_seconds=details.get("delay_seconds") if live else None,
        details=details,
        manifest=event_manifest(event, evidence_rows(session, event.id)),
    )


@router.post("/{event_id}/acknowledge", response_model=EventRead)
def acknowledge(
    event_id: UUID, body: Acknowledge, session: Session = Depends(get_session)
) -> EventRead:
    """Confirm or dismiss (or back to new). A note given replaces the old one."""
    event, trigger, camera = _get(session, event_id)
    event.status = body.status
    event.acknowledged_at = None if body.status == EventStatus.NEW else datetime.now(UTC)
    if "note" in body.model_fields_set:
        event.note = body.note
    session.commit()
    keyframe = best_keyframes(session, [event]).get(event.id)
    return EventRead(**_read(event, trigger, camera, keyframe))


def _export_state(event: Event, trigger: str, camera: str, storage: StorageClient) -> ExportState:
    if event.export_status == ExportStatus.DONE and _stale(event):
        return ExportState(status=ExportStatus.NONE, exported_at=event.exported_at)
    state = ExportState(
        status=event.export_status, error=event.export_error, exported_at=event.exported_at
    )
    if event.export_status == ExportStatus.DONE:
        state.file_name = export_file_name(trigger, camera, event.start_time)
        state.url = storage.presigned_get_url(
            storage.key_from_uri(event.export_uri), download_name=state.file_name
        )
    return state


@router.post("/{event_id}/export", response_model=ExportState, status_code=202)
def export(
    event_id: UUID,
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
    queue=Depends(get_export_queue),
) -> ExportState:
    """Join the evidence into one MP4 in the background; an up-to-date export is reused."""
    event, trigger, camera = _get(session, event_id)
    up_to_date = event.export_status == ExportStatus.DONE and not _stale(event)
    if up_to_date or event.export_status == ExportStatus.RUNNING:
        return _export_state(event, trigger, camera, storage)
    event.export_status = ExportStatus.RUNNING
    event.export_error = None
    session.commit()
    try:
        queue(event.id)
    except Exception as error:  # noqa: BLE001 - any broker failure
        event.export_status = ExportStatus.FAILED
        event.export_error = "the export queue is not reachable"
        session.commit()
        raise HTTPException(503, f"the export queue is not reachable (is Redis running?): {error}")
    return _export_state(event, trigger, camera, storage)


@router.get("/{event_id}/export", response_model=ExportState)
def export_state(
    event_id: UUID,
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
) -> ExportState:
    """When done: a time-limited link that downloads the MP4."""
    event, trigger, camera = _get(session, event_id)
    return _export_state(event, trigger, camera, storage)


def delete_events(session: Session, storage: StorageClient, events: list[Event]) -> None:
    """Events with their evidence rows (database cascade) and export files (ADR 0034)."""
    for event in events:
        storage.delete(export_key(event.id))
        session.delete(event)


@router.delete("/{event_id}", status_code=204)
def delete_event(
    event_id: UUID,
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
) -> Response:
    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "event not found")
    delete_events(session, storage, [event])
    session.commit()
    return Response(status_code=204)
