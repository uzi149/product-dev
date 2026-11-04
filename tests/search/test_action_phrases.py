"""Query words mapped to the action vocabulary (step 4.2). No database or models."""

import pytest

from shared.schemas.action_phrases import query_actions
from shared.schemas.observation import ACTION_VOCABULARY


@pytest.mark.parametrize(
    ("text", "actions"),
    [
        ("someone rings the doorbell", {"ring_doorbell"}),
        ("a man pressed the bell", {"ring_doorbell"}),
        ("a person walks to the door", {"walk"}),
        ("someone runs across the yard", {"run"}),
        ("a person enters the shop", {"enter"}),
        ("a person arrives", {"enter"}),
        ("someone leaves the building", {"leave"}),
        ("a pickup truck parks", {"vehicle_park"}),
        ("a car arrives", {"vehicle_arrive"}),
        ("a truck drives out of the parking lot", {"vehicle_depart"}),
        ("a van pulls in", {"vehicle_arrive"}),
        ("a blue pickup truck arrives and parks", {"vehicle_arrive", "vehicle_park"}),
        ("a person left a bag behind", {"leave", "leave_object_behind"}),
        ("a bag left unattended", {"leave", "leave_object_behind"}),
        (
            "someone placed an object and later left without it",
            {"place_object", "leave", "leave_object_behind"},
        ),
        ("someone puts a box down", {"place_object"}),
        ("a woman picks a parcel up", {"pick_up_object"}),
        ("two people carry boxes", {"carry_object"}),
        ("people unload things from a pickup truck", {"place_object", "pick_up_object"}),
        ("two people talk", {"interact_with_person"}),
        ("someone falls down", {"fall"}),
        ("a man opens the door", {"open_door"}),
        ("she shut the door", {"close_door"}),
        ("a person sits on the bench", {"sit"}),
        ("someone loiters near the gate", {"loiter"}),
    ],
)
def test_query_words_name_actions(text, actions):
    assert query_actions(text) == actions


@pytest.mark.parametrize(
    "text",
    [
        "a man in a red shirt",
        "a car in the parking lot",  # "parking lot" is not parking
        "a pickup truck",  # "pickup" is not picking up
        "a person near the door",
    ],
)
def test_no_action_named(text):
    assert query_actions(text) == set()


def test_every_action_has_words():
    named = set()
    for text in [
        "enter", "leave", "walk", "run", "stand", "sit", "loiter", "place", "pick it up",
        "carry", "left the bag", "a car arrives", "a car departs", "a car parks", "talk",
        "falls", "opens the door", "closes the door", "rings the bell",
    ]:  # fmt: skip
        named |= query_actions(text)
    assert named == set(ACTION_VOCABULARY)
