"""An upload that overlaps footage already on its camera is refused (step 5.6, ADR 0037)."""

import threading
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from shared.database.models import (
    Camera,
    ProcessingStatus,
    StaticPeriod,
    Upload,
    VideoSegment,
)
from tests.api.test_upload import count, needs_ffmpeg, run_worker, upload

pytestmark = needs_ffmpeg

T0 = datetime(2026, 3, 7, 9, 5, tzinfo=UTC)


def at(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


@pytest.fixture
def camera_id(client):
    return client.post("/cameras", json={"display_name": "Lobby", "source_type": "file"}).json()[
        "id"
    ]


@pytest.fixture
def clip(tmp_path):
    """40 s with activity at 20-25 s (the same clip as test_upload.py)."""
    from tests.ingestion.videos import activity_video

    return activity_video(tmp_path / "clip.avi", 40, [(20, 25)])


def check(client, camera_id, start, end=None):
    params = {"camera_id": camera_id, "start": start}
    if end is not None:
        params["end"] = end
    return client.get("/videos/overlap", params=params)


def test_an_upload_over_a_pending_one_is_refused_with_the_conflict(
    client, camera_id, clip, engine, storage, queued
):
    first = upload(client, clip, camera_id, at(0), name="video9.avi").json()
    response = upload(client, clip, camera_id, at(30), name="LeftBag_PickedUp.mpg")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["message"].startswith("Lobby already has footage at this time")
    assert detail["conflicts"] == [
        {"start": "2026-03-07T09:05:00Z", "end": "2026-03-07T09:05:40Z", "filename": "video9.avi"}
    ]
    assert count(engine, Upload) == 1
    assert queued == [UUID(first["id"])]


def test_the_same_start_after_processing_is_refused_once_not_per_stored_part(
    client, camera_id, clip, engine, storage
):
    first = upload(client, clip, camera_id, at(0), name="video9.avi").json()
    run_worker(engine, storage, first["id"])  # 1 segment + 2 static periods inside its span

    response = upload(client, clip, camera_id, at(0))
    assert response.status_code == 409
    assert [c["filename"] for c in response.json()["detail"]["conflicts"]] == ["video9.avi"]


def test_touching_spans_and_other_cameras_are_accepted(client, camera_id, clip):
    assert upload(client, clip, camera_id, at(0)).status_code == 202
    assert upload(client, clip, camera_id, at(40)).status_code == 202  # starts as the first ends
    assert upload(client, clip, camera_id, at(-40)).status_code == 202  # ends as the first starts
    other = client.post("/cameras", json={"display_name": "Hall", "source_type": "file"}).json()
    assert upload(client, clip, other["id"], at(0)).status_code == 202


def test_a_clip_that_runs_into_later_footage_is_refused(client, camera_id, clip):
    assert upload(client, clip, camera_id, at(0)).status_code == 202
    # Starts before the first clip, but its 40 s reach into it.
    response = upload(client, clip, camera_id, at(-30))
    assert response.status_code == 409


def test_a_failed_upload_does_not_block(client, camera_id, clip, engine):
    first = upload(client, clip, camera_id, at(0)).json()
    with Session(engine) as session:
        session.execute(
            update(Upload)
            .where(Upload.id == UUID(first["id"]))
            .values(status=ProcessingStatus.FAILED)
        )
        session.commit()
    assert upload(client, clip, camera_id, at(0)).status_code == 202


def test_live_footage_blocks_through_segments_and_static_periods(client, camera_id, clip, engine):
    with Session(engine) as session:
        cam = UUID(camera_id)
        session.add_all(
            [
                VideoSegment(
                    camera_id=cam,
                    start_time=T0,
                    end_time=T0 + timedelta(seconds=30),
                    duration_ms=30_000,
                    storage_uri="s3://sentinel/segments/x.mp4",
                ),
                StaticPeriod(  # cut 13 ms after the segment ends, as ingestion does
                    camera_id=cam,
                    start_time=T0 + timedelta(seconds=30.013),
                    end_time=T0 + timedelta(seconds=90),
                ),
            ]
        )
        session.commit()

    response = upload(client, clip, camera_id, at(-20))
    assert response.status_code == 409
    # The segment and the static period after it are one stretch of footage.
    assert response.json()["detail"]["conflicts"] == [
        {"start": "2026-03-07T09:05:00Z", "end": "2026-03-07T09:06:30Z", "filename": None}
    ]
    assert upload(client, clip, camera_id, at(90)).status_code == 202


def test_an_upload_from_before_the_length_was_kept_blocks_through_its_footage(
    client, camera_id, clip, engine, storage
):
    first = upload(client, clip, camera_id, at(0)).json()
    run_worker(engine, storage, first["id"])
    with Session(engine) as session:
        session.execute(update(Upload).values(duration_ms=None))
        session.commit()

    response = upload(client, clip, camera_id, at(5))
    assert response.status_code == 409
    conflicts = response.json()["detail"]["conflicts"]
    assert [c["filename"] for c in conflicts] == [None]
    assert conflicts[0]["start"] == "2026-03-07T09:05:00Z"


def test_a_refused_upload_stages_nothing(client, camera_id, clip, storage, engine, monkeypatch):
    upload(client, clip, camera_id, at(0))
    staged: list[str] = []
    monkeypatch.setattr(storage, "upload_file", lambda key, *a, **k: staged.append(key))
    assert upload(client, clip, camera_id, at(10)).status_code == 409
    assert staged == []


def test_uploads_sent_together_cannot_both_pass(client, camera_id, clip, engine):
    """While one upload holds the camera's lock and inserts its row, the other waits for it and
    is then refused."""
    results: list[int] = []
    with Session(engine) as holder:
        holder.scalars(select(Camera).where(Camera.id == UUID(camera_id)).with_for_update()).one()
        sender = threading.Thread(
            target=lambda: results.append(upload(client, clip, camera_id, at(0)).status_code)
        )
        sender.start()
        sender.join(timeout=3)
        assert sender.is_alive(), "the second upload should wait for the camera's lock"
        holder.add(
            Upload(
                camera_id=UUID(camera_id),
                filename="first.avi",
                start_time=T0,
                duration_ms=40_000,
                status=ProcessingStatus.PENDING,
            )
        )
        holder.commit()
    sender.join(timeout=60)
    assert results == [409]


def test_the_form_check_lists_footage_over_a_span_or_at_an_instant(client, camera_id, clip):
    upload(client, clip, camera_id, at(0), name="video9.avi")

    inside = check(client, camera_id, at(10)).json()
    assert [c["filename"] for c in inside] == ["video9.avi"]
    assert check(client, camera_id, at(40)).json() == []  # the instant it ends is free
    assert check(client, camera_id, at(-60)).json() == []  # before it, length unknown
    assert len(check(client, camera_id, at(-60), at(20)).json()) == 1  # reaches into it
    assert check(client, camera_id, at(-60), at(0)).json() == []


def test_the_form_check_validates_its_input(client, camera_id):
    missing = "00000000-0000-0000-0000-000000000000"
    assert check(client, missing, at(0)).status_code == 404
    assert check(client, camera_id, "2026-03-07T09:05:00").status_code == 422
    assert check(client, camera_id, at(10), at(0)).status_code == 422
