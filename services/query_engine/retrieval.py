"""Find the analysis windows that match a plain-English query (scope §9.2, §10, ADR 0023).

The query is embedded twice: with bge (and its query instruction) for the text index of VLM
descriptions, and with SigLIP 2's text tower for the visual index of keyframes. Each index gives
its closest candidates inside the camera and time filters, and the two ranked lists are combined
with reciprocal rank fusion, because the two models' scores are on different scales (ADR 0020).
Phase 4 (4.2) replaces the fusion with weighted scores.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from shared.database.index_settings import ensure_index_settings
from shared.database.models import (
    AnalysisWindow,
    Camera,
    Keyframe,
    ProcessingStatus,
    TextEmbedding,
    VideoSegment,
)
from shared.model_adapters import (
    ImageEmbedder,
    TextEmbedder,
    get_image_embedder,
    get_text_embedder,
)

# Reciprocal rank fusion constant: each list contributes 1 / (RRF_K + rank).
RRF_K = 60
CANDIDATES = 50
# Keyframes per window: the visual index fetches this many times the candidates, so that after
# keeping each window's best keyframe about `candidates` windows remain.
KEYFRAMES_PER_CANDIDATE = 3
# HNSW looks at this many neighbours, and with iterative scans keeps looking until enough rows
# pass the filters (pgvector 0.8), so a camera that has few windows still gets its candidates.
EF_SEARCH = 100


@dataclass
class WindowHit:
    window_id: UUID
    segment_id: UUID
    camera_id: UUID
    camera_name: str
    start_time: datetime
    end_time: datetime
    # Where the window is in its segment, in seconds: for playback.
    segment_start_seconds: float
    segment_end_seconds: float
    description: str | None
    actions: list[dict[str, Any]] = field(default_factory=list)
    fused_score: float = 0.0
    text_score: float | None = None  # cosine similarity, None when not in the text list
    visual_score: float | None = None  # best keyframe's similarity
    text_rank: int | None = None  # 1 = closest
    visual_rank: int | None = None
    keyframe_id: UUID | None = None  # the best keyframe

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        for key, value in data.items():
            if isinstance(value, UUID):
                data[key] = str(value)
            elif isinstance(value, datetime):
                data[key] = value.isoformat()
        return data


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[UUID]], k: int = RRF_K
) -> list[tuple[UUID, float]]:
    """Items of several best-first lists, by summed 1 / (k + rank).

    Ties keep a stable order: the better best rank first, then the earlier list.
    """
    scores: dict[UUID, float] = {}
    order: dict[UUID, tuple[int, int]] = {}
    for list_index, ranked in enumerate(ranked_lists):
        for rank, item in enumerate(ranked, 1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
            order[item] = min(order.get(item, (rank, list_index)), (rank, list_index))
    return sorted(scores.items(), key=lambda pair: (-pair[1], order[pair[0]]))


def _filters(
    camera_ids: Sequence[UUID] | None, start: datetime | None, end: datetime | None
) -> list:
    """Done windows of these cameras that overlap [start, end)."""
    conditions = [AnalysisWindow.processing_status == ProcessingStatus.DONE]
    if camera_ids:
        conditions.append(AnalysisWindow.camera_id.in_(list(camera_ids)))
    if start is not None:
        conditions.append(AnalysisWindow.end_time > start)
    if end is not None:
        conditions.append(AnalysisWindow.start_time < end)
    return conditions


def _use_iterative_scans(session: Session) -> None:
    # SET LOCAL: only for this transaction, so other queries keep the defaults.
    session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    session.execute(text(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}"))


def text_candidates(
    session: Session, vector: np.ndarray, conditions: list, limit: int
) -> list[tuple[UUID, float]]:
    """(window id, cosine similarity) of the closest descriptions, best first."""
    distance = TextEmbedding.embedding.cosine_distance(vector)
    rows = session.execute(
        select(TextEmbedding.analysis_window_id, distance.label("distance"))
        .join(AnalysisWindow, AnalysisWindow.id == TextEmbedding.analysis_window_id)
        .where(*conditions)
        .order_by(distance)
        .limit(limit)
    ).all()
    # relaxed_order can return neighbours slightly out of order: sort exactly.
    return sorted(((w, 1.0 - d) for w, d in rows), key=lambda pair: -pair[1])


def visual_candidates(
    session: Session, vector: np.ndarray, conditions: list, limit: int
) -> list[tuple[UUID, float, UUID]]:
    """(window id, best keyframe similarity, that keyframe's id), best first."""
    distance = Keyframe.visual_embedding.cosine_distance(vector)
    rows = session.execute(
        select(Keyframe.analysis_window_id, Keyframe.id, distance.label("distance"))
        .join(AnalysisWindow, AnalysisWindow.id == Keyframe.analysis_window_id)
        .where(Keyframe.visual_embedding.is_not(None), *conditions)
        .order_by(distance)
        .limit(limit * KEYFRAMES_PER_CANDIDATE)
    ).all()
    best: dict[UUID, tuple[float, UUID]] = {}
    for window, keyframe, d in rows:
        if window not in best or 1.0 - d > best[window][0]:
            best[window] = (1.0 - d, keyframe)
    ranked = sorted(best.items(), key=lambda item: -item[1][0])[:limit]
    return [(window, score, keyframe) for window, (score, keyframe) in ranked]


def search(
    session: Session,
    query: str,
    *,
    camera_ids: Sequence[UUID] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 10,
    candidates: int = CANDIDATES,
    text_embedder: TextEmbedder | None = None,
    image_embedder: ImageEmbedder | None = None,
) -> list[WindowHit]:
    """The best `limit` windows for `query`, inside the filters. Raises IndexSettingsMismatch
    when the indexes were built with other embedding models."""
    text_embedder = text_embedder or get_text_embedder()
    image_embedder = image_embedder or get_image_embedder()
    ensure_index_settings(session, text_embedder.name, image_embedder.name)

    text_vector = text_embedder.embed_query(query)
    visual_vector = image_embedder.embed_text(query)
    conditions = _filters(camera_ids, start, end)
    _use_iterative_scans(session)
    by_text = text_candidates(session, text_vector, conditions, candidates)
    by_picture = visual_candidates(session, visual_vector, conditions, candidates)
    session.commit()  # ends the transaction (and the SET LOCAL); records index_settings

    fused = reciprocal_rank_fusion([[w for w, _ in by_text], [w for w, _, _ in by_picture]])
    top = fused[:limit]
    if not top:
        return []
    text_info = {w: (rank, score) for rank, (w, score) in enumerate(by_text, 1)}
    visual_info = {w: (rank, score, k) for rank, (w, score, k) in enumerate(by_picture, 1)}
    details = _window_details(session, [w for w, _ in top])

    hits = []
    for window_id, fused_score in top:
        window, segment, camera = details[window_id]
        text_rank, text_score = text_info.get(window_id, (None, None))
        visual_rank, visual_score, keyframe_id = visual_info.get(window_id, (None, None, None))
        observation = window.structured_observation_json or {}
        hits.append(
            WindowHit(
                window_id=window.id,
                segment_id=segment.id,
                camera_id=camera.id,
                camera_name=camera.display_name,
                start_time=window.start_time,
                end_time=window.end_time,
                segment_start_seconds=(window.start_time - segment.start_time).total_seconds(),
                segment_end_seconds=(window.end_time - segment.start_time).total_seconds(),
                description=window.vlm_description,
                actions=observation.get("actions", []),
                fused_score=fused_score,
                text_score=text_score,
                visual_score=visual_score,
                text_rank=text_rank,
                visual_rank=visual_rank,
                keyframe_id=keyframe_id,
            )
        )
    return hits


def _window_details(
    session: Session, window_ids: list[UUID]
) -> dict[UUID, tuple[AnalysisWindow, VideoSegment, Camera]]:
    rows = session.execute(
        select(AnalysisWindow, VideoSegment, Camera)
        .join(VideoSegment, VideoSegment.id == AnalysisWindow.segment_id)
        .join(Camera, Camera.id == AnalysisWindow.camera_id)
        .where(AnalysisWindow.id.in_(window_ids))
    ).all()
    return {window.id: (window, segment, camera) for window, segment, camera in rows}
