"""The query API (step 4.4): create, state, results, WebSocket progress, keyframe pictures; the
history (step 4.6): list, Run again, delete."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import ClassVar
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from services.api.deps import get_query_events, get_query_queue
from shared.database.models import (
    AnalysisWindow,
    Camera,
    Keyframe,
    QueryEvidence,
    QueryResult,
    QuerySession,
    VideoSegment,
)

T0 = datetime(2026, 3, 7, 9, 0, tzinfo=UTC)


class FakeEvents:
    """No Redis: `wait` just pauses briefly, so the WebSocket re-reads the database."""

    notified: ClassVar[list] = []

    def notify(self, query_id):
        self.notified.append(query_id)

    @asynccontextmanager
    async def subscribe(self, query_id):
        class Subscription:
            async def wait(self, timeout):
                await asyncio.sleep(0.05)
                return False

        yield Subscription()


@pytest.fixture
def queries_queued(client):
    ids = []
    client.app.dependency_overrides[get_query_queue] = lambda: ids.append
    FakeEvents.notified = []
    client.app.dependency_overrides[get_query_events] = FakeEvents
    return ids


@pytest.fixture
def camera(engine):
    with Session(engine) as session:
        camera = Camera(display_name="CAM-02", source_type="file")
        session.add(camera)
        session.commit()
        return camera.id


def post(client, text, **extra):
    return client.post("/queries", json={"query_text": text, "timezone": "UTC", **extra})


def test_a_query_is_stored_with_its_filters_and_queued(client, queries_queued, camera):
    response = post(client, "someone left a bag on CAM-02 yesterday")
    assert response.status_code == 202
    state = response.json()
    assert state["status"] == "pending" and state["search_text"] == "someone left a bag"
    assert [(u["kind"], u["label"]) for u in state["understood"]] == [
        ("camera", "CAM-02"),
        ("time", "Yesterday"),
    ]
    assert state["camera_ids"] == [str(camera)] and state["start"] and state["end"]
    assert [str(i) for i in queries_queued] == [state["id"]]
    assert client.get(f"/queries/{state['id']}").json()["search_text"] == "someone left a bag"


def test_chosen_filters_win_and_parsing_can_be_switched_off(client, queries_queued, camera):
    start = "2026-03-07T00:00:00+00:00"
    state = post(client, "a bag on CAM-02 yesterday", start=start, parse_cameras=False).json()
    assert state["search_text"] == "a bag on CAM-02"
    assert state["camera_ids"] is None and state["start"].startswith("2026-03-07T00:00:00")
    assert state["understood"] == []


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"query_text": "CAM-02 yesterday"}, "Add what you're looking for"),
        ({"query_text": "a bag", "camera_ids": [str(uuid4())]}, "unknown camera"),
        ({"query_text": "a bag", "timezone": "Mars/Olympus"}, "unknown time zone"),
        ({"query_text": ""}, "at least 1 character"),
    ],
)
def test_bad_queries_are_refused(client, queries_queued, camera, body, message):
    response = client.post("/queries", json={"timezone": "UTC", **body})
    assert response.status_code == 422 and message in str(response.json()["detail"])
    assert queries_queued == []


def test_a_queue_that_is_down_is_503_and_the_query_fails(client, camera, engine):
    def broker_down(query_id):
        raise ConnectionError("Redis is down (test)")

    client.app.dependency_overrides[get_query_queue] = lambda: broker_down
    response = post(client, "a bag")
    assert response.status_code == 503
    with Session(engine) as session:
        (query,) = session.query(QuerySession).all()
        assert query.status == "failed"


def test_unknown_queries_are_404(client, queries_queued):
    assert client.get(f"/queries/{uuid4()}").status_code == 404
    assert client.get(f"/queries/{uuid4()}/results").status_code == 404


@pytest.fixture
def finished(engine, camera):
    """A finished query with three results: verified, rejected, possible."""
    with Session(engine) as session:
        query = QuerySession(
            query_text="a bag",
            search_text="a bag",
            status="done",
            stage="done",
            verification="on",
            finished_at=T0,
        )
        session.add(query)
        session.flush()
        for rank, status in enumerate(("verified", "rejected", "possible"), 1):
            result_id = uuid4()
            session.add(
                QueryEvidence(
                    id=result_id,
                    query_session_id=query.id,
                    rank=rank,
                    camera_id=camera,
                    start_time=T0,
                    end_time=T0 + timedelta(seconds=30),
                    score=1 - rank / 10,
                    status=status,
                    verification_confidence=0.9 if status != "possible" else None,
                    verification_reason="reason" if status != "possible" else None,
                    summary=f"result {rank}",
                    manifest_json={
                        "result_id": str(result_id),
                        "camera_id": str(camera),
                        "start_time": T0.isoformat(),
                        "end_time": (T0 + timedelta(seconds=30)).isoformat(),
                        "parts": [{"type": "gap", "duration_ms": 1000}],
                    },
                )
            )
        session.commit()
        return query.id


def test_results_leave_out_the_rejected_ones_unless_asked(client, queries_queued, finished):
    results = client.get(f"/queries/{finished}/results").json()
    assert [(r["rank"], r["status"]) for r in results] == [(1, "verified"), (3, "possible")]
    assert results[0]["camera_name"] == "CAM-02" and results[0]["manifest"]["parts"]
    everything = client.get(f"/queries/{finished}/results", params={"include_rejected": True})
    assert len(everything.json()) == 3


def test_the_state_counts_the_results(client, queries_queued, finished):
    counts = client.get(f"/queries/{finished}").json()["counts"]
    assert counts == {
        "candidates": 3,
        "to_verify": 3,
        "verified": 1,
        "rejected": 1,
        "unverifiable": 0,
        "possible": 1,
    }


def test_the_websocket_sends_the_state_and_closes_when_finished(client, queries_queued, finished):
    with client.websocket_connect(f"/queries/{finished}/progress") as ws:
        message = ws.receive_json()
        assert message["state"]["status"] == "done" and len(message["results"]) == 2
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_the_websocket_follows_a_running_query(client, queries_queued, engine, finished):
    with Session(engine) as session:
        query = session.get(QuerySession, finished)
        query.status, query.stage = "running", "verifying"
        session.commit()
    with client.websocket_connect(f"/queries/{finished}/progress") as ws:
        assert ws.receive_json()["state"]["stage"] == "verifying"
        with Session(engine) as session:
            query = session.get(QuerySession, finished)
            query.status, query.stage = "done", "done"
            session.commit()
        assert ws.receive_json()["state"]["status"] == "done"


def test_the_websocket_closes_for_an_unknown_query(client, queries_queued):
    with client.websocket_connect(f"/queries/{uuid4()}/progress") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 4404


def test_a_keyframe_picture_redirects_to_storage(client, engine, camera):
    with Session(engine) as session:
        segment = VideoSegment(
            camera_id=camera,
            start_time=T0,
            end_time=T0 + timedelta(seconds=12),
            duration_ms=12_000,
            storage_uri="s3://sentinel/x.mp4",
        )
        session.add(segment)
        session.flush()
        window = AnalysisWindow(
            segment_id=segment.id,
            camera_id=camera,
            start_time=T0,
            end_time=T0 + timedelta(seconds=12),
        )
        session.add(window)
        session.flush()
        keyframe = Keyframe(
            analysis_window_id=window.id,
            timestamp=T0,
            storage_uri=f"s3://sentinel/keyframes/{camera}/{window.id}/1.jpg",
        )
        session.add(keyframe)
        session.commit()
        keyframe_id = keyframe.id
    response = client.get(f"/keyframes/{keyframe_id}/image", follow_redirects=False)
    assert response.status_code == 307 and "X-Amz-Signature" in response.headers["location"]
    assert client.get(f"/keyframes/{uuid4()}/image", follow_redirects=False).status_code == 404


# Query history (step 4.6, ADR 0031)


def test_post_stores_the_request_as_sent(client, queries_queued, camera):
    state = post(client, "a bag on CAM-02", parse_times=False).json()
    assert state["request"] == {
        "query_text": "a bag on CAM-02",
        "camera_ids": None,
        "start": None,
        "end": None,
        "timezone": "UTC",
        "parse_cameras": True,
        "parse_times": False,
    }


def test_the_history_lists_the_newest_first_with_counts(client, queries_queued, finished):
    second = post(client, "a red car").json()["id"]
    third = post(client, "a person runs").json()["id"]
    history = client.get("/queries").json()
    assert history["total"] == 3
    assert [q["id"] for q in history["items"]] == [third, second, str(finished)]
    assert history["items"][2]["counts"]["verified"] == 1
    assert history["items"][2]["counts"]["candidates"] == 3
    assert history["items"][0]["counts"]["candidates"] == 0
    page = client.get("/queries", params={"limit": 1, "offset": 1}).json()
    assert [q["id"] for q in page["items"]] == [second] and page["total"] == 3
    assert client.get("/queries", params={"limit": 0}).status_code == 422


def test_the_history_can_be_searched_by_words_of_the_question(client, queries_queued):
    for text in ("a red car parks", "a person walks", "the CAR leaves", "100% sure_thing"):
        post(client, text)

    def found(q):
        history = client.get("/queries", params={"q": q}).json()
        return sorted(item["query_text"] for item in history["items"]), history["total"]

    assert found("car") == (["a red car parks", "the CAR leaves"], 2)  # any case
    assert found("parks  car") == (["a red car parks"], 1)  # every word, any order
    assert found("bicycle") == ([], 0)
    assert found("100%") == (["100% sure_thing"], 1)
    assert found("_") == (["100% sure_thing"], 1)  # % and _ are plain characters
    assert found("   ")[1] == 4  # blank: everything
    page = client.get("/queries", params={"q": "e", "limit": 1, "offset": 1}).json()
    assert page["total"] == 4 and len(page["items"]) == 1  # "e" is in every question


def test_an_old_query_without_its_request_is_rebuilt_from_the_text(
    client, queries_queued, finished
):
    request = client.get(f"/queries/{finished}").json()["request"]
    assert request["query_text"] == "a bag" and request["parse_cameras"] is True


def test_run_again_reads_relative_times_now(client, queries_queued, engine, camera):
    first = post(client, "a bag on CAM-02 yesterday").json()
    with Session(engine) as session:  # as if it had been asked long ago
        query = session.get(QuerySession, first["id"])
        query.start_filter = datetime(2020, 1, 1, tzinfo=UTC)
        query.end_filter = datetime(2020, 1, 2, tzinfo=UTC)
        session.commit()
    response = client.post(f"/queries/{first['id']}/rerun")
    assert response.status_code == 202
    again = response.json()
    assert again["id"] != first["id"] and again["query_text"] == first["query_text"]
    assert again["start"] == first["start"] and again["end"] == first["end"]
    assert again["camera_ids"] == [str(camera)]
    assert [str(i) for i in queries_queued] == [first["id"], again["id"]]
    assert client.get(f"/queries/{first['id']}").json()["start"].startswith("2020-01-01")


def test_run_again_keeps_chosen_filters_and_removed_chips(client, queries_queued, camera):
    start = "2026-03-07T00:00:00+00:00"
    first = post(
        client, "a bag on CAM-02", camera_ids=[str(camera)], start=start, parse_cameras=False
    ).json()
    again = client.post(f"/queries/{first['id']}/rerun").json()
    assert again["request"] == first["request"]
    assert again["camera_ids"] == [str(camera)] and again["start"].startswith("2026-03-07")
    assert again["search_text"] == "a bag on CAM-02"


def test_run_again_of_an_old_query_and_of_an_unknown_one(client, queries_queued, finished):
    again = client.post(f"/queries/{finished}/rerun")
    assert again.status_code == 202 and again.json()["search_text"] == "a bag"
    assert client.post(f"/queries/{uuid4()}/rerun").status_code == 404


def test_delete_removes_the_query_and_what_it_created(
    client, queries_queued, engine, camera, finished
):
    with Session(engine) as session:
        segment = VideoSegment(
            camera_id=camera,
            start_time=T0,
            end_time=T0 + timedelta(seconds=12),
            duration_ms=12_000,
            storage_uri="s3://sentinel/x.mp4",
        )
        session.add(segment)
        session.flush()
        window = AnalysisWindow(
            segment_id=segment.id,
            camera_id=camera,
            start_time=T0,
            end_time=T0 + timedelta(seconds=12),
        )
        session.add(window)
        session.flush()
        session.add(QueryResult(query_session_id=finished, analysis_window_id=window.id))
        session.commit()
        window_id = window.id
    other = post(client, "a red car").json()["id"]

    assert client.delete(f"/queries/{finished}").status_code == 204
    assert client.get(f"/queries/{finished}").status_code == 404
    assert FakeEvents.notified == [finished]
    with Session(engine) as session:
        assert session.query(QueryEvidence).count() == 0
        assert session.query(QueryResult).count() == 0
        assert session.get(AnalysisWindow, window_id) is not None  # the analysis stays
        assert [str(q.id) for q in session.query(QuerySession)] == [other]
    assert client.delete(f"/queries/{finished}").status_code == 404


def test_the_websocket_closes_when_its_query_is_deleted(client, queries_queued, engine, finished):
    with Session(engine) as session:
        query = session.get(QuerySession, finished)
        query.status, query.stage = "running", "verifying"
        session.commit()
    with client.websocket_connect(f"/queries/{finished}/progress") as ws:
        assert ws.receive_json()["state"]["stage"] == "verifying"
        assert client.delete(f"/queries/{finished}").status_code == 204
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 4404
