"""Deleting a camera with all its data (step 3.6, ADR 0026)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.camera_status import CameraStatus, HeartbeatLock, LiveState, write_status
from shared.database.models import EMBEDDING_DIM as DIM
from shared.database.models import (
    AnalysisWindow,
    Camera,
    Event,
    EventEvidence,
    Keyframe,
    StaticPeriod,
    TextEmbedding,
    Trigger,
    TriggerCamera,
    Upload,
    VideoSegment,
)
from shared.storage.keys import (
    keyframe_key,
    segment_key,
    snapshot_key,
    static_frame_key,
    upload_key,
)

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def full_camera(engine, storage):
    """A file camera with a segment, an analysed window (keyframe + text vector), a static period,
    a finished upload, a trigger assignment and a file under every prefix; plus another camera."""
    with Session(engine) as session:
        camera = Camera(display_name="Doomed", source_type="file")
        other = Camera(display_name="Kept", source_type="file")
        session.add_all([camera, other])
        session.flush()
        files = []

        def put(key):
            storage.upload_bytes(key, b"x", "application/octet-stream")
            files.append(key)
            return storage.to_uri(key)

        def segment(cam):
            segment_id = uuid4()
            row = VideoSegment(
                id=segment_id,
                camera_id=cam.id,
                start_time=T0,
                end_time=T0 + timedelta(seconds=30),
                duration_ms=30_000,
                storage_uri=put(segment_key(cam.id, T0, segment_id)),
                status="done",
            )
            session.add(row)
            session.flush()
            return row

        kept_segment = segment(other)
        kept_file = files.pop()
        seg = segment(camera)
        window = AnalysisWindow(
            segment_id=seg.id,
            camera_id=camera.id,
            start_time=T0,
            end_time=T0 + timedelta(seconds=12),
            processing_status="done",
        )
        session.add(window)
        session.flush()
        session.add(
            Keyframe(
                analysis_window_id=window.id,
                timestamp=T0,
                storage_uri=put(keyframe_key(camera.id, window.id, 1)),
                visual_embedding=[0.0] * DIM,
            )
        )
        session.add(
            TextEmbedding(analysis_window_id=window.id, embedding=[0.0] * DIM, source_text="x")
        )
        period_id = uuid4()
        session.add(
            StaticPeriod(
                id=period_id,
                camera_id=camera.id,
                start_time=T0 - timedelta(minutes=5),
                end_time=T0,
                reference_frame_uri=put(static_frame_key(camera.id, period_id)),
            )
        )
        upload = Upload(id=uuid4(), camera_id=camera.id, filename="a.mp4", start_time=T0)
        upload.status = "done"
        session.add(upload)
        put(upload_key(upload.id))
        put(snapshot_key(camera.id))
        trigger = Trigger(name="t", natural_language_rule="someone enters")
        session.add(trigger)
        session.flush()
        session.add(TriggerCamera(trigger_id=trigger.id, camera_id=camera.id))
        session.commit()
        yield {
            "id": str(camera.id),
            "other": str(other.id),
            "kept_segment": kept_segment.id,
            "upload": upload.id,
            "trigger": trigger.id,
            "files": files,
            "kept_file": kept_file,
        }
        storage.delete_prefix(f"segments/{other.id}/")


def count(engine, model, **where) -> int:
    with Session(engine) as session:
        query = select(func.count()).select_from(model)
        for name, value in where.items():
            query = query.where(getattr(model, name) == value)
        return session.scalar(query)


def test_the_preview_counts_what_goes(client, full_camera):
    preview = client.get(f"/cameras/{full_camera['id']}/delete-preview").json()
    assert preview == {
        "segments": 1,
        "static_periods": 1,
        "analysed_windows": 1,
        "uploads": 1,
        "blocker": None,
    }


def test_deleting_removes_every_row_and_file_of_the_camera(
    client, full_camera, engine, storage, fake_redis
):
    camera_id = UUID(full_camera["id"])
    write_status(fake_redis, camera_id, CameraStatus(state=LiveState.OFFLINE), ttl=30)
    assert client.delete(f"/cameras/{camera_id}").status_code == 204

    assert client.get(f"/cameras/{camera_id}").status_code == 404
    for model in (VideoSegment, StaticPeriod, AnalysisWindow, Upload):
        assert count(engine, model, camera_id=camera_id) == 0, model
    assert count(engine, Keyframe) == 0 and count(engine, TextEmbedding) == 0
    assert count(engine, TriggerCamera) == 0 and count(engine, Trigger) == 1
    assert not any(storage.exists(key) for key in full_camera["files"]), "files left"
    assert fake_redis.data == {}

    # The other camera and its footage are untouched.
    assert client.get(f"/cameras/{full_camera['other']}").status_code == 200
    assert count(engine, VideoSegment, id=full_camera["kept_segment"]) == 1
    assert storage.exists(full_camera["kept_file"])


def test_an_unknown_camera_is_404(client):
    assert client.delete(f"/cameras/{uuid4()}").status_code == 404
    assert client.get(f"/cameras/{uuid4()}/delete-preview").status_code == 404


def test_a_camera_with_events_is_refused(client, full_camera, engine):
    with Session(engine) as session:
        segment = session.scalars(
            select(VideoSegment).where(VideoSegment.camera_id == UUID(full_camera["id"]))
        ).one()
        event = Event(
            trigger_id=full_camera["trigger"],
            camera_id=segment.camera_id,
            start_time=T0,
            end_time=T0 + timedelta(seconds=10),
        )
        session.add(event)
        session.flush()
        session.add(
            EventEvidence(
                event_id=event.id, segment_id=segment.id, start_offset_ms=0, end_offset_ms=10_000
            )
        )
        session.commit()
    response = client.delete(f"/cameras/{full_camera['id']}")
    assert response.status_code == 409 and "events" in response.json()["detail"]
    assert count(engine, Camera, id=UUID(full_camera["id"])) == 1


def test_an_upload_being_processed_blocks_deletion(client, full_camera, engine):
    with Session(engine) as session:
        session.get(Upload, full_camera["upload"]).status = "processing"
        session.commit()
    response = client.delete(f"/cameras/{full_camera['id']}")
    assert response.status_code == 409 and "upload" in response.json()["detail"]
    preview = client.get(f"/cameras/{full_camera['id']}/delete-preview").json()
    assert "upload" in preview["blocker"]


def rtsp_camera(client, enabled: bool) -> str:
    body = {
        "display_name": f"live-{uuid4().hex[:6]}",
        "source_type": "rtsp",
        "source_uri": "rtsp://user:pw@127.0.0.1:8554/cam",
        "enabled": enabled,
    }
    return client.post("/cameras", json=body).json()["id"]


def test_a_recording_camera_must_be_disabled_first(client, fake_redis):
    camera_id = rtsp_camera(client, enabled=True)
    response = client.delete(f"/cameras/{camera_id}")
    assert response.status_code == 409 and "Disable it first" in response.json()["detail"]

    # Disabled, but live ingestion hasn't stopped it yet (it reloads every 30 s).
    client.patch(f"/cameras/{camera_id}", json={"enabled": False})
    HeartbeatLock(fake_redis, ttl=30).acquire()
    write_status(fake_redis, UUID(camera_id), CameraStatus(state=LiveState.ONLINE), ttl=30)
    response = client.delete(f"/cameras/{camera_id}")
    assert response.status_code == 409 and "still stopping" in response.json()["detail"]

    # Ingestion stopped it: its status is gone.
    fake_redis.delete(f"camera_status:{camera_id}")
    assert client.delete(f"/cameras/{camera_id}").status_code == 204


def test_without_redis_a_disabled_rtsp_camera_is_not_deleted(client, fake_redis):
    camera_id = rtsp_camera(client, enabled=False)
    fake_redis.down = True
    response = client.delete(f"/cameras/{camera_id}")
    assert response.status_code == 409 and "Redis" in response.json()["detail"]


def test_storage_down_is_503_and_deletes_nothing(client, full_camera, engine, storage, monkeypatch):
    monkeypatch.setattr(type(storage), "is_healthy", lambda self: False)
    response = client.delete(f"/cameras/{full_camera['id']}")
    assert response.status_code == 503
    assert count(engine, Camera, id=UUID(full_camera["id"])) == 1
    assert all(storage.exists(key) for key in full_camera["files"])
