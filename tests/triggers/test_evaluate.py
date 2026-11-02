"""A window against its camera's triggers (step 5.2, ADR 0033), on a real database with fake
models. File cameras finish their events at once (the whole file is stored)."""

import json
import logging
from datetime import timedelta

import pytest
from sqlalchemy import select

from services.triggers import evaluate as evaluate_module
from services.triggers.evaluate import enabled_triggers, evaluate_window
from shared.database.models import Event, EventEvidence
from shared.model_adapters import VLMUnavailable
from tests.fakes import FakeVLM
from tests.search.conftest import Q_PICTURE, Q_TEXT, TEXT, near
from tests.triggers.conftest import SETTINGS, rule

YES = json.dumps({"match": True, "confidence": 0.93, "reason": "A person leaves a bag."})
NO = json.dumps({"match": False, "confidence": 0.8, "reason": "The person takes the bag."})
PLACE_AND_LEAVE = [("enter", 1, 2), ("place_object", 4, 6), ("leave", 9, 11)]
PEOPLE_AND_BAG = {"entities": ["man", "bag"]}


@pytest.fixture
def scene(cameras, add_segment, add_window):
    """CAM-01's left-bag window, at 9:00:00-9:00:12 of a 30 s segment."""
    one, _ = cameras
    segment = add_segment(one)
    window = add_window(one, segment, 0, PLACE_AND_LEAVE, **PEOPLE_AND_BAG)
    return one, segment, window


def events(session):
    session.expire_all()
    return session.scalars(select(Event).order_by(Event.start_time)).all()


def test_a_match_makes_an_event_with_evidence_and_a_notice(
    session, scene, add_trigger, tools, recorder
):
    camera, segment, window = scene
    trigger = add_trigger([camera])
    made = evaluate_window(session, window.id, tools())

    (event,) = events(session)
    assert made == [event.id]
    assert event.trigger_id == trigger.id and event.camera_id == camera.id
    assert (event.start_time - segment.start_time).total_seconds() == 4  # place_object
    assert (event.end_time - segment.start_time).total_seconds() == 11  # leave
    assert event.confidence == 0.9 and event.status == "new"
    details = event.details_json
    assert details["mode"] == "structured" and details["window_ids"] == [str(window.id)]
    assert [a["type"] for a in details["matches"][0]["actions"]] == ["place_object", "leave"]
    assert details["verification"]["status"] == "not_required"
    # An uploaded file is stored whole: the event finishes at once.
    assert event.alerted_at is not None and details["finished_because"] == "uploaded file"
    (evidence,) = session.scalars(select(EventEvidence)).all()
    assert evidence.segment_id == segment.id
    assert (evidence.start_offset_ms, evidence.end_offset_ms) == (0, 30_000)  # 11 s + 20 s post
    (notice,) = recorder.notices
    assert notice["event_id"] == str(event.id) and notice["camera_name"] == "CAM-01"
    assert notice["trigger_name"] == "Object left behind"


def test_another_cameras_trigger_is_never_loaded(
    session, cameras, add_segment, add_window, add_trigger, tools, caplog
):
    one, two = cameras
    add_trigger([one])
    window = add_window(two, add_segment(two), 0, PLACE_AND_LEAVE, **PEOPLE_AND_BAG)
    assert enabled_triggers(session, two.id) == []
    with caplog.at_level(logging.INFO, logger="services.triggers.evaluate"):
        assert evaluate_window(session, window.id, tools()) == []
    assert events(session) == []
    assert "checking 0 trigger(s)" in caplog.text


def test_disabled_and_uncompiled_triggers_are_not_loaded(session, scene, add_trigger, tools):
    camera, _, window = scene
    add_trigger([camera], enabled=False)
    add_trigger([camera], name="Pending", compiled=False)
    assert enabled_triggers(session, camera.id) == []
    assert evaluate_window(session, window.id, tools()) == []


def test_missing_entities_or_actions_are_no_match(
    session, cameras, add_segment, add_window, add_trigger, tools
):
    one, _ = cameras
    add_trigger([one])
    segment = add_segment(one)
    no_bag = add_window(one, segment, 0, PLACE_AND_LEAVE, entities=["man"])
    no_leave = add_window(one, segment, 40, [("place_object", 1, 2)], **PEOPLE_AND_BAG)
    assert evaluate_window(session, no_bag.id, tools()) == []
    assert evaluate_window(session, no_leave.id, tools()) == []


def test_an_alternative_sequence_matches_too(
    session, cameras, add_segment, add_window, add_trigger, tools
):
    """The analysis may say leave_object_behind where the rule names place_object, leave."""
    one, _ = cameras
    add_trigger([one])
    window = add_window(
        one,
        add_segment(one),
        0,
        [("walk", 1, 3), ("leave_object_behind", 6, 8), ("leave", 9, 11)],
        **PEOPLE_AND_BAG,
    )
    (made,) = evaluate_window(session, window.id, tools())
    (event,) = events(session)
    assert made == event.id
    assert [a["type"] for a in event.details_json["matches"][0]["actions"]] == [
        "leave_object_behind"
    ]
    add_trigger([one], name="Strict", rule=rule(alternative_action_sequences=[]))
    assert evaluate_window(session, window.id, tools()) == []  # only the first trigger matches


def test_a_sequence_across_windows_is_reported_by_the_window_that_completes_it(
    session, cameras, add_segment, add_window, add_trigger, tools
):
    one, _ = cameras
    add_trigger([one])
    segment = add_segment(one)
    first = add_window(one, segment, 0, [("place_object", 8, 10)], **PEOPLE_AND_BAG)
    second = add_window(one, segment, 9, [("leave", 6, 8)], entities=["man"])
    assert evaluate_window(session, first.id, tools()) == []
    (made,) = evaluate_window(session, second.id, tools())
    (event,) = events(session)
    assert made == event.id
    assert event.details_json["window_ids"] == [str(first.id), str(second.id)]


@pytest.fixture
def verifying(scene, add_trigger, pictures):
    camera, _, window = scene
    add_trigger([camera], rule=rule(requires_vlm_verification=True))
    pictures()
    return window


def test_a_verified_match_takes_the_verifications_confidence(session, verifying, tools):
    vlm = FakeVLM([YES])
    evaluate_window(session, verifying.id, tools(vlm))
    (event,) = events(session)
    assert event.confidence == 0.93 and event.summary == "A person leaves a bag."
    assert event.details_json["verification"]["status"] == "verified"
    (call,) = vlm.calls
    assert call["frames"] == 3 and "someone places an object and leaves it behind" in call["prompt"]


def test_a_match_the_vlm_rejects_makes_no_event(session, verifying, tools, recorder):
    evaluate_window(session, verifying.id, tools(FakeVLM([NO])))
    assert events(session) == [] and recorder.notices == []


def test_a_failed_verification_still_alerts_marked(session, verifying, tools, recorder):
    evaluate_window(session, verifying.id, tools(FakeVLM([VLMUnavailable("timeout")])))
    (event,) = events(session)
    verification = event.details_json["verification"]
    assert verification["status"] == "unverifiable" and "timeout" in verification["reason"]
    assert event.confidence == 0.9  # the window's
    assert len(recorder.notices) == 1 and recorder.notices[0]["verification"] == "unverifiable"


def test_no_verification_with_the_local_model(session, verifying, tools):
    vlm = FakeVLM([YES])
    local = SETTINGS.model_copy(update={"vlm_backend": "local"})
    evaluate_window(session, verifying.id, tools(vlm, local))
    (event,) = events(session)
    assert event.details_json["verification"]["status"] == "not_verified_local"
    assert vlm.calls == []


def test_confidence_below_the_minimum_makes_no_event(
    session, cameras, add_segment, add_window, add_trigger, tools
):
    one, _ = cameras
    add_trigger([one])
    window = add_window(one, add_segment(one), 0, PLACE_AND_LEAVE, confidence=0.6, **PEOPLE_AND_BAG)
    assert evaluate_window(session, window.id, tools()) == []
    add_trigger([one], name="Lenient", minimum_confidence=0.5)
    assert len(evaluate_window(session, window.id, tools())) == 1


def test_within_the_cooldown_the_event_is_extended(
    session, cameras, add_segment, add_window, add_trigger, tools, recorder
):
    one, _ = cameras
    add_trigger([one], cooldown_seconds=120)
    segment = add_segment(one, 0, 300)
    first = add_window(one, segment, 0, PLACE_AND_LEAVE, **PEOPLE_AND_BAG)
    again = add_window(one, segment, 60, PLACE_AND_LEAVE, **PEOPLE_AND_BAG)
    later = add_window(one, segment, 250, PLACE_AND_LEAVE, **PEOPLE_AND_BAG)
    evaluate_window(session, first.id, tools())
    evaluate_window(session, again.id, tools())
    (event,) = events(session)
    assert (event.end_time - segment.start_time).total_seconds() == 71
    assert len(event.details_json["matches"]) == 2
    assert len(recorder.notices) == 1  # no second alert for an extension
    evaluate_window(session, later.id, tools())  # 179 s after the extended end: a new event
    assert len(events(session)) == 2 and len(recorder.notices) == 2


def test_a_reanalysed_window_makes_no_second_event(session, scene, add_trigger, tools):
    camera, _, window = scene
    add_trigger([camera], cooldown_seconds=0)
    evaluate_window(session, window.id, tools())
    assert evaluate_window(session, window.id, tools()) == []
    assert len(events(session)) == 1


SEMANTIC = rule(
    mode="semantic",
    required_entities=["person"],
    required_action_sequence=[],
    search_text="a crowd gathers near the entrance",
    source_rule="Alert me if a crowd gathers near the entrance",
)


def test_a_semantic_rule_matches_by_score(
    session, cameras, add_segment, add_window, add_trigger, tools
):
    one, _ = cameras
    add_trigger([one], rule=SEMANTIC, name="Crowd")
    segment = add_segment(one, 0, 60)
    close = add_window(
        one,
        segment,
        0,
        description="a crowd gathers near the entrance",
        text_vector=near(Q_TEXT, 0.95, "close"),
        picture_vectors=[near(Q_PICTURE, 0.95, "cp")] * 3,
    )
    far = add_window(
        one,
        segment,
        30,
        description="an empty corridor",
        text_vector=near(Q_TEXT, 0.0, "far"),
        picture_vectors=[near(Q_PICTURE, 0.0, "fp")] * 3,
    )
    evaluate_module._rule_vectors.clear()
    asked = len(TEXT.queries)
    assert len(evaluate_window(session, close.id, tools())) == 1
    assert evaluate_window(session, far.id, tools()) == []
    (event,) = events(session)
    match = event.details_json["matches"][0]
    assert event.details_json["mode"] == "semantic" and match["score"] >= 0.6
    assert len(TEXT.queries) == asked + 1  # the rule's vectors are computed once per worker


def test_an_event_is_only_finished_once_its_footage_is_stored(
    session, live_camera, add_segment, add_window, add_trigger, tools, recorder, clock
):
    """A live camera: held while the post-roll isn't stored."""
    add_trigger([live_camera])
    segment = add_segment(live_camera, 0, 12)
    window = add_window(live_camera, segment, 0, PLACE_AND_LEAVE, **PEOPLE_AND_BAG)
    clock.now = segment.end_time + timedelta(seconds=5)
    evaluate_window(session, window.id, tools())
    (event,) = events(session)
    assert event.alerted_at is None and recorder.notices == []
    assert session.scalars(select(EventEvidence)).all() == []
    assert recorder.scheduled == [(event.id, SETTINGS.trigger_finish_poll_seconds)]
