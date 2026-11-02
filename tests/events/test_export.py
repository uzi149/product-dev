"""Exporting an event's evidence as one MP4 with real FFmpeg (step 5.3)."""

import numpy as np
import pytest
from sqlalchemy.orm import Session

from services.events.export import EXPORT_FPS, GAP_CARD_SECONDS, gap_card, process_export
from shared.database.models import Event
from shared.schemas.query import GapPart
from shared.storage.keys import export_key, segment_key
from shared.video.ffmpeg import probe
from tests.events.conftest import at
from tests.ingestion.videos import activity_video
from tests.video.scenes import HEIGHT, WIDTH


@pytest.fixture
def stored_segment(add_segment, storage, camera, tmp_path):
    """A segment whose video (4 s, movement) is really in storage."""
    keys = []

    def add(start_s: float):
        path = activity_video(tmp_path / f"segment-{start_s:g}.avi", 4, [(0, 4)])
        segment = add_segment(start_s, 4)
        key = segment_key(camera.id, segment.start_time, segment.id)
        segment.storage_uri = storage.upload_file(key, path, content_type="video/x-msvideo")
        keys.append(key)
        return segment

    yield add
    for key in keys:
        storage.delete(key)


@pytest.fixture
def cleanup_export(storage):
    events = []
    yield events.append
    for event_id in events:
        storage.delete(export_key(event_id))


def test_the_parts_are_joined_with_a_gap_card_between_them(
    session, engine, storage, stored_segment, add_event, cleanup_export, tmp_path
):
    first = stored_segment(0)
    second = stored_segment(30)  # 26 s after the first ends: a gap
    session.commit()
    event = add_event([(first, 1_000, 4_000), (second, 0, 3_000)], start_s=1, end_s=33)
    cleanup_export(event.id)

    process_export(event.id, lambda: Session(engine), storage)

    session.expire_all()
    done = session.get(Event, event.id)
    assert done.export_status == "done" and done.export_error is None
    assert done.export_uri == storage.to_uri(export_key(event.id)) and done.exported_at
    out = tmp_path / "export.mp4"
    storage.download_file(export_key(event.id), out)
    info = probe(out)
    assert info.codec == "h264" and info.pix_fmt == "yuv420p"
    assert (info.width, info.height) == (WIDTH - WIDTH % 2, HEIGHT - HEIGHT % 2)
    # 3 s + the 2 s gap card + 3 s
    assert info.duration_s == pytest.approx(3 + GAP_CARD_SECONDS + 3, abs=1 / EXPORT_FPS * 3)


def test_back_to_back_parts_have_no_card(
    session, engine, storage, stored_segment, add_event, cleanup_export, tmp_path
):
    first, second = stored_segment(0), stored_segment(4)
    session.commit()
    event = add_event([(first, 2_000, 4_000), (second, 0, 2_000)])
    cleanup_export(event.id)
    process_export(event.id, lambda: Session(engine), storage)
    out = tmp_path / "export.mp4"
    storage.download_file(export_key(event.id), out)
    assert probe(out).duration_s == pytest.approx(4, abs=3 / EXPORT_FPS)


def test_missing_footage_fails_the_export(session, engine, storage, add_segment, add_event):
    gone = add_segment(0, 30)  # its file was never stored
    event = add_event([(gone, 0, 10_000)])
    process_export(event.id, lambda: Session(engine), storage)
    session.expire_all()
    failed = session.get(Event, event.id)
    assert failed.export_status == "failed" and failed.export_error


def test_an_event_without_evidence_fails_the_export(session, engine, storage, add_event):
    event = add_event([])
    process_export(event.id, lambda: Session(engine), storage)
    session.expire_all()
    assert "no stored evidence" in session.get(Event, event.id).export_error


def test_the_gap_card_shows_text_on_a_dark_picture():
    card = gap_card(640, 360, GapPart(duration_ms=312_000), "CAM-01", at(0), at(312))
    assert card.shape == (360, 640, 3)
    assert card[0, 0].tolist() == [24, 24, 24]  # background
    middle = card[140:220]
    assert (middle > 200).any()  # bright text across the middle
    assert np.count_nonzero(card.max(axis=2) > 100) > 500
