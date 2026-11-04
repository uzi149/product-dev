"""Making and storing marks, with the database and MinIO (step 5.7, ADR 0038)."""

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.marks.job import MarkTools, process_marks
from services.marks.state import marks_state, request_event_marks, request_result_marks
from shared.config import Settings
from shared.database.models import (
    Event,
    EvidenceMarks,
    EvidenceStatus,
    QueryEvidence,
    QuerySession,
)
from shared.model_adapters.vlm import VLMQuotaExhausted
from shared.schemas.query import PlaybackManifest, VideoPart
from tests.events.conftest import at
from tests.fakes import FakeVLM
from tests.marks.videos import Mover, grounder

API = Settings(_env_file=None, vlm_backend="api", marks_concurrency=2)
LOCAL = Settings(_env_file=None, vlm_backend="local")
WALKER = Mover("person", seed=1, x0=10, speed=40)


@pytest.fixture
def tools(storage):
    def make(vlm, settings=API) -> MarkTools:
        return MarkTools(settings=settings, storage=storage, vlm=lambda: vlm, now=lambda: at(500))

    return make


@pytest.fixture
def event(session, stored_clip, add_event):
    segment = stored_clip(0, 5, [WALKER])
    return add_event([(segment, 0, 5_000)], start_s=1, end_s=4)


def mark(engine, marks_id, tools):
    process_marks(marks_id, lambda: Session(engine), tools)


def row_of(session, event):
    session.expire_all()
    return session.scalars(select(EvidenceMarks).where(EvidenceMarks.event_id == event.id)).one()


def test_an_events_evidence_is_marked(session, engine, event, tools):
    marks_id = request_event_marks(session, event, API)
    session.commit()
    vlm = FakeVLM([grounder([WALKER])])

    mark(engine, marks_id, tools(vlm))

    row = row_of(session, event)
    assert row.status == "done" and row.reason is None
    assert row.model == "fake-vlm" and row.prompt_version == 1 and row.keyframes == 5
    assert row.evidence_updated_at == event.updated_at and row.finished_at == at(500)
    assert row.pick_status == "none"
    state = marks_state(row)
    assert [t.label for t in state.tracks] == ["person"] and not state.updating
    # The trigger's rule names "someone" and "an object": person and the object words.
    assert "person, bag" in vlm.calls[0]["prompt"]


def test_with_the_local_model_nothing_is_queued(session, event):
    assert request_event_marks(session, event, LOCAL) is None
    session.commit()
    assert row_of(session, event).status == "skipped"


def test_a_vlm_failure_fails_the_marks_not_the_event(session, engine, event, tools):
    marks_id = request_event_marks(session, event, API)
    session.commit()
    mark(engine, marks_id, tools(FakeVLM([VLMQuotaExhausted("daily limit reached")])))
    row = row_of(session, event)
    assert row.status == "failed" and "daily limit" in row.reason
    session.refresh(event)
    assert event.status == "new" and event.alerted_at is not None


def test_an_event_extended_while_marking_keeps_the_new_request(session, engine, event, tools):
    marks_id = request_event_marks(session, event, API)
    session.commit()

    def extend_meanwhile(frames, prompt):
        with Session(engine) as other:
            changed = other.get(Event, event.id)
            if changed.updated_at == event.updated_at:
                changed.updated_at = event.updated_at + timedelta(seconds=30)
                row = other.get(EvidenceMarks, marks_id)
                row.status = "pending"  # what finishing does when it rewrites the evidence
                other.commit()
        return grounder([WALKER])(frames, prompt)

    mark(engine, marks_id, tools(FakeVLM([extend_meanwhile])))

    row = row_of(session, event)
    assert row.status == "pending" and row.tracks_json  # the old marks shown as "updating"
    assert marks_state(row).updating


def test_an_older_run_never_overwrites_newer_marks(session, engine, event, tools):
    marks_id = request_event_marks(session, event, API)
    session.commit()
    row = row_of(session, event)
    row.evidence_updated_at = event.updated_at + timedelta(seconds=30)
    row.tracks_json = [{"id": 99, "label": "newer", "kind": None, "picked": False, "runs": []}]
    session.commit()
    mark(engine, marks_id, tools(FakeVLM([grounder([WALKER])])))
    assert row_of(session, event).tracks_json[0]["label"] == "newer"


def test_marks_of_a_deleted_event_stop_quietly(session, engine, event, tools):
    marks_id = request_event_marks(session, event, API)
    session.commit()
    session.delete(session.get(Event, event.id))
    session.commit()
    vlm = FakeVLM([grounder([WALKER])])
    mark(engine, marks_id, tools(vlm))
    assert vlm.calls == []


@pytest.fixture
def result(session, stored_clip, camera):
    segment = stored_clip(100, 4, [WALKER])
    query = QuerySession(query_text="a person walks by on CAM-01", search_text="a person walks by")
    session.add(query)
    session.flush()
    manifest = PlaybackManifest(
        result_id=query.id,
        camera_id=camera.id,
        start_time=segment.start_time,
        end_time=segment.end_time,
        parts=[
            VideoPart(
                camera_id=camera.id,
                segment_id=segment.id,
                source_start_ms=0,
                source_end_ms=4_000,
                start_time=segment.start_time,
                end_time=segment.end_time,
            )
        ],
    )
    evidence = QueryEvidence(
        query_session_id=query.id,
        rank=1,
        camera_id=camera.id,
        start_time=segment.start_time,
        end_time=segment.end_time,
        score=0.8,
        status=EvidenceStatus.VERIFIED,
        manifest_json=manifest.model_dump(mode="json"),
    )
    session.add(evidence)
    session.commit()
    return evidence


def test_a_result_is_marked_once(session, engine, result, tools):
    row, queue = request_result_marks(session, result, API)
    session.commit()
    assert queue and row.status == "pending"
    vlm = FakeVLM([grounder([WALKER])])
    mark(engine, row.id, tools(vlm))
    session.expire_all()
    assert session.get(EvidenceMarks, row.id).status == "done"
    assert "objects in the image: person." in vlm.calls[0]["prompt"]

    again, queue = request_result_marks(session, result, API)
    assert again.id == row.id and not queue


def test_a_failed_result_is_tried_again(session, result):
    row, _ = request_result_marks(session, result, API)
    row.status = "failed"
    session.commit()
    again, queue = request_result_marks(session, result, API)
    assert again.id == row.id and queue and again.status == "pending"


def test_a_result_left_running_by_a_stopped_worker_is_asked_again(session, result):
    row, _ = request_result_marks(session, result, API)
    row.status, row.started_at = "running", at(0)
    session.commit()
    same, queue = request_result_marks(session, result, API, now=at(60))
    assert same.status == "running" and not queue
    again, queue = request_result_marks(session, result, API, now=at(0) + timedelta(hours=1))
    assert again.id == row.id and queue and again.status == "pending"
