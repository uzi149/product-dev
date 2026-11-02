"""Event tests (step 5.3): a camera, a trigger, stored segments and alerted events with their
evidence rows, as live evaluation (5.2) leaves them."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from shared.database.models import (
    AnalysisWindow,
    Camera,
    Event,
    EventEvidence,
    Keyframe,
    Trigger,
    VideoSegment,
)

# The API tests' client (and the fixtures it needs), for test_api.py.
from tests.api.conftest import client, fake_redis, fernet_key, queued  # noqa: F401

T0 = datetime(2026, 3, 7, 9, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


@pytest.fixture
def camera(session):
    camera = Camera(display_name="CAM-01", source_type="file")
    session.add(camera)
    session.commit()
    return camera


@pytest.fixture
def trigger(session, camera):
    trigger = Trigger(
        name="Object left behind",
        natural_language_rule="Report when someone places an object and leaves it behind",
        compile_status="done",
        enabled=True,
    )
    trigger.cameras = [camera]
    session.add(trigger)
    session.commit()
    return trigger


@pytest.fixture
def add_segment(session, camera):
    def add(start_s: float, seconds: float, storage_uri: str | None = None) -> VideoSegment:
        segment = VideoSegment(
            camera_id=camera.id,
            start_time=at(start_s),
            end_time=at(start_s + seconds),
            duration_ms=round(seconds * 1000),
            storage_uri=storage_uri or f"s3://sentinel/segments/{camera.id}/{uuid4()}.mp4",
            status="done",
        )
        session.add(segment)
        session.commit()
        return segment

    return add


@pytest.fixture
def add_event(session, camera, trigger):
    """An event over `parts` = [(segment, start_ms, end_ms)], alerted unless `held`."""

    def add(
        parts,
        start_s: float = 0,
        end_s: float = 10,
        held: bool = False,
        status: str = "new",
        alerted_s: float = 100,
        window: AnalysisWindow | None = None,
        **fields,
    ) -> Event:
        event = Event(
            trigger_id=trigger.id,
            camera_id=camera.id,
            start_time=at(start_s),
            end_time=at(end_s),
            confidence=0.9,
            summary="A person leaves a bag.",
            status=status,
            alerted_at=None if held else at(alerted_s),
            updated_at=at(alerted_s - 1),
            details_json={
                "mode": "structured",
                "window_ids": [str(window.id)] if window else [],
                "matches": [
                    {
                        "actions": [{"type": "leave", "window_id": str(window.id)}]
                        if window
                        else [],
                        "verification": {"status": "verified", "reason": "A bag is left."},
                    }
                ],
                "verification": {
                    "status": "verified",
                    "confidence": 0.9,
                    "reason": "A bag is left.",
                },
                "delay_seconds": 34.0,
            },
            **fields,
        )
        event.evidence = [
            EventEvidence(
                segment_id=segment.id,
                start_offset_ms=start_ms,
                end_offset_ms=end_ms,
                evidence_reason="the matched activity",
                sequence_order=n,
            )
            for n, (segment, start_ms, end_ms) in enumerate(parts)
        ]
        session.add(event)
        session.commit()
        return event

    return add


@pytest.fixture
def add_window(session, camera):
    """A window with three keyframes, for the event's picture."""

    def add(segment: VideoSegment) -> AnalysisWindow:
        window = AnalysisWindow(
            segment_id=segment.id,
            camera_id=camera.id,
            start_time=segment.start_time,
            end_time=segment.start_time + timedelta(seconds=12),
            processing_status="done",
        )
        session.add(window)
        session.flush()
        for n in range(3):
            session.add(
                Keyframe(
                    analysis_window_id=window.id,
                    timestamp=window.start_time + timedelta(seconds=2 * n + 1),
                    storage_uri=f"s3://sentinel/keyframes/{camera.id}/{window.id}/{n}.jpg",
                )
            )
        session.commit()
        return window

    return add
