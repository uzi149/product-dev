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
    parts: list[VideoPart | GapPart] = []
    previous_end: datetime | None = None
    for evidence, segment in rows:
        start = segment.start_time + timedelta(milliseconds=evidence.start_offset_ms)
        end = segment.start_time + timedelta(milliseconds=evidence.end_offset_ms)
        if previous_end is not None:
            start = max(start, previous_end)  # overlapping segments: never play twice
        if end <= start:
            continue
        if previous_end is not None and start - previous_end >= MIN_GAP_SHOWN:
            parts.append(GapPart(duration_ms=_ms(start - previous_end)))
        parts.append(
            VideoPart(
                camera_id=event.camera_id,
                segment_id=segment.id,
                source_start_ms=_ms(start - segment.start_time),
                source_end_ms=_ms(end - segment.start_time),
                start_time=start,
                end_time=end,
            )
        )
        previous_end = end
    videos = [p for p in parts if isinstance(p, VideoPart)]
    return PlaybackManifest(
        result_id=event.id,
        camera_id=event.camera_id,
        start_time=videos[0].start_time if videos else event.start_time,
        end_time=videos[-1].end_time if videos else event.end_time,
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


def export_file_name(trigger_name: str, camera_name: str, start: datetime) -> str:
    """object-left-behind_cam-01_2026-03-07_092104.mp4 (the time in this machine's zone)."""
    return f"{_slug(trigger_name)}_{_slug(camera_name)}_{start.astimezone():%Y-%m-%d_%H%M%S}.mp4"
