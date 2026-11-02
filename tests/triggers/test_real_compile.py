"""The real VLM compiles rules (marked `models`: only with RUN_MODEL_TESTS=1).

Uses the configured VLM (OpenRouter with a key in .env, else the local model). Only the rule text
is sent, no footage. Run with `-s` to see the compiled rules.
"""

import pytest

from services.triggers.compile import compile_rule
from shared.config import Settings
from shared.model_adapters import build_vlm

pytestmark = pytest.mark.models


@pytest.fixture(scope="module")
def vlm():
    return build_vlm(Settings())


def test_the_scopes_example_rule_is_a_sequence(vlm):
    rule = compile_rule(vlm, "Report when someone places an object and leaves it behind")
    print(f"\n{rule.model_dump_json(indent=1)}")
    assert rule.mode == "structured"
    actions = rule.required_action_sequence
    assert actions in (["place_object", "leave"], ["leave_object_behind"]) or (
        "place_object" in actions and ("leave" in actions or "leave_object_behind" in actions)
    ), actions
    assert "person" in rule.required_entities
    assert rule.requires_vlm_verification


def test_a_rule_without_vocabulary_actions_is_semantic(vlm):
    rule = compile_rule(vlm, "Alert me if the room gets crowded")
    print(f"\n{rule.model_dump_json(indent=1)}")
    assert rule.mode == "semantic"
    assert "crowd" in rule.search_text.lower()


def test_a_time_in_the_rule_becomes_the_gap(vlm):
    rule = compile_rule(vlm, "Report when a vehicle arrives and then leaves within 2 minutes")
    print(f"\n{rule.model_dump_json(indent=1)}")
    assert rule.required_action_sequence[-1] == "vehicle_depart"
    assert rule.maximum_sequence_gap_seconds == 120
    assert "minute" not in rule.search_text  # the time limit is the gap, not something to see
