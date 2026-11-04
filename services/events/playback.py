"""An event's evidence as a playback manifest (scope §11, step 5.3), and the words for a gap.

The evidence rows (5.2) are already cut to the pre-roll and post-roll and in time order. Each
becomes a video part; where 1 s or more of footage between two parts isn't stored (activity-only
storage), a gap part says so, as in query results (4.3), so separated footage never looks
continuous.
"""

import re
import unicodedata
from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.query_engine.evidence import MIN_GAP_SHOWN
from shared.database.models import Event, EventEvidence, Keyframe, VideoSegment
from shared.schemas.query import GapPart, PlaybackManifest, VideoPart


def _ms(delta: timedelta) -> int:
    return round(delta.total_seconds() * 1000)


def evidence_rows(session: Session, event_id: UUID) -> list[tuple[EventEvidence, VideoSegment]]:
    rows = session.execute(
        select(EventEvidence, VideoSegment)
        .join(VideoSegment, VideoSegment.id == EventEvidence.segment_id)
        .where(EventEvidence.event_id == event_id)
        .order_by(EventEvidence.sequence_order, VideoSegment.start_time)
    )
    return [(evidence, segment) for evidence, segment in rows]


def event_manifest(
    event: Event, rows: Sequence[tuple[EventEvidence, VideoSegment]]
) -> PlaybackManifest:
    spans = [
        (
            segment,
            segment.start_time + timedelta(milliseconds=evidence.start_offset_ms),
            segment.start_time + timedelta(milliseconds=evidence.end_offset_ms),
        )
        for evidence, segment in rows
    ]
    return spans_manifest(event.id, event.camera_id, spans, event.start_time, event.end_time)


def clipped_spans(
    segments: Sequence[VideoSegment], begin: datetime, finish: datetime
) -> list[tuple[VideoSegment, datetime, datetime]]:
    """The stored segments' footage inside [begin, finish], in time order."""
    return [
        (s, max(begin, s.start_time), min(finish, s.end_time))
        for s in sorted(segments, key=lambda s: s.start_time)
        if s.end_time > begin and s.start_time < finish
    ]


def spans_manifest(
    result_id: UUID,
    camera_id: UUID,
    spans: Sequence[tuple[VideoSegment, datetime, datetime]],
    start: datetime,
    end: datetime,
) -> PlaybackManifest:
    """(segment, start, end) spans in time order as video parts, with explicit gaps.
    `start`/`end` are used when there is no footage at all."""
    parts: list[VideoPart | GapPart] = []
    previous_end: datetime | None = None
    for segment, start_at, end_at in spans:
        if previous_end is not None:
            start_at = max(start_at, previous_end)  # overlapping segments: never play twice
        if end_at <= start_at:
            continue
        if previous_end is not None and start_at - previous_end >= MIN_GAP_SHOWN:
            parts.append(GapPart(duration_ms=_ms(start_at - previous_end)))
        parts.append(
            VideoPart(
                camera_id=camera_id,
                segment_id=segment.id,
                source_start_ms=_ms(start_at - segment.start_time),
                source_end_ms=_ms(end_at - segment.start_time),
                start_time=start_at,
                end_time=end_at,
            )
        )
        previous_end = end_at
    videos = [p for p in parts if isinstance(p, VideoPart)]
    return PlaybackManifest(
        result_id=result_id,
        camera_id=camera_id,
        start_time=videos[0].start_time if videos else start,
        end_time=videos[-1].end_time if videos else end,
        parts=parts,
    )


def deciding_window(event: Event) -> str | None:
    """The window that completed the first match (its last action), else the first matched one."""
    details = event.details_json or {}
    matches = details.get("matches") or []
    if matches and matches[0].get("actions"):
        return matches[0]["actions"][-1].get("window_id")
    window_ids = details.get("window_ids") or []
    return window_ids[0] if window_ids else None


def best_keyframes(session: Session, events: Sequence[Event]) -> dict[UUID, UUID]:
    """A picture per event: the middle keyframe of its deciding window."""
    windows = {e.id: deciding_window(e) for e in events}
    wanted = [UUID(w) for w in windows.values() if w]
    if not wanted:
        return {}
    by_window: dict[str, list[UUID]] = {}
    rows = session.execute(
        select(Keyframe.analysis_window_id, Keyframe.id)
        .where(Keyframe.analysis_window_id.in_(wanted))
        .order_by(Keyframe.analysis_window_id, Keyframe.timestamp)
    )
    for window_id, keyframe_id in rows:
        by_window.setdefault(str(window_id), []).append(keyframe_id)
    result = {}
    for event_id, window_id in windows.items():
        frames = by_window.get(window_id or "")
        if frames:
            result[event_id] = frames[len(frames) // 2]
    return result


def gap_wording(ms: int) -> str:
    """312000 -> "5 minutes 12 seconds": the player's wording (frontend lib/queries.ts)."""
    total = max(0, round(ms / 1000))
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)

    def plural(n: int, word: str) -> str:
        return f"{n} {word}{'' if n == 1 else 's'}"

    words = []
    if hours:
        words.append(plural(hours, "hour"))
    if minutes:
        words.append(plural(minutes, "minute"))
    if seconds or not words:
        words.append(plural(seconds, "second"))
    return " ".join(words)


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-") or "event"


def export_file_name(
    trigger_name: str, camera_name: str, start: datetime, marked: bool = False
) -> str:
    """object-left-behind_cam-01_2026-03-07_092104.mp4 (the time in this machine's zone);
    "..._marked.mp4" for the export with the marks drawn in."""
    suffix = "_marked" if marked else ""
    time = f"{start.astimezone():%Y-%m-%d_%H%M%S}"
    return f"{_slug(trigger_name)}_{_slug(camera_name)}_{time}{suffix}.mp4"
