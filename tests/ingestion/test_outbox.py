"""Outbox + uploader: footage waits on disk while MinIO or PostgreSQL is down, and is stored once."""

import json
import shutil
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.ingestion.outbox import ITEM_FILE, Outbox, Uploader
from shared.database.models import Camera, HealthStatus, StaticPeriod, VideoSegment
from shared.storage import StorageError
from tests.api.conftest import FFMPEG, make_video

START = datetime(2026, 3, 7, 9, 0, tzinfo=UTC)
JPEG = b"\xff\xd8\xff\xe0 not really a jpeg"


@pytest.fixture
def outbox(tmp_path):
    return Outbox(tmp_path / "outbox")


@pytest.fixture
def camera(session, storage):
    camera = Camera(display_name="Outbox cam", source_type="rtsp")
    session.add(camera)
    session.commit()
    yield camera
    storage.delete_prefix(f"segments/{camera.id}/")
    storage.delete_prefix(f"static/{camera.id}/")


@pytest.fixture
def uploader(outbox, storage, engine):
    return Uploader(outbox, storage, lambda: Session(engine))


class DownStorage:
    """MinIO that can't be reached."""

    def upload_file(self, *args, **kwargs):
        raise StorageError("connection refused (test)")

    upload_bytes = upload_file


def count(engine, model) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def fragments(tmp_path, n=2):
    if FFMPEG is None:
        pytest.skip("ffmpeg is not installed")
    paths = []
    for i in range(n):
        paths.append(
            make_video(tmp_path / f"frag-{i:06d}.ts", extra=("-pix_fmt", "yuv420p", "-f", "mpegts"))
        )
    return paths


def test_items_are_written_complete_and_listed_oldest_first(outbox, tmp_path):
    first = outbox.add_static("c1", START, START + timedelta(seconds=5), HealthStatus.OK, JPEG)
    second = outbox.add_static("c1", START, START, HealthStatus.OFFLINE, None)
    assert outbox.pending() == [first, second]
    data = json.loads((first / ITEM_FILE).read_text())
    assert data["kind"] == "static" and data["reference_frame"] == "reference.jpg"
    assert (first / "reference.jpg").read_bytes() == JPEG


def test_folders_without_item_json_are_ignored_and_removed_at_start(outbox):
    item = outbox.add_static("c1", START, START, HealthStatus.OK, None)
    unfinished = outbox.folder / "00000000000000000000-000000-segment"
    unfinished.mkdir()
    (unfinished / "frag-000001.ts").write_bytes(b"x")
    assert outbox.pending() == [item]
    outbox.remove_unfinished()
    assert not unfinished.exists() and item.exists()


def test_a_segment_is_moved_in_joined_uploaded_and_stored(
    outbox, uploader, camera, engine, storage, tmp_path
):
    paths = fragments(tmp_path)
    outbox.add_segment(camera.id, paths, START, 0.03)
    assert not any(p.exists() for p in paths)  # moved, not copied

    assert uploader.run_once()
    assert outbox.pending() == []
    with Session(engine) as session:
        segment = session.scalars(select(VideoSegment)).one()
    assert segment.start_time == START
    assert segment.duration_ms == pytest.approx(6000, abs=250)  # two 3 s fragments
    assert (segment.codec, segment.activity_score) == ("h264", 0.03)
    assert storage.exists(storage.key_from_uri(segment.storage_uri))


def test_footage_waits_while_storage_is_down_and_is_stored_once_back(
    outbox, camera, engine, storage
):
    outbox.add_static(camera.id, START, START + timedelta(seconds=30), HealthStatus.OK, JPEG)
    down = Uploader(outbox, DownStorage(), lambda: Session(engine))
    assert not down.run_once()
    assert not down.run_once()
    assert len(outbox.pending()) == 1
    assert count(engine, StaticPeriod) == 0

    assert Uploader(outbox, storage, lambda: Session(engine)).run_once()
    assert outbox.pending() == []
    assert count(engine, StaticPeriod) == 1


def test_an_item_stored_before_is_not_stored_twice(outbox, uploader, camera, engine):
    item = outbox.add_static(camera.id, START, START + timedelta(seconds=9), HealthStatus.OK, None)
    copy = outbox.folder / (item.name + "-again")
    shutil.copytree(item, copy)  # e.g. a crash after the commit, before the folder was removed
    assert uploader.run_once()
    assert outbox.pending() == []
    assert count(engine, StaticPeriod) == 1


def test_offline_periods_have_no_reference_frame(outbox, uploader, camera, engine):
    outbox.add_static(camera.id, START, START + timedelta(minutes=2), HealthStatus.OFFLINE, None)
    assert uploader.run_once()
    with Session(engine) as session:
        period = session.scalars(select(StaticPeriod)).one()
    assert (period.health_status, period.reference_frame_uri) == ("offline", None)


def test_footage_of_a_deleted_camera_is_dropped(outbox, uploader, camera, session, engine):
    outbox.add_static(camera.id, START, START + timedelta(seconds=9), HealthStatus.OK, None)
    session.delete(camera)
    session.commit()
    assert uploader.run_once()
    assert outbox.pending() == []
    assert count(engine, StaticPeriod) == 0


def test_damaged_footage_is_set_aside_instead_of_retried(
    outbox, uploader, camera, engine, tmp_path
):
    bad = tmp_path / "frag-000001.ts"
    bad.write_bytes(b"not a video")
    outbox.add_segment(camera.id, [bad], START, 0.0)
    assert uploader.run_once()
    assert outbox.pending() == []
    assert len(list(outbox.failed_folder.iterdir())) == 1
    assert count(engine, VideoSegment) == 0


def test_drain_stores_everything_pending(outbox, uploader, camera):
    for n in range(3):
        outbox.add_static(camera.id, START, START + timedelta(seconds=n + 2), HealthStatus.OK, None)
    assert uploader.drain(timeout=30)
    assert outbox.pending() == []
