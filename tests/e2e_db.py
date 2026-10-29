"""Changes the e2e database directly, for states the e2e stack never reaches by itself.

    python -m tests.e2e_db segment-status SEGMENT_ID failed
    python -m tests.e2e_db seed-query SEGMENT_ID SEGMENT_ID      # prints the query id
    python -m tests.e2e_db seed-query --pending CAMERA_ID         # a query no worker took

The e2e stack runs with analysis off and without models, so no segment fails or gets stuck on
its own and no query finds anything; seeded queries let the Investigate page be tested.
Refuses to touch the development database.
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from shared.config import Settings
from shared.database.models import (
    Camera,
    EvidenceStatus,
    ProcessingStatus,
    QueryEvidence,
    QuerySession,
    QueryStatus,
    VideoSegment,
)
from tests.e2e_stack import e2e_settings

PART_MS = 3_000  # each seeded video part plays 3 s
GAP_MS = 312_000  # "5 minutes 12 seconds not shown"


def _set_segment_status(session: Session, segment_id: UUID, status: str, settings) -> None:
    segment = session.get(VideoSegment, segment_id)
    if segment is None:
        sys.exit(f"no segment {segment_id}")
    if status == "stuck":
        long_ago = datetime.now(UTC) - timedelta(minutes=settings.analysis_stuck_minutes + 1)
        segment.set_status(ProcessingStatus.PROCESSING, long_ago)
    else:
        segment.set_status(status)


def _part(segment: VideoSegment) -> dict:
    return {
        "type": "video",
        "camera_id": str(segment.camera_id),
        "segment_id": str(segment.id),
        "source_start_ms": 0,
        "source_end_ms": PART_MS,
        "start_time": segment.start_time.isoformat(),
        "end_time": (segment.start_time + timedelta(milliseconds=PART_MS)).isoformat(),
    }


def _evidence(query: QuerySession, rank: int, parts: list[dict], **fields) -> QueryEvidence:
    result_id = uuid4()
    videos = [p for p in parts if p["type"] == "video"]
    camera_id = UUID(videos[0]["camera_id"])
    start, end = videos[0]["start_time"], videos[-1]["end_time"]
    return QueryEvidence(
        id=result_id,
        query_session_id=query.id,
        rank=rank,
        camera_id=camera_id,
        start_time=datetime.fromisoformat(start),
        end_time=datetime.fromisoformat(end),
        score=1 - rank / 10,
        manifest_json={
            "result_id": str(result_id),
            "camera_id": str(camera_id),
            "start_time": start,
            "end_time": end,
            "parts": parts,
        },
        **fields,
    )


def seed_finished(session: Session, first_id: UUID, second_id: UUID) -> UUID:
    """A finished query with two results: a verified one in two parts with a gap between them
    (one part of each segment), and one that couldn't be verified."""
    first, second = session.get(VideoSegment, first_id), session.get(VideoSegment, second_id)
    if first is None or second is None:
        sys.exit("both segments must exist")
    query = QuerySession(
        query_text="a test pattern moves",
        search_text="a test pattern moves",
        understood_json=[],
        status=QueryStatus.DONE,
        stage="done",
        verification="on",
        finished_at=datetime.now(UTC),
    )
    session.add(query)
    session.flush()
    session.add(
        _evidence(
            query,
            1,
            [_part(first), {"type": "gap", "duration_ms": GAP_MS}, _part(second)],
            status=EvidenceStatus.VERIFIED,
            verification_confidence=0.9,
            verification_reason="A test pattern moves across a grey picture.",
            summary="A test pattern moves across a grey picture.",
        )
    )
    session.add(
        _evidence(
            query,
            2,
            [_part(second)],
            status=EvidenceStatus.UNVERIFIABLE,
            verification_reason="couldn't be verified: the VLM was not reachable",
            summary="A grey picture.",
        )
    )
    return query.id


def seed_pending(session: Session, camera_id: UUID) -> UUID:
    """A query no worker has taken, understood as a camera and a time."""
    camera = session.get(Camera, camera_id)
    if camera is None:
        sys.exit(f"no camera {camera_id}")
    query = QuerySession(
        query_text=f"a bag on {camera.display_name} yesterday",
        search_text="a bag",
        understood_json=[
            {"kind": "camera", "label": camera.display_name, "camera_id": str(camera.id)},
            {"kind": "time", "label": "Yesterday", "camera_id": None},
        ],
        camera_filter=[camera.id],
        status=QueryStatus.PENDING,
    )
    session.add(query)
    session.flush()
    return query.id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("segment-status", help="set a segment's analysis status")
    status.add_argument("segment", type=UUID)
    status.add_argument("status", choices=[*ProcessingStatus, "stuck"])
    seed = commands.add_parser("seed-query", help="store a query with results; prints its id")
    seed.add_argument("--pending", action="store_true", help="a pending query for CAMERA_ID")
    seed.add_argument("ids", type=UUID, nargs="+", help="two segment ids, or one camera id")
    args = parser.parse_args(argv)

    settings = e2e_settings()
    if settings.postgres_db == Settings().postgres_db:
        sys.exit("the e2e database is the development one (check .env); nothing was changed")
    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session:
            if args.command == "segment-status":
                _set_segment_status(session, args.segment, args.status, settings)
            elif args.pending:
                print(seed_pending(session, args.ids[0]))
            else:
                if len(args.ids) != 2:
                    sys.exit("seed-query needs two segment ids")
                print(seed_finished(session, *args.ids))
            session.commit()
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
