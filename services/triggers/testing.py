"""Test a trigger on stored footage before enabling it (scope §12.4, step 5.4, ADR 0035).

"Historical testing uses vector retrieval to find candidates and VLM verification to estimate
false positives." Runs on the query worker (ADR 0028) with the trigger's current rule and
settings, and never creates events:

    searching   the best TRIGGER_TEST_CANDIDATES windows for the rule's search text (4.1-4.2),
                inside the cameras and range
    checking    structured rules: 5.2's matcher replayed on each candidate's neighbourhood (every
                window as the one completing the match); semantic rules: the candidates at
                TRIGGER_SEMANTIC_MIN_SCORE or more. Matches closer than the cooldown on one camera
                are one would-be event, as live.
    verifying   when the rule asks, the VLM checks up to TRIGGER_TEST_VERIFY_MAX would-be events
                (none with the local model, ADR 0033)
    done        every would-be event with its verdict: would report, rejected by the VLM, or below
                the confidence; and a playback manifest with the pre- and post-roll
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.events.playback import clipped_spans, spans_manifest
from services.query_engine.retrieval import search
from services.triggers.evaluate import (
    confidence_of,
    facts,
    semantic_scores,
    verify_match,
)
from services.triggers.events import Match, Verification
from services.triggers.matching import WindowFacts, match_rule
from shared.config import Settings, get_settings
from shared.database.models import (
    AnalysisWindow,
    Camera,
    Keyframe,
    ProcessingStatus,
    QueryStatus,
    TriggerTest,
    VideoSegment,
)
from shared.database.session import get_sessionmaker
from shared.jobs.celery_app import TEST_TRIGGER, celery_app
from shared.model_adapters import (
    ImageEmbedder,
    TextEmbedder,
    VisionLanguageModel,
    get_image_embedder,
    get_text_embedder,
    get_vlm,
)
from shared.schemas.trigger import CompiledRule
from shared.storage import StorageClient, get_storage

log = logging.getLogger(__name__)

SEARCHING, CHECKING, VERIFYING, DONE = "searching", "checking", "verifying", "done"
# Verdicts, in the order the results are listed.
VERDICTS = ("would_report", "rejected", "below_confidence")


@dataclass
class TestTools:
    """What a trigger test needs; tests pass fakes."""

    __test__ = False  # not a pytest class

    session_factory: Callable[[], Session]
    storage: StorageClient
    settings: Settings
    vlm: Callable[[], VisionLanguageModel]
    text_embedder: TextEmbedder | None = None
    image_embedder: ImageEmbedder | None = None


def default_tools() -> TestTools:
    return TestTools(get_sessionmaker(), get_storage(), get_settings(), get_vlm)


def _set_stage(session: Session, test: TriggerTest, stage: str) -> None:
    test.stage = stage
    session.commit()


def _neighbourhood(
    session: Session, camera_id: UUID, start: datetime, end: datetime
) -> list[WindowFacts]:
    rows = session.scalars(
        select(AnalysisWindow)
        .where(
            AnalysisWindow.camera_id == camera_id,
            AnalysisWindow.processing_status == ProcessingStatus.DONE,
            AnalysisWindow.end_time >= start,
            AnalysisWindow.start_time <= end,
        )
        .order_by(AnalysisWindow.start_time)
    ).all()
    return [facts(r) for r in rows]


def structured_matches(
    session: Session, rule: CompiledRule, hits, overlap_seconds: float
) -> list[tuple[UUID, Match]]:
    """Replay live evaluation around each candidate: every window as the one completing the
    rule, with the windows it would have seen. (camera, match) pairs, each match once."""
    gap = timedelta(seconds=rule.maximum_sequence_gap_seconds)
    done: set[UUID] = set()
    found: list[tuple[UUID, Match]] = []
    for hit in hits:
        windows = _neighbourhood(session, hit.camera_id, hit.start_time - gap, hit.end_time + gap)
        for window in windows:
            if window.id in done:
                continue
            done.add(window.id)
            seen = [w for w in windows if w.start <= window.end]
            result = match_rule(
                seen,
                rule.action_sequences,
                rule.maximum_sequence_gap_seconds,
                overlap_seconds,
                rule.required_entities,
                window.id,
            )
            if result is not None:
                chain, used = result
                found.append((hit.camera_id, Match("structured", used, chain)))
    return found


def semantic_matches(
    session: Session, rule: CompiledRule, hits, tools: TestTools
) -> list[tuple[UUID, Match]]:
    scores = semantic_scores(
        session,
        rule,
        [h.window_id for h in hits],
        tools.text_embedder,
        tools.image_embedder,
        tools.settings,
    )
    rows = {
        r.id: r
        for r in session.scalars(select(AnalysisWindow).where(AnalysisWindow.id.in_(list(scores))))
    }
    return [
        (rows[window_id].camera_id, Match("semantic", [facts(rows[window_id])], score=round(s, 3)))
        for window_id, s in scores.items()
        if s >= tools.settings.trigger_semantic_min_score and window_id in rows
    ]


def group_by_cooldown(
    matches: list[tuple[UUID, Match]], cooldown_seconds: int
) -> list[tuple[UUID, Match]]:
    """Matches on one camera closer than the cooldown are one would-be event, as live (5.2)."""
    cooldown = timedelta(seconds=cooldown_seconds)
    grouped: list[tuple[UUID, Match]] = []
    for camera_id, match in sorted(matches, key=lambda m: (str(m[0]), m[1].start, m[1].end)):
        last = grouped[-1] if grouped else None
        if last is not None and last[0] == camera_id and match.start <= last[1].end + cooldown:
            previous = last[1]
            windows = {w.id: w for w in [*previous.windows, *match.windows]}
            actions = {
                (a.type, a.start, a.window_id): a for a in [*previous.actions, *match.actions]
            }
            scores = [s for s in (previous.score, match.score) if s is not None]
            grouped[-1] = (
                camera_id,
                Match(
                    previous.mode,
                    sorted(windows.values(), key=lambda w: w.start),
                    sorted(actions.values(), key=lambda a: a.start),
                    max(scores) if scores else None,
                ),
            )
        else:
            grouped.append((camera_id, match))
    return grouped


def _keyframe(session: Session, match: Match) -> UUID | None:
    """The middle keyframe of the window that completed the match."""
    window_id = match.actions[-1].window_id if match.actions else match.windows[0].id
    frames = session.scalars(
        select(Keyframe.id)
        .where(Keyframe.analysis_window_id == window_id)
        .order_by(Keyframe.timestamp)
    ).all()
    return frames[len(frames) // 2] if frames else None


def footage_seconds(
    session: Session, camera_ids: list[UUID], start: datetime, end: datetime
) -> float:
    segments = session.scalars(
        select(VideoSegment).where(
            VideoSegment.camera_id.in_(camera_ids),
            VideoSegment.end_time > start,
            VideoSegment.start_time < end,
        )
    ).all()
    return sum((b - a).total_seconds() for _, a, b in clipped_spans(segments, start, end))


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _result(
    session: Session,
    camera_id: UUID,
    camera_name: str,
    match: Match,
    verification: Verification,
    confidence: float,
    verdict: str,
    pre_roll: int,
    post_roll: int,
) -> dict[str, Any]:
    begin = match.start - timedelta(seconds=pre_roll)
    finish = match.end + timedelta(seconds=post_roll)
    segments = session.scalars(
        select(VideoSegment).where(
            VideoSegment.camera_id == camera_id,
            VideoSegment.end_time > begin,
            VideoSegment.start_time < finish,
        )
    ).all()
    manifest = spans_manifest(
        uuid4(), camera_id, clipped_spans(segments, begin, finish), begin, finish
    )
    descriptions = list(
        dict.fromkeys(w.description for w in match.deciding_windows if w.description)
    )
    keyframe = _keyframe(session, match)
    return {
        "camera_id": str(camera_id),
        "camera_name": camera_name,
        "start_time": _iso(match.start),
        "end_time": _iso(match.end),
        "verdict": verdict,
        "confidence": confidence,
        "verification": {
            "status": verification.status,
            "confidence": verification.confidence,
            "reason": verification.reason,
        },
        "mode": match.mode,
        "score": match.score,
        "actions": [
            {"type": a.type, "start": _iso(a.start), "end": _iso(a.end)} for a in match.actions
        ],
        "window_ids": [str(w.id) for w in match.windows],
        "summary": " ".join(descriptions)[:1000] or None,
        "keyframe_id": str(keyframe) if keyframe else None,
        "manifest": manifest.model_dump(mode="json"),
    }


def _run(session: Session, test: TriggerTest, tools: TestTools) -> dict[str, Any]:
    settings = tools.settings
    rule = CompiledRule.model_validate(test.rule_json["rule"])
    trigger_settings = test.rule_json["settings"]
    hits = search(
        session,
        rule.search_text,
        camera_ids=test.camera_ids,
        start=test.start_time,
        end=test.end_time,
        limit=settings.trigger_test_candidates,
        settings=settings,
        text_embedder=tools.text_embedder,
        image_embedder=tools.image_embedder,
    )
    _set_stage(session, test, CHECKING)
    if rule.mode == "structured":
        matches = structured_matches(session, rule, hits, settings.window_overlap_seconds)
    else:
        matches = semantic_matches(session, rule, hits, tools)
    grouped = group_by_cooldown(matches, trigger_settings["cooldown_seconds"])

    if grouped and rule.requires_vlm_verification:
        _set_stage(session, test, VERIFYING)
    names = dict(session.execute(select(Camera.id, Camera.display_name)).all())
    vlm = tools.vlm() if grouped and rule.requires_vlm_verification else None
    results = []
    for n, (camera_id, match) in enumerate(grouped):
        if rule.requires_vlm_verification and n >= settings.trigger_test_verify_max:
            verification = Verification(
                "not_checked",
                reason=f"only the first {settings.trigger_test_verify_max} are verified",
            )
        else:
            verification = verify_match(
                session, rule, match, camera_id, vlm, tools.storage, settings
            )
        confidence = confidence_of(match, verification)
        if verification.status == "rejected":
            verdict = "rejected"
        elif confidence < trigger_settings["minimum_confidence"]:
            verdict = "below_confidence"
        else:
            verdict = "would_report"
        results.append(
            _result(
                session,
                camera_id,
                names.get(camera_id, "?"),
                match,
                verification,
                confidence,
                verdict,
                trigger_settings["pre_roll_seconds"],
                trigger_settings["post_roll_seconds"],
            )
        )
    results.sort(key=lambda r: (VERDICTS.index(r["verdict"]), r["start_time"]))
    return {
        "candidates": len(hits),
        "counts": {v: sum(r["verdict"] == v for r in results) for v in VERDICTS},
        "footage_seconds": round(
            footage_seconds(session, test.camera_ids, test.start_time, test.end_time), 1
        ),
        "matches": results,
    }


def process_test(test_id: UUID, tools: TestTools | None = None) -> None:
    tools = tools or default_tools()
    if tools.text_embedder is None:
        tools.text_embedder = get_text_embedder()
    if tools.image_embedder is None:
        tools.image_embedder = get_image_embedder()
    with tools.session_factory() as session:
        test = session.get(TriggerTest, test_id)
        if test is None:
            log.warning("trigger test %s does not exist (deleted?); nothing to run", test_id)
            return
        if test.status in (QueryStatus.DONE, QueryStatus.FAILED):
            return  # delivered twice
        test.status = QueryStatus.RUNNING
        _set_stage(session, test, SEARCHING)
        try:
            results = _run(session, test, tools)
        except Exception as error:
            log.exception("trigger test %s failed", test_id)
            session.rollback()
            test = session.get(TriggerTest, test_id)
            if test is None:
                return
            test.status, test.stage = QueryStatus.FAILED, DONE
            test.error = str(error)[:1000] or type(error).__name__
            test.finished_at = datetime.now(UTC)
            session.commit()
            return
        test.results_json = results
        test.status, test.stage = QueryStatus.DONE, DONE
        test.finished_at = datetime.now(UTC)
        session.commit()
        log.info("trigger test %s: %s", test_id, results["counts"])


@celery_app.task(name=TEST_TRIGGER)
def test_trigger(test_id: str) -> None:
    process_test(UUID(test_id))
