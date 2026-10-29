"""Celery task: run one query end to end (scope §10, step 4.4).

    searching   filters were read when the query was made (POST /queries); hybrid search (4.2)
    candidates  merged evidence results (4.3) stored as "possible match"
    verifying   the best QUERY_VERIFY_TOP results checked by the VLM one by one (ADR 0029);
                rejected ones leave the list, the rest stay possible; skipped with the local VLM
    done

Every change is committed, then announced on Redis, so the UI can show candidates quickly and
add verified results as they come. Runs on its own worker: `-Q query` (ADR 0028).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.orm import Session

from services.query_engine import progress
from services.query_engine.evidence import EvidenceResult, evidence_results
from services.query_engine.retrieval import WindowHit, search
from services.query_engine.verify import InvalidVerdict, verify
from shared.camera_status import get_redis
from shared.config import Settings, get_settings
from shared.database.models import (
    EvidenceStatus,
    QueryEvidence,
    QueryResult,
    QuerySession,
    QueryStatus,
)
from shared.database.session import get_sessionmaker
from shared.jobs.celery_app import RUN_QUERY, celery_app
from shared.model_adapters import (
    ImageEmbedder,
    TextEmbedder,
    VisionLanguageModel,
    VLMError,
    get_vlm,
    resolve_vlm_backend,
)
from shared.storage import StorageClient, StorageError, get_storage

log = logging.getLogger(__name__)

SEARCHING, CANDIDATES, VERIFYING, DONE = "searching", "candidates", "verifying", "done"


@dataclass
class Tools:
    """What a query run needs; tests pass fakes."""

    session_factory: Callable[[], Session]
    storage: StorageClient
    settings: Settings
    publish: Callable[[UUID], None]
    vlm: Callable[[], VisionLanguageModel]
    text_embedder: TextEmbedder | None = None
    image_embedder: ImageEmbedder | None = None


def default_tools() -> Tools:
    client = get_redis()
    return Tools(
        session_factory=get_sessionmaker(),
        storage=get_storage(),
        settings=get_settings(),
        publish=lambda query_id: progress.publish(client, query_id),
        vlm=get_vlm,
    )


def _summary(result: EvidenceResult) -> str | None:
    """The best hit's description: what the result is about."""
    hits = [w for w in result.windows if w.score is not None and w.description]
    return max(hits, key=lambda w: w.score).description if hits else None


def _store(session: Session, query: QuerySession, results: list[EvidenceResult], hits) -> list:
    by_window = {h.window_id: h for h in hits}
    rows = []
    for rank, result in enumerate(results, 1):
        best = max(
            (by_window[w.id] for w in result.windows if w.id in by_window),
            key=lambda h: h.fused_score,
        )
        row = QueryEvidence(
            id=result.id,
            query_session_id=query.id,
            rank=rank,
            camera_id=result.camera_id,
            start_time=result.start,
            end_time=result.end,
            score=result.score,
            status=EvidenceStatus.POSSIBLE,
            summary=_summary(result),
            keyframe_id=best.keyframe_id,
            manifest_json=result.manifest.model_dump(mode="json"),
        )
        session.add(row)
        rows.append(row)
        for window in result.windows:
            hit: WindowHit | None = by_window.get(window.id)
            session.add(
                QueryResult(
                    query_session_id=query.id,
                    analysis_window_id=window.id,
                    vector_score=max(
                        (s for s in (hit.text_score, hit.visual_score) if s is not None),
                        default=None,
                    )
                    if hit
                    else None,
                    rerank_score=hit.fused_score if hit else None,
                    sequence_group_id=result.id,
                )
            )
    return rows


def _set_verdict(session: Session, row: QueryEvidence, status, confidence, reason) -> None:
    row.status = status
    row.verification_confidence = confidence
    row.verification_reason = reason
    session.execute(
        update(QueryResult)
        .where(QueryResult.sequence_group_id == row.id)
        .values(verification_score=confidence, verified=status == EvidenceStatus.VERIFIED)
    )


def process_query(query_id: UUID, tools: Tools | None = None) -> None:
    tools = tools or default_tools()
    settings = tools.settings
    with tools.session_factory() as session:
        query = session.get(QuerySession, query_id)
        if query is None:
            log.warning("query %s does not exist; nothing to run", query_id)
            return
        if query.status in (QueryStatus.DONE, QueryStatus.FAILED):
            return  # delivered twice
        query.status, query.stage = QueryStatus.RUNNING, SEARCHING
        session.commit()
        tools.publish(query_id)
        try:
            _run(session, query, tools, settings)
        except Exception as error:
            log.exception("query %s failed", query_id)
            session.rollback()
            query = session.get(QuerySession, query_id)
            query.status, query.stage = QueryStatus.FAILED, DONE
            query.error = str(error)[:1000] or type(error).__name__
            query.finished_at = datetime.now(UTC)
            session.commit()
            tools.publish(query_id)


def _run(session: Session, query: QuerySession, tools: Tools, settings: Settings) -> None:
    hits = search(
        session,
        query.search_text,
        camera_ids=query.camera_filter or None,
        start=query.start_filter,
        end=query.end_filter,
        limit=settings.query_expand_top,
        settings=settings,
        text_embedder=tools.text_embedder,
        image_embedder=tools.image_embedder,
    )
    results = evidence_results(session, hits, settings)
    rows = _store(session, query, results, hits)
    skip = resolve_vlm_backend(settings) == "local"
    query.verification = "skipped" if skip else "on"
    query.stage = CANDIDATES
    session.commit()
    tools.publish(query.id)

    to_verify = [] if skip else list(zip(results, rows, strict=True))[: settings.query_verify_top]
    if to_verify:
        query.stage = VERIFYING
        session.commit()
        tools.publish(query.id)
        vlm = tools.vlm()
        for result, row in to_verify:
            status, confidence, reason = _check(vlm, session, tools, settings, result, query)
            _set_verdict(session, row, status, confidence, reason)
            session.commit()
            tools.publish(query.id)

    query.status, query.stage = QueryStatus.DONE, DONE
    query.finished_at = datetime.now(UTC)
    session.commit()
    tools.publish(query.id)
    log.info("query %s: %d result(s), verification %s", query.id, len(results), query.verification)


def _check(vlm, session, tools, settings, result, query):
    """(status, confidence, reason) for one result."""
    try:
        verdict = verify(
            vlm,
            session,
            tools.storage,
            result,
            query.search_text,
            settings.query_verify_frames,
            settings.vlm_image_max_side,
        )
    except (InvalidVerdict, VLMError, StorageError) as error:
        log.warning("query %s result %s couldn't be verified: %s", query.id, result.id, error)
        return EvidenceStatus.UNVERIFIABLE, None, f"couldn't be verified: {error}"[:500]
    verified = verdict.match and verdict.confidence >= settings.query_verify_min_confidence
    status = EvidenceStatus.VERIFIED if verified else EvidenceStatus.REJECTED
    return status, verdict.confidence, verdict.reason


@celery_app.task(name=RUN_QUERY)
def run_query(query_id: str) -> None:
    process_query(UUID(query_id))
