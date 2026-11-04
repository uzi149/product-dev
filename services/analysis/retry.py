"""Analyse segments again on purpose: failed ones, or ones stuck in `processing` (ADR 0024).

A segment is stuck when it has been `processing` with no progress for ANALYSIS_STUCK_MINUTES,
usually because its worker stopped. The task redoes only the windows that aren't done (step
3.3), so a retry never repeats finished work. Used by the API's Retry buttons and by
`python -m services.analysis.requeue`, so both behave the same.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.config import Settings, get_settings
from shared.database.models import ProcessingStatus, VideoSegment
from shared.jobs import celery_app as jobs


class QueueUnavailable(Exception):
    """The broker (Redis) couldn't be reached; the segments not queued kept their old status."""


@dataclass
class RetryResult:
    queued: list[UUID] = field(default_factory=list)
    # Segment id -> why it was left alone.
    skipped: dict[UUID, str] = field(default_factory=dict)


def stuck_after(settings: Settings | None = None) -> timedelta:
    return timedelta(minutes=(settings or get_settings()).analysis_stuck_minutes)


def is_stuck(segment: VideoSegment, now: datetime, after: timedelta) -> bool:
    return (
        segment.status == ProcessingStatus.PROCESSING
        and segment.status_changed_at is not None
        and segment.status_changed_at < now - after
    )


def is_retryable(segment: VideoSegment, now: datetime, after: timedelta) -> bool:
    return segment.status == ProcessingStatus.FAILED or is_stuck(segment, now, after)


def skip_reason(
    segment: VideoSegment, now: datetime, after: timedelta, include_pending: bool = False
) -> str | None:
    """Why the segment can't be retried, or None when it can."""
    if is_retryable(segment, now, after):
        return None
    if segment.status == ProcessingStatus.PENDING:
        return None if include_pending else "waiting in the queue"
    if segment.status == ProcessingStatus.DONE:
        return "already analysed"
    return "being analysed"


def retry_segments(
    session: Session,
    segment_ids: Iterable[UUID],
    queue: Callable[[UUID], None] | None = None,
    now: datetime | None = None,
    after: timedelta | None = None,
    include_pending: bool = False,
) -> RetryResult:
    """Set each retryable segment back to `pending`, then queue it.

    The status is committed before queueing, so the UI shows "Analysis queued" at once.
    `include_pending` also queues `pending` segments (the requeue command). Raises
    QueueUnavailable when the broker is down, after restoring the segments not yet queued.
    """
    queue = queue or jobs.queue_analyze_segment
    now = now or datetime.now(UTC)
    after = after if after is not None else stuck_after()
    ids = list(dict.fromkeys(segment_ids))
    found = {s.id: s for s in session.scalars(select(VideoSegment).where(VideoSegment.id.in_(ids)))}

    result = RetryResult()
    taken: list[tuple[VideoSegment, str, datetime]] = []
    for segment_id in ids:
        segment = found.get(segment_id)
        reason = (
            "not found" if segment is None else skip_reason(segment, now, after, include_pending)
        )
        if reason is not None:
            result.skipped[segment_id] = reason
            continue
        taken.append((segment, segment.status, segment.status_changed_at))
        segment.set_status(ProcessingStatus.PENDING, now)
    session.commit()

    for index, (segment, _, _) in enumerate(taken):
        try:
            queue(segment.id)
        except Exception as error:  # any broker failure
            for unqueued, status, changed_at in taken[index:]:
                unqueued.status = status
                unqueued.status_changed_at = changed_at
            session.commit()
            raise QueueUnavailable(
                f"the analysis queue is not reachable (is Redis running?): {error}"
            ) from error
        result.queued.append(segment.id)
    return result
