"""Trigger evaluation tests (step 5.2): cameras, segments and analysed windows with whole
observations, compiled triggers, and fake tools with a clock the test moves."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import cv2
import numpy as np
import pytest
from sqlalchemy.orm import Session

from services.triggers.evaluate import TriggerTools
from services.triggers.events import FinishTools
from shared.camera_status import CameraStatus
from shared.config import Settings
from shared.database.models import (
    AnalysisWindow,
    Camera,
    Keyframe,
    TextEmbedding,
    Trigger,
    VideoSegment,
)
from tests.fakes import FakeVLM
from tests.search.conftest import IMAGE, TEXT

START = datetime(2026, 3, 7, 9, 0, tzinfo=UTC)
# An API backend, so verification runs (the local model skips it, ADR 0033).
SETTINGS = Settings(_env_file=None, vlm_backend="api")


@dataclass
class Clock:
    now: datetime = START + timedelta(hours=1)

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Recorder:
    """What the finish tools did: notices published, checks scheduled."""

    notices: list = field(default_factory=list)
    scheduled: list = field(default_factory=list)
    statuses: dict = field(default_factory=dict)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def recorder():
    return Recorder()


@pytest.fixture
def finish_tools(clock, recorder):
    def make(settings=SETTINGS) -> FinishTools:
        return FinishTools(
            settings=settings,
            publish=recorder.notices.append,
            schedule_finish=lambda event_id, seconds: recorder.scheduled.append(
                (event_id, seconds)
            ),
            camera_status=lambda camera_id: recorder.statuses.get(camera_id),
            now=clock,
        )

    return make


@pytest.fixture
def tools(storage, finish_tools):
    def make(vlm=None, settings=SETTINGS) -> TriggerTools:
        return TriggerTools(storage, vlm or FakeVLM(["{}"]), TEXT, IMAGE, finish_tools(settings))

    return make


@pytest.fixture
def cameras(session):
    one = Camera(display_name="CAM-01", source_type="file")
    two = Camera(display_name="CAM-02", source_type="file")
    session.add_all([one, two])
    session.commit()
    return one, two


@pytest.fixture
def live_camera(session):
    camera = Camera(display_name="Live", source_type="rtsp", source_uri_encrypted="x")
    session.add(camera)
    session.commit()
    return camera


@pytest.fixture
def add_segment(session):
    def add(camera, offset_s: float = 0, seconds: float = 30) -> VideoSegment:
        segment = VideoSegment(
            camera_id=camera.id,
            start_time=START + timedelta(seconds=offset_s),
            end_time=START + timedelta(seconds=offset_s + seconds),
            duration_ms=round(seconds * 1000),
            storage_uri=f"s3://sentinel/segments/{camera.id}/{uuid4()}.mp4",
            status="done",
        )
        session.add(segment)
        session.commit()
        return segment

    return add


@pytest.fixture
def add_window(session):
    """A done window at `offset_s` of the camera's day: `actions` as (type, start, end) seconds
    into the window."""

    def add(
        camera,
        segment,
        offset_s: float,
        actions=(),
        entities=(),
        objects=(),
        confidence=0.9,
        description="something happens",
        text_vector=None,
        picture_vectors=(),
        seconds: float = 12,
    ) -> AnalysisWindow:
        window = AnalysisWindow(
            id=uuid4(),
            segment_id=segment.id,
            camera_id=camera.id,
            start_time=START + timedelta(seconds=offset_s),
            end_time=START + timedelta(seconds=offset_s + seconds),
            vlm_description=description,
            confidence=confidence,
            structured_observation_json={
                "summary": description,
                "entities": [
                    {"temporary_id": f"e{n}", "type": t} for n, t in enumerate(entities, 1)
                ],
                "actions": [
                    {"type": t, "subject": "e1", "start_second": a, "end_second": b}
                    for t, a, b in actions
                ],
                "visible_objects": list(objects),
                "uncertainties": [],
                "overall_confidence": confidence,
            },
            processing_status="done",
        )
        session.add(window)
        if text_vector is not None:
            session.add(
                TextEmbedding(
                    analysis_window_id=window.id, embedding=text_vector, source_text=description
                )
            )
        for n, vector in enumerate(picture_vectors or [None] * 3):
            session.add(
                Keyframe(
                    analysis_window_id=window.id,
                    timestamp=window.start_time + timedelta(seconds=2 * n + 1),
                    storage_uri=f"s3://sentinel/keyframes/{camera.id}/{window.id}/{n}.jpg",
                    visual_embedding=vector,
                )
            )
        session.commit()
        return window

    return add


@pytest.fixture
def pictures(session, storage):
    """A small JPEG in MinIO for every keyframe; call it after adding the windows."""
    from sqlalchemy import select

    keys = []

    def store():
        jpeg = cv2.imencode(".jpg", np.full((48, 64, 3), 128, np.uint8))[1].tobytes()
        for uri in session.scalars(select(Keyframe.storage_uri)):
            key = storage.key_from_uri(uri)
            storage.upload_bytes(key, jpeg, "image/jpeg")
            keys.append(key)

    yield store
    for key in keys:
        storage.delete(key)


LEFT_BEHIND = {
    "mode": "structured",
    "required_entities": ["person", "portable_object"],
    "required_action_sequence": ["place_object", "leave"],
    "alternative_action_sequences": [["leave_object_behind"]],
    "maximum_sequence_gap_seconds": 30,
    "requires_vlm_verification": False,
    "search_text": "someone places an object and leaves it behind",
    "source_rule": "Report when someone places an object and leaves it behind",
    "prompt_version": 1,
}


@pytest.fixture
def add_trigger(session):
    def add(
        cameras,
        rule=None,
        name="Object left behind",
        enabled=True,
        compiled=True,
        **settings,
    ) -> Trigger:
        trigger = Trigger(
            name=name,
            natural_language_rule=(rule or LEFT_BEHIND)["source_rule"],
            compiled_rule_json=rule or LEFT_BEHIND,
            compile_status="done" if compiled else "pending",
            enabled=enabled,
            **{
                "minimum_confidence": 0.8,
                "cooldown_seconds": 120,
                "pre_roll_seconds": 10,
                "post_roll_seconds": 20,
                **settings,
            },
        )
        trigger.cameras = list(cameras)
        session.add(trigger)
        session.commit()
        return trigger

    return add


def rule(**changes) -> dict:
    return {**LEFT_BEHIND, **changes}


def fresh(engine, model, id_):
    with Session(engine) as session:
        row = session.get(model, id_)
        if row is not None:
            session.expunge(row)
        return row


def online(activity: bool) -> CameraStatus:
    from shared.camera_status import LiveState

    return CameraStatus(state=LiveState.ONLINE, activity=activity)
