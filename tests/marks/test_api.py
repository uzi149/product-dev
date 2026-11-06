"""The marks routes and the export with marks (step 5.7, ADR 0038)."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from services.api.deps import get_export_queue, get_marks_queue
from shared.config import get_settings
from shared.database.models import (
    Event,
    EvidenceMarks,
    EvidenceStatus,
    QueryEvidence,
    QuerySession,
)
from shared.storage.keys import export_key, marked_export_key
from tests.events.conftest import at

TRACK = {
    "id": 1,
    "label": "person",
    "kind": "person",
    "picked": True,
    "runs": [{"segment_id": str(uuid4()), "points": [[0, 0.1, 0.2, 0.3, 0.4]]}],
}


@pytest.fixture
def evidence(add_segment):
    return [(add_segment(0, 30), 10_000, 30_000)]


@pytest.fixture
def add_marks(session):
    def add(**fields) -> EvidenceMarks:
        row = EvidenceMarks(**fields)
        session.add(row)
        session.commit()
        return row

    return add


@pytest.fixture
def exports(client):
    asked = []
    client.app.dependency_overrides[get_export_queue] = lambda: (
        lambda event_id, marks=False: asked.append((event_id, marks))
    )
    return asked


def test_an_event_without_marks(client, add_event, evidence):
    event = add_event(evidence)
    assert client.get(f"/events/{event.id}/marks").json() == {
        "status": "none",
        "reason": None,
        "updating": False,
        "pick_status": None,
        "tracks": [],
    }
    assert client.get(f"/events/{event.id}").json()["marks_status"] == "none"


def test_an_events_marks(client, add_event, evidence, add_marks):
    event = add_event(evidence)
    add_marks(event_id=event.id, status="done", pick_status="done", tracks_json=[TRACK])
    marks = client.get(f"/events/{event.id}/marks").json()
    assert marks["status"] == "done" and marks["pick_status"] == "done"
    assert marks["tracks"][0]["runs"][0]["points"] == [[0, 0.1, 0.2, 0.3, 0.4]]
    assert client.get(f"/events/{event.id}").json()["marks_status"] == "done"


def test_new_marks_on_the_way_are_shown_as_updating(client, add_event, evidence, add_marks):
    event = add_event(evidence)
    add_marks(event_id=event.id, status="running", tracks_json=[TRACK])
    marks = client.get(f"/events/{event.id}/marks").json()
    assert marks["updating"] and marks["tracks"]


def test_marks_of_an_unknown_event_are_404(client):
    assert client.get(f"/events/{uuid4()}/marks").status_code == 404


def test_the_marked_export_waits_for_the_marks(client, exports, add_event, evidence, add_marks):
    event = add_event(evidence)
    assert client.post(f"/events/{event.id}/export?marks=true").status_code == 409
    add_marks(event_id=event.id, status="running")
    assert client.post(f"/events/{event.id}/export?marks=true").status_code == 409
    assert exports == []


def test_the_marked_export_is_its_own_file(
    client, exports, add_event, evidence, add_marks, session
):
    event = add_event(evidence)
    add_marks(event_id=event.id, status="done", tracks_json=[TRACK], finished_at=at(120))
    state = client.post(f"/events/{event.id}/export?marks=true").json()
    assert state["status"] == "running" and exports == [(event.id, True)]
    assert client.get(f"/events/{event.id}/export").json()["status"] == "none"  # the plain one
    assert client.get(f"/events/{event.id}").json()["marked_export_status"] == "running"

    row = session.get(Event, event.id)
    row.marked_export_status, row.marked_exported_at = "done", at(150)
    row.marked_export_uri = f"s3://sentinel/{marked_export_key(event.id)}"
    session.commit()
    done = client.get(f"/events/{event.id}/export?marks=true").json()
    assert done["status"] == "done" and done["file_name"].endswith("_marked.mp4")


def test_new_marks_make_the_marked_export_out_of_date(
    client, exports, add_event, evidence, add_marks
):
    event = add_event(evidence, marked_export_status="done", marked_exported_at=at(150),
                      marked_export_uri="s3://sentinel/x.mp4")  # fmt: skip
    add_marks(event_id=event.id, status="done", tracks_json=[TRACK], finished_at=at(160))
    assert client.get(f"/events/{event.id}/export?marks=true").json()["status"] == "none"
    assert client.post(f"/events/{event.id}/export?marks=true").json()["status"] == "running"
    assert exports == [(event.id, True)]


def test_delete_removes_both_exports_and_the_marks(
    client, add_event, evidence, add_marks, storage, engine
):
    event = add_event(evidence)
    add_marks(event_id=event.id, status="done", tracks_json=[])
    storage.upload_bytes(export_key(event.id), b"mp4", "video/mp4")
    storage.upload_bytes(marked_export_key(event.id), b"mp4", "video/mp4")
    assert client.delete(f"/events/{event.id}").status_code == 204
    assert not storage.exists(marked_export_key(event.id))
    with Session(engine) as s:
        assert s.query(EvidenceMarks).count() == 0


@pytest.fixture
def result(session, camera, add_segment):
    segment = add_segment(0, 30)
    query = QuerySession(query_text="a person walks by", search_text="a person walks by")
    session.add(query)
    session.flush()
    evidence = QueryEvidence(
        query_session_id=query.id, rank=1, camera_id=camera.id, start_time=segment.start_time,
        end_time=segment.start_time + timedelta(seconds=10), score=0.7,
        status=EvidenceStatus.POSSIBLE, manifest_json={},
    )  # fmt: skip
    session.add(evidence)
    session.commit()
    return query, evidence


@pytest.fixture
def backend(monkeypatch):
    """Run the API with this VLM backend (and, for the local one, marks switched on or not)."""

    def use(name: str, local_marks: bool = False) -> None:
        monkeypatch.setenv("VLM_BACKEND", name)
        monkeypatch.setenv("MARKS_LOCAL_MODEL", "true" if local_marks else "false")
        get_settings.cache_clear()

    yield use
    get_settings.cache_clear()


@pytest.fixture
def marking(client):
    asked = []
    client.app.dependency_overrides[get_marks_queue] = lambda: asked.append
    return asked


def test_opening_a_result_marks_it_once(client, result, marking, backend):
    backend("api")
    query, evidence = result
    url = f"/queries/{query.id}/results/{evidence.id}/marks"
    assert client.get(url).json()["status"] == "none"
    first = client.post(url)
    assert first.status_code == 202 and first.json()["status"] == "pending"
    assert client.post(url).json()["status"] == "pending"
    assert len(marking) == 1


def test_with_the_local_model_a_result_is_not_marked(client, result, marking, backend):
    backend("local")
    query, evidence = result
    state = client.post(f"/queries/{query.id}/results/{evidence.id}/marks").json()
    assert state["status"] == "skipped" and "local model" in state["reason"]
    assert marking == []


def test_with_marks_on_for_the_local_model_a_result_is_marked(client, result, marking, backend):
    backend("local", local_marks=True)
    query, evidence = result
    state = client.post(f"/queries/{query.id}/results/{evidence.id}/marks").json()
    assert state["status"] == "pending"
    assert len(marking) == 1


def test_a_result_of_another_query_is_404(client, result, session):
    _, evidence = result
    other = QuerySession(query_text="x")
    session.add(other)
    session.commit()
    assert client.get(f"/queries/{other.id}/results/{evidence.id}/marks").status_code == 404
    assert client.post(f"/queries/{uuid4()}/results/{evidence.id}/marks").status_code == 404
