"""Footage already on a camera that a new upload would overlap (step 5.6, ADR 0037).

A camera can't record two scenes at once: two files on one camera at the same time would be
interleaved on its timeline, and triggers, trigger tests and search would combine actions from
both. So an upload is refused when its span touches the inside of footage already there:

- the camera's uploads that are pending, processing or done, over their whole file span (static
  parts included); failed ones stored nothing and don't count;
- its stored segments and static periods: live footage, and uploads made before the length of
  the file was recorded.

Spans that only touch (one ends when the next starts) don't overlap.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.database.models import ProcessingStatus, StaticPeriod, Upload, VideoSegment

# Probed lengths and cut points differ by a few milliseconds: a part this close to an upload's
# span still belongs to it.
_SLACK = timedelta(seconds=1)
# How far around the span stored footage is merged into stretches.
_AROUND = timedelta(days=1)


@dataclass(frozen=True)
class Conflict:
    """Footage in the way: an upload (with its file name) or other stored footage (None)."""

    start: datetime
    end: datetime
    filename: str | None


def _overlaps(a_start: datetime, a_end: datetime, start: datetime, end: datetime) -> bool:
    """[a_start, a_end) against [start, end), or against the instant `start` when end <= start."""
    if end <= start:
        return a_start <= start < a_end
    return a_start < end and a_end > start


def _merge(spans: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + _SLACK:  # parts are cut a few ms apart
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def footage_conflicts(
    session: Session, camera_id: UUID, start: datetime, end: datetime | None = None
) -> list[Conflict]:
    """The camera's footage that [start, end) would overlap, earliest first. Without `end`, the
    footage running at `start` (the upload form checks this when it can't read the file's length).
    """
    end = start if end is None else end
    reach = max(start, end)

    uploads: list[Conflict] = []
    upload_spans: list[tuple[datetime, datetime]] = []
    rows = session.scalars(
        select(Upload).where(
            Upload.camera_id == camera_id,
            Upload.status != ProcessingStatus.FAILED,
            Upload.duration_ms.is_not(None),
            Upload.start_time <= reach + _AROUND,
        )
    )
    for upload in rows:
        upload_end = upload.start_time + timedelta(milliseconds=upload.duration_ms)
        upload_spans.append((upload.start_time, upload_end))
        if _overlaps(upload.start_time, upload_end, start, end):
            uploads.append(Conflict(upload.start_time, upload_end, upload.filename))

    # Footage not covered by a known upload span: live recording, or uploads from before 5.6.
    # Merged into continuous stretches first, so the conflict shows where the footage really ends
    # (a segment followed by a static period is one stretch); looked at a day either side.
    stored: list[tuple[datetime, datetime]] = []
    for model in (VideoSegment, StaticPeriod):
        for part_start, part_end in session.execute(
            select(model.start_time, model.end_time).where(
                model.camera_id == camera_id,
                model.start_time <= reach + _AROUND,
                model.end_time >= start - _AROUND,
            )
        ):
            covered = any(
                a - _SLACK <= part_start and part_end <= b + _SLACK for a, b in upload_spans
            )
            if not covered:
                stored.append((part_start, part_end))
    stretches = [(a, b) for a, b in _merge(stored) if _overlaps(a, b, start, end)]

    others = [Conflict(a, b, None) for a, b in stretches]
    return sorted(uploads + others, key=lambda c: (c.start, c.end))
