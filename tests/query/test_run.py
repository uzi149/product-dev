"""A query run end to end with fake embedders and a fake VLM (step 4.4, ADR 0029)."""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.query_engine.run import process_query
from shared.database.models import QueryEvidence, QueryResult, QuerySession
from shared.model_adapters import VLMUnavailable
from tests.fakes import FakeVLM
from tests.query.conftest import SETTINGS
from tests.search.conftest import Q_PICTURE, Q_TEXT, near

YES = json.dumps({"match": True, "confidence": 0.9, "reason": "A person rings the bell."})
NO = json.dumps({"match": False, "confidence": 0.8, "reason": "Nobody is at the door."})
UNSURE = json.dumps({"match": True, "confidence": 0.3, "reason": "Too dark to tell."})


@pytest.fixture
def footage(cameras, add_window, pictures):
    """Front door: a good match at 0 s and its neighbour; yard: a weaker match."""
    front, yard = cameras
    best = add_window(front, 0, near(Q_TEXT, 0.95, "a"), [near(Q_PICTURE, 0.9, "ap")] * 3)
    add_window(front, 9, near(Q_TEXT, 0.4, "b"), [near(Q_PICTURE, 0.3, "bp")] * 3)
    other = add_window(yard, 0, near(Q_TEXT, 0.7, "c"), [near(Q_PICTURE, 0.6, "cp")] * 3)
    pictures()
    return best, other


@pytest.fixture
def make_query(session):
    def make(text="someone rings the doorbell", status="pending"):
        query = QuerySession(query_text=text, search_text=text, status=status)
        session.add(query)
        session.commit()
        return query.id

    return make


def load(engine, query_id):
    with Session(engine) as s:
        query = s.get(QuerySession, query_id)
        rows = s.scalars(
            select(QueryEvidence)
            .where(QueryEvidence.query_session_id == query_id)
            .order_by(QueryEvidence.rank)
        ).all()
        s.expunge_all()
        return query, rows


def test_results_are_stored_then_verified_one_by_one(engine, footage, make_query, make_tools):
    best, _ = footage
    query_id = make_query()
    stages = []
    vlm = FakeVLM([YES])

    def published(qid):
        with Session(engine) as s:
            q = s.get(QuerySession, qid)
            done = s.scalars(
                select(QueryEvidence.status).where(QueryEvidence.query_session_id == qid)
            ).all()
            stages.append((q.stage, sorted(done)))

    tools = make_tools(vlm)
    tools.publish = published
    process_query(query_id, tools)

    query, rows = load(engine, query_id)
    assert query.status == "done" and query.verification == "on" and query.finished_at
    assert [s for s, _ in stages] == [
        "searching",
        "candidates",
        "verifying",
        "verifying",
        "verifying",
        "done",
    ]
    assert stages[1][1] == ["possible", "possible"]  # candidates first, as possible matches
    assert stages[3][1] == ["possible", "verified"]  # then one result at a time
    assert [r.status for r in rows] == ["verified", "verified"]
    first = rows[0]
    assert (
        first.verification_confidence == 0.9
        and first.verification_reason == "A person rings the bell."
    )
    assert first.summary and first.keyframe_id
    assert first.manifest_json["parts"][0]["type"] == "video"
    # The frames are the stored keyframes: one window's 3 keyframes plus its neighbour's.
    assert vlm.calls[0]["frames"] == 6 and "someone rings the doorbell" in vlm.calls[0]["prompt"]
    with Session(engine) as s:
        windows = s.scalars(
            select(QueryResult).where(QueryResult.sequence_group_id == first.id)
        ).all()
        assert {w.analysis_window_id for w in windows} >= {best.id}
        assert all(w.verified and w.verification_score == 0.9 for w in windows)
        # The open test settings keep every window as a hit; the best one scores highest.
        top = max(windows, key=lambda w: w.rerank_score or 0)
        assert top.analysis_window_id == best.id and top.vector_score is not None


def test_rejected_and_unsure_results_are_rejected(engine, footage, make_query, make_tools):
    query_id = make_query()
    process_query(query_id, make_tools(FakeVLM([NO, UNSURE])))
    _, rows = load(engine, query_id)
    assert [(r.status, r.verification_confidence) for r in rows] == [
        ("rejected", 0.8),
        ("rejected", 0.3),  # a match below QUERY_VERIFY_MIN_CONFIDENCE is not verified
    ]


def test_only_the_best_results_are_verified(engine, footage, make_query, make_tools):
    query_id = make_query()
    vlm = FakeVLM([YES])
    process_query(query_id, make_tools(vlm, SETTINGS.model_copy(update={"query_verify_top": 1})))
    _, rows = load(engine, query_id)
    assert [r.status for r in rows] == ["verified", "possible"]
    assert len(vlm.calls) == 1


def test_no_verification_with_the_local_model(engine, footage, make_query, make_tools):
    query_id = make_query()
    vlm = FakeVLM([YES])
    local = SETTINGS.model_copy(update={"vlm_backend": "local"})
    published = []
    process_query(query_id, make_tools(vlm, local, published))
    query, rows = load(engine, query_id)
    assert query.verification == "skipped" and query.status == "done"
    assert [r.status for r in rows] == ["possible", "possible"]
    assert vlm.calls == [] and len(published) == 3  # searching, candidates, done


def test_a_vlm_that_is_down_leaves_results_unverifiable(engine, footage, make_query, make_tools):
    query_id = make_query()
    process_query(query_id, make_tools(FakeVLM([VLMUnavailable("connection refused")])))
    query, rows = load(engine, query_id)
    assert query.status == "done"
    assert {r.status for r in rows} == {"unverifiable"}
    assert "connection refused" in rows[0].verification_reason


def test_an_unusable_answer_is_asked_again_once(engine, footage, make_query, make_tools):
    query_id = make_query()
    vlm = FakeVLM(["I think so", YES, "no idea", "still no JSON"])
    process_query(query_id, make_tools(vlm))
    _, rows = load(engine, query_id)
    assert [r.status for r in rows] == ["verified", "unverifiable"]
    assert "previous answer could not be used" in vlm.calls[1]["prompt"]


def test_no_matches_finishes_with_no_results(engine, cameras, make_query, make_tools):
    query_id = make_query()
    process_query(query_id, make_tools(FakeVLM([YES])))
    query, rows = load(engine, query_id)
    assert (query.status, query.stage, rows) == ("done", "done", [])


def test_a_failure_is_recorded(engine, footage, make_query, make_tools, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("the index is broken (test)")

    monkeypatch.setattr("services.query_engine.run.search", broken)
    query_id = make_query()
    published = []
    process_query(query_id, make_tools(FakeVLM([YES]), published=published))
    query, _ = load(engine, query_id)
    assert (query.status, query.error) == ("failed", "the index is broken (test)")
    assert query.finished_at and len(published) == 2


def test_a_finished_query_is_not_run_again(engine, footage, make_query, make_tools):
    query_id = make_query(status="done")
    vlm = FakeVLM([YES])
    process_query(query_id, make_tools(vlm))
    assert load(engine, query_id)[1] == [] and vlm.calls == []


def test_a_query_deleted_while_running_stops_quietly(engine, footage, make_query, make_tools):
    """Deleted after its candidates were stored (step 4.6): no VLM call, no failed row."""
    query_id = make_query()
    vlm = FakeVLM([YES, YES])
    tools = make_tools(vlm)
    published = []

    def delete_after_candidates(qid):
        published.append(qid)
        if len(published) == 2:  # searching, candidates
            with Session(engine) as s:
                s.delete(s.get(QuerySession, qid))
                s.commit()

    tools.publish = delete_after_candidates
    process_query(query_id, tools)
    query, rows = load(engine, query_id)
    assert query is None and rows == [] and vlm.calls == []


def test_a_query_deleted_during_verification_stops_quietly(engine, footage, make_query, make_tools):
    query_id = make_query()
    vlm = FakeVLM([YES, YES])
    tools = make_tools(vlm)
    published = []

    def delete_after_first_verdict(qid):
        published.append(qid)
        if len(published) == 4:  # searching, candidates, verifying, first verdict
            with Session(engine) as s:
                s.delete(s.get(QuerySession, qid))
                s.commit()

    tools.publish = delete_after_first_verdict
    process_query(query_id, tools)
    query, _ = load(engine, query_id)
    assert query is None and len(vlm.calls) == 1  # the second result isn't sent to the VLM
