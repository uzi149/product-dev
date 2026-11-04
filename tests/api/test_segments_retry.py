"""Analysing segments again from the UI (step 3.5, ADR 0024 and 0025)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from shared.config import get_settings
from shared.database.models import Camera, VideoSegment
from shared.jobs import celery_app as jobs

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def seeded(engine):
    """cam1: failed segments at 12:00 and 12:20, stuck at 12:05, running at 12:10, done at 12:15,
    pending at 12:25. cam2: one failed segment."""
    now = datetime.now(UTC)
    with Session(engine) as session:
        cam1 = Camera(display_name="cam1", source_type="file")
        cam2 = Camera(display_name="cam2", source_type="file")
        session.add_all([cam1, cam2])
        session.flush()
        made = {}
        for name, camera, minutes, status, changed in (
            ("failed", cam1, 0, "failed", 60),
            ("stuck", cam1, 5, "processing", 20),
            ("running", cam1, 10, "processing", 1),
            ("done", cam1, 15, "done", 60),
            ("failed_late", cam1, 20, "failed", 60),
            ("pending", cam1, 25, "pending", 60),
            ("other", cam2, 0, "failed", 60),
        ):
            start = T0 + timedelta(minutes=minutes)
            segment = VideoSegment(
                camera_id=camera.id,
                start_time=start,
                end_time=start + timedelta(seconds=60),
                duration_ms=60_000,
                storage_uri=f"s3://sentinel/segments/{camera.id}/x.mp4",
                status=status,
                status_changed_at=now - timedelta(minutes=changed),
            )
            session.add(segment)
            made[name] = segment
        session.commit()
        return {"cam1": str(cam1.id), "cam2": str(cam2.id)} | {
            name: str(s.id) for name, s in made.items()
        }


def status_of(engine, segment_id) -> str:
    with Session(engine) as session:
        return session.get(VideoSegment, segment_id).status


def test_each_row_says_whether_it_can_be_retried(client, seeded):
    rows = client.get("/segments", params={"camera_id": seeded["cam1"]}).json()
    retryable = {r["id"] for r in rows if r["retryable"]}
    assert retryable == {seeded["failed"], seeded["stuck"], seeded["failed_late"]}
    assert client.get(f"/segments/{seeded['stuck']}").json()["retryable"] is True


def test_a_failed_segment_is_queued_again(client, seeded, engine, analysis_queue):
    response = client.post(f"/segments/{seeded['failed']}/reanalyse")
    assert response.status_code == 202 and response.json() == {"queued": 1}
    assert [str(i) for i in analysis_queue] == [seeded["failed"]]
    assert status_of(engine, seeded["failed"]) == "pending"


def test_a_stuck_segment_is_queued_again(client, seeded, engine, analysis_queue):
    assert client.post(f"/segments/{seeded['stuck']}/reanalyse").status_code == 202
    assert status_of(engine, seeded["stuck"]) == "pending"


@pytest.mark.parametrize(
    ("name", "reason"),
    [("running", "being analysed"), ("done", "already analysed"), ("pending", "waiting")],
)
def test_other_segments_are_refused_with_the_reason(client, seeded, analysis_queue, name, reason):
    response = client.post(f"/segments/{seeded[name]}/reanalyse")
    assert response.status_code == 409 and reason in response.json()["detail"]
    assert analysis_queue == []


def test_an_unknown_segment_is_404(client, seeded):
    assert client.post(f"/segments/{uuid4()}/reanalyse").status_code == 404


def test_a_queue_that_is_down_is_503_and_changes_nothing(client, seeded, engine, monkeypatch):
    def broker_down(segment_id):
        raise ConnectionError("Redis is down (test)")

    monkeypatch.setattr(jobs, "queue_analyze_segment", broker_down)
    response = client.post(f"/segments/{seeded['failed']}/reanalyse")
    assert response.status_code == 503 and "Redis" in response.json()["detail"]
    assert status_of(engine, seeded["failed"]) == "failed"


def test_retry_failed_covers_only_the_cameras_failed_segments_in_range(
    client, seeded, engine, analysis_queue
):
    body = {
        "camera_id": seeded["cam1"],
        "start": (T0 + timedelta(minutes=2)).isoformat(),
        "end": (T0 + timedelta(minutes=30)).isoformat(),
    }
    response = client.post("/segments/reanalyse", json=body)
    assert response.status_code == 202 and response.json() == {"queued": 1}
    assert [str(i) for i in analysis_queue] == [seeded["failed_late"]]
    assert status_of(engine, seeded["failed"]) == "failed"  # before the range
    assert status_of(engine, seeded["other"]) == "failed"  # other camera
    assert status_of(engine, seeded["stuck"]) == "processing"  # one at a time

    response = client.post("/segments/reanalyse", json={"camera_id": seeded["cam1"]})
    assert response.json() == {"queued": 1}  # the 12:00 one; 12:20 is pending now


def test_retry_failed_needs_a_known_camera_and_timezones(client, seeded):
    assert client.post("/segments/reanalyse", json={"camera_id": str(uuid4())}).status_code == 404
    naive = {"camera_id": seeded["cam1"], "start": "2026-01-01T12:00:00"}
    assert client.post("/segments/reanalyse", json=naive).status_code == 422


def test_status_counts_per_camera_and_range(client, seeded):
    counts = client.get("/segments/status-counts", params={"camera_id": seeded["cam1"]}).json()
    assert counts == {"pending": 1, "processing": 1, "stuck": 1, "done": 1, "failed": 2}
    later = client.get(
        "/segments/status-counts",
        params={"camera_id": seeded["cam1"], "start": (T0 + timedelta(minutes=12)).isoformat()},
    ).json()
    assert later == {"pending": 1, "processing": 0, "stuck": 0, "done": 1, "failed": 1}


def test_health_names_the_vlm_backend_without_a_key(client, monkeypatch):
    monkeypatch.setenv("VLM_BACKEND", "api")
    monkeypatch.setenv("VLM_API_KEY", "secret-key")
    get_settings.cache_clear()
    body = client.get("/health").json()
    assert body["vlm_backend"] == {"kind": "api", "host": "openrouter.ai"}
    assert "secret-key" not in str(body)

    monkeypatch.setenv("VLM_BACKEND", "local")
    get_settings.cache_clear()
    assert client.get("/health").json()["vlm_backend"] == {"kind": "local", "host": None}
