"""Events from trigger matches (scope §12.3, step 5.2, ADR 0033).

A match becomes an event, or extends the same trigger's event on the same camera when it is
within the cooldown. A new event is **held** (`alerted_at` empty): the alert waits until the
footage up to `end + post_roll` is stored, so it arrives with its complete evidence. For an
uploaded file that is at once (the whole file is stored before it is analysed); for a live camera
when a segment reaching that time is stored, or the camera has stopped recording, or at the
latest `max_segment_seconds` + TRIGGER_FINISH_MARGIN_SECONDS after it. Until then `finish_event`
checks again every TRIGGER_FINISH_POLL_SECONDS.

Finishing writes the evidence rows (the stored segments overlapping
`[start - pre_roll, end + post_roll]`) and, the first time, sends the notice for
`WS /live/events`. An event extended after its alert gets its evidence rewritten, without a
second alert. Each time, its marks are asked for again (step 5.7), after the alert.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from celery.signals import worker_ready
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from services.marks.state import request_event_marks
from services.triggers import notices
from services.triggers.matching import TimedAction, WindowFacts
from shared.camera_status import CameraStatus, LiveState, get_redis, read_statuses
from shared.config import Settings, get_settings
from shared.database.models import (
    Camera,
    Event,
    EventEvidence,
    EventStatus,
    Trigger,
    VideoSegment,
)
from shared.database.session import get_sessionmaker
from shared.jobs import celery_app as jobs
from shared.jobs.celery_app import DEFAULT_QUEUE, FINISH_EVENT, celery_app

log = logging.getLogger(__name__)

# The event's verification is the best of its matches', in this order.
VERIFICATION_RANK = ("verified", "not_required", "unverifiable", "not_verified_local")
SUMMARY_LENGTH = 1000


@dataclass(frozen=True)
class Verification:
    status: str  # verified, rejected, not_required, unverifiable, not_verified_local
    confidence: float | None = None
    reason: str | None = None


@dataclass
class Match:
    """What a trigger matched: the windows it rests on and, for a structured rule, the chain of
    actions; for a semantic rule, the window's score."""

    mode: str
    windows: list[WindowFacts]
    actions: list[TimedAction] = field(default_factory=list)
    score: float | None = None

    @property
    def start(self) -> datetime:
        if self.actions:
            return self.actions[0].start
        return min(w.start for w in self.windows)

    @property
    def end(self) -> datetime:
        if self.actions:
            return max(a.end for a in self.actions)
        return max(w.end for w in self.windows)

    @property
    def deciding_windows(self) -> list[WindowFacts]:
        """The windows whose observations the match needs: those with a matched action."""
        if not self.actions:
            return self.windows
        ids = {a.window_id for a in self.actions}
        return [w for w in self.windows if w.id in ids]


@dataclass
class FinishTools:
    """What finishing an event needs; tests pass fakes."""

    settings: Settings
    publish: Callable[[dict[str, Any]], None]
    schedule_finish: Callable[[UUID, float], None]  # event id, seconds from now
    camera_status: Callable[[UUID], CameraStatus | None]
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    # Queues the marking of the evidence (step 5.7); tests that don't care leave it out.
    queue_marks: Callable[[UUID], None] = lambda marks_id: None


def default_finish_tools(settings: Settings | None = None) -> FinishTools:
    client = get_redis()

    def camera_status(camera_id: UUID) -> CameraStatus | None:
        try:
            return read_statuses(client, [camera_id]).get(camera_id)
        except Exception as error:  # noqa: BLE001 - Redis down: treated as not recording
            log.debug("no live status for camera %s: %s", camera_id, error)
            return None

    return FinishTools(
        settings=settings or get_settings(),
        publish=lambda notice: notices.publish(client, notice),
        schedule_finish=lambda event_id, seconds: queue_finish(event_id, seconds),
        camera_status=camera_status,
        queue_marks=queue_marks,
    )


def queue_marks(marks_id: UUID) -> None:
    try:
        jobs.queue_mark_evidence(marks_id)
    except Exception as error:  # noqa: BLE001 - the alert must go out anyway
        log.warning("could not queue the marking of %s: %s", marks_id, error)


def queue_finish(event_id: UUID, seconds: float) -> None:
    try:
        celery_app.send_task(FINISH_EVENT, args=[str(event_id)], countdown=max(0.0, seconds))
    except Exception as error:  # noqa: BLE001 - the worker-start sweep finishes it later
        log.warning("could not queue the finishing of event %s: %s", event_id, error)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def match_record(
    match: Match, verification: Verification, confidence: float, now: datetime
) -> dict[str, Any]:
    return {
        "at": _iso(now),
        "window_ids": [str(w.id) for w in match.windows],
        "actions": [
            {
                "type": a.type,
                "start": _iso(a.start),
                "end": _iso(a.end),
                "window_id": str(a.window_id),
            }
            for a in match.actions
        ],
        "score": match.score,
        "confidence": confidence,
        "verification": {
            "status": verification.status,
            "confidence": verification.confidence,
            "reason": verification.reason,
        },
    }


def _best_verification(matches: list[dict[str, Any]]) -> dict[str, Any]:
    return min(
        (m["verification"] for m in matches),
        key=lambda v: (
            VERIFICATION_RANK.index(v["status"])
            if v["status"] in VERIFICATION_RANK
            else len(VERIFICATION_RANK)
        ),
    )


def _summary(match: Match, verification: Verification) -> str:
    if verification.status == "verified" and verification.reason:
        return verification.reason[:SUMMARY_LENGTH]
    texts = list(dict.fromkeys(w.description for w in match.deciding_windows if w.description))
    return " ".join(texts)[:SUMMARY_LENGTH] or "Matched the trigger's rule."


def record_match(
    session: Session,
    trigger: Trigger,
    camera_id: UUID,
    match: Match,
    verification: Verification,
    confidence: float,
    now: datetime,
) -> tuple[Event, bool]:
    """(the event, whether it is new). Within the cooldown of the trigger's latest event on this
    camera, that event is extended instead (ADR 0033)."""
    cooldown = timedelta(seconds=trigger.cooldown_seconds)
    previous = session.scalars(
        select(Event)
        .where(
            Event.trigger_id == trigger.id,
            Event.camera_id == camera_id,
            Event.end_time >= match.start - cooldown,
            Event.start_time <= match.end + cooldown,
        )
        .order_by(Event.end_time.desc())
        .limit(1)
        .with_for_update()
    ).first()
    record = match_record(match, verification, confidence, now)
    if previous is not None:
        details = dict(previous.details_json or {})
        matches = [*details.get("matches", []), record]
        details["matches"] = matches
        details["window_ids"] = list(
            dict.fromkeys([*details.get("window_ids", []), *record["window_ids"]])
        )
        details["verification"] = _best_verification(matches)
        previous.details_json = details
        previous.start_time = min(previous.start_time, match.start)
        previous.end_time = max(previous.end_time, match.end)
        previous.confidence = max(previous.confidence or 0.0, confidence)
        if verification.status == "verified" and details["verification"] == record["verification"]:
            previous.summary = _summary(match, verification)
        previous.updated_at = now
        session.flush()
        return previous, False
    event = Event(
        trigger_id=trigger.id,
        camera_id=camera_id,
        start_time=match.start,
        end_time=match.end,
        confidence=confidence,
        summary=_summary(match, verification),
        status=EventStatus.NEW,
        details_json={
            "mode": match.mode,
            "window_ids": record["window_ids"],
            "matches": [record],
            "verification": record["verification"],
        },
        alerted_at=None,
        updated_at=now,
    )
    session.add(event)
    session.flush()
    return event, True


def footage_ready(
    session: Session,
    camera: Camera,
    target: datetime,
    now: datetime,
    tools: FinishTools,
) -> str | None:
    """Why the footage up to `target` can be taken as stored, or None to wait."""
    if camera.source_type == "file":
        return "uploaded file"
    reached = session.scalar(
        select(VideoSegment.id)
        .where(VideoSegment.camera_id == camera.id, VideoSegment.end_time >= target)
        .limit(1)
    )
    if reached is not None:
        return "footage stored"
    if now < target:
        return None
    settings = tools.settings
    status = tools.camera_status(camera.id)
    recording = status is not None and status.state == LiveState.ONLINE and status.activity
    # Ingestion stores the last segment of an activity a fragment or two after it ends.
    if not recording and now >= target + timedelta(seconds=2 * settings.fragment_seconds):
        return "camera not recording"
    limit = settings.max_segment_seconds + settings.trigger_finish_margin_seconds
    if now >= target + timedelta(seconds=limit):
        return "time limit"
    return None


def write_evidence(session: Session, event: Event, trigger: Trigger) -> int:
    """The stored segments overlapping [start - pre_roll, end + post_roll], clipped to it."""
    begin = event.start_time - timedelta(seconds=trigger.pre_roll_seconds)
    finish = event.end_time + timedelta(seconds=trigger.post_roll_seconds)
    segments = session.scalars(
        select(VideoSegment)
        .where(
            VideoSegment.camera_id == event.camera_id,
            VideoSegment.end_time > begin,
            VideoSegment.start_time < finish,
        )
        .order_by(VideoSegment.start_time)
    ).all()
    session.execute(delete(EventEvidence).where(EventEvidence.event_id == event.id))
    for order, segment in enumerate(segments):
        start = max(begin, segment.start_time)
        end = min(finish, segment.end_time)
        if end <= event.start_time:
            reason = "before the event (pre-roll)"
        elif start >= event.end_time:
            reason = "after the event (post-roll)"
        else:
            reason = "the matched activity"
        session.add(
            EventEvidence(
                event_id=event.id,
                segment_id=segment.id,
                start_offset_ms=round((start - segment.start_time).total_seconds() * 1000),
                end_offset_ms=round((end - segment.start_time).total_seconds() * 1000),
                evidence_reason=reason,
                sequence_order=order,
            )
        )
    return len(segments)


def notice(event: Event, trigger: Trigger, camera: Camera) -> dict[str, Any]:
    verification = (event.details_json or {}).get("verification") or {}
    return {
        "event_id": str(event.id),
        "trigger_id": str(trigger.id),
        "trigger_name": trigger.name,
        "camera_id": str(camera.id),
        "camera_name": camera.display_name,
        "start_time": _iso(event.start_time),
        "end_time": _iso(event.end_time),
        "confidence": event.confidence,
        "summary": event.summary,
        "verification": verification.get("status"),
    }


def process_finish(session: Session, event_id: UUID, tools: FinishTools) -> bool:
    """Finish the event if its footage is stored: evidence, then the alert the first time.
    Otherwise check again later. True when finished (or gone)."""
    event = session.scalars(select(Event).where(Event.id == event_id).with_for_update()).first()
    if event is None:
        session.rollback()
        return True  # deleted meanwhile
    trigger = session.get(Trigger, event.trigger_id)
    camera = session.get(Camera, event.camera_id)
    now = tools.now()
    target = event.end_time + timedelta(seconds=trigger.post_roll_seconds)
    why = footage_ready(session, camera, target, now, tools)
    if why is None:
        session.rollback()
        tools.schedule_finish(event.id, tools.settings.trigger_finish_poll_seconds)
        return False
    parts = write_evidence(session, event, trigger)
    first = event.alerted_at is None
    if first:
        event.alerted_at = now
        details = dict(event.details_json or {})
        details["delay_seconds"] = round((now - event.end_time).total_seconds(), 1)
        details["finished_because"] = why
        event.details_json = details
    event.updated_at = now
    message = notice(event, trigger, camera) if first else None
    marks_id = request_event_marks(session, event, tools.settings) if parts else None
    session.commit()
    log.info(
        "event %s (%s on %s): %d evidence part(s), %s%s",
        event.id,
        trigger.name,
        camera.display_name,
        parts,
        why,
        ", alert sent" if first else ", evidence updated",
    )
    if message is not None:
        tools.publish(message)
    if marks_id is not None:
        tools.queue_marks(marks_id)
    return True


def finish_held_events(
    session_factory: Callable[[], Session], tools: FinishTools, older_than: timedelta
) -> int:
    """Finish the held events nobody is waiting for any more (a worker stopped while a check was
    queued). Run when a worker starts."""
    cutoff = tools.now() - older_than
    with session_factory() as session:
        ids = list(
            session.scalars(
                select(Event.id).where(Event.alerted_at.is_(None), Event.updated_at <= cutoff)
            )
        )
    for event_id in ids:
        with session_factory() as session:
            process_finish(session, event_id, tools)
    return len(ids)


@celery_app.task(name=FINISH_EVENT)
def finish_event(event_id: str) -> None:
    with get_sessionmaker()() as session:
        process_finish(session, UUID(event_id), default_finish_tools())


def _consumes_default(sender) -> bool:
    queues = getattr(getattr(sender, "task_consumer", None), "queues", None)
    return queues is None or any(queue.name == DEFAULT_QUEUE for queue in queues)


@worker_ready.connect
def _on_worker_ready(sender=None, **_) -> None:
    """Held events whose check was lost with a stopped worker are finished now."""
    if not _consumes_default(sender):
        return
    tools = default_finish_tools()
    try:
        finished = finish_held_events(
            get_sessionmaker(),
            tools,
            timedelta(seconds=tools.settings.trigger_finish_poll_seconds),
        )
    except Exception:  # the worker must start anyway
        log.exception("could not check the held events")
        return
    if finished:
        log.info("checked %d held event(s)", finished)
