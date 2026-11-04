"""A trigger tested on stored footage (step 5.4, ADR 0035), with fake models on a real database.
Never creates events."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.triggers import testing
from services.triggers.testing import TestTools, process_test
from shared.database.models import Event, TriggerTest
from tests.fakes import FakeVLM
from tests.search.conftest import IMAGE, Q_PICTURE, Q_TEXT, TEXT, near
from tests.triggers.conftest import SETTINGS, START, rule

YES = json.dumps({"match": True, "confidence": 0.92, "reason": "A bag is left on the floor."})
NO = json.dumps({"match": False, "confidence": 0.8, "reason": "The person takes the bag along."})
PEOPLE_AND_BAG = {"entities": ["man", "bag"]}


@pytest.fixture
def close(add_window):
    """A window close to the rule's search text (a search candidate) with these actions."""

    def add(camera, segment, offset_s, actions=(), seed="w", **extra):
        return add_window(
            camera,
            segment,
            offset_s,
            actions,
            text_vector=near(Q_TEXT, 0.95, seed),
            picture_vectors=[near(Q_PICTURE, 0.9, seed + "p")] * 3,
            **extra,
        )

    return add


@pytest.fixture
def run_test(engine, storage):
    def run(trigger, camera_ids, start, end, vlm=None, settings=SETTINGS) -> TriggerTest:
        with Session(engine) as session:
            test = TriggerTest(
                trigger_id=trigger.id,
                source="range",
                camera_ids=camera_ids,
                start_time=start,
                end_time=end,
                rule_json={
                    "rule": trigger.compiled_rule_json,
                    "settings": {
                        "minimum_confidence": trigger.minimum_confidence,
                        "cooldown_seconds": trigger.cooldown_seconds,
                        "pre_roll_seconds": trigger.pre_roll_seconds,
                        "post_roll_seconds": trigger.post_roll_seconds,
                    },
                },
            )
            session.add(test)
            session.commit()
            test_id = test.id
        tools = TestTools(
            session_factory=lambda: Session(engine),
            storage=storage,
            settings=settings,
            vlm=lambda: vlm or FakeVLM([YES]),
            text_embedder=TEXT,
            image_embedder=IMAGE,
        )
        process_test(test_id, tools)
        with Session(engine) as session:
            test = session.get(TriggerTest, test_id)
            session.expunge(test)
            return test

    return run


DAY = (START - timedelta(hours=1), START + timedelta(hours=2))


def verdicts(test):
    return [m["verdict"] for m in test.results_json["matches"]]


def test_a_structured_rule_finds_the_sequence_and_not_a_partial_one(
    session, cameras, add_segment, close, add_trigger, run_test
):
    one, _ = cameras
    trigger = add_trigger([one])
    segment = add_segment(one, 0, 600)
    close(one, segment, 0, [("place_object", 4, 6)], **PEOPLE_AND_BAG)
    close(one, segment, 9, [("leave", 5, 7)], seed="x", entities=["man"])
    close(one, segment, 300, [("place_object", 2, 3)], seed="y", **PEOPLE_AND_BAG)  # no leave
    test = run_test(trigger, [one.id], *DAY)
    assert test.status == "done" and test.stage == "done" and test.error is None
    (match,) = test.results_json["matches"]
    assert match["verdict"] == "would_report" and match["camera_name"] == "CAM-01"
    assert [a["type"] for a in match["actions"]] == ["place_object", "leave"]
    assert match["verification"]["status"] == "not_required" and match["keyframe_id"]
    # The playback covers the 10 s pre-roll and 20 s post-roll around 4-16 s.
    (part,) = match["manifest"]["parts"]
    assert (part["source_start_ms"], part["source_end_ms"]) == (0, 36_000)
    assert test.results_json["counts"] == {"would_report": 1, "rejected": 0, "below_confidence": 0}
    assert test.results_json["footage_seconds"] == 600
    assert session.scalars(select(Event)).all() == []  # a test never makes events


def test_the_alternative_sequence_counts_too(cameras, add_segment, close, add_trigger, run_test):
    one, _ = cameras
    trigger = add_trigger([one])
    close(one, add_segment(one), 0, [("leave_object_behind", 5, 7)], **PEOPLE_AND_BAG)
    assert verdicts(run_test(trigger, [one.id], *DAY)) == ["would_report"]


def test_matches_closer_than_the_cooldown_are_one(
    cameras, add_segment, close, add_trigger, run_test
):
    one, _ = cameras
    segment = add_segment(one, 0, 600)
    close(one, segment, 0, [("place_object", 1, 2), ("leave", 5, 6)], **PEOPLE_AND_BAG)
    close(one, segment, 60, [("place_object", 1, 2), ("leave", 5, 6)], seed="b", **PEOPLE_AND_BAG)
    grouped = run_test(add_trigger([one], cooldown_seconds=120), [one.id], *DAY)
    assert len(grouped.results_json["matches"]) == 1
    apart = run_test(add_trigger([one], name="No cooldown", cooldown_seconds=0), [one.id], *DAY)
    assert len(apart.results_json["matches"]) == 2


def test_verification_shows_what_would_report_and_what_the_vlm_rejects(
    cameras, add_segment, close, add_trigger, run_test, pictures
):
    one, _ = cameras
    trigger = add_trigger([one], rule=rule(requires_vlm_verification=True), cooldown_seconds=0)
    segment = add_segment(one, 0, 600)
    close(one, segment, 0, [("place_object", 1, 2), ("leave", 5, 6)], **PEOPLE_AND_BAG)
    close(one, segment, 300, [("place_object", 1, 2), ("leave", 5, 6)], seed="b", **PEOPLE_AND_BAG)
    pictures()
    vlm = FakeVLM([NO, YES])  # the earlier match is checked first
    test = run_test(trigger, [one.id], *DAY, vlm=vlm)
    assert verdicts(test) == ["would_report", "rejected"]  # would report first
    reported, rejected = test.results_json["matches"]
    assert reported["confidence"] == 0.92 and reported["verification"]["status"] == "verified"
    assert rejected["verification"]["reason"] == "The person takes the bag along."
    assert test.results_json["counts"] == {"would_report": 1, "rejected": 1, "below_confidence": 0}
    assert len(vlm.calls) == 2


def test_only_the_first_matches_are_verified(
    cameras, add_segment, close, add_trigger, run_test, pictures
):
    one, _ = cameras
    trigger = add_trigger([one], rule=rule(requires_vlm_verification=True), cooldown_seconds=0)
    segment = add_segment(one, 0, 600)
    close(one, segment, 0, [("leave_object_behind", 1, 2)], **PEOPLE_AND_BAG)
    close(one, segment, 300, [("leave_object_behind", 1, 2)], seed="b", **PEOPLE_AND_BAG)
    pictures()
    one_check = SETTINGS.model_copy(update={"trigger_test_verify_max": 1})
    vlm = FakeVLM([YES])
    test = run_test(trigger, [one.id], *DAY, vlm=vlm, settings=one_check)
    statuses = [m["verification"]["status"] for m in test.results_json["matches"]]
    assert statuses == ["verified", "not_checked"] and len(vlm.calls) == 1


def test_below_the_confidence_is_listed_greyed(cameras, add_segment, close, add_trigger, run_test):
    one, _ = cameras
    trigger = add_trigger([one])
    close(one, add_segment(one), 0, [("leave_object_behind", 1, 2)], confidence=0.5,
          **PEOPLE_AND_BAG)  # fmt: skip
    test = run_test(trigger, [one.id], *DAY)
    assert verdicts(test) == ["below_confidence"]
    assert test.results_json["matches"][0]["confidence"] == 0.5


def test_with_the_local_model_nothing_is_verified(
    cameras, add_segment, close, add_trigger, run_test
):
    one, _ = cameras
    trigger = add_trigger([one], rule=rule(requires_vlm_verification=True))
    close(one, add_segment(one), 0, [("leave_object_behind", 1, 2)], **PEOPLE_AND_BAG)
    vlm = FakeVLM([YES])
    local = SETTINGS.model_copy(update={"vlm_backend": "local"})
    test = run_test(trigger, [one.id], *DAY, vlm=vlm, settings=local)
    (match,) = test.results_json["matches"]
    assert match["verification"]["status"] == "not_verified_local" and vlm.calls == []


def test_only_the_tests_cameras_and_range_are_searched(
    cameras, add_segment, close, add_trigger, run_test
):
    one, two = cameras
    trigger = add_trigger([one, two])
    close(two, add_segment(two), 0, [("leave_object_behind", 1, 2)], **PEOPLE_AND_BAG)
    late = add_segment(one, 3 * 3600, 30)  # after DAY
    close(one, late, 3 * 3600, [("leave_object_behind", 1, 2)], seed="l", **PEOPLE_AND_BAG)
    assert verdicts(run_test(trigger, [one.id], *DAY)) == []  # CAM-02 isn't in the test
    end = START + timedelta(hours=4)
    assert verdicts(run_test(trigger, [one.id], START, end)) == ["would_report"]


SEMANTIC = rule(
    mode="semantic",
    required_entities=["person"],
    required_action_sequence=[],
    alternative_action_sequences=[],
    search_text="a crowd gathers near the entrance",
    source_rule="Alert me if a crowd gathers near the entrance",
)


def test_a_semantic_rule_keeps_the_candidates_above_the_threshold(
    cameras, add_segment, add_window, add_trigger, run_test
):
    one, _ = cameras
    trigger = add_trigger([one], rule=SEMANTIC, cooldown_seconds=0)
    segment = add_segment(one, 0, 600)
    add_window(one, segment, 0, description="a crowd gathers near the entrance",
               text_vector=near(Q_TEXT, 0.95, "c"), picture_vectors=[near(Q_PICTURE, 0.95, "cp")] * 3)  # fmt: skip
    add_window(one, segment, 300, description="a person walks past the entrance",
               text_vector=near(Q_TEXT, 0.0, "m"), picture_vectors=[near(Q_PICTURE, 0.0, "mp")] * 3)  # fmt: skip
    test = run_test(trigger, [one.id], *DAY)
    (match,) = test.results_json["matches"]
    assert match["mode"] == "semantic" and match["score"] >= SETTINGS.trigger_semantic_min_score
    assert test.results_json["candidates"] >= 1


def test_a_failure_is_recorded(cameras, add_trigger, run_test, monkeypatch):
    one, _ = cameras

    def broken(*args, **kwargs):
        raise RuntimeError("search failed (test)")

    monkeypatch.setattr(testing, "search", broken)
    test = run_test(add_trigger([one]), [one.id], *DAY)
    assert test.status == "failed" and "search failed" in test.error and test.finished_at
