"""Compile a trigger's rule into structured criteria (scope §12.2, step 5.1, ADR 0032).

Runs as a task on the query worker (ADR 0028), so the API never loads the local model. The
answer is read strictly about meaning and forgivingly about form, as for observations: action
words outside the vocabulary and unknown entity classes are dropped, the gap is clipped. A rule
whose answer has no vocabulary action is `semantic` (matched by similarity, 5.2). An answer that
can't be used twice, or a VLM that fails, leaves the trigger `failed`: it can't be enabled until
a compile succeeds.
"""

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from services.triggers import compile_prompt
from shared.database.models import CompileStatus, Trigger
from shared.database.session import get_sessionmaker
from shared.jobs.celery_app import COMPILE_TRIGGER, celery_app
from shared.model_adapters import VisionLanguageModel, VLMError, get_vlm
from shared.model_adapters.json_output import extract_json
from shared.schemas.entities import ENTITY_CLASSES, entity_class
from shared.schemas.observation import ACTION_VOCABULARY
from shared.schemas.trigger import (
    DEFAULT_GAP_SECONDS,
    MAX_GAP_SECONDS,
    MIN_GAP_SECONDS,
    CompiledRule,
)

log = logging.getLogger(__name__)

# "Report when …", "alert me if …": what a rule says about reporting, not what to look for.
_LEAD_IN = re.compile(
    r"^\s*(?:please\s+)?(?:report|alert(?:\s+me)?|notify(?:\s+me)?|tell\s+me|warn(?:\s+me)?|"
    r"trigger|flag)\s+(?:when(?:ever)?|if|on)\s+",
    re.IGNORECASE,
)


class InvalidRule(Exception):
    """The model's answer can't be used."""


def plain_search_text(rule: str) -> str:
    return _LEAD_IN.sub("", rule).strip().rstrip(".") or rule.strip()


def _action_word(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    word = re.sub(r"[\s-]+", "_", value.strip().lower())
    return word if word in ACTION_VOCABULARY else None


def _entity(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    word = value.strip().lower().replace(" ", "_")
    return word if word in ENTITY_CLASSES else entity_class(value)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def read_rule(text: str, rule: str, model: str | None) -> CompiledRule:
    data = extract_json(text, expect=dict)
    if data is None:
        raise InvalidRule("no JSON object found")
    raw_actions = data.get("required_action_sequence")
    if not isinstance(raw_actions, list):
        raise InvalidRule("'required_action_sequence' must be a list")
    actions = [a for a in map(_action_word, raw_actions) if a]
    # The same word twice in a row is one step ("walk, walk").
    actions = [a for i, a in enumerate(actions) if i == 0 or a != actions[i - 1]]
    raw_entities = data.get("required_entities")
    entities = _unique(
        [e for e in map(_entity, raw_entities if isinstance(raw_entities, list) else []) if e]
    )
    try:
        gap = round(float(data.get("maximum_sequence_gap_seconds", DEFAULT_GAP_SECONDS)))
    except (TypeError, ValueError):
        gap = DEFAULT_GAP_SECONDS
    verify = data.get("requires_vlm_verification", True)
    if isinstance(verify, str):
        verify = verify.strip().lower() != "false"
    search = data.get("search_text")
    search = search.strip() if isinstance(search, str) and search.strip() else ""
    return CompiledRule(
        mode="structured" if actions else "semantic",
        required_entities=entities,
        required_action_sequence=actions,
        maximum_sequence_gap_seconds=min(MAX_GAP_SECONDS, max(MIN_GAP_SECONDS, gap)),
        requires_vlm_verification=bool(verify),
        search_text=(search or plain_search_text(rule))[:500],
        source_rule=rule,
        prompt_version=compile_prompt.PROMPT_VERSION,
        model=model,
    )


def compile_rule(vlm: VisionLanguageModel, rule: str) -> CompiledRule:
    """Raises InvalidRule after two unusable answers; VLM errors pass through."""
    ask = compile_prompt.compile_prompt(rule)
    for attempt in (1, 2):
        reply = vlm.complete(
            [],
            ask,
            system=compile_prompt.SYSTEM_PROMPT,
            max_tokens=compile_prompt.MAX_ANSWER_TOKENS,
        )
        try:
            return read_rule(reply.text, rule, reply.model)
        except InvalidRule as error:
            if attempt == 2:
                raise
            log.info("%s gave an unusable rule (%s); asking again", reply.model, error)
            ask = compile_prompt.retry_prompt(str(error), rule)
    raise AssertionError("unreachable")


def process_compile(
    trigger_id: UUID,
    session_factory: Callable[[], Session] | None = None,
    vlm: Callable[[], VisionLanguageModel] = get_vlm,
) -> None:
    session_factory = session_factory or get_sessionmaker()
    with session_factory() as session:
        trigger = session.get(Trigger, trigger_id)
        if trigger is None:
            log.warning("trigger %s does not exist (deleted?); nothing to compile", trigger_id)
            return
        rule = trigger.natural_language_rule
        try:
            compiled = compile_rule(vlm(), rule)
        except (InvalidRule, VLMError) as error:
            session.refresh(trigger)
            if trigger.natural_language_rule != rule:
                return  # the rule changed meanwhile: its own compile is queued
            log.warning("trigger %s couldn't be compiled: %s", trigger_id, error)
            trigger.compile_status = CompileStatus.FAILED
            trigger.compile_error = f"{type(error).__name__}: {error}"[:1000]
            trigger.updated_at = datetime.now(UTC)
            session.commit()
            return
        session.refresh(trigger)
        if trigger.natural_language_rule != rule:
            return  # compiled an older wording; the new one has its own compile queued
        previous = trigger.compiled_rule_json or {}
        if previous.get("verification_set_by_operator"):
            compiled.requires_vlm_verification = bool(previous["requires_vlm_verification"])
            compiled.verification_set_by_operator = True
        trigger.compiled_rule_json = compiled.model_dump(mode="json")
        trigger.compile_status = CompileStatus.DONE
        trigger.compile_error = None
        trigger.updated_at = datetime.now(UTC)
        session.commit()
        log.info(
            "trigger %s compiled (%s): %s",
            trigger_id,
            compiled.mode,
            " -> ".join(compiled.required_action_sequence) or compiled.search_text,
        )


@celery_app.task(name=COMPILE_TRIGGER)
def compile_trigger(trigger_id: str) -> None:
    process_compile(UUID(trigger_id))
