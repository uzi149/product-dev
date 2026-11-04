"""Ground-truth loading and the matching rules of the acceptance test (no models)."""

from datetime import UTC, datetime

import pytest

from tests.acceptance import ground_truth as gt
from tests.conftest import ROOT

YAML = """\
video: ../../virat/motion/clip.mp4
start_time: '2026-03-07T09:00:00Z'
entities: [Person, bag]
events:
  - {action: place_object, start_s: 9, end_s: 12, also: [put_down]}
  - {action: leave, start_s: 14, end_s: 18}
queries:
  - {text: "someone leaves a bag behind", start_s: 9, end_s: 18}
"""


def test_a_yaml_file_loads(tmp_path):
    path = tmp_path / "acceptance" / "virat" / "clip.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(YAML)
    truth = gt.load(path)
    assert truth.name == "clip"
    assert truth.video == (tmp_path / "virat" / "motion" / "clip.mp4").resolve()
    assert truth.start_time == datetime(2026, 3, 7, 9, 0, tzinfo=UTC)
    assert truth.entities == ["person", "bag"]
    assert truth.events[0] == gt.Event("place_object", 9, 12, ("put_down",))
    assert truth.events[0].words == {"place_object", "put_down"}
    assert truth.queries == [gt.Query("someone leaves a bag behind", 9, 18)]
    assert truth.queries[0].known_miss is None
    assert truth.actions_needed == gt.ACTIONS_NEEDED == 0.8


def test_a_known_miss_is_read(tmp_path):
    path = tmp_path / "clip.yaml"
    query = 'bag behind", start_s: 9, end_s: 18'
    path.write_text(YAML.replace(query, query + ', known_miss: "too far away"'))
    assert gt.load(path).queries[0].known_miss == "too far away"


def test_the_committed_ground_truth_loads():
    truths = gt.load_folder(ROOT / "test-data" / "acceptance" / "virat")
    assert len(truths) >= 3
    for truth in truths:
        assert truth.events and truth.queries and truth.entities
        assert truth.actions_needed == 0.7  # ADR 0023 amendment
        for item in [*truth.events, *truth.queries]:
            assert 0 <= item.start_s < item.end_s


EVENT = gt.Event("place_object", 9, 12, ("put_down",))


@pytest.mark.parametrize(
    ("found", "ok"),
    [
        ([gt.FoundAction("place_object", 10, 11)], True),
        ([gt.FoundAction("put_down", 10, 11)], True),  # an `also` word
        ([gt.FoundAction("place_object", 13, 15)], True),  # within 2 s after
        ([gt.FoundAction("place_object", 5, 7.5)], True),  # within 2 s before
        ([gt.FoundAction("place_object", 15, 16)], False),  # 3 s after
        ([gt.FoundAction("walk", 9, 12)], False),  # another action
        ([], False),
    ],
)
def test_an_action_is_found_when_its_type_and_time_match(found, ok):
    assert gt.action_found(EVENT, found) is ok


QUERY = gt.Query("someone leaves a bag behind", 9, 18)


@pytest.mark.parametrize(
    ("hits", "ok"),
    [
        ([(0, 12), (30, 42), (60, 72)], True),  # the first hit overlaps partly
        ([(30, 42), (60, 72), (9, 21)], True),  # third
        ([(30, 42), (60, 72), (80, 92), (9, 21)], False),  # fourth is too late
        ([(18, 30)], False),  # touching the end is not overlapping
        ([], False),
    ],
)
def test_a_query_passes_with_an_overlapping_hit_in_the_top_3(hits, ok):
    assert gt.query_passes(QUERY, hits) is ok


@pytest.mark.parametrize(
    ("expected", "found", "same"),
    [
        ("person", "man", True),
        ("person", "people", True),
        ("person", "Pedestrians", True),
        ("car", "pickup truck", True),
        ("car", "cars", True),
        ("bag", "backpack", True),
        ("person", "car", False),
        ("bus", "bus", True),
    ],
)
def test_the_synonym_table(expected, found, same):
    assert gt.same_entity(expected, found) is same


def test_missing_entities_are_listed():
    assert gt.missing_entities(["person", "bag", "car"], ["man", "vehicle"]) == ["bag"]
