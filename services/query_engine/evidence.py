"""Ranked windows turned into evidence results, grouped by time (scope §10 steps 7-8, §11).

    CAM-02  14:20:51-14:21:12  ┐ overlapping windows
    CAM-02  14:21:09-14:21:31  │ -> one sequence 14:20:51-14:21:48
    CAM-02  14:21:30-14:21:48  ┘
    CAM-02  14:27:00-14:27:29    -> the same result, after "5 minutes 12 seconds not shown"

1. expand: each of the best hits takes its neighbouring window before and after on the same
   camera (overlapping or touching it, in the same segment or a back-to-back one), for context.
2. merge_sequences: per camera, windows at most `merge_gap` apart join into one sequence.
3. group_results: sequences on one camera at most `group_gap` apart form one result (ADR 0027);
   results never mix cameras. Results are sorted by their best hit's score.
4. build_manifest: each sequence is cut along the segments it covers into video parts; a gap
   part goes between parts that are 1 s or more apart, so separated footage never looks
   continuous.

The steps are pure functions on plain data; load_neighbours and load_segments read the database.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.query_engine.retrieval import WindowHit
from shared.config import Settings, get_settings
from shared.database.models import AnalysisWindow, VideoSegment
from shared.schemas.query import GapPart, PlaybackManifest, VideoPart

# Gaps shorter than this are not shown: back-to-back live segments meet within a frame or two.
MIN_GAP_SHOWN = timedelta(seconds=1)


@dataclass(frozen=True)
class Window:
    id: UUID
    camera_id: UUID
    segment_id: UUID
    start: datetime
    end: datetime
    description: str | None = None
    score: float | None = None  # None: a neighbour added for context, not a hit


@dataclass(frozen=True)
class SegmentSpan:
    id: UUID
    camera_id: UUID
    start: datetime
    end: datetime


@dataclass
class EvidenceSequence:
    camera_id: UUID
    start: datetime
    end: datetime
    windows: list[Window] = field(default_factory=list)

    @property
    def score(self) -> float:
        return max((w.score for w in self.windows if w.score is not None), default=0.0)


@dataclass
class EvidenceResult:
    camera_id: UUID
    sequences: list[EvidenceSequence]
    id: UUID = field(default_factory=uuid4)
    manifest: PlaybackManifest | None = None

    @property
    def start(self) -> datetime:
        return self.sequences[0].start

    @property
    def end(self) -> datetime:
        return self.sequences[-1].end

    @property
    def score(self) -> float:
        return max(s.score for s in self.sequences)

    @property
    def windows(self) -> list[Window]:
        return [w for s in self.sequences for w in s.windows]


def expand(hits: Sequence[Window], neighbours: dict[UUID, list[Window]]) -> list[Window]:
    """The hits and their neighbours, each window once (a neighbour that is also a hit keeps
    its score)."""
    by_id = {w.id: w for w in hits}
    for hit in hits:
        for neighbour in neighbours.get(hit.id, []):
            by_id.setdefault(neighbour.id, neighbour)
    return list(by_id.values())


def merge_sequences(windows: Iterable[Window], merge_gap: timedelta) -> list[EvidenceSequence]:
    """Windows of one camera that overlap or are at most `merge_gap` apart, as sequences in time
    order (all cameras, sorted by camera then time)."""
    sequences: list[EvidenceSequence] = []
    for w in sorted(windows, key=lambda w: (str(w.camera_id), w.start, w.end)):
        last = sequences[-1] if sequences else None
        if last is not None and last.camera_id == w.camera_id and w.start <= last.end + merge_gap:
            last.windows.append(w)
            last.end = max(last.end, w.end)
        else:
            sequences.append(EvidenceSequence(w.camera_id, w.start, w.end, [w]))
    return sequences


def group_results(
    sequences: Iterable[EvidenceSequence], group_gap: timedelta
) -> list[EvidenceResult]:
    """Sequences of one camera at most `group_gap` apart form one result; best score first."""
    results: list[EvidenceResult] = []
    for s in sorted(sequences, key=lambda s: (str(s.camera_id), s.start)):
        last = results[-1] if results else None
        if last is not None and last.camera_id == s.camera_id and s.start - last.end <= group_gap:
            last.sequences.append(s)
        else:
            results.append(EvidenceResult(s.camera_id, [s]))
    return sorted(results, key=lambda r: (-r.score, r.start))


def _ms(delta: timedelta) -> int:
    return round(delta.total_seconds() * 1000)


def build_manifest(result: EvidenceResult, segments: dict[UUID, SegmentSpan]) -> PlaybackManifest:
    """The result's parts in time order: video parts cut along the segments, with gap parts
    where 1 s or more of footage is not shown."""
    parts: list[VideoPart | GapPart] = []
    previous_end: datetime | None = None
    for sequence in result.sequences:
        spans = sorted(
            {segments[w.segment_id] for w in sequence.windows}, key=lambda s: (s.start, s.end)
        )
        for span in spans:
            start, end = max(sequence.start, span.start), min(sequence.end, span.end)
            if previous_end is not None:
                start = max(start, previous_end)  # overlapping segments: never play twice
            if end <= start:
                continue
            if previous_end is not None and start - previous_end >= MIN_GAP_SHOWN:
                parts.append(GapPart(duration_ms=_ms(start - previous_end)))
            parts.append(
                VideoPart(
                    camera_id=result.camera_id,
                    segment_id=span.id,
                    source_start_ms=_ms(start - span.start),
                    source_end_ms=_ms(end - span.start),
                    start_time=start,
                    end_time=end,
                )
            )
            previous_end = end
    return PlaybackManifest(
        result_id=result.id,
        camera_id=result.camera_id,
        start_time=result.start,
        end_time=result.end,
        parts=parts,
    )


def _window(row: AnalysisWindow, score: float | None = None) -> Window:
    return Window(
        row.id,
        row.camera_id,
        row.segment_id,
        row.start_time,
        row.end_time,
        row.vlm_description,
        score,
    )


def load_neighbours(
    session: Session, hits: Sequence[Window], touch: timedelta
) -> dict[UUID, list[Window]]:
    """Each hit's closest window before and after it on the same camera, if one overlaps it or
    is at most `touch` away. Windows of any status count: the footage is there either way."""
    result: dict[UUID, list[Window]] = {}
    for hit in hits:
        rows = session.scalars(
            select(AnalysisWindow).where(
                AnalysisWindow.camera_id == hit.camera_id,
                AnalysisWindow.id != hit.id,
                AnalysisWindow.end_time >= hit.start - touch,
                AnalysisWindow.start_time <= hit.end + touch,
            )
        ).all()
        before = [r for r in rows if r.start_time < hit.start]
        after = [r for r in rows if r.start_time > hit.start]
        chosen = []
        if before:
            chosen.append(max(before, key=lambda r: (r.start_time, r.end_time)))
        if after:
            chosen.append(min(after, key=lambda r: (r.start_time, r.end_time)))
        result[hit.id] = [_window(r) for r in chosen]
    return result


def load_segments(session: Session, segment_ids: Iterable[UUID]) -> dict[UUID, SegmentSpan]:
    rows = session.scalars(select(VideoSegment).where(VideoSegment.id.in_(set(segment_ids)))).all()
    return {r.id: SegmentSpan(r.id, r.camera_id, r.start_time, r.end_time) for r in rows}


def hit_window(hit: WindowHit) -> Window:
    return Window(
        hit.window_id,
        hit.camera_id,
        hit.segment_id,
        hit.start_time,
        hit.end_time,
        hit.description,
        hit.fused_score,
    )


def evidence_results(
    session: Session, hits: Sequence[WindowHit], settings: Settings | None = None
) -> list[EvidenceResult]:
    """The evidence results for ranked hits (best first), each with its manifest."""
    settings = settings or get_settings()
    top = [hit_window(h) for h in hits[: settings.query_expand_top]]
    if not top:
        return []
    merge_gap = timedelta(seconds=settings.query_merge_gap_seconds)
    windows = expand(top, load_neighbours(session, top, merge_gap))
    results = group_results(
        merge_sequences(windows, merge_gap), timedelta(minutes=settings.query_group_gap_minutes)
    )
    segments = load_segments(session, (w.segment_id for w in windows))
    for result in results:
        result.manifest = build_manifest(result, segments)
    return results
