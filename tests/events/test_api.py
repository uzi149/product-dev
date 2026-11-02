"""The events API (step 5.3): list, counts, detail, acknowledge, export, delete."""

from datetime import timedelta
from urllib.parse import unquote
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.api.deps import get_export_queue
from shared.database.models import Camera, Event, EventEvidence, Trigger, VideoSegment
from shared.storage.keys import export_key
from tests.events.conftest import at


@pytest.fixture
def exports(client):
    asked = []
    client.app.dependency_overrides[get_export_queue] = lambda: asked.append
    return asked


@pytest.fixture
def evidence(add_segment):
    first = add_segment(0, 30)
    second = add_segment(342, 30)
    return [(first, 10_000, 30_000), (second, 0, 12_000)]


def test_alerted_events_are_listed_latest_alert_first(client, add_event, evidence):
    older = add_event(evidence, alerted_s=100)
    newer = add_event(evidence, alerted_s=200, status="confirmed")
    add_event(evidence, held=True)  # waits for its post-roll: not shown
    listed = client.get("/events").json()
    assert [e["id"] for e in listed["items"]] == [str(newer.id), str(older.id)]
    assert listed["total"] == 2
    item = listed["items"][1]
    assert item["trigger_name"] == "Object left behind" and item["camera_name"] == "CAM-01"
    assert item["verification"] == "verified" and item["verification_reason"] == "A bag is left."
    assert item["export_status"] == "none"


def test_filters_and_paging(client, add_event, evidence, session, camera):
    new = add_event(evidence, start_s=0, alerted_s=100)
    confirmed = add_event(evidence, start_s=500, alerted_s=200, status="confirmed")

    def ids(**params):
        return [e["id"] for e in client.get("/events", params=params).json()["items"]]

    assert ids(status="new") == [str(new.id)]
    assert ids(status="confirmed") == [str(confirmed.id)]
    assert ids(camera_id=str(camera.id)) == [str(confirmed.id), str(new.id)]
    assert ids(camera_id=str(uuid4())) == []
    assert ids(start=at(100).isoformat()) == [str(confirmed.id)]
    assert ids(end=at(100).isoformat()) == [str(new.id)]
    page = client.get("/events", params={"limit": 1, "offset": 1}).json()
    assert [e["id"] for e in page["items"]] == [str(new.id)] and page["total"] == 2
    assert client.get("/events", params={"status": "maybe"}).status_code == 422


def test_counts(client, add_event, evidence):
    add_event(evidence)
    add_event(evidence)
    add_event(evidence, status="dismissed")
    add_event(evidence, held=True)
    assert client.get("/events/counts").json() == {
        "new": 2,
        "confirmed": 0,
        "dismissed": 1,
        "total": 3,
    }


def test_the_detail_has_a_manifest_with_the_gap(client, add_event, evidence, add_window):
    window = add_window(evidence[0][0])
    event = add_event(evidence, window=window)
    detail = client.get(f"/events/{event.id}").json()
    assert [p["type"] for p in detail["manifest"]["parts"]] == ["video", "gap", "video"]
    assert detail["manifest"]["parts"][1]["duration_ms"] == 312_000
    assert detail["mode"] == "structured"
    assert detail["delay_seconds"] is None  # an upload's footage can be from any day
    assert detail["keyframe_id"] is not None and detail["details"]["window_ids"]


def test_the_alert_delay_is_shown_for_a_live_camera(client, add_event, evidence, session, camera):
    camera.source_type, camera.source_uri_encrypted = "rtsp", "x"
    session.commit()
    event = add_event(evidence)
    assert client.get(f"/events/{event.id}").json()["delay_seconds"] == 34.0


def test_a_held_or_unknown_event_is_404(client, add_event, evidence):
    held = add_event(evidence, held=True)
    assert client.get(f"/events/{held.id}").status_code == 404
    assert client.get(f"/events/{uuid4()}").status_code == 404
    assert (
        client.post(f"/events/{uuid4()}/acknowledge", json={"status": "confirmed"}).status_code
        == 404
    )


def test_confirm_dismiss_and_back_to_new(client, add_event, evidence):
    event = add_event(evidence)
    confirmed = client.post(
        f"/events/{event.id}/acknowledge", json={"status": "confirmed", "note": "  real bag  "}
    ).json()
    assert confirmed["status"] == "confirmed" and confirmed["acknowledged_at"]
    assert confirmed["note"] == "real bag"
    dismissed = client.post(f"/events/{event.id}/acknowledge", json={"status": "dismissed"}).json()
    assert dismissed["status"] == "dismissed" and dismissed["note"] == "real bag"  # kept
    reopened = client.post(
        f"/events/{event.id}/acknowledge", json={"status": "new", "note": None}
    ).json()
    assert reopened["status"] == "new" and reopened["acknowledged_at"] is None
    assert reopened["note"] is None
    bad = client.post(f"/events/{event.id}/acknowledge", json={"status": "maybe"})
    assert bad.status_code == 422


def test_acknowledging_does_not_make_an_export_out_of_date(
    client,
    add_event,
    evidence,
    session,
):
    event = add_event(evidence, export_status="done", export_uri="s3://sentinel/x.mp4",
                      exported_at=at(150))  # fmt: skip
    client.post(f"/events/{event.id}/acknowledge", json={"status": "confirmed"})
    assert client.get(f"/events/{event.id}/export").json()["status"] == "done"


def test_an_export_is_queued_once(client, exports, add_event, evidence):
    event = add_event(evidence)
    assert client.get(f"/events/{event.id}/export").json()["status"] == "none"
    response = client.post(f"/events/{event.id}/export")
    assert response.status_code == 202 and response.json()["status"] == "running"
    assert client.post(f"/events/{event.id}/export").json()["status"] == "running"
    assert exports == [event.id]  # asking again while running queues nothing more


def test_a_finished_export_gives_a_download_link(client, exports, add_event, evidence):
    event = add_event(evidence, export_status="done", exported_at=at(150),
                      export_uri=f"s3://sentinel/{export_key(uuid4())}")  # fmt: skip
    state = client.post(f"/events/{event.id}/export").json()
    assert state["status"] == "done" and exports == []  # up to date: reused
    assert state["file_name"].startswith("object-left-behind_cam-01_")
    assert "attachment" in unquote(state["url"]) and state["file_name"] in unquote(state["url"])


def test_an_extended_event_is_exported_again(client, exports, add_event, evidence, session):
    event = add_event(evidence, export_status="done", exported_at=at(150),
                      export_uri="s3://sentinel/x.mp4")  # fmt: skip
    row = session.get(Event, event.id)
    row.updated_at = at(160)  # extended after the export
    session.commit()
    assert client.get(f"/events/{event.id}/export").json()["status"] == "none"
    assert client.post(f"/events/{event.id}/export").json()["status"] == "running"
    assert exports == [event.id]


def test_a_queue_that_is_down_fails_the_export(client, add_event, evidence):
    def down(event_id):
        raise ConnectionError("Redis is down (test)")

    client.app.dependency_overrides[get_export_queue] = lambda: down
    event = add_event(evidence)
    assert client.post(f"/events/{event.id}/export").status_code == 503
    state = client.get(f"/events/{event.id}/export").json()
    assert state["status"] == "failed" and "not reachable" in state["error"]


def test_delete_removes_the_event_its_evidence_and_its_export(
    client,
    add_event,
    evidence,
    session,
    storage,
    engine,
):
    event = add_event(evidence)
    other = add_event(evidence)
    storage.upload_bytes(export_key(event.id), b"mp4", "video/mp4")
    assert client.delete(f"/events/{event.id}").status_code == 204
    assert not storage.exists(export_key(event.id))
    with Session(engine) as s:
        assert [e for e in s.scalars(select(Event.id))] == [other.id]
        assert s.query(EventEvidence).count() == 2  # the other event's
        assert s.query(VideoSegment).count() == 2  # the footage stays
    assert client.delete(f"/events/{event.id}").status_code == 404


def test_deleting_a_trigger_removes_its_events_exports(
    client,
    add_event,
    evidence,
    trigger,
    storage,
    engine,
):
    event = add_event(evidence)
    storage.upload_bytes(export_key(event.id), b"mp4", "video/mp4")
    assert client.delete(f"/triggers/{trigger.id}").status_code == 204
    assert not storage.exists(export_key(event.id))
    with Session(engine) as s:
        assert s.get(Trigger, trigger.id) is None and s.query(Event).count() == 0
        assert s.query(Camera).count() == 1


def test_events_newer_than_a_day_old_alert(client, add_event, evidence):
    """`start`/`end` filter on when the footage happened, not when it was alerted."""
    event = add_event(evidence, start_s=3600, alerted_s=10)
    found = client.get(
        "/events", params={"start": (at(0) + timedelta(minutes=30)).isoformat()}
    ).json()
    assert [e["id"] for e in found["items"]] == [str(event.id)]
