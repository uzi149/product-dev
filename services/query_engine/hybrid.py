"""Hybrid scoring of candidate windows (scope §10 steps 2-6, step 4.2).

Candidates are the union of the nearest descriptions (text index), the nearest windows by their
best keyframe (visual index) and the best full-text matches of the description. Every candidate
then gets every signal, each mapped to 0-1:

    text     cosine(query, description), linear between QUERY_TEXT_LOW and _HIGH
    visual   the best keyframe's cosine(query, picture), between QUERY_VISUAL_LOW and _HIGH
    keyword  Postgres ts_rank_cd of the description for the query words, rank / (rank + 0.1)
    action   the share of the query's actions (shared/schemas/action_phrases.py) in the window
    event    the best confidence of an event whose evidence covers the window (Phase 5)

fused = the weighted sum, with the weights normalised over the signals that apply: the action
signal only when the query names an action, the event signal only when a candidate has an event,
the keyword signal only when the query has searchable words. The calibration is fixed, not
stretched per query, so a weak best match stays weak and QUERY_MIN_SCORE can drop it.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

import numpy as np
from sqlalchemy import and_, func, literal_column, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from shared.config import Settings
from shared.database.models import (
    AnalysisWindow,
    Event,
    EventEvidence,
    Keyframe,
    TextEmbedding,
    VideoSegment,
)
from shared.schemas.action_phrases import query_actions

# Same expression as the GIN index ix_analysis_windows_vlm_description_fts, so it is used.
_DOCUMENT = func.to_tsvector(literal_column("'english'"), AnalysisWindow.vlm_description)
KEYWORD_HALF = 0.1  # ts_rank_cd at which the keyword signal is 0.5


@dataclass
class Signals:
    """One candidate's raw values and its 0-1 signals."""

    text_similarity: float | None = None
    visual_similarity: float | None = None
    keyframe_id: UUID | None = None
    keyword_rank: float = 0.0
    action_share: float = 0.0
    event_confidence: float = 0.0
    text: float = 0.0
    visual: float = 0.0
    keyword: float = 0.0
    action: float = 0.0
    event: float = 0.0
    fused: float = 0.0


def calibrate(value: float | None, low: float, high: float) -> float:
    """0 at or below `low`, 1 at or above `high`, linear in between."""
    if value is None:
        return 0.0
    return min(1.0, max(0.0, (value - low) / (high - low)))


def keyword_query(search_text: str):
    """The query words OR-ed (any of them may match), stemmed by the English configuration;
    None when nothing is left to search for (only stop words or punctuation)."""
    words = re.findall(r"[a-z0-9]+", search_text.lower())
    if not words:
        return None
    return func.websearch_to_tsquery(literal_column("'english'"), " or ".join(words))


def keyword_candidates(
    session: Session, search_text: str, conditions: list, limit: int
) -> list[tuple[UUID, float]]:
    """(window id, ts_rank_cd) of the best full-text matches inside the filters, best first."""
    query = keyword_query(search_text)
    if query is None or limit == 0:
        return []
    rank = func.ts_rank_cd(_DOCUMENT, query)
    rows = session.execute(
        select(AnalysisWindow.id, rank.label("rank"))
        .where(_DOCUMENT.op("@@")(query), *conditions)
        .order_by(rank.desc(), AnalysisWindow.id)
        .limit(limit)
    ).all()
    return [(window_id, float(r)) for window_id, r in rows]


def _text_similarities(session, ids, vector) -> dict[UUID, float]:
    distance = TextEmbedding.embedding.cosine_distance(vector)
    rows = session.execute(
        select(TextEmbedding.analysis_window_id, distance).where(
            TextEmbedding.analysis_window_id.in_(ids)
        )
    ).all()
    return {w: 1.0 - d for w, d in rows}


def _visual_similarities(session, ids, vector) -> dict[UUID, tuple[float, UUID]]:
    """Each window's best keyframe: (similarity, keyframe id)."""
    distance = Keyframe.visual_embedding.cosine_distance(vector)
    rows = session.execute(
        select(Keyframe.analysis_window_id, Keyframe.id, distance)
        .where(Keyframe.analysis_window_id.in_(ids), Keyframe.visual_embedding.is_not(None))
        .order_by(Keyframe.analysis_window_id, distance)
        .ext(distinct_on(Keyframe.analysis_window_id))
    ).all()
    return {w: (1.0 - d, k) for w, k, d in rows}


def _keyword_ranks(session, ids, search_text) -> dict[UUID, float]:
    query = keyword_query(search_text)
    if query is None:
        return {}
    rows = session.execute(
        select(AnalysisWindow.id, func.ts_rank_cd(_DOCUMENT, query)).where(
            AnalysisWindow.id.in_(ids), _DOCUMENT.op("@@")(query)
        )
    ).all()
    return {w: float(r) for w, r in rows}


def _window_actions(session, ids) -> dict[UUID, set[str]]:
    rows = session.execute(
        select(AnalysisWindow.id, AnalysisWindow.structured_observation_json).where(
            AnalysisWindow.id.in_(ids)
        )
    ).all()
    return {
        w: {a.get("type") for a in (observation or {}).get("actions", []) if isinstance(a, dict)}
        for w, observation in rows
    }


def _event_confidences(session, ids) -> dict[UUID, float]:
    """The best confidence of an event whose evidence overlaps the window (Phase 5 creates
    events; until then this is empty)."""
    millisecond = literal_column("interval '1 millisecond'")
    evidence_start = VideoSegment.start_time + EventEvidence.start_offset_ms * millisecond
    evidence_end = VideoSegment.start_time + EventEvidence.end_offset_ms * millisecond
    rows = session.execute(
        select(AnalysisWindow.id, func.max(Event.confidence))
        .join(EventEvidence, EventEvidence.segment_id == AnalysisWindow.segment_id)
        .join(VideoSegment, VideoSegment.id == EventEvidence.segment_id)
        .join(Event, Event.id == EventEvidence.event_id)
        .where(
            AnalysisWindow.id.in_(ids),
            and_(
                AnalysisWindow.start_time < evidence_end, AnalysisWindow.end_time > evidence_start
            ),
        )
        .group_by(AnalysisWindow.id)
    ).all()
    return {w: float(c or 0.0) for w, c in rows}


def weights(settings: Settings, *, actions: bool, events: bool, keywords: bool) -> dict[str, float]:
    """The weights of the signals that apply, summing to 1 (all zero if every weight is 0)."""
    raw = {
        "text": settings.query_weight_text,
        "visual": settings.query_weight_visual,
        "keyword": settings.query_weight_keyword if keywords else 0.0,
        "action": settings.query_weight_action if actions else 0.0,
        "event": settings.query_weight_event if events else 0.0,
    }
    total = sum(raw.values())
    return {k: (v / total if total else 0.0) for k, v in raw.items()}


def score(
    session: Session,
    window_ids: Iterable[UUID],
    search_text: str,
    text_vector: np.ndarray,
    visual_vector: np.ndarray,
    settings: Settings,
) -> dict[UUID, Signals]:
    """Every signal of every candidate, and the fused score."""
    ids = list(dict.fromkeys(window_ids))
    if not ids:
        return {}
    texts = _text_similarities(session, ids, text_vector)
    visuals = _visual_similarities(session, ids, visual_vector)
    ranks = _keyword_ranks(session, ids, search_text)
    events = _event_confidences(session, ids)
    wanted = query_actions(search_text)
    present = _window_actions(session, ids) if wanted else {}
    w = weights(
        settings,
        actions=bool(wanted),
        events=bool(events),
        keywords=keyword_query(search_text) is not None,
    )

    result = {}
    for window_id in ids:
        visual, keyframe = visuals.get(window_id, (None, None))
        s = Signals(
            text_similarity=texts.get(window_id),
            visual_similarity=visual,
            keyframe_id=keyframe,
            keyword_rank=ranks.get(window_id, 0.0),
            action_share=len(wanted & present.get(window_id, set())) / len(wanted)
            if wanted
            else 0.0,
            event_confidence=events.get(window_id, 0.0),
        )
        s.text = calibrate(s.text_similarity, settings.query_text_low, settings.query_text_high)
        s.visual = calibrate(
            s.visual_similarity, settings.query_visual_low, settings.query_visual_high
        )
        s.keyword = s.keyword_rank / (s.keyword_rank + KEYWORD_HALF)
        s.action = s.action_share
        s.event = s.event_confidence
        s.fused = sum(w[name] * getattr(s, name) for name in w)
        result[window_id] = s
    return result
