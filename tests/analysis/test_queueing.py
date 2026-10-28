"""New segments are queued for analysis; ingestion never fails because of the queue (ADR 0022)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from services.analysis import requeue
from services.ingestion.outbox import Outbox, Uploader
from services.ingestion.tasks import process_upload
from shared.config import Settings
from shared.database.models import Camera, Upload, VideoSegment
from shared.jobs import celery_app as jobs
from shared.storage.keys import upload_key
from tests.api.conftest import FFMPEG, make_video
from tests.ingestion.videos import activity_video

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def broker_down(segment_id):
    raise ConnectionError("Redis is down (test)")


def test_new_segments_are_queued(analysis_queue):
    ids = [uuid4(), uuid4()]
    assert jobs.request_analysis(ids, Settings(_env_file=None)) == 2
    assert analysis_queue == ids


def test_nothing_is_queued_when_analysis_is_off(analysis_queue):
    settings = Settings(_env_file=None, analysis_enabled=False)
    assert jobs.request_analysis([uuid4()], settings) == 0
    assert analysis_queue == []


def test_a_broker_that_is_down_is_logged_not_raised(monkeypatch, caplog):
    monkeypatch.setattr(jobs, "queue_analyze_segment", broker_down)
    assert jobs.request_analysis([uuid4()], Settings(_env_file=None)) == 0
    assert "could not queue the analysis" in caplog.text


@pytest.fixture
def camera(session, storage):
    camera = Camera(display_name="Queue cam", source_type="file")
    session.add(camera)
    session.commit()
    yield camera
    for prefix in ("segments", "static"):
        storage.delete_prefix(f"{prefix}/{camera.id}/")


def test_a_stored_live_segment_is_handed_on_once(camera, storage, engine, tmp_path):
    if FFMPEG is None:
        pytest.skip("ffmpeg is not installed")
    fragments = [
        make_video(tmp_path / f"frag-{i:06d}.ts", extra=("-pix_fmt", "yuv420p", "-f", "mpegts"))
        for i in range(2)
    ]
    outbox = Outbox(tmp_path / "outbox")
    outbox.add_segment(camera.id, fragments, NOW, 0.03)
    stored = []
    uploader = Uploader(outbox, storage, lambda: Session(engine), on_segment_stored=stored.append)
    assert uploader.run_once()
    with Session(engine) as session:
        segment = session.query(VideoSegment).one()
    assert stored == [segment.id]


def test_an_uploads_segments_are_queued(camera, session, storage, engine, tmp_path, analysis_queue):
    path = activity_video(tmp_path / "two.avi", 60, [(10, 15), (40, 45)])
    upload = Upload(id=uuid4(), camera_id=camera.id, filename=path.name, start_time=NOW)
    storage.upload_file(upload_key(upload.id), path)
    session.add(upload)
    session.commit()
    process_upload(upload.id, lambda: Session(engine), storage, Settings(_env_file=None))
    ids = {s.id for s in session.query(VideoSegment).filter_by(camera_id=camera.id)}
    assert len(ids) == 2 and set(analysis_queue) == ids


def test_an_upload_is_stored_even_when_the_queue_is_down(
    camera, session, storage, engine, tmp_path, monkeypatch
):
    monkeypatch.setattr(jobs, "queue_analyze_segment", broker_down)
    path = activity_video(tmp_path / "one.avi", 30, [(10, 15)])
    upload = Upload(id=uuid4(), camera_id=camera.id, filename=path.name, start_time=NOW)
    storage.upload_file(upload_key(upload.id), path)
    session.add(upload)
    session.commit()
    process_upload(upload.id, lambda: Session(engine), storage, Settings(_env_file=None))
    session.expire_all()
    assert session.get(Upload, upload.id).status == "done"
    segment = session.query(VideoSegment).filter_by(camera_id=camera.id).one()
    assert segment.status == "pending"  # the sweep or the requeue command picks it up


@pytest.fixture
def segments(session, camera):
    """Segments 1 h, 5 h and 30 h old, in different states."""
    other = Camera(display_name="Other cam", source_type="file")
    session.add(other)
    session.flush()

    def add(hours, status, cam=camera):
        start = NOW - timedelta(hours=hours)
        segment = VideoSegment(
            camera_id=cam.id,
            start_time=start,
            end_time=start + timedelta(seconds=30),
            duration_ms=30_000,
            storage_uri="s3://sentinel/x.mp4",
            status=status,
        )
        session.add(segment)
        return segment

    made = {
        "recent": add(1, "pending"),
        "older": add(5, "pending"),
        "oldest": add(30, "pending"),
        "failed": add(2, "failed"),
        "done": add(1, "done"),
        "other": add(1, "pending", other),
    }
    session.commit()
    return made


def test_requeue_finds_segments_by_status_camera_and_age(session, segments):
    def ids(*names):
        return [segments[n].id for n in names]

    def found(**kw):
        return requeue.find_segments(session, now=NOW, **kw)

    # "recent" and "other" start at the same time, so only the set is fixed.
    assert set(found(status="pending")) == set(ids("oldest", "older", "recent", "other"))
    assert found(status="pending", camera="Queue cam") == ids("oldest", "older", "recent")
    assert found(status="pending", camera="Queue cam", since=timedelta(hours=6)) == ids(
        "older", "recent"
    )
    assert found(status="failed") == ids("failed")
    with pytest.raises(LookupError):
        found(status="pending", camera="Nope")


@pytest.mark.parametrize(
    ("text", "age"),
    [
        ("90s", timedelta(seconds=90)),
        ("30m", timedelta(minutes=30)),
        ("2h", timedelta(hours=2)),
        ("1d", timedelta(days=1)),
    ],
)
def test_ages(text, age):
    assert requeue.parse_age(text) == age


def test_a_bad_age_is_refused():
    with pytest.raises(Exception, match="not an age"):
        requeue.parse_age("yesterday")


def test_the_requeue_command_queues_even_when_analysis_is_off(
    monkeypatch, engine, segments, analysis_queue, capsys
):
    monkeypatch.setenv("ANALYSIS_ENABLED", "false")
    monkeypatch.setattr(requeue, "get_sessionmaker", lambda: lambda: Session(engine))
    assert requeue.main(["--status", "failed"]) == 0
    assert analysis_queue == [segments["failed"].id]
    assert "queued 1 segment(s)" in capsys.readouterr().out


def test_requeue_of_one_segment(monkeypatch, engine, segments, analysis_queue, capsys):
    monkeypatch.setattr(requeue, "get_sessionmaker", lambda: lambda: Session(engine))
    assert requeue.main(["--segment", str(segments["done"].id)]) == 0
    assert analysis_queue == [] and "already analysed" in capsys.readouterr().out
    assert requeue.main(["--segment", str(segments["oldest"].id)]) == 0
    assert analysis_queue == [segments["oldest"].id]
    assert requeue.main(["--segment", str(uuid4())]) == 2
