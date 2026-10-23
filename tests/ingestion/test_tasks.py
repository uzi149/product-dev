from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.ingestion import tasks
from services.ingestion.tasks import TransientError, process_upload
from shared.config import Settings
from shared.database.models import Camera, StaticPeriod, Upload, VideoSegment
from shared.storage import StorageError
from shared.storage.keys import upload_key
from tests.api.conftest import FFMPEG
from tests.ingestion.videos import activity_video

START = datetime(2026, 3, 7, 9, 0, tzinfo=UTC)

needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")


@pytest.fixture
def camera(session, storage):
    camera = Camera(display_name="Task cam", source_type="file")
    session.add(camera)
    session.commit()
    yield camera
    storage.delete_prefix(f"segments/{camera.id}/")
    storage.delete_prefix(f"static/{camera.id}/")


@pytest.fixture
def staged(session, storage, camera):
    """Stage a file and create its `uploads` row, as the API does."""
    created = []

    def stage(path) -> UUID:
        upload = Upload(id=uuid4(), camera_id=camera.id, filename=path.name, start_time=START)
        storage.upload_file(upload_key(upload.id), path)
        session.add(upload)
        session.commit()
        created.append(upload.id)
        return upload.id

    yield stage
    for upload_id in created:
        storage.delete(upload_key(upload_id))


@pytest.fixture
def run(engine, storage):
    def run_upload(upload_id, storage_client=None):
        process_upload(
            upload_id,
            session_factory=lambda: Session(engine),
            storage=storage_client or storage,
            settings=Settings(_env_file=None),
        )

    return run_upload


def reload(session, upload_id) -> Upload:
    session.expire_all()
    return session.get(Upload, upload_id)


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


@needs_ffmpeg
def test_success_records_the_counts_and_removes_the_staged_file(
    session, storage, staged, run, tmp_path
):
    upload_id = staged(activity_video(tmp_path / "a.avi", 60, [(20, 25)]))
    run(upload_id)

    upload = reload(session, upload_id)
    assert upload.status == "done"
    assert (upload.segments_created, upload.static_periods_created) == (1, 2)
    assert upload.error is None
    assert upload.finished_at is not None
    assert count(session, VideoSegment) == 1
    assert count(session, StaticPeriod) == 2
    assert not storage.exists(upload_key(upload_id))


@needs_ffmpeg
def test_running_a_finished_upload_again_changes_nothing(session, staged, run, tmp_path):
    upload_id = staged(activity_video(tmp_path / "a.avi", 30, [(10, 14)]))
    run(upload_id)
    run(upload_id)
    assert count(session, VideoSegment) == 1
    assert reload(session, upload_id).segments_created == 1


def test_a_file_that_is_not_a_video_fails_with_a_message(session, storage, staged, run, tmp_path):
    bogus = tmp_path / "notes.mp4"
    bogus.write_text("not a video")
    upload_id = staged(bogus)
    run(upload_id)

    upload = reload(session, upload_id)
    assert upload.status == "failed"
    assert "ffprobe could not read the file" in upload.error
    assert upload.finished_at is not None
    assert not storage.exists(upload_key(upload_id))
    assert count(session, VideoSegment) == 0


def test_a_missing_staged_file_fails(session, storage, staged, run, tmp_path):
    upload_id = staged(activity_video(tmp_path / "a.avi", 5, []))
    storage.delete(upload_key(upload_id))
    run(upload_id)
    upload = reload(session, upload_id)
    assert upload.status == "failed"
    assert upload.error == "the uploaded file is no longer in storage"


@needs_ffmpeg
def test_storage_trouble_is_transient_and_keeps_the_staged_file(
    session, storage, staged, run, tmp_path, monkeypatch
):
    upload_id = staged(activity_video(tmp_path / "a.avi", 40, [(20, 25)]))

    def unreachable(*args, **kwargs):
        raise StorageError("connection refused")

    monkeypatch.setattr(storage, "upload_file", unreachable)
    with pytest.raises(TransientError):
        run(upload_id)

    assert reload(session, upload_id).status == "processing"
    assert storage.exists(upload_key(upload_id))  # kept for the retry
    assert count(session, VideoSegment) == 0

    monkeypatch.undo()
    run(upload_id)  # the retry succeeds
    assert reload(session, upload_id).status == "done"


def test_an_unknown_upload_is_ignored(run):
    run(uuid4())


class FakeRequest:
    def __init__(self, retries):
        self.retries = retries


def test_the_task_retries_transient_errors_then_gives_up(monkeypatch):
    calls, failed = [], []

    def flaky(upload_id):
        calls.append(upload_id)
        raise TransientError("MinIO is down")

    monkeypatch.setattr(tasks, "process_upload", flaky)
    monkeypatch.setattr(tasks, "mark_failed", lambda upload_id, message: failed.append(message))
    upload_id = str(uuid4())

    # Celery's eager mode runs the retries straight away, without the countdown.
    tasks.ingest_upload.apply(args=[upload_id])

    assert len(calls) == tasks.MAX_RETRIES + 1
    assert failed == ["gave up after 2 retries: MinIO is down"]


def test_the_task_is_registered_under_the_name_the_api_sends():
    from shared.jobs.celery_app import INGEST_UPLOAD, celery_app

    assert celery_app.tasks[INGEST_UPLOAD].name == tasks.ingest_upload.name == INGEST_UPLOAD
    # The worker imports the task module itself (`celery -A shared.jobs.celery_app worker`).
    assert tasks.__name__ in celery_app.conf.include
