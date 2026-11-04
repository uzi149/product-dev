"""Retrying failed and stuck segments (step 3.5, ADR 0024), shared by the API and requeue."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from services.analysis import requeue
from services.analysis.retry import QueueUnavailable, retry_segments
from shared.database.models import Camera, VideoSegment

# Near the real time: the requeue command reads the clock (the margins are minutes).
NOW = datetime.now(UTC).replace(microsecond=0)
AFTER = timedelta(minutes=15)


@pytest.fixture
def segments(session):
    camera = Camera(display_name="Retry cam", source_type="file")
    session.add(camera)
    session.flush()

    def add(status, changed_minutes_ago, start_minutes_ago):
        start = NOW - timedelta(minutes=start_minutes_ago)
        segment = VideoSegment(
            camera_id=camera.id,
            start_time=start,
            end_time=start + timedelta(seconds=30),
            duration_ms=30_000,
            storage_uri="s3://sentinel/x.mp4",
            status=status,
            status_changed_at=NOW - timedelta(minutes=changed_minutes_ago),
        )
        session.add(segment)
        return segment

    made = {
        "failed": add("failed", 60, 60),
        "stuck": add("processing", 20, 50),
        "running": add("processing", 5, 40),
        "pending": add("pending", 30, 30),
        "done": add("done", 30, 20),
    }
    session.commit()
    return made


def fresh(session, segment) -> VideoSegment:
    session.expire_all()
    return session.get(VideoSegment, segment.id)


def test_failed_and_stuck_segments_go_back_to_pending_and_are_queued(session, segments):
    queued = []
    ids = [s.id for s in segments.values()]
    result = retry_segments(session, ids, queued.append, now=NOW, after=AFTER)

    assert queued == result.queued == [segments["failed"].id, segments["stuck"].id]
    for name in ("failed", "stuck"):
        segment = fresh(session, segments[name])
        assert segment.status == "pending" and segment.status_changed_at == NOW
    assert result.skipped == {
        segments["running"].id: "being analysed",
        segments["pending"].id: "waiting in the queue",
        segments["done"].id: "already analysed",
    }
    assert fresh(session, segments["running"]).status == "processing"


def test_pending_segments_are_queued_only_on_request(session, segments):
    queued = []
    result = retry_segments(
        session, [segments["pending"].id], queued.append, now=NOW, after=AFTER, include_pending=True
    )
    assert queued == result.queued == [segments["pending"].id]


def test_an_unknown_segment_is_skipped(session, segments):
    missing = uuid4()
    result = retry_segments(session, [missing], [].append, now=NOW, after=AFTER)
    assert result.queued == [] and result.skipped == {missing: "not found"}


def test_a_broker_that_is_down_restores_the_segments_not_queued(session, segments):
    queued = []

    def queue(segment_id):
        if queued:
            raise ConnectionError("Redis is down (test)")
        queued.append(segment_id)

    ids = [segments["failed"].id, segments["stuck"].id]
    with pytest.raises(QueueUnavailable, match="Redis is down"):
        retry_segments(session, ids, queue, now=NOW, after=AFTER)

    assert queued == [segments["failed"].id]
    assert fresh(session, segments["failed"]).status == "pending"
    stuck = fresh(session, segments["stuck"])
    assert stuck.status == "processing"
    assert stuck.status_changed_at == NOW - timedelta(minutes=20)


@pytest.fixture
def command(monkeypatch, engine, segments):
    monkeypatch.setattr(requeue, "get_sessionmaker", lambda: lambda: Session(engine))
    return requeue.main


def test_the_requeue_command_retries_stuck_segments(command, segments, analysis_queue, capsys):
    assert command(["--status", "stuck"]) == 0
    assert analysis_queue == [segments["stuck"].id]
    assert "queued 1 segment(s)" in capsys.readouterr().out


def test_find_segments_finds_stuck_ones(session, segments):
    found = requeue.find_segments(session, "stuck", now=NOW, after=AFTER)
    assert found == [segments["stuck"].id]


def test_the_requeue_command_skips_a_segment_being_analysed(
    command, segments, analysis_queue, capsys
):
    assert command(["--segment", str(segments["running"].id)]) == 0
    assert analysis_queue == []
    assert "being analysed" in capsys.readouterr().out
