"""Reported events (scope §13, step 5.3): review, acknowledge, export, delete.

Only alerted events are shown: a held event waits for its post-roll footage (ADR 0033). The
evidence plays through a playback manifest like a query result's (scope §11); an export joins it
into one MP4 with "… not shown" cards between separated parts. Deleting an event removes its
evidence rows and export files; its footage is then no longer protected (ADR 0034).

Marks (step 5.7, ADR 0038): `GET /events/{id}/marks` gives the boxes the player draws; an export
`?marks=true` is a second MP4 with them drawn in, once the marks are done.
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
from services.marks.state import marks_state
from shared.database.models import (
    Camera,
    Event,
    EventStatus,
    EvidenceMarks,
    ExportStatus,
    MarksStatus,
    Trigger,
)
from shared.schemas.event import (
    Acknowledge,
    EventCounts,
    EventDetail,
    EventList,
    EventRead,
    ExportState,
)
from shared.schemas.marks import MarksState
from shared.storage import StorageClient
from shared.storage.keys import export_key, marked_export_key

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


def _marks(session: Session, event: Event) -> EvidenceMarks | None:
    return session.scalars(select(EvidenceMarks).where(EvidenceMarks.event_id == event.id)).first()


class _Export:
    """The plain export's columns, or the marked one's (step 5.7)."""

    def __init__(self, event: Event, marked: bool):
        self.event = event
        self.prefix = "marked_export" if marked else "export"
        self.at = "marked_exported_at" if marked else "exported_at"

    def get(self, name: str):
        return getattr(self.event, f"{self.prefix}_{name}")

    def set(self, name: str, value) -> None:
        setattr(self.event, f"{self.prefix}_{name}", value)

    @property
    def exported_at(self) -> datetime | None:
        return getattr(self.event, self.at)


def _stale(event: Event, marked: bool = False, marks: EvidenceMarks | None = None) -> bool:
    """The export was made before the event was last extended (or, with marks, before the marks
    were last made)."""
    exported_at = _Export(event, marked).exported_at
    if exported_at is None or exported_at < event.updated_at:
        return True
    made = marks.finished_at if marked and marks is not None else None
    return made is not None and exported_at < made


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
        marks_status=marks_state(_marks(session, event)).status,
        marked_export_status=event.marked_export_status,
    )


@router.get("/{event_id}/marks", response_model=MarksState)
def get_marks(event_id: UUID, session: Session = Depends(get_session)) -> MarksState:
    """The boxes around the people and objects of the evidence (step 5.7)."""
    event, _, _ = _get(session, event_id)
    return marks_state(_marks(session, event))


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


def _export_state(
    session: Session,
    event: Event,
    trigger: str,
    camera: str,
    storage: StorageClient,
    marked: bool = False,
) -> ExportState:
    export = _Export(event, marked)
    status = export.get("status")
    marks = _marks(session, event) if marked else None
    if status == ExportStatus.DONE and _stale(event, marked, marks):
        return ExportState(status=ExportStatus.NONE, exported_at=export.exported_at)
    state = ExportState(status=status, error=export.get("error"), exported_at=export.exported_at)
    if status == ExportStatus.DONE:
        state.file_name = export_file_name(trigger, camera, event.start_time, marked=marked)
        state.url = storage.presigned_get_url(
            storage.key_from_uri(export.get("uri")), download_name=state.file_name
        )
    return state


@router.post("/{event_id}/export", response_model=ExportState, status_code=202)
def export(
    event_id: UUID,
    marks: bool = Query(False, description="the second file, with the marks drawn in"),
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
    queue=Depends(get_export_queue),
) -> ExportState:
    """Join the evidence into one MP4 in the background; an up-to-date export is reused. With
    `marks`, refused (409) until the event's marks are done."""
    event, trigger, camera = _get(session, event_id)
    row = _marks(session, event) if marks else None
    if marks and (row is None or row.status != MarksStatus.DONE):
        raise HTTPException(409, "the marks of this event aren't ready")
    export = _Export(event, marks)
    status = export.get("status")
    up_to_date = status == ExportStatus.DONE and not _stale(event, marks, row)
    if up_to_date or status == ExportStatus.RUNNING:
        return _export_state(session, event, trigger, camera, storage, marks)
    export.set("status", ExportStatus.RUNNING)
    export.set("error", None)
    session.commit()
    try:
        if marks:
            queue(event.id, marks=True)
        else:
            queue(event.id)
    except Exception as error:  # noqa: BLE001 - any broker failure
        export.set("status", ExportStatus.FAILED)
        export.set("error", "the export queue is not reachable")
        session.commit()
        raise HTTPException(503, f"the export queue is not reachable (is Redis running?): {error}")
    return _export_state(session, event, trigger, camera, storage, marks)


@router.get("/{event_id}/export", response_model=ExportState)
def export_state(
    event_id: UUID,
    marks: bool = Query(False, description="the file with the marks drawn in"),
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
) -> ExportState:
    """When done: a time-limited link that downloads the MP4."""
    event, trigger, camera = _get(session, event_id)
    return _export_state(session, event, trigger, camera, storage, marks)


def delete_events(session: Session, storage: StorageClient, events: list[Event]) -> None:
    """Events with their evidence rows and marks (database cascade) and export files (ADR 0034)."""
    for event in events:
        storage.delete(export_key(event.id))
        storage.delete(marked_export_key(event.id))
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
