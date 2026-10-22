from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import DBAPIError, IntegrityError

from shared.database.models import (
    EMBEDDING_DIM,
    AnalysisWindow,
    Camera,
    Event,
    EventEvidence,
    Keyframe,
    QueryResult,
    QuerySession,
    TextEmbedding,
    Trigger,
    VideoSegment,
)
from shared.database.session import session_scope

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def vec(*head: float) -> list[float]:
    return list(head) + [0.0] * (EMBEDDING_DIM - len(head))


def make_camera(name="CAM-01") -> Camera:
    return Camera(display_name=name, source_type="rtsp", source_uri_encrypted="x")


def make_segment(camera: Camera) -> VideoSegment:
    return VideoSegment(
        camera=camera,
        start_time=T0,
        end_time=T0 + timedelta(seconds=30),
        duration_ms=30_000,
        storage_uri="segments/a.mp4",
    )


def make_window(segment: VideoSegment, description="a person walks in") -> AnalysisWindow:
    return AnalysisWindow(
        segment=segment,
        camera_id=segment.camera.id,
        start_time=T0,
        end_time=T0 + timedelta(seconds=12),
        vlm_description=description,
        structured_observation_json={"summary": description},
    )


def seed_segment(session) -> tuple[Camera, VideoSegment]:
    camera = make_camera()
    segment = make_segment(camera)
    session.add_all([camera, segment])
    session.flush()
    return camera, segment


def test_defaults_are_applied(session):
    camera = make_camera()
    session.add(camera)
    session.flush()
    session.refresh(camera)
    assert camera.id is not None
    assert camera.enabled is True
    assert camera.retention_days == 14
    assert camera.analysis_fps == 1
    assert camera.detection_config == {}
    assert camera.created_at is not None


def test_text_embeddings_nearest_neighbour(session):
    _, segment = seed_segment(session)
    near = make_window(segment, "person rings the doorbell")
    far = make_window(segment, "car parks")
    session.add_all([near, far])
    session.flush()
    session.add_all(
        [
            Keyframe(
                analysis_window_id=near.id,
                timestamp=T0,
                storage_uri="k1.jpg",
                visual_embedding=vec(1),
            ),
            TextEmbedding(analysis_window_id=near.id, embedding=vec(1), source_text="doorbell"),
            TextEmbedding(analysis_window_id=far.id, embedding=vec(0, 1), source_text="car"),
        ]
    )
    session.flush()

    best = session.scalars(
        select(TextEmbedding).order_by(TextEmbedding.embedding.cosine_distance(vec(1))).limit(1)
    ).one()
    assert best.source_text == "doorbell"
    assert list(best.embedding)[:2] == [1.0, 0.0]

    nearest_frame = session.scalars(
        select(Keyframe).order_by(Keyframe.visual_embedding.cosine_distance(vec(1))).limit(1)
    ).one()
    assert nearest_frame.storage_uri == "k1.jpg"


def test_full_text_search_on_description(session):
    _, segment = seed_segment(session)
    session.add_all(
        [
            make_window(segment, "a person rings the doorbell"),
            make_window(segment, "empty hallway"),
        ]
    )
    session.flush()

    hits = session.scalars(
        select(AnalysisWindow).where(
            func.to_tsvector("english", AnalysisWindow.vlm_description).op("@@")(
                func.plainto_tsquery("english", "doorbell")
            )
        )
    ).all()
    assert [w.vlm_description for w in hits] == ["a person rings the doorbell"]


def test_trigger_event_evidence_and_query_tables(session):
    cam1, cam2 = make_camera("CAM-01"), make_camera("CAM-02")
    segment = make_segment(cam1)
    trigger = Trigger(name="Bag", natural_language_rule="bag left behind", cameras=[cam1])
    session.add_all([cam1, cam2, segment, trigger])
    session.flush()

    event = Event(
        trigger_id=trigger.id,
        camera_id=cam1.id,
        start_time=T0,
        end_time=T0 + timedelta(seconds=20),
    )
    event.evidence = [
        EventEvidence(
            segment_id=segment.id, start_offset_ms=0, end_offset_ms=5000, sequence_order=1
        ),
        EventEvidence(
            segment_id=segment.id, start_offset_ms=9000, end_offset_ms=12000, sequence_order=0
        ),
    ]
    window = make_window(segment)
    query = QuerySession(query_text="bag", camera_filter=[cam1.id], start_filter=T0)
    session.add_all([event, window, query])
    session.flush()
    session.add(
        QueryResult(query_session_id=query.id, analysis_window_id=window.id, vector_score=0.9)
    )
    session.flush()
    session.refresh(event)
    session.refresh(query)
    session.refresh(trigger)

    assert event.status == "new"
    assert trigger.minimum_confidence == 0.8
    assert [e.sequence_order for e in event.evidence] == [0, 1]
    assert query.camera_filter == [cam1.id]
    assert [c.display_name for c in trigger.cameras] == ["CAM-01"]


def test_invalid_camera_source_type_is_rejected(session):
    session.add(Camera(display_name="x", source_type="usb"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_invalid_segment_status_is_rejected(session):
    camera = make_camera()
    segment = make_segment(camera)
    segment.status = "weird"
    session.add_all([camera, segment])
    with pytest.raises(IntegrityError):
        session.flush()


def test_invalid_event_status_is_rejected(session):
    camera = make_camera()
    trigger = Trigger(name="t", natural_language_rule="r")
    session.add_all([camera, trigger])
    session.flush()
    session.add(
        Event(
            trigger_id=trigger.id, camera_id=camera.id, start_time=T0, end_time=T0, status="bogus"
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_wrong_vector_size_is_rejected(session):
    _, segment = seed_segment(session)
    window = make_window(segment)
    session.add(window)
    session.flush()
    session.add(TextEmbedding(analysis_window_id=window.id, embedding=[0.1] * 10, source_text="x"))
    with pytest.raises(DBAPIError):
        session.flush()


def test_deleting_a_segment_cascades_to_its_analysis_data(session):
    _, segment = seed_segment(session)
    window = make_window(segment)
    session.add(window)
    session.flush()
    session.add_all(
        [
            Keyframe(analysis_window_id=window.id, timestamp=T0, storage_uri="k.jpg"),
            TextEmbedding(analysis_window_id=window.id, embedding=vec(1), source_text="x"),
        ]
    )
    session.flush()

    session.execute(delete(VideoSegment).where(VideoSegment.id == segment.id))
    for model in (AnalysisWindow, Keyframe, TextEmbedding):
        assert session.scalars(select(model)).all() == []


def test_segment_used_as_evidence_cannot_be_deleted(session):
    camera, segment = seed_segment(session)
    trigger = Trigger(name="t", natural_language_rule="r")
    session.add(trigger)
    session.flush()
    event = Event(trigger_id=trigger.id, camera_id=camera.id, start_time=T0, end_time=T0)
    event.evidence = [EventEvidence(segment_id=segment.id, start_offset_ms=0, end_offset_ms=1000)]
    session.add(event)
    session.flush()

    with pytest.raises(IntegrityError):
        session.execute(delete(VideoSegment).where(VideoSegment.id == segment.id))


def test_camera_with_events_cannot_be_deleted_but_one_without_can(session):
    used, unused = make_camera("used"), make_camera("unused")
    trigger = Trigger(name="t", natural_language_rule="r")
    session.add_all([used, unused, trigger])
    session.flush()
    session.add(Event(trigger_id=trigger.id, camera_id=used.id, start_time=T0, end_time=T0))
    session.flush()

    session.execute(delete(Camera).where(Camera.id == unused.id))
    with pytest.raises(IntegrityError):
        session.execute(delete(Camera).where(Camera.id == used.id))


def test_session_scope_commits_and_rolls_back(migrated_db, monkeypatch):
    from shared.config import get_settings
    from shared.database import session as session_module

    def reset_caches():
        get_settings.cache_clear()
        session_module.get_engine.cache_clear()
        session_module.get_sessionmaker.cache_clear()

    monkeypatch.setenv("POSTGRES_DB", migrated_db.rsplit("/", 1)[1])
    reset_caches()
    try:
        with session_scope() as s:
            s.add(make_camera("kept"))
        with pytest.raises(RuntimeError), session_scope() as s:
            s.add(make_camera("lost"))
            s.flush()
            raise RuntimeError("boom")
        with session_scope() as s:
            names = set(s.scalars(select(Camera.display_name)))
        assert names == {"kept"}
    finally:
        session_module.get_engine().dispose()
        reset_caches()
