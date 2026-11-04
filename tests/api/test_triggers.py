"""The trigger API (step 5.1): create, list, edit, compile, enable, disable, delete."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.api.deps import get_trigger_queue, get_trigger_test_queue
from shared.database.models import (
    Camera,
    Event,
    EventEvidence,
    Trigger,
    TriggerCamera,
    TriggerTest,
    Upload,
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


# Trigger tests (step 5.4, ADR 0035)


@pytest.fixture
def tests_queued(client):
    asked = []
    client.app.dependency_overrides[get_trigger_test_queue] = lambda: asked.append
    return asked


@pytest.fixture
def ready(client, compiles, cameras, engine):
    """A compiled trigger on CAM-01."""
    trigger = create(client, cameras[:1]).json()["id"]
    compiled(engine, trigger)
    return trigger


@pytest.fixture
def upload(engine, cameras):
    """An uploaded video on CAM-02 that was stored as two segments (9:00-9:00:30 and
    9:01-9:01:30), plus another camera's segment stored meanwhile and an older segment."""
    ingested_at = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
    with Session(engine) as session:
        upload = Upload(
            camera_id=cameras[1],
            filename="LeftBag.mpg",
            start_time=T0,
            status="done",
            created_at=ingested_at,
            finished_at=ingested_at + timedelta(minutes=2),
        )
        session.add(upload)

        def segment(camera, offset_s, stored_at):
            session.add(
                VideoSegment(
                    camera_id=camera,
                    start_time=T0 + timedelta(seconds=offset_s),
                    end_time=T0 + timedelta(seconds=offset_s + 30),
                    duration_ms=30_000,
                    storage_uri="s3://sentinel/x.mp4",
                    created_at=stored_at,
                )
            )

        segment(cameras[1], 0, ingested_at + timedelta(seconds=30))
        segment(cameras[1], 60, ingested_at + timedelta(seconds=60))
        segment(cameras[1], -3600, ingested_at - timedelta(days=1))  # an earlier upload
        segment(cameras[0], 0, ingested_at + timedelta(seconds=40))  # another camera
        session.commit()
        return upload.id


def test_a_test_on_an_uploaded_video_covers_its_footage(
    client, tests_queued, ready, upload, cameras
):
    response = client.post(
        f"/triggers/{ready}/test", json={"source": "video", "upload_id": str(upload)}
    )
    assert response.status_code == 202
    test = response.json()
    assert test["status"] == "pending" and test["upload_filename"] == "LeftBag.mpg"
    assert test["camera_ids"] == [str(cameras[1])]  # the upload's camera, not the trigger's
    assert test["start"].startswith("2026-03-07T09:00:00") and test["end"].startswith(
        "2026-03-07T09:01:30"
    )
    assert test["rule"]["required_action_sequence"] == ["place_object", "leave"]
    assert (test["minimum_confidence"], test["cooldown_seconds"]) == (0.8, 120)
    assert [str(i) for i in tests_queued] == [test["id"]]


def test_a_test_on_the_last_hour_uses_the_triggers_cameras(client, tests_queued, ready, cameras):
    test = client.post(f"/triggers/{ready}/test", json={"source": "last_hour"}).json()
    assert test["camera_ids"] == [str(cameras[0])]
    start, end = (datetime.fromisoformat(test[k]) for k in ("start", "end"))
    assert end - start == timedelta(hours=1)
    day = client.post(f"/triggers/{ready}/test", json={"source": "last_24h"}).json()
    assert datetime.fromisoformat(day["end"]) - datetime.fromisoformat(day["start"]) == timedelta(
        hours=24
    )
    span = {"source": "range", "start": "2026-03-07T09:00:00Z", "end": "2026-03-07T10:00:00Z"}
    ranged = client.post(f"/triggers/{ready}/test", json=span).json()
    assert ranged["start"].startswith("2026-03-07T09:00:00")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"source": "video"}, "choose the uploaded video"),
        ({"source": "video", "upload_id": str(uuid4())}, "unknown uploaded video"),
        ({"source": "range", "start": "2026-03-07T09:00:00Z"}, "needs a start and an end"),
        (
            {"source": "range", "start": "2026-03-07T10:00:00Z", "end": "2026-03-07T09:00:00Z"},
            "the end must be after the start",
        ),
        ({"source": "yesterday"}, "Input should be"),
    ],
)
def test_bad_tests_are_refused(client, tests_queued, ready, body, message):
    response = client.post(f"/triggers/{ready}/test", json=body)
    assert response.status_code == 422 and message in str(response.json()["detail"])
    assert tests_queued == []


def test_a_video_without_stored_footage_is_refused(client, tests_queued, ready, engine, cameras):
    with Session(engine) as session:
        empty = Upload(camera_id=cameras[0], filename="still.mp4", start_time=T0, status="done")
        session.add(empty)
        session.commit()
        empty_id = empty.id
    response = client.post(
        f"/triggers/{ready}/test", json={"source": "video", "upload_id": str(empty_id)}
    )
    assert response.status_code == 422 and "no stored footage" in response.json()["detail"]


def test_an_uncompiled_trigger_cant_be_tested(client, tests_queued, compiles, cameras):
    trigger = create(client, cameras[:1]).json()["id"]
    response = client.post(f"/triggers/{trigger}/test", json={"source": "last_hour"})
    assert response.status_code == 409


def test_a_queue_that_is_down_fails_the_test(client, ready):
    def down(test_id):
        raise ConnectionError("Redis is down (test)")

    client.app.dependency_overrides[get_trigger_test_queue] = lambda: down
    assert client.post(f"/triggers/{ready}/test", json={"source": "last_hour"}).status_code == 503
    (test,) = client.get(f"/triggers/{ready}/tests").json()
    assert test["status"] == "failed" and "not reachable" in test["error"]


def test_tests_are_listed_newest_first_and_read_back(
    client, tests_queued, ready, compiles, cameras, engine
):
    first = client.post(f"/triggers/{ready}/test", json={"source": "last_hour"}).json()["id"]
    second = client.post(f"/triggers/{ready}/test", json={"source": "last_24h"}).json()["id"]
    with Session(engine) as session:  # what the query worker stores
        test = session.get(TriggerTest, first)
        test.status, test.stage = "done", "done"
        test.results_json = {
            "candidates": 12,
            "footage_seconds": 3600.0,
            "counts": {"would_report": 0, "rejected": 0, "below_confidence": 0},
            "matches": [],
        }
        session.commit()
    assert [t["id"] for t in client.get(f"/triggers/{ready}/tests").json()] == [second, first]
    done = client.get(f"/triggers/{ready}/tests/{first}").json()
    assert done["status"] == "done" and done["candidates"] == 12
    assert done["footage_seconds"] == 3600.0
    other = create(client, cameras[:1], name="Other").json()["id"]
    assert client.get(f"/triggers/{other}/tests/{first}").status_code == 404


def test_deleting_a_trigger_deletes_its_tests(client, tests_queued, ready, engine):
    client.post(f"/triggers/{ready}/test", json={"source": "last_hour"})
    assert client.delete(f"/triggers/{ready}").status_code == 204
    with Session(engine) as session:
        assert session.query(TriggerTest).count() == 0
