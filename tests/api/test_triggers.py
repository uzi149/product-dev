"""The trigger API (step 5.1): create, list, edit, compile, enable, disable, delete."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.api.deps import get_trigger_queue
from shared.database.models import (
    Camera,
    Event,
    EventEvidence,
    Trigger,
    TriggerCamera,
    VideoSegment,
)

T0 = datetime(2026, 3, 7, 9, 0, tzinfo=UTC)
RULE = "Report when someone places an object and leaves it behind"
COMPILED = {
    "mode": "structured",
    "required_entities": ["person", "portable_object"],
    "required_action_sequence": ["place_object", "leave"],
    "maximum_sequence_gap_seconds": 30,
    "requires_vlm_verification": True,
    "search_text": "someone places an object and leaves it behind",
    "source_rule": RULE,
    "prompt_version": 1,
    "model": "fake-vlm",
}


@pytest.fixture
def compiles(client):
    queued = []
    client.app.dependency_overrides[get_trigger_queue] = lambda: queued.append
    return queued


@pytest.fixture
def cameras(engine):
    with Session(engine) as session:
        one, two = (
            Camera(display_name="CAM-01", source_type="file"),
            Camera(display_name="CAM-02", source_type="file"),
        )
        session.add_all([one, two])
        session.commit()
        return one.id, two.id


def create(client, camera_ids, **extra):
    body = {
        "name": "Object left behind",
        "natural_language_rule": RULE,
        "camera_ids": [str(c) for c in camera_ids],
        **extra,
    }
    return client.post("/triggers", json=body)


def compiled(engine, trigger_id, status="done"):
    """What the query worker's compile task would store."""
    with Session(engine) as session:
        trigger = session.get(Trigger, trigger_id)
        trigger.compile_status = status
        trigger.compiled_rule_json = COMPILED if status == "done" else None
        trigger.compile_error = "VLMUnavailable: down" if status == "failed" else None
        session.commit()


def test_a_trigger_is_stored_disabled_and_its_rule_queued(client, compiles, cameras):
    response = create(client, cameras[:1])
    assert response.status_code == 201
    trigger = response.json()
    assert trigger["enabled"] is False and trigger["compile_status"] == "pending"
    assert trigger["compiled"] is None
    assert [c["display_name"] for c in trigger["cameras"]] == ["CAM-01"]
    assert (trigger["minimum_confidence"], trigger["cooldown_seconds"]) == (0.8, 120)
    assert (trigger["pre_roll_seconds"], trigger["post_roll_seconds"]) == (10, 20)
    assert trigger["events"] == {"new": 0, "total": 0}
    assert [str(i) for i in compiles] == [trigger["id"]]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"name": "  "}, "must not be blank"),
        ({"natural_language_rule": ""}, "at least 1 character"),
        ({"camera_ids": []}, "at least 1 item"),
        ({"camera_ids": [str(uuid4())]}, "unknown camera"),
        ({"minimum_confidence": 1.5}, "less than or equal to 1"),
        ({"cooldown_seconds": -1}, "greater than or equal to 0"),
        ({"post_roll_seconds": 500}, "less than or equal to 120"),
    ],
)
def test_bad_triggers_are_refused(client, compiles, cameras, change, message):
    body = {
        "name": "Object left behind",
        "natural_language_rule": RULE,
        "camera_ids": [str(cameras[0])],
        **change,
    }
    response = client.post("/triggers", json=body)
    assert response.status_code == 422 and message in str(response.json()["detail"])
    assert compiles == []


def test_a_queue_that_is_down_leaves_the_trigger_failed(client, cameras):
    def broker_down(trigger_id):
        raise ConnectionError("Redis is down (test)")

    client.app.dependency_overrides[get_trigger_queue] = lambda: broker_down
    trigger = create(client, cameras[:1]).json()
    assert trigger["compile_status"] == "failed" and "not reachable" in trigger["compile_error"]


def test_list_and_get(client, compiles, cameras, engine):
    first = create(client, cameras[:1]).json()["id"]
    second = create(client, cameras, name="Someone falls").json()["id"]
    compiled(engine, first)
    listed = client.get("/triggers").json()
    assert [t["id"] for t in listed] == [second, first]  # newest first
    one = client.get(f"/triggers/{first}").json()
    assert one["compiled"]["required_action_sequence"] == ["place_object", "leave"]
    assert client.get(f"/triggers/{uuid4()}").status_code == 404


def test_enabling_needs_a_compiled_rule(client, compiles, cameras, engine):
    trigger = create(client, cameras[:1]).json()["id"]
    response = client.post(f"/triggers/{trigger}/enable")
    assert response.status_code == 409 and "still being compiled" in response.json()["detail"]
    compiled(engine, trigger, "failed")
    response = client.post(f"/triggers/{trigger}/enable")
    assert response.status_code == 409 and "compile it again" in response.json()["detail"]
    compiled(engine, trigger)
    assert client.post(f"/triggers/{trigger}/enable").json()["enabled"] is True
    assert client.post(f"/triggers/{trigger}/disable").json()["enabled"] is False


def test_compile_again(client, compiles, cameras, engine):
    trigger = create(client, cameras[:1]).json()["id"]
    compiled(engine, trigger, "failed")
    response = client.post(f"/triggers/{trigger}/compile")
    assert response.status_code == 202
    assert (
        response.json()["compile_status"] == "pending" and response.json()["compile_error"] is None
    )
    assert [str(i) for i in compiles] == [trigger, trigger]


def test_editing_settings_keeps_the_compiled_rule(client, compiles, cameras, engine):
    trigger = create(client, cameras[:1]).json()["id"]
    compiled(engine, trigger)
    client.post(f"/triggers/{trigger}/enable")
    response = client.patch(
        f"/triggers/{trigger}",
        json={
            "name": "Left bag",
            "camera_ids": [str(c) for c in cameras],
            "minimum_confidence": 0.6,
            "cooldown_seconds": 300,
            "pre_roll_seconds": 5,
            "post_roll_seconds": 30,
            "natural_language_rule": RULE,  # unchanged
        },
    )
    updated = response.json()
    assert updated["name"] == "Left bag" and len(updated["cameras"]) == 2
    assert (updated["minimum_confidence"], updated["cooldown_seconds"]) == (0.6, 300)
    assert (updated["pre_roll_seconds"], updated["post_roll_seconds"]) == (5, 30)
    assert updated["enabled"] is True and updated["compile_status"] == "done"
    assert len(compiles) == 1  # only the first compile


def test_a_new_wording_compiles_again_and_disables(client, compiles, cameras, engine):
    trigger = create(client, cameras[:1]).json()["id"]
    compiled(engine, trigger)
    client.post(f"/triggers/{trigger}/enable")
    updated = client.patch(
        f"/triggers/{trigger}", json={"natural_language_rule": "Report when someone falls"}
    ).json()
    assert updated["natural_language_rule"] == "Report when someone falls"
    assert updated["compile_status"] == "pending" and updated["enabled"] is False
    assert len(compiles) == 2


def test_the_operator_can_switch_verification(client, compiles, cameras, engine):
    trigger = create(client, cameras[:1]).json()["id"]
    response = client.patch(f"/triggers/{trigger}", json={"requires_vlm_verification": False})
    assert response.status_code == 409  # nothing compiled yet
    compiled(engine, trigger)
    rule = client.patch(f"/triggers/{trigger}", json={"requires_vlm_verification": False}).json()
    assert rule["compiled"]["requires_vlm_verification"] is False
    assert rule["compiled"]["verification_set_by_operator"] is True


def test_delete_removes_the_trigger_with_its_events(client, compiles, cameras, engine):
    trigger = create(client, cameras[:1]).json()["id"]
    other = create(client, cameras[:1], name="Other").json()["id"]
    with Session(engine) as session:
        segment = VideoSegment(
            camera_id=cameras[0],
            start_time=T0,
            end_time=T0 + timedelta(seconds=30),
            duration_ms=30_000,
            storage_uri="s3://sentinel/x.mp4",
        )
        session.add(segment)
        session.flush()
        for trigger_id in (trigger, other):
            event = Event(
                trigger_id=trigger_id,
                camera_id=cameras[0],
                start_time=T0,
                end_time=T0 + timedelta(seconds=10),
            )
            event.evidence = [
                EventEvidence(segment_id=segment.id, start_offset_ms=0, end_offset_ms=10_000)
            ]
            session.add(event)
        session.commit()
        segment_id = segment.id
    assert client.get(f"/triggers/{trigger}").json()["events"] == {"new": 1, "total": 1}

    assert client.delete(f"/triggers/{trigger}").status_code == 204
    assert client.get(f"/triggers/{trigger}").status_code == 404
    with Session(engine) as session:
        assert session.scalars(select(Event.trigger_id)).all() == [session.get(Trigger, other).id]
        assert session.query(EventEvidence).count() == 1
        assert session.query(TriggerCamera).count() == 1  # the other trigger's
        assert session.get(VideoSegment, segment_id) is not None  # footage stays
        assert session.get(Camera, cameras[0]) is not None
    assert client.delete(f"/triggers/{trigger}").status_code == 404
