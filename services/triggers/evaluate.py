"""Live trigger evaluation: one analysed window against its camera's triggers (scope §12.3, step
5.2, ADR 0033).

Runs in the analysis worker right after a window is stored, with the observation, vectors and
models already there: "a clip is analysed once", and the VLM is only asked again to verify a
match the rule wants verified. Only the enabled, compiled triggers assigned to the window's own
camera are loaded.

    structured   the rule's actions in order within its gap, the last one in this window, and
                 its entity classes seen (matching.py)
    semantic     this window's hybrid score (4.2) for the rule's search text, at least
                 TRIGGER_SEMANTIC_MIN_SCORE
    verification as for search results (4.4): verified at QUERY_VERIFY_MIN_CONFIDENCE; rejected
                 makes no event; a failed verification, or the local model, still alerts, marked
    confidence   the verification's when verified, else the lowest of the deciding windows'

Then events.py records the event (or extends one within the cooldown) and holds it until its
post-roll footage is stored.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.query_engine import hybrid
from services.query_engine.evidence import (
    EvidenceResult,
    Window,
    build_manifest,
    load_segments,
    merge_sequences,
)
from services.query_engine.verify import InvalidVerdict, verify
from services.triggers.events import (
    FinishTools,
    Match,
    Verification,
    process_finish,
    record_match,
)
from services.triggers.matching import (
    WindowFacts,
    classes_seen,
    match_sequence,
    timeline,
    window_facts,
)
from shared.database.models import (
    AnalysisWindow,
    CompileStatus,
    Event,
    ProcessingStatus,
    Trigger,
    TriggerCamera,
)
from shared.model_adapters import (
    ImageEmbedder,
    TextEmbedder,
    VisionLanguageModel,
    VLMError,
    resolve_vlm_backend,
)
from shared.schemas.observation import DEFAULT_CONFIDENCE
from shared.schemas.trigger import CompiledRule
from shared.storage import StorageClient, StorageError

log = logging.getLogger(__name__)


@dataclass
class TriggerTools:
    """What evaluating a window needs; the analysis worker passes its models."""

    storage: StorageClient
    vlm: VisionLanguageModel
    text_embedder: TextEmbedder
    image_embedder: ImageEmbedder
    finish: FinishTools

    @property
    def settings(self):
        return self.finish.settings


# The rule's text and picture vectors, computed once per worker process.
_rule_vectors: dict[tuple[str, str, str], tuple[np.ndarray, np.ndarray]] = {}


def rule_vectors(text: str, tools: TriggerTools) -> tuple[np.ndarray, np.ndarray]:
    key = (tools.text_embedder.name, tools.image_embedder.name, text)
    if key not in _rule_vectors:
        _rule_vectors[key] = (
            tools.text_embedder.embed_query(text),
            tools.image_embedder.embed_text(text),
        )
    return _rule_vectors[key]


def enabled_triggers(session: Session, camera_id: UUID) -> list[Trigger]:
    """The enabled, compiled triggers assigned to this camera; never any other camera's."""
    return list(
        session.scalars(
            select(Trigger)
            .join(TriggerCamera, TriggerCamera.trigger_id == Trigger.id)
            .where(
                TriggerCamera.camera_id == camera_id,
                Trigger.enabled.is_(True),
                Trigger.compile_status == CompileStatus.DONE,
            )
            .order_by(Trigger.name, Trigger.id)
        )
    )


def _facts(row: AnalysisWindow) -> WindowFacts:
    return window_facts(
        row.id,
        row.segment_id,
        row.start_time,
        row.end_time,
        row.vlm_description,
        row.confidence,
        row.structured_observation_json,
    )


def structured_match(
    session: Session, rule: CompiledRule, current: AnalysisWindow, overlap_seconds: float
) -> Match | None:
    gap = timedelta(seconds=rule.maximum_sequence_gap_seconds)
    rows = session.scalars(
        select(AnalysisWindow)
        .where(
            AnalysisWindow.camera_id == current.camera_id,
            AnalysisWindow.processing_status == ProcessingStatus.DONE,
            AnalysisWindow.end_time >= current.start_time - gap,
            AnalysisWindow.start_time <= current.end_time,
        )
        .order_by(AnalysisWindow.start_time)
    ).all()
    windows = [_facts(r) for r in rows]
    actions = timeline(windows, overlap_seconds)
    # The required sequence, or any alternative way the analysis may describe the same event.
    for sequence in rule.action_sequences:
        chain = match_sequence(actions, sequence, rule.maximum_sequence_gap_seconds, current.id)
        if chain is None:
            continue
        start, end = chain[0].start, max(a.end for a in chain)
        if not set(rule.required_entities) <= classes_seen(windows, start, end):
            continue
        used = [w for w in windows if w.start <= end and w.end >= start]
        return Match("structured", used, chain)
    return None


def semantic_match(
    session: Session, rule: CompiledRule, current: AnalysisWindow, tools: TriggerTools
) -> Match | None:
    text_vector, visual_vector = rule_vectors(rule.search_text, tools)
    # The event signal would let this trigger's own earlier events raise the score.
    settings = tools.settings.model_copy(update={"query_weight_event": 0.0})
    signals = hybrid.score(
        session, [current.id], rule.search_text, text_vector, visual_vector, settings
    ).get(current.id)
    if signals is None or signals.fused < tools.settings.trigger_semantic_min_score:
        return None
    return Match("semantic", [_facts(current)], score=round(signals.fused, 3))


def _evidence(session: Session, match: Match, camera_id: UUID, settings) -> EvidenceResult:
    """The match as a 4.3 evidence result, so 4.4's verifier picks its frames and gaps."""
    deciding = {w.id for w in match.deciding_windows}
    windows = [
        Window(
            w.id,
            camera_id,
            w.segment_id,
            w.start,
            w.end,
            w.description,
            1.0 if w.id in deciding else None,
        )
        for w in match.windows
    ]
    result = EvidenceResult(
        camera_id,
        merge_sequences(windows, timedelta(seconds=settings.query_merge_gap_seconds)),
    )
    result.manifest = build_manifest(
        result, load_segments(session, (w.segment_id for w in windows))
    )
    return result


def verify_match(
    session: Session, rule: CompiledRule, match: Match, camera_id: UUID, tools: TriggerTools
) -> Verification:
    settings = tools.settings
    if not rule.requires_vlm_verification:
        return Verification("not_required")
    if resolve_vlm_backend(settings) == "local":
        return Verification("not_verified_local", reason="verification is off with the local model")
    try:
        verdict = verify(
            tools.vlm,
            session,
            tools.storage,
            _evidence(session, match, camera_id, settings),
            rule.search_text,
            settings.query_verify_frames,
            settings.vlm_image_max_side,
        )
    except (InvalidVerdict, VLMError, StorageError) as error:
        return Verification("unverifiable", reason=f"couldn't be verified: {error}"[:500])
    verified = verdict.match and verdict.confidence >= settings.query_verify_min_confidence
    return Verification("verified" if verified else "rejected", verdict.confidence, verdict.reason)


def confidence_of(match: Match, verification: Verification) -> float:
    """ADR 0033: the verification's when verified, else the lowest deciding window's."""
    if verification.status == "verified" and verification.confidence is not None:
        return verification.confidence
    values = [
        w.confidence if w.confidence is not None else DEFAULT_CONFIDENCE
        for w in match.deciding_windows
    ]
    return min(values, default=DEFAULT_CONFIDENCE)


def already_counted(session: Session, trigger_id: UUID, window_id: UUID) -> bool:
    """A window that is already part of this trigger's event (a retried or re-analysed
    segment) never makes a second one."""
    return (
        session.scalar(
            select(Event.id)
            .where(
                Event.trigger_id == trigger_id,
                Event.details_json.contains({"window_ids": [str(window_id)]}),
            )
            .limit(1)
        )
        is not None
    )


def evaluate_window(session: Session, window_id: UUID, tools: TriggerTools) -> list[UUID]:
    """The events this window made or extended."""
    current = session.get(AnalysisWindow, window_id)
    if current is None or current.processing_status != ProcessingStatus.DONE:
        return []
    triggers = enabled_triggers(session, current.camera_id)
    log.info(
        "window %s (camera %s): checking %d trigger(s)%s",
        window_id,
        current.camera_id,
        len(triggers),
        f": {', '.join(t.name for t in triggers)}" if triggers else "",
    )
    events = []
    for trigger in triggers:
        if already_counted(session, trigger.id, window_id):
            continue
        rule = CompiledRule.model_validate(trigger.compiled_rule_json)
        if rule.mode == "structured":
            match = structured_match(session, rule, current, tools.settings.window_overlap_seconds)
        else:
            match = semantic_match(session, rule, current, tools)
        if match is None:
            continue
        verification = verify_match(session, rule, match, current.camera_id, tools)
        if verification.status == "rejected":
            log.info(
                "trigger %s on window %s: rejected by the VLM (%s)",
                trigger.name,
                window_id,
                verification.reason,
            )
            continue
        confidence = confidence_of(match, verification)
        if confidence < trigger.minimum_confidence:
            log.info(
                "trigger %s on window %s: confidence %.2f below %.2f",
                trigger.name,
                window_id,
                confidence,
                trigger.minimum_confidence,
            )
            continue
        event, new = record_match(
            session, trigger, current.camera_id, match, verification, confidence, tools.finish.now()
        )
        session.commit()
        log.info(
            "trigger %s on window %s: %s event %s (%s, confidence %.2f)",
            trigger.name,
            window_id,
            "new" if new else "extended",
            event.id,
            verification.status,
            confidence,
        )
        events.append(event.id)
        process_finish(session, event.id, tools.finish)
    return events
