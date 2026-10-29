"""Plain-English queries (scope §10, step 4.4).

POST reads the cameras and times out of the text (4.1) at once, stores the query and queues it
for the query worker (ADR 0028); GET and the WebSocket show its progress and results.
"""

from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from services.api.deps import get_query_events, get_query_queue, get_session
from services.query_engine.filters import CameraRef, apply_explicit, parse_query
from services.query_engine.progress import QueryEvents
from shared.config import get_settings
from shared.database.models import Camera, EvidenceStatus, QueryEvidence, QuerySession, QueryStatus
from shared.schemas.query import (
    EvidenceItem,
    PlaybackManifest,
    QueryCounts,
    QueryCreate,
    QueryState,
    UnderstoodItem,
)

router = APIRouter(prefix="/queries", tags=["queries"])

# The WebSocket re-reads the state this often even without a notice (a lost notice, Redis down).
RECHECK_SECONDS = 3.0
FINISHED = (QueryStatus.DONE, QueryStatus.FAILED)


def _get_query(session: Session, query_id: UUID) -> QuerySession:
    query = session.get(QuerySession, query_id)
    if query is None:
        raise HTTPException(404, "query not found")
    return query


def query_state(session: Session, query: QuerySession) -> QueryState:
    statuses = list(
        session.scalars(
            select(QueryEvidence.status).where(QueryEvidence.query_session_id == query.id)
        )
    )
    counts = QueryCounts(
        candidates=len(statuses),
        to_verify=min(get_settings().query_verify_top, len(statuses))
        if query.verification == "on"
        else 0,
        verified=statuses.count(EvidenceStatus.VERIFIED),
        rejected=statuses.count(EvidenceStatus.REJECTED),
        unverifiable=statuses.count(EvidenceStatus.UNVERIFIABLE),
        possible=statuses.count(EvidenceStatus.POSSIBLE),
    )
    return QueryState(
        id=query.id,
        query_text=query.query_text,
        search_text=query.search_text,
        understood=[UnderstoodItem(**u) for u in query.understood_json or []],
        camera_ids=query.camera_filter,
        start=query.start_filter,
        end=query.end_filter,
        status=query.status,
        stage=query.stage,
        verification=query.verification,
        error=query.error,
        created_at=query.created_at,
        finished_at=query.finished_at,
        counts=counts,
    )


def query_results(
    session: Session, query: QuerySession, include_rejected: bool = False
) -> list[EvidenceItem]:
    statement = (
        select(QueryEvidence, Camera.display_name)
        .join(Camera, Camera.id == QueryEvidence.camera_id)
        .where(QueryEvidence.query_session_id == query.id)
        .order_by(QueryEvidence.rank)
    )
    if not include_rejected:
        statement = statement.where(QueryEvidence.status != EvidenceStatus.REJECTED)
    return [
        EvidenceItem(
            id=row.id,
            rank=row.rank,
            camera_id=row.camera_id,
            camera_name=name,
            start_time=row.start_time,
            end_time=row.end_time,
            score=row.score,
            status=row.status,
            verification_confidence=row.verification_confidence,
            verification_reason=row.verification_reason,
            summary=row.summary,
            keyframe_id=row.keyframe_id,
            manifest=PlaybackManifest.model_validate(row.manifest_json),
        )
        for row, name in session.execute(statement).all()
    ]


@router.post("", response_model=QueryState, status_code=202)
def create_query(
    body: QueryCreate,
    session: Session = Depends(get_session),
    queue=Depends(get_query_queue),
) -> QueryState:
    """Read the filters out of the text, store the query and queue it (scope §10 step 1)."""
    cameras = [CameraRef(c.id, c.display_name) for c in session.scalars(select(Camera))]
    if body.camera_ids:
        unknown = set(body.camera_ids) - {c.id for c in cameras}
        if unknown:
            raise HTTPException(422, f"unknown camera: {', '.join(map(str, sorted(unknown)))}")
    parsed = parse_query(
        body.query_text,
        cameras,
        datetime.now(UTC),
        ZoneInfo(body.timezone),
        parse_cameras=body.parse_cameras,
        parse_times=body.parse_times,
    )
    parsed = apply_explicit(parsed, body.camera_ids, body.start, body.end)
    if not parsed.search_text:
        raise HTTPException(
            422, "Add what you're looking for: the query only names cameras or times"
        )
    query = QuerySession(
        query_text=body.query_text,
        search_text=parsed.search_text,
        understood_json=[
            {
                "kind": u.kind,
                "label": u.label,
                "camera_id": str(u.camera_id) if u.camera_id else None,
            }
            for u in parsed.understood
        ],
        camera_filter=parsed.camera_ids or None,
        start_filter=parsed.start,
        end_filter=parsed.end,
        timezone=body.timezone,
        status=QueryStatus.PENDING,
    )
    session.add(query)
    session.commit()
    try:
        queue(query.id)
    except Exception as error:  # noqa: BLE001 - any broker failure
        query.status, query.error = QueryStatus.FAILED, "the query queue is not reachable"
        query.finished_at = datetime.now(UTC)
        session.commit()
        raise HTTPException(503, f"the query queue is not reachable (is Redis running?): {error}")
    session.refresh(query)
    return query_state(session, query)


@router.get("/{query_id}", response_model=QueryState)
def get_query(query_id: UUID, session: Session = Depends(get_session)) -> QueryState:
    return query_state(session, _get_query(session, query_id))


@router.get("/{query_id}/results", response_model=list[EvidenceItem])
def get_results(
    query_id: UUID,
    include_rejected: bool = Query(False, description="also the results the VLM rejected"),
    session: Session = Depends(get_session),
) -> list[EvidenceItem]:
    """Best first. Rejected results are left out unless asked for (ADR 0029)."""
    return query_results(session, _get_query(session, query_id), include_rejected)


@router.websocket("/{query_id}/progress")
async def progress(
    websocket: WebSocket,
    query_id: UUID,
    session: Session = Depends(get_session),
    events: QueryEvents = Depends(get_query_events),
) -> None:
    """Sends {"state", "results"} at once and after every change; closes when the query is
    done or failed. Closes with code 4404 for an unknown query."""
    await websocket.accept()

    def snapshot() -> dict | None:
        session.expire_all()
        query = session.get(QuerySession, query_id)
        if query is None:
            return None
        return {
            "state": query_state(session, query).model_dump(mode="json"),
            "results": [r.model_dump(mode="json") for r in query_results(session, query)],
        }

    try:
        async with events.subscribe(query_id) as subscription:
            last = None
            while True:
                payload = await run_in_threadpool(snapshot)
                if payload is None:
                    await websocket.close(code=4404, reason="query not found")
                    return
                if payload != last:
                    await websocket.send_json(payload)
                    last = payload
                if payload["state"]["status"] in FINISHED:
                    await websocket.close()
                    return
                await subscription.wait(RECHECK_SECONDS)
    except WebSocketDisconnect:
        return
