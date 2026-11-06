"""Asking for evidence marks and reading them (step 5.7, ADR 0038). Light: the API and the
event finishing use it without loading any model or video code."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.config import Settings
from shared.database.models import Event, EvidenceMarks, MarksStatus, QueryEvidence
from shared.model_adapters import resolve_vlm_backend
from shared.schemas.marks import MarksState

LOCAL_MODEL_REASON = "Marks are off with the local model (MARKS_LOCAL_MODEL turns them on)."
ABANDONED_AFTER = timedelta(minutes=30)


def _off_reason(settings: Settings) -> str | None:
    if not settings.marks_enabled:
        return "Marks are switched off (MARKS_ENABLED)."
    if resolve_vlm_backend(settings) == "local" and not settings.marks_local_model:
        return LOCAL_MODEL_REASON
    return None


def _request(session: Session, row: EvidenceMarks, settings: Settings) -> bool:
    """Set the row to pending (or skipped); True when it must be queued."""
    reason = _off_reason(settings)
    row.reason = reason
    row.finished_at = None
    if reason is not None:
        row.status = MarksStatus.SKIPPED
        return False
    row.status = MarksStatus.PENDING
    row.started_at = None
    return True


def request_event_marks(session: Session, event: Event, settings: Settings) -> UUID | None:
    """Ask for (new) marks of an event whose evidence was just written; the caller commits and
    then queues the returned id. None: nothing to queue (marks off)."""
    row = session.scalars(select(EvidenceMarks).where(EvidenceMarks.event_id == event.id)).first()
    if row is None:
        row = EvidenceMarks(event_id=event.id)
        session.add(row)
    queue = _request(session, row, settings)
    session.flush()
    return row.id if queue else None


def _abandoned(row: EvidenceMarks, now: datetime | None) -> bool:
    """Running for longer than any marking takes: its worker was stopped mid-way (the task comes
    back only when the broker gives it out again, up to an hour later). A pending one is still
    waiting in the queue."""
    if row.status != MarksStatus.RUNNING or row.started_at is None:
        return False
    return (now or datetime.now(UTC)) - row.started_at > ABANDONED_AFTER


def request_result_marks(
    session: Session, evidence: QueryEvidence, settings: Settings, now: datetime | None = None
) -> tuple[EvidenceMarks, bool]:
    """Ask for the marks of a query result the operator opened. A result already marked, being
    marked or skipped is left alone; a failed or abandoned one is tried again. Returns the row and whether to
    queue it (after the caller commits)."""
    row = session.scalars(
        select(EvidenceMarks).where(EvidenceMarks.query_evidence_id == evidence.id)
    ).first()
    if row is not None and row.status != MarksStatus.FAILED and not _abandoned(row, now):
        return row, False
    if row is None:
        row = EvidenceMarks(query_evidence_id=evidence.id)
        session.add(row)
    queue = _request(session, row, settings)
    session.flush()
    return row, queue


def marks_state(row: EvidenceMarks | None) -> MarksState:
    if row is None:
        return MarksState(status="none")
    busy = row.status in (MarksStatus.PENDING, MarksStatus.RUNNING)
    return MarksState(
        status=row.status,
        reason=row.reason,
        updating=busy and row.tracks_json is not None,
        pick_status=row.pick_status,
        tracks=row.tracks_json or [],
    )
