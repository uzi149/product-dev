"""Compiling a trigger's rule into structured criteria (step 5.1, ADR 0032), with a fake VLM."""

import json

import pytest
from sqlalchemy.orm import Session

from services.triggers import compile_prompt
from services.triggers.compile import (
    InvalidRule,
    compile_rule,
    plain_search_text,
    process_compile,
    read_rule,
)
from shared.database.models import Camera, Trigger
from shared.model_adapters import VLMUnavailable
from shared.schemas.entities import entity_class
from shared.schemas.observation import ACTION_VOCABULARY
from tests.fakes import FakeVLM

RULE = "Report when someone places an object and leaves it behind"
LEFT_BEHIND = json.dumps(
    {
        "required_entities": ["person", "portable_object"],
        "required_action_sequence": ["place_object", "leave"],
        "maximum_sequence_gap_seconds": 30,
        "requires_vlm_verification": True,
        "search_text": "someone places an object and leaves it behind",
    }
)


def test_a_rule_with_actions_is_structured():
    rule = read_rule(LEFT_BEHIND, RULE, "m")
    assert rule.mode == "structured"
    assert rule.required_action_sequence == ["place_object", "leave"]
    assert rule.required_entities == ["person", "portable_object"]
    assert rule.maximum_sequence_gap_seconds == 30 and rule.requires_vlm_verification
    assert rule.source_rule == RULE and rule.model == "m"
    assert rule.prompt_version == compile_prompt.PROMPT_VERSION


def test_unknown_actions_are_dropped_and_none_left_is_semantic():
    answer = {
        "required_entities": ["person"],
        "required_action_sequence": ["gather", "form_crowd"],
        "search_text": "a crowd gathers",
    }
    rule = read_rule(json.dumps(answer), "Alert if a crowd gathers", None)
    assert rule.mode == "semantic" and rule.required_action_sequence == []
    assert rule.search_text == "a crowd gathers"


def test_answers_are_read_forgivingly():
    answer = {
        "required_entities": ["Person", "backpack", "spaceship"],
        "required_action_sequence": ["Place Object", "place_object", "person_moves_away", "leave"],
        "maximum_sequence_gap_seconds": "9000",
        "requires_vlm_verification": "false",
    }
    text = f"Here you go:\n```json\n{json.dumps(answer)}\n```"
    rule = read_rule(text, "report when a bag is put down and the person leaves.", None)
    assert rule.required_entities == ["person", "portable_object"]  # synonym; unknown dropped
    assert rule.required_action_sequence == ["place_object", "leave"]  # repeat counted once
    assert rule.maximum_sequence_gap_seconds == 600  # clipped
    assert rule.requires_vlm_verification is False
    assert rule.search_text == "a bag is put down and the person leaves"  # from the rule


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("I can't help with that", "no JSON object"),
        ('{"required_action_sequence": "leave"}', "must be a list"),
        ("[1, 2]", "no JSON object"),
    ],
)
def test_unusable_answers_are_refused(text, problem):
    with pytest.raises(InvalidRule, match=problem):
        read_rule(text, RULE, None)


@pytest.mark.parametrize(
    ("rule", "plain"),
    [
        ("Report when someone falls", "someone falls"),
        ("alert me if a van parks.", "a van parks"),
        ("Notify me whenever a dog runs", "a dog runs"),
        ("a person loiters", "a person loiters"),
    ],
)
def test_lead_in_words_are_left_out_of_the_search_text(rule, plain):
    assert plain_search_text(rule) == plain


def test_the_prompt_has_the_rule_the_vocabulary_and_the_classes():
    vlm = FakeVLM([LEFT_BEHIND])
    compile_rule(vlm, RULE)
    (call,) = vlm.calls
    assert call["frames"] == 0  # text only
    assert RULE in call["prompt"]
    assert all(word in call["prompt"] for word in ACTION_VOCABULARY)
    assert "portable_object" in call["prompt"] and call["system"] == compile_prompt.SYSTEM_PROMPT


def test_an_unusable_answer_is_asked_again_once():
    vlm = FakeVLM(["no idea", LEFT_BEHIND])
    assert compile_rule(vlm, RULE).mode == "structured"
    assert "could not be used" in vlm.calls[1]["prompt"]
    with pytest.raises(InvalidRule):
        compile_rule(FakeVLM(["no idea"]), RULE)


@pytest.mark.parametrize(
    ("entity_type", "expected"),
    [
        ("man", "person"),
        ("women", "person"),
        ("people", "person"),
        ("pickup truck", "vehicle"),
        ("white van", "vehicle"),
        ("red backpack", "portable_object"),
        ("cardboard box", "portable_object"),
        ("Package", "portable_object"),
        ("dog", "animal"),
        ("traffic cone", None),
    ],
)
def test_entity_types_map_to_classes(entity_type, expected):
    assert entity_class(entity_type) == expected


@pytest.fixture
def trigger(engine):
    with Session(engine) as session:
        camera = Camera(display_name="CAM-01", source_type="file")
        trigger = Trigger(name="Object left behind", natural_language_rule=RULE, enabled=False)
        trigger.cameras = [camera]
        session.add(trigger)
        session.commit()
        return trigger.id


def load(engine, trigger_id) -> Trigger:
    with Session(engine) as session:
        trigger = session.get(Trigger, trigger_id)
        session.expunge(trigger)
        return trigger


def test_the_task_stores_the_compiled_rule(engine, trigger):
    process_compile(trigger, lambda: Session(engine), lambda: FakeVLM([LEFT_BEHIND]))
    stored = load(engine, trigger)
    assert stored.compile_status == "done" and stored.compile_error is None
    assert stored.compiled_rule_json["required_action_sequence"] == ["place_object", "leave"]
    assert stored.enabled is False  # enabling is the operator's step


def test_a_failed_compile_is_recorded(engine, trigger):
    vlm = FakeVLM([VLMUnavailable("connection refused")])
    process_compile(trigger, lambda: Session(engine), lambda: vlm)
    stored = load(engine, trigger)
    assert stored.compile_status == "failed" and "connection refused" in stored.compile_error


def test_the_operators_verification_choice_survives_a_new_compile(engine, trigger):
    process_compile(trigger, lambda: Session(engine), lambda: FakeVLM([LEFT_BEHIND]))
    with Session(engine) as session:
        row = session.get(Trigger, trigger)
        row.compiled_rule_json = {
            **row.compiled_rule_json,
            "requires_vlm_verification": False,
            "verification_set_by_operator": True,
        }
        session.commit()
    process_compile(trigger, lambda: Session(engine), lambda: FakeVLM([LEFT_BEHIND]))
    assert load(engine, trigger).compiled_rule_json["requires_vlm_verification"] is False


def test_a_compile_of_an_older_wording_is_not_stored(engine, trigger):
    def reworded_meanwhile(frames, prompt):
        with Session(engine) as session:
            session.get(Trigger, trigger).natural_language_rule = "Report when someone falls"
            session.commit()
        return LEFT_BEHIND

    process_compile(trigger, lambda: Session(engine), lambda: FakeVLM([reworded_meanwhile]))
    stored = load(engine, trigger)
    assert stored.compile_status == "pending" and stored.compiled_rule_json is None


def test_a_deleted_trigger_is_skipped(engine):
    from uuid import uuid4

    vlm = FakeVLM([LEFT_BEHIND])
    process_compile(uuid4(), lambda: Session(engine), lambda: vlm)
    assert vlm.calls == []
