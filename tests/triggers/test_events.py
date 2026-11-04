"""Holding an event until its post-roll footage is stored, then its evidence and alert (step 5.2,
ADR 0033)."""

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.triggers.events import (
    Match,
    Verification,
    finish_held_events,
    process_finish,
    record_match,
)
from services.triggers.matching import TimedAction, window_facts
from shared.config import Settings
from shared.database.models import Event, EventEvidence, EvidenceMarks
from tests.triggers.conftest import SETTINGS, START, online

NOT_REQUIRED = Verification("not_required")


def at(seconds):
    return START + timedelta(seconds=seconds)


def match(window, start_s, end_s):
    """A structured match of two actions at start_s and end_s (seconds of the day)."""
    facts = window_facts(
        window.id,
        window.segment_id,
        window.start_time,
        window.end_time,
        "a person leaves a bag",
        0.9,
        window.structured_observation_json,
    )
    actions = [
        TimedAction("place_object", at(start_s), at(start_s + 1), window.id),
        TimedAction("leave", at(end_s - 1), at(end_s), window.id),
    ]
    return Match("structured", [facts], actions)


@pytest.fixture
def held(session, live_camera, add_segment, add_window, add_trigger):
    """A held event on a live camera: actions at 4-11 s; its segment ends at 12 s, so the 20 s
    post-roll (until 31 s) isn't stored yet."""
    trigger = add_trigger([live_camera])
    segment = add_segment(live_camera, 0, 12)
    window = add_window(live_camera, segment, 0)
    event, new = record_match(
        session, trigger, live_camera.id, match(window, 4, 11), NOT_REQUIRED, 0.9, at(15)
    )
    session.commit()
    assert new and event.alerted_at is None
    return event, trigger, window


def finish(session, event, finish_tools, settings=SETTINGS):
    return process_finish(session, event.id, finish_tools(settings))


def evidence(session, event):
    session.expire_all()
    return session.scalars(
        select(EventEvidence)
        .where(EventEvidence.event_id == event.id)
        .order_by(EventEvidence.sequence_order)
    ).all()


def test_held_until_a_segment_reaches_the_end_of_the_post_roll(
    session, held, live_camera, add_segment, finish_tools, recorder, clock
):
    event, _, _ = held
    clock.now = at(25)
    assert finish(session, event, finish_tools) is False
    assert evidence(session, event) == [] and recorder.notices == []
    assert recorder.scheduled == [(event.id, SETTINGS.trigger_finish_poll_seconds)]

    add_segment(live_camera, 12, 30)  # 12-42 s: the post-roll is stored
    clock.now = at(45)
    assert finish(session, event, finish_tools) is True
    session.refresh(event)
    assert event.alerted_at == at(45)
    assert event.details_json["finished_because"] == "footage stored"
    assert event.details_json["delay_seconds"] == 34.0  # alert at 45 s, last action ended at 11 s
    parts = evidence(session, event)
    assert [(p.start_offset_ms, p.end_offset_ms) for p in parts] == [(0, 12_000), (0, 19_000)]
    assert [p.evidence_reason for p in parts] == [
        "the matched activity",
        "after the event (post-roll)",
    ]
    assert len(recorder.notices) == 1


def test_a_camera_that_stopped_recording_finishes_after_a_short_wait(
    session, held, live_camera, finish_tools, recorder, clock
):
    event, _, _ = held
    recorder.statuses[live_camera.id] = online(activity=False)
    clock.now = at(31 + 5)  # post-roll over, but within two fragments (10 s)
    assert finish(session, event, finish_tools) is False
    clock.now = at(31 + 10)
    assert finish(session, event, finish_tools) is True
    session.refresh(event)
    assert event.details_json["finished_because"] == "camera not recording"
    assert len(evidence(session, event)) == 1  # only what exists; the rest is a gap


def test_a_recording_camera_is_waited_for_until_the_time_limit(
    session, held, live_camera, finish_tools, recorder, clock
):
    event, _, _ = held
    recorder.statuses[live_camera.id] = online(activity=True)
    limit = SETTINGS.max_segment_seconds + SETTINGS.trigger_finish_margin_seconds
    clock.now = at(31 + limit - 1)
    assert finish(session, event, finish_tools) is False
    clock.now = at(31 + limit)
    assert finish(session, event, finish_tools) is True
    session.refresh(event)
    assert event.details_json["finished_because"] == "time limit"


def test_evidence_is_clipped_to_pre_and_post_roll_across_segments(
    session, cameras, add_segment, add_window, add_trigger, finish_tools, clock
):
    one, _ = cameras
    trigger = add_trigger([one], pre_roll_seconds=10, post_roll_seconds=20)
    early = add_segment(one, 0, 40)  # 0-40 s
    during = add_segment(one, 40, 30)  # 40-70 s
    after = add_segment(one, 75, 30)  # 75-105 s, after a 5 s gap
    add_segment(one, 200, 30)  # far after: not evidence
    window = add_window(one, during, 40)
    event, _ = record_match(
        session, trigger, one.id, match(window, 45, 70), NOT_REQUIRED, 0.9, at(100)
    )
    session.commit()
    clock.now = at(300)
    assert finish(session, event, finish_tools) is True
    parts = evidence(session, event)
    # Actions 45-70 s: evidence from 35 s (pre-roll) to 90 s (post-roll).
    assert [(p.segment_id, p.start_offset_ms, p.end_offset_ms) for p in parts] == [
        (early.id, 35_000, 40_000),
        (during.id, 0, 30_000),
        (after.id, 0, 15_000),
    ]
    assert [p.sequence_order for p in parts] == [0, 1, 2]
    assert [p.evidence_reason for p in parts] == [
        "before the event (pre-roll)",
        "the matched activity",
        "after the event (post-roll)",
    ]


def test_a_held_event_extended_before_its_alert_alerts_once(
    session, held, live_camera, add_segment, add_window, finish_tools, recorder, clock
):
    event, trigger, _ = held
    later = add_window(live_camera, add_segment(live_camera, 12, 30), 20)
    extended, new = record_match(
        session, trigger, live_camera.id, match(later, 25, 30), NOT_REQUIRED, 0.95, at(40)
    )
    session.commit()
    assert not new and extended.id == event.id and extended.end_time == at(30)
    add_segment(live_camera, 42, 30)  # reaches 50 s (30 s + 20 s post-roll)
    clock.now = at(60)
    assert finish(session, event, finish_tools) is True
    assert finish(session, event, finish_tools) is True  # a second check sends nothing more
    assert len(recorder.notices) == 1
    session.refresh(event)
    assert event.confidence == 0.95 and len(event.details_json["matches"]) == 2


def test_an_event_extended_after_its_alert_gets_new_evidence_without_a_new_alert(
    session, cameras, add_segment, add_window, add_trigger, finish_tools, recorder, clock
):
    one, _ = cameras
    trigger = add_trigger([one], post_roll_seconds=0, pre_roll_seconds=0)
    first = add_segment(one, 0, 30)
    second = add_segment(one, 30, 30)
    window = add_window(one, first, 0)
    event, _ = record_match(
        session, trigger, one.id, match(window, 4, 11), NOT_REQUIRED, 0.9, at(20)
    )
    session.commit()
    clock.now = at(100)
    finish(session, event, finish_tools)
    assert len(evidence(session, event)) == 1
    record_match(session, trigger, one.id, match(window, 35, 40), NOT_REQUIRED, 0.9, at(100))
    session.commit()
    finish(session, event, finish_tools)
    assert [p.segment_id for p in evidence(session, event)] == [first.id, second.id]
    assert len(recorder.notices) == 1


def marks_of(session, event):
    session.expire_all()
    return session.scalars(select(EvidenceMarks).where(EvidenceMarks.event_id == event.id)).one()


def test_finishing_asks_for_marks_after_the_alert_and_again_on_extension(
    session, cameras, add_segment, add_window, add_trigger, finish_tools, recorder, clock
):
    """Step 5.7: the evidence is marked on the query worker; the alert never waits for it."""
    one, _ = cameras
    trigger = add_trigger([one], post_roll_seconds=0, pre_roll_seconds=0)
    first = add_segment(one, 0, 30)
    add_segment(one, 30, 30)
    window = add_window(one, first, 0)
    event, _ = record_match(
        session, trigger, one.id, match(window, 4, 11), NOT_REQUIRED, 0.9, at(20)
    )
    session.commit()
    clock.now = at(100)
    finish(session, event, finish_tools)
    row = marks_of(session, event)
    assert row.status == "pending" and recorder.marks == [row.id]
    assert len(recorder.notices) == 1

    row.status, row.tracks_json = "done", []
    session.commit()
    record_match(session, trigger, one.id, match(window, 35, 40), NOT_REQUIRED, 0.9, at(100))
    session.commit()
    finish(session, event, finish_tools)
    again = marks_of(session, event)
    assert again.id == row.id and again.status == "pending"
    assert recorder.marks == [row.id, row.id]


def test_with_the_local_model_marks_are_skipped(
    session, cameras, add_segment, add_window, add_trigger, finish_tools, recorder, clock
):
    one, _ = cameras
    trigger = add_trigger([one], post_roll_seconds=0, pre_roll_seconds=0)
    window = add_window(one, add_segment(one, 0, 30), 0)
    event, _ = record_match(
        session, trigger, one.id, match(window, 4, 11), NOT_REQUIRED, 0.9, at(20)
    )
    session.commit()
    clock.now = at(100)
    finish(session, event, finish_tools, Settings(_env_file=None, vlm_backend="local"))
    row = marks_of(session, event)
    assert row.status == "skipped" and "local model" in row.reason
    assert recorder.marks == [] and len(recorder.notices) == 1


def test_a_deleted_event_is_finished_quietly(session, held, finish_tools, recorder):
    event, _, _ = held
    session.delete(session.get(Event, event.id))
    session.commit()
    assert finish(session, event, finish_tools) is True
    assert recorder.notices == []


def test_held_events_whose_check_was_lost_are_finished_on_worker_start(
    engine, held, live_camera, add_segment, finish_tools, recorder, clock
):
    event, _, _ = held
    add_segment(live_camera, 12, 30)
    clock.now = at(300)
    assert finish_held_events(lambda: Session(engine), finish_tools(), timedelta(seconds=10)) == 1
    assert [n["event_id"] for n in recorder.notices] == [str(event.id)]
    assert finish_held_events(lambda: Session(engine), finish_tools(), timedelta(seconds=10)) == 0
