"""The analysis task end to end: scratch database, real MinIO, fake VLM and embedders."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from services.analysis import prompts
from services.analysis.tasks import Models, TransientError, give_up, process_segment, sweep
from services.analysis.windows import window_id
from shared.config import Settings
from shared.database.index_settings import ensure_index_settings
from shared.database.models import (
    AnalysisWindow,
    Camera,
    Keyframe,
    TextEmbedding,
    VideoSegment,
)
from shared.model_adapters import VLMQuotaExhausted, VLMUnavailable
from shared.storage.keys import segment_key
from tests.fakes import FakeImageEmbedder, FakeTextEmbedder, FakeVLM
from tests.ingestion.videos import activity_video

START = datetime(2026, 3, 7, 9, 0, tzinfo=UTC)
GOOD = json.dumps(prompts.EXAMPLE)


@pytest.fixture
def camera(session, storage):
    camera = Camera(display_name="Analysis cam", source_type="file")
    session.add(camera)
    session.commit()
    yield camera
    for prefix in ("segments", "keyframes"):
        storage.delete_prefix(f"{prefix}/{camera.id}/")


@pytest.fixture(scope="module")
def video_30s(tmp_path_factory):
    return activity_video(tmp_path_factory.mktemp("analysis") / "30s.avi", 30, [(5, 20)])


@pytest.fixture
def make_segment(session, storage, camera):
    def make(path=None, seconds=30, start=START, status="pending") -> VideoSegment:
        segment_id = uuid4()
        key = segment_key(camera.id, start, segment_id)
        uri = storage.to_uri(key)
        if path is not None:
            uri = storage.upload_file(key, path, content_type="video/mp4")
        segment = VideoSegment(
            id=segment_id,
            camera_id=camera.id,
            start_time=start,
            end_time=start + timedelta(seconds=seconds),
            duration_ms=seconds * 1000,
            storage_uri=uri,
            status=status,
        )
        session.add(segment)
        session.commit()
        return segment

    return make


@pytest.fixture
def run(engine, storage):
    def run_segment(segment, vlm, text=None, image=None):
        models = Models(vlm, text or FakeTextEmbedder(), image or FakeImageEmbedder())
        process_segment(
            segment.id,
            session_factory=lambda: Session(engine),
            storage=storage,
            settings=Settings(_env_file=None),
            models=models,
        )
        return models

    return run_segment


def windows(session, segment) -> list[AnalysisWindow]:
    session.expire_all()
    return session.scalars(
        select(AnalysisWindow)
        .where(AnalysisWindow.segment_id == segment.id)
        .order_by(AnalysisWindow.start_time)
    ).all()


def status(session, segment) -> str:
    session.expire_all()
    return session.get(VideoSegment, segment.id).status


def count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def test_every_window_is_described_embedded_and_stored(
    session, storage, make_segment, run, video_30s
):
    segment = make_segment(video_30s)
    vlm = FakeVLM([GOOD])
    models = run(segment, vlm)

    rows = windows(session, segment)
    assert [(w.start_time, w.end_time) for w in rows] == [
        (START + timedelta(seconds=a), START + timedelta(seconds=b))
        for a, b in [(0, 12), (9, 21), (18, 30)]
    ]
    assert all(w.processing_status == "done" and w.error is None for w in rows)
    assert rows[0].id == window_id(segment.id, 0)
    assert rows[0].vlm_description == prompts.EXAMPLE["summary"]
    assert rows[0].vlm_model == "fake-vlm" and rows[0].confidence == 0.88
    assert rows[0].structured_observation_json["prompt_version"] == prompts.PROMPT_VERSION
    assert rows[0].structured_observation_json["actions"][0]["type"] == "enter"
    assert status(session, segment) == "done"

    keyframes = session.scalars(select(Keyframe).order_by(Keyframe.timestamp)).all()
    assert len(keyframes) == 18
    assert all(k.visual_embedding is not None for k in keyframes)
    assert all(storage.exists(storage.key_from_uri(k.storage_uri)) for k in keyframes)
    first = [k.timestamp - START for k in keyframes if k.analysis_window_id == rows[0].id]
    assert [round(d.total_seconds()) for d in first] == [1, 3, 5, 7, 9, 11]

    texts = session.scalars(select(TextEmbedding)).all()
    assert len(texts) == 3 and texts[0].source_text.startswith(prompts.EXAMPLE["summary"])
    assert "Actions: person_1 enter" in texts[0].source_text

    assert len(vlm.calls) == 3 and all(c["frames"] == 6 for c in vlm.calls)
    assert '"Analysis cam"' in vlm.calls[0]["prompt"]
    assert models.image.images_embedded == 18
    recorded = session.execute(text("SELECT index_name, model_name FROM index_settings")).all()
    assert sorted(recorded) == [("text", "fake-text-embedder"), ("visual", "fake-image-embedder")]


def test_running_again_changes_nothing(session, make_segment, run, video_30s):
    segment = make_segment(video_30s)
    run(segment, FakeVLM([GOOD]))
    vlm = FakeVLM([GOOD])
    run(segment, vlm)
    assert vlm.calls == []  # the segment is done

    session.get(VideoSegment, segment.id).status = "pending"  # e.g. requeued by mistake
    session.commit()
    run(segment, vlm)
    assert vlm.calls == []  # every window is done
    assert (
        count(session, AnalysisWindow),
        count(session, Keyframe),
        count(session, TextEmbedding),
    ) == (3, 18, 3)
    assert status(session, segment) == "done"


def test_one_bad_answer_is_retried_with_the_stricter_prompt(session, make_segment, run, video_30s):
    segment = make_segment(video_30s, seconds=12)
    vlm = FakeVLM(["Sorry, I can't.", GOOD])
    run(segment, vlm)
    (row,) = windows(session, segment)
    assert row.processing_status == "done"
    assert row.structured_observation_json["attempts"] == 2
    assert "could not be used" in vlm.calls[1]["prompt"]


def test_a_window_without_a_valid_answer_fails_and_the_others_carry_on(
    session, make_segment, run, video_30s
):
    segment = make_segment(video_30s)
    run(segment, FakeVLM([GOOD, "nonsense", "still nonsense", GOOD]))
    rows = windows(session, segment)
    assert [w.processing_status for w in rows] == ["done", "failed", "done"]
    assert "did not return a valid observation" in rows[1].error
    assert count(session, Keyframe) == 12
    assert status(session, segment) == "failed"


def test_an_unavailable_vlm_retries_from_the_unfinished_window(
    session, make_segment, run, video_30s
):
    segment = make_segment(video_30s)
    with pytest.raises(TransientError):
        run(segment, FakeVLM([GOOD, VLMUnavailable("HTTP 503")]))
    assert [w.processing_status for w in windows(session, segment)] == ["done", "processing"]
    assert status(session, segment) == "processing"

    vlm = FakeVLM([GOOD])
    run(segment, vlm)
    assert len(vlm.calls) == 2  # windows 2 and 3 only
    assert [w.processing_status for w in windows(session, segment)] == ["done"] * 3
    assert status(session, segment) == "done"


def test_each_status_change_and_window_is_recorded_as_progress(
    session, make_segment, run, video_30s
):
    long_ago = datetime(2026, 1, 1, tzinfo=UTC)
    seen = []

    def watch(frames, prompt):
        """Notes the segment's status and progress time when each window is described."""
        session.expire_all()
        row = session.get(VideoSegment, segment.id)
        seen.append((row.status, row.status_changed_at))
        return GOOD

    segment = make_segment(video_30s)
    segment.status_changed_at = long_ago
    session.commit()
    before = datetime.now(UTC)
    run(segment, FakeVLM([watch]))

    assert [s for s, _ in seen] == ["processing"] * 3
    times = [t for _, t in seen]
    assert times[0] >= before and times == sorted(times) and len(set(times)) == 3
    session.expire_all()
    finished = session.get(VideoSegment, segment.id)
    assert finished.status == "done" and finished.status_changed_at > times[-1]


def test_a_used_up_quota_fails_the_rest_without_retrying(session, make_segment, run, video_30s):
    segment = make_segment(video_30s)
    vlm = FakeVLM([GOOD, VLMQuotaExhausted("daily limit reached")])
    run(segment, vlm)  # no TransientError: retrying today won't help
    rows = windows(session, segment)
    assert [w.processing_status for w in rows] == ["done", "failed", "failed"]
    assert all("daily limit reached" in w.error for w in rows[1:])
    assert len(vlm.calls) == 2
    assert status(session, segment) == "failed"


def test_an_unreadable_video_fails_the_segment(session, make_segment, run, tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"this is not a video")
    segment = make_segment(bad, seconds=12)
    vlm = FakeVLM([GOOD])
    run(segment, vlm)
    assert status(session, segment) == "failed"
    (row,) = windows(session, segment)
    assert row.processing_status == "failed" and "could not open" in row.error
    assert vlm.calls == []


def test_a_video_missing_from_storage_fails_the_segment(session, make_segment, run):
    segment = make_segment(None, seconds=12)
    run(segment, FakeVLM([GOOD]))
    assert status(session, segment) == "failed"
    assert windows(session, segment)[0].error == "the video is no longer in storage"


def test_other_embedding_models_are_refused(session, make_segment, run, video_30s):
    ensure_index_settings(session, "other/text-model", "fake-image-embedder")
    session.commit()
    segment = make_segment(video_30s)
    vlm = FakeVLM([GOOD])
    run(segment, vlm)
    assert vlm.calls == [] and windows(session, segment) == []
    assert status(session, segment) == "pending"


def test_giving_up_fails_what_is_unfinished(session, engine, make_segment, run, video_30s):
    segment = make_segment(video_30s)
    with pytest.raises(TransientError):
        run(segment, FakeVLM([GOOD, VLMUnavailable("HTTP 503")]))
    give_up(
        segment.id,
        "gave up after 3 retries: HTTP 503",
        settings=Settings(_env_file=None),
        session_factory=lambda: Session(engine),
    )
    rows = windows(session, segment)
    assert [w.processing_status for w in rows] == ["done", "failed", "failed"]
    assert rows[2].error == "gave up after 3 retries: HTTP 503"
    assert status(session, segment) == "failed"


def test_the_sweep_queues_only_recent_pending_segments(
    session, engine, make_segment, analysis_queue
):
    now = START + timedelta(hours=30)
    recent = make_segment(start=now - timedelta(hours=2))
    make_segment(start=now - timedelta(hours=26))  # older than 24 h
    make_segment(start=now - timedelta(hours=1), status="done")
    make_segment(start=now - timedelta(hours=1), status="failed")
    queued = sweep(lambda: Session(engine), Settings(_env_file=None), now=now)
    assert queued == 1 and analysis_queue == [recent.id]


def test_the_sweep_does_nothing_when_analysis_is_off(engine, make_segment, analysis_queue):
    make_segment(start=datetime.now(UTC))
    settings = Settings(_env_file=None, analysis_enabled=False)
    assert sweep(lambda: Session(engine), settings) == 0
    assert analysis_queue == []
