"""Matching a structured rule on a camera's timeline (step 5.2): no database, no models."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from services.triggers.matching import (
    classes_seen,
    match_sequence,
    timeline,
    window_facts,
)

T0 = datetime(2026, 3, 7, 9, 0, tzinfo=UTC)


def window(offset, actions=(), entities=(), objects=(), seconds=12):
    """A window at `offset` s; actions as (type, start, end) seconds into it."""
    observation = {
        "actions": [{"type": t, "start_second": a, "end_second": b} for t, a, b in actions],
        "entities": [{"type": e} for e in entities],
        "visible_objects": list(objects),
    }
    start = T0 + timedelta(seconds=offset)
    return window_facts(
        uuid4(), uuid4(), start, start + timedelta(seconds=seconds), "d", 0.9, observation
    )


def chain_types(chain):
    return [a.type for a in chain] if chain is not None else None


def test_actions_get_absolute_times_and_unknown_ones_are_ignored():
    w = window(9, [("place_object", 2, 4), ("dance", 1, 2), ("leave", 20, 30)])
    assert [
        (a.type, (a.start - T0).total_seconds(), (a.end - T0).total_seconds()) for a in w.actions
    ] == [
        ("place_object", 11, 13),
        ("leave", 21, 21),  # clipped to the window's 12 s
    ]


def test_entity_classes_come_from_entities_and_visible_objects():
    w = window(0, entities=["man"], objects=["red backpack", "tree"])
    assert w.classes == {"person", "portable_object"}


def test_a_sequence_in_one_window():
    w = window(0, [("enter", 1, 2), ("place_object", 5, 7), ("leave", 9, 11)])
    chain = match_sequence(timeline([w], 3), ["place_object", "leave"], 30, w.id)
    assert chain_types(chain) == ["place_object", "leave"]


def test_a_sequence_across_windows():
    first = window(0, [("place_object", 8, 10)])
    second = window(9, [("walk", 3, 5)])
    third = window(18, [("leave", 4, 6)])
    actions = timeline([first, second, third], 3)
    chain = match_sequence(actions, ["place_object", "leave"], 30, third.id)
    assert chain_types(chain) == ["place_object", "leave"]
    assert chain[0].window_id == first.id and chain[1].window_id == third.id


def test_the_order_matters():
    w = window(0, [("leave", 1, 2), ("place_object", 5, 7)])
    assert match_sequence(timeline([w], 3), ["place_object", "leave"], 30, w.id) is None


def test_a_gap_longer_than_the_rules_is_no_match():
    first = window(0, [("place_object", 1, 2)])
    later = window(45, [("leave", 1, 2)])  # 44 s after the object was placed
    actions = timeline([first, later], 3)
    assert match_sequence(actions, ["place_object", "leave"], 30, later.id) is None
    assert chain_types(match_sequence(actions, ["place_object", "leave"], 60, later.id)) == [
        "place_object",
        "leave",
    ]


def test_the_last_step_must_be_in_the_window_being_evaluated():
    """So a match is reported once, by the window that completes it."""
    done = window(0, [("place_object", 1, 2), ("leave", 5, 6)])
    after = window(9, [("walk", 4, 5)])
    actions = timeline([done, after], 3)
    assert match_sequence(actions, ["place_object", "leave"], 30, after.id) is None
    assert match_sequence(actions, ["place_object", "leave"], 30, done.id) is not None


def test_an_action_seen_by_two_overlapping_windows_counts_once():
    first = window(0, [("place_object", 2, 4), ("leave", 10, 11)])  # leave at 10-11 s
    second = window(9, [("leave", 1.5, 2.5)])  # the same leave, seen again (10.5-11.5 s)
    actions = timeline([first, second], 3)
    assert [a.type for a in actions] == ["place_object", "leave"]
    # The second window doesn't report the match the first one already completed.
    assert match_sequence(actions, ["place_object", "leave"], 30, second.id) is None


def test_the_same_action_later_is_a_new_one():
    first = window(0, [("leave", 1, 2)])
    second = window(9, [("leave", 9, 10)])  # 17 s: a different moment
    assert len(timeline([first, second], 3)) == 2


def test_a_single_action_rule():
    w = window(0, [("walk", 1, 3), ("fall", 4, 5)])
    assert chain_types(match_sequence(timeline([w], 3), ["fall"], 30, w.id)) == ["fall"]


@pytest.mark.parametrize("sequence", [[], ["run"]])
def test_no_match_for_an_empty_rule_or_a_missing_action(sequence):
    w = window(0, [("walk", 1, 3)])
    assert match_sequence(timeline([w], 3), sequence, 30, w.id) is None


def test_classes_of_the_windows_around_the_match():
    early = window(0, entities=["dog"])
    during = window(30, entities=["woman"], objects=["suitcase"])
    seen = classes_seen([early, during], T0 + timedelta(seconds=32), T0 + timedelta(seconds=35))
    assert seen == {"person", "portable_object"}
