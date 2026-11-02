"""Changes the e2e database directly, for states the e2e stack never reaches by itself.

    python -m tests.e2e_db segment-status SEGMENT_ID failed
    python -m tests.e2e_db seed-query SEGMENT_ID SEGMENT_ID      # prints the query id
    python -m tests.e2e_db seed-query --pending CAMERA_ID         # a query no worker took
    python -m tests.e2e_db seed-query --text "…" …                 # with this question (history)
    python -m tests.e2e_db seed-history "TEXT" 25                 # 25 finished queries "TEXT 01"…
    python -m tests.e2e_db seed-trigger CAMERA_ID --name "…" [--enabled]  # a compiled trigger
    python -m tests.e2e_db seed-event TRIGGER_ID SEGMENT_ID [--status confirmed]  # an alerted event
    python -m tests.e2e_db seed-trigger-test TRIGGER_ID SEGMENT_ID  # a finished test, 2 matches
    python -m tests.e2e_db notify EVENT_ID                        # its live notice, on Redis

The e2e stack runs with analysis off and without models, so no segment fails or gets stuck on
its own, no query finds anything and no rule compiles; seeded queries, triggers, tests and events
let the pages be tested.
Refuses to touch the development database.
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import redis
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from shared.config import Settings
from shared.database.models import (
    Camera,
    CompileStatus,
    Event,
    EventEvidence,
    EvidenceStatus,
    ProcessingStatus,
    QueryEvidence,
    QuerySession,
    QueryStatus,
    Trigger,
    TriggerTest,
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


def seed_finished(
    session: Session, first_id: UUID, second_id: UUID, text: str = "a test pattern moves"
) -> UUID:
    """A finished query with two results: a verified one in two parts with a gap between them
    (one part of each segment), and one that couldn't be verified."""
    first, second = session.get(VideoSegment, first_id), session.get(VideoSegment, second_id)
    if first is None or second is None:
        sys.exit("both segments must exist")
    query = QuerySession(
        query_text=text,
        search_text=text,
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


def seed_pending(session: Session, camera_id: UUID, text: str | None = None) -> UUID:
    """A query no worker has taken, understood as a camera and a time."""
    camera = session.get(Camera, camera_id)
    if camera is None:
        sys.exit(f"no camera {camera_id}")
    query = QuerySession(
        query_text=text or f"a bag on {camera.display_name} yesterday",
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


def seed_history(session: Session, text: str, count: int) -> None:
    """`count` finished queries without results, "TEXT 01" the oldest, a second apart."""
    start = datetime.now(UTC) - timedelta(seconds=count)
    for n in range(1, count + 1):
        question = f"{text} {n:02d}"
        session.add(
            QuerySession(
                query_text=question,
                search_text=question,
                understood_json=[],
                status=QueryStatus.DONE,
                stage="done",
                verification="on",
                created_at=start + timedelta(seconds=n),
                finished_at=start + timedelta(seconds=n),
            )
        )


LEFT_BEHIND_RULE = "Report when someone places an object and leaves it behind"
LEFT_BEHIND = {
    "mode": "structured",
    "required_entities": ["person", "portable_object"],
    "required_action_sequence": ["place_object", "leave"],
    "alternative_action_sequences": [["leave_object_behind"]],
    "maximum_sequence_gap_seconds": 30,
    "requires_vlm_verification": True,
    "verification_set_by_operator": False,
    "search_text": "someone places an object and leaves it behind",
    "source_rule": LEFT_BEHIND_RULE,
    "prompt_version": 2,
    "model": "e2e",
}


def seed_trigger(session: Session, camera_id: UUID, name: str, enabled: bool) -> UUID:
    """A trigger whose rule is already compiled, on one camera."""
    camera = session.get(Camera, camera_id)
    if camera is None:
        sys.exit(f"no camera {camera_id}")
    trigger = Trigger(
        name=name,
        natural_language_rule=LEFT_BEHIND_RULE,
        compiled_rule_json=LEFT_BEHIND,
        compile_status=CompileStatus.DONE,
        enabled=enabled,
    )
    trigger.cameras = [camera]
    session.add(trigger)
    session.flush()
    return trigger.id


def _segment(session: Session, segment_id: UUID) -> VideoSegment:
    segment = session.get(VideoSegment, segment_id)
    if segment is None:
        sys.exit(f"no segment {segment_id}")
    return segment


def seed_event(session: Session, trigger_id: UUID, segment_id: UUID, status: str) -> UUID:
    """An alerted event over the whole segment: its evidence plays and exports for real."""
    segment = _segment(session, segment_id)
    start = segment.start_time + timedelta(seconds=2)
    end = segment.start_time + timedelta(seconds=6)
    event = Event(
        trigger_id=trigger_id,
        camera_id=segment.camera_id,
        start_time=start,
        end_time=end,
        confidence=0.9,
        summary="A person places a bag on the floor and walks away, leaving it behind.",
        status=status,
        alerted_at=datetime.now(UTC),
        updated_at=datetime.now(UTC) - timedelta(seconds=1),
        details_json={
            "mode": "structured",
            "window_ids": [],
            "matches": [
                {
                    "actions": [
                        {
                            "type": "place_object",
                            "start": start.isoformat(),
                            "end": start.isoformat(),
                        },
                        {"type": "leave", "start": end.isoformat(), "end": end.isoformat()},
                    ],
                    "verification": {"status": "verified", "confidence": 0.9, "reason": None},
                }
            ],
            "verification": {
                "status": "verified",
                "confidence": 0.9,
                "reason": "A person places a bag on the floor and walks away, leaving it behind.",
            },
            "delay_seconds": 34.0,
        },
    )
    event.evidence = [
        EventEvidence(
            segment_id=segment.id,
            start_offset_ms=0,
            end_offset_ms=segment.duration_ms,
            evidence_reason="the matched activity",
            sequence_order=0,
        )
    ]
    session.add(event)
    session.flush()
    return event.id


def seed_trigger_test(session: Session, trigger_id: UUID, segment_id: UUID) -> UUID:
    """A finished test with a match that would report and one the VLM rejected."""
    trigger = session.get(Trigger, trigger_id)
    segment = _segment(session, segment_id)
    part = _part(segment)

    def match(verdict: str, status: str, reason: str) -> dict:
        return {
            "camera_id": str(segment.camera_id),
            "camera_name": session.get(Camera, segment.camera_id).display_name,
            "start_time": part["start_time"],
            "end_time": part["end_time"],
            "verdict": verdict,
            "confidence": 0.9,
            "verification": {"status": status, "confidence": 0.9, "reason": reason},
            "mode": "structured",
            "score": None,
            "actions": [
                {
                    "type": "leave_object_behind",
                    "start": part["start_time"],
                    "end": part["end_time"],
                }
            ],
            "summary": "A person leaves a bag on the floor.",
            "keyframe_id": None,
            "manifest": {
                "result_id": str(uuid4()),
                "camera_id": str(segment.camera_id),
                "start_time": part["start_time"],
                "end_time": part["end_time"],
                "parts": [part],
            },
        }

    test = TriggerTest(
        trigger_id=trigger_id,
        source="last_24h",
        camera_ids=[segment.camera_id],
        start_time=segment.start_time - timedelta(hours=1),
        end_time=segment.end_time + timedelta(hours=1),
        status=QueryStatus.DONE,
        stage="done",
        finished_at=datetime.now(UTC),
        rule_json={
            "rule": trigger.compiled_rule_json,
            "settings": {
                "minimum_confidence": trigger.minimum_confidence,
                "cooldown_seconds": trigger.cooldown_seconds,
                "pre_roll_seconds": trigger.pre_roll_seconds,
                "post_roll_seconds": trigger.post_roll_seconds,
            },
        },
        results_json={
            "candidates": 4,
            "counts": {"would_report": 1, "rejected": 1, "below_confidence": 0},
            "footage_seconds": 7200.0,
            "matches": [
                match("would_report", "verified", "A person leaves a bag on the floor."),
                match("rejected", "rejected", "The person takes the bag along."),
            ],
        },
    )
    session.add(test)
    session.flush()
    return test.id


def notify(session: Session, event_id: UUID, settings: Settings) -> None:
    """The live notice the worker sends when the event's evidence is stored (5.2)."""
    from services.triggers import notices
    from services.triggers.events import notice

    event = session.get(Event, event_id)
    if event is None:
        sys.exit(f"no event {event_id}")
    message = notice(
        event, session.get(Trigger, event.trigger_id), session.get(Camera, event.camera_id)
    )
    notices.publish(redis.Redis.from_url(settings.redis_url), message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("segment-status", help="set a segment's analysis status")
    status.add_argument("segment", type=UUID)
    status.add_argument("status", choices=[*ProcessingStatus, "stuck"])
    seed = commands.add_parser("seed-query", help="store a query with results; prints its id")
    seed.add_argument("--pending", action="store_true", help="a pending query for CAMERA_ID")
    seed.add_argument("--text", help="the question (default: one that fits the seeded results)")
    seed.add_argument("ids", type=UUID, nargs="+", help="two segment ids, or one camera id")
    history = commands.add_parser("seed-history", help="store COUNT finished queries")
    history.add_argument("text")
    history.add_argument("count", type=int)
    trigger = commands.add_parser("seed-trigger", help="a compiled trigger; prints its id")
    trigger.add_argument("camera", type=UUID)
    trigger.add_argument("--name", default="Object left behind")
    trigger.add_argument("--enabled", action="store_true")
    event = commands.add_parser("seed-event", help="an alerted event; prints its id")
    event.add_argument("trigger", type=UUID)
    event.add_argument("segment", type=UUID)
    event.add_argument("--status", default="new", choices=["new", "confirmed", "dismissed"])
    test = commands.add_parser("seed-trigger-test", help="a finished test; prints its id")
    test.add_argument("trigger", type=UUID)
    test.add_argument("segment", type=UUID)
    live = commands.add_parser("notify", help="publish an event's live notice")
    live.add_argument("event", type=UUID)
    args = parser.parse_args(argv)

    settings = e2e_settings()
    if settings.postgres_db == Settings().postgres_db:
        sys.exit("the e2e database is the development one (check .env); nothing was changed")
    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session:
            if args.command == "segment-status":
                _set_segment_status(session, args.segment, args.status, settings)
            elif args.command == "seed-history":
                seed_history(session, args.text, args.count)
            elif args.command == "seed-trigger":
                print(seed_trigger(session, args.camera, args.name, args.enabled))
            elif args.command == "seed-event":
                print(seed_event(session, args.trigger, args.segment, args.status))
            elif args.command == "seed-trigger-test":
                print(seed_trigger_test(session, args.trigger, args.segment))
            elif args.command == "notify":
                notify(session, args.event, settings)
            elif args.pending:
                print(seed_pending(session, args.ids[0], args.text))
            else:
                if len(args.ids) != 2:
                    sys.exit("seed-query needs two segment ids")
                extra = {"text": args.text} if args.text else {}
                print(seed_finished(session, *args.ids, **extra))
            session.commit()
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
