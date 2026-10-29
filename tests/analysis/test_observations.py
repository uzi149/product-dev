"""Turning the VLM's answer into a validated observation (scope §8.1)."""

import copy
import json

import numpy as np
import pytest

from services.analysis import prompts
from services.analysis.observations import clean_summary, describe_window, index_text
from shared.model_adapters import VLMUnavailable
from shared.schemas.observation import InvalidObservation, read_observation
from tests.fakes import FakeVLM

SCOPE_EXAMPLE = prompts.EXAMPLE  # the scope §8.1 example response


def read(data, seconds=12.0):
    return read_observation(data, seconds)


def test_the_scope_example_passes_unchanged():
    observation = read(copy.deepcopy(SCOPE_EXAMPLE))
    dumped = observation.model_dump(exclude_none=True)
    assert dumped == SCOPE_EXAMPLE


def test_an_unknown_action_is_dropped_and_noted():
    data = copy.deepcopy(SCOPE_EXAMPLE)
    data["actions"].append(
        {"type": "wave", "subject": "person_1", "start_second": 3, "end_second": 4}
    )
    observation = read(data)
    assert [a.type for a in observation.actions] == ["enter", "place_object", "leave"]
    assert observation.uncertainties == ["dropped unknown action 'wave'"]


def test_action_words_are_normalised():
    data = {
        "summary": "s",
        "actions": [{"type": "Ring Doorbell", "start_second": 1, "end_second": 2}],
    }
    assert read(data).actions[0].type == "ring_doorbell"


def test_times_are_clamped_to_the_window_and_swapped():
    data = {
        "summary": "s",
        "actions": [
            {"type": "walk", "start_second": -2, "end_second": 30},
            {"type": "run", "start_second": 8, "end_second": 3},
            {"type": "stand"},
        ],
    }
    actions = read(data, seconds=12).actions
    assert [(a.start_second, a.end_second) for a in actions] == [(0, 12), (3, 8), (0, 12)]


def test_an_unknown_subject_is_kept_and_noted():
    data = {"summary": "s", "entities": [], "actions": [{"type": "walk", "subject": "person_9"}]}
    observation = read(data)
    assert observation.actions[0].subject == "person_9"
    assert "unknown subject 'person_9'" in observation.uncertainties[0]


@pytest.mark.parametrize(("given", "stored"), [(1.4, 1.0), (-0.2, 0.0), ("0.7", 0.7)])
def test_confidence_is_clamped(given, stored):
    assert read({"summary": "s", "overall_confidence": given}).overall_confidence == stored


def test_a_missing_confidence_gets_the_default_and_a_note():
    observation = read({"summary": "s"})
    assert observation.overall_confidence == 0.5
    assert observation.uncertainties == ["the model gave no confidence"]
    assert observation.entities == observation.actions == observation.visible_objects == []


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        ([SCOPE_EXAMPLE], "not a JSON object"),
        ("text", "not a JSON object"),
        ({"entities": []}, "summary is missing"),
        ({"summary": "  "}, "summary is missing"),
    ],
)
def test_unusable_answers_are_invalid(data, problem):
    with pytest.raises(InvalidObservation, match=problem):
        read(data)


def test_malformed_items_are_skipped():
    data = {
        "summary": "s",
        "entities": [
            "person_1",
            {"temporary_id": "person_1"},
            {"temporary_id": "p", "type": "person"},
        ],
        "actions": ["walk", None],
        "visible_objects": ["person", 3, ""],
    }
    observation = read(data)
    assert [e.temporary_id for e in observation.entities] == ["p"]
    assert observation.actions == []
    assert observation.visible_objects == ["person"]


@pytest.mark.parametrize(
    ("text", "cleaned"),
    [
        ("A person walks to the door.", "A person walks to the door."),
        ("Across the frames, a person walks in. Frame 3 shows a bag.", "A person walks in."),
        ("Summary: - Frame 1: a car. - Frame 2: a car parks. A car parks.", "A car parks."),
        ("A car parks in all frames.", "A car parks."),
        ("Frame 1 shows a car.", "Frame 1 shows a car."),  # nothing left: the original
        (
            "A person walks towards the lower left of the frame. The scene is quiet.",
            "A person walks towards the lower left of the frame. The scene is quiet.",
        ),
        ("A car parks. In the last frame it is gone.", "A car parks."),
        ("A van stops. Each frame shows rain.", "A van stops."),
    ],
)
def test_frame_by_frame_text_is_removed_from_the_summary(text, cleaned):
    assert clean_summary(text) == cleaned


def test_the_index_text_has_the_summary_actions_and_objects():
    text = index_text(read(copy.deepcopy(SCOPE_EXAMPLE)))
    assert text == (
        "A person enters, places a dark bag near the wall and leaves.\n"
        "Actions: person_1 enter, person_1 place_object object_1 (bag), person_1 leave.\n"
        "Visible: person, bag."
    )


FRAMES = [np.zeros((36, 64, 3), np.uint8)] * 6
TIMES = [1.0, 3.0, 5.0, 7.0, 9.0, 11.0]


def describe(vlm):
    return describe_window(vlm, FRAMES, "Front door", TIMES, 12.0)


def test_a_good_answer_is_used_with_the_strict_prompt():
    vlm = FakeVLM([f"Here you go:\n```json\n{json.dumps(SCOPE_EXAMPLE)}\n```"])
    described = describe(vlm)
    assert described.attempts == 1 and described.observation.summary.startswith("A person enters")
    call = vlm.calls[0]
    assert call["frames"] == 6 and call["system"] == prompts.SYSTEM_PROMPT
    assert call["max_tokens"] == prompts.MAX_ANSWER_TOKENS and '"Front door"' in call["prompt"]


def test_one_bad_answer_gets_the_stricter_prompt():
    vlm = FakeVLM(["I see a person.", json.dumps(SCOPE_EXAMPLE)])
    described = describe(vlm)
    assert described.attempts == 2
    assert "could not be used: no JSON object found" in vlm.calls[1]["prompt"]


def test_two_bad_answers_are_invalid():
    vlm = FakeVLM(["I see a person.", '{"entities": []}'])
    with pytest.raises(InvalidObservation, match="did not return a valid observation"):
        describe(vlm)
    assert len(vlm.calls) == 2


def test_vlm_errors_pass_through():
    with pytest.raises(VLMUnavailable):
        describe(FakeVLM([VLMUnavailable("timeout")]))


class CutOffVLM:
    """Answers like a model that listed every parked car and hit the token limit, then well."""

    name = "cut-off-vlm"

    def __init__(self, head='{"summary": "A busy car park.", "entities": ['):
        self.head = head
        self.calls = []

    def complete(self, frames, prompt, *, system=None, max_tokens=800):
        from shared.model_adapters import VLMReply

        self.calls.append(prompt)
        if len(self.calls) == 1:
            cars = ", ".join(f'{{"temporary_id": "vehicle_{n}", "type": "car"}}' for n in range(60))
            text = self.head + cars + ', {"temporary_id": "vehi'
            return VLMReply(text, self.name, 1, prompt_tokens=1965, completion_tokens=max_tokens)
        return VLMReply(json.dumps(SCOPE_EXAMPLE), self.name, 1, 1965, 120)


def test_a_cut_off_answer_keeps_what_is_complete():
    vlm = CutOffVLM()
    described = describe(vlm)
    assert described.attempts == 1 and len(vlm.calls) == 1
    observation = described.observation
    assert observation.summary == "A busy car park."
    assert len(observation.entities) == 60
    assert "the answer was cut off after 1200 tokens" in observation.uncertainties[-1]


def test_a_cut_off_answer_without_a_summary_is_asked_again_shorter():
    vlm = CutOffVLM(head='{"entities": [')
    described = describe(vlm)
    assert described.attempts == 2
    assert "too long and was cut off after 1200 tokens" in vlm.calls[1]
    assert "at most 10 entities" in vlm.calls[1]


def test_repeated_actions_are_kept_once():
    action = {"type": "vehicle_arrive", "subject": "vehicle_1", "start_second": 3, "end_second": 5}
    observation = read({"summary": "s", "actions": [action] * 9})
    assert len(observation.actions) == 1
