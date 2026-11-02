"""An event's evidence as a playback manifest, its picture and the gap wording (step 5.3)."""

import pytest
from sqlalchemy import select

from services.events.playback import (
    best_keyframes,
    event_manifest,
    evidence_rows,
    export_file_name,
    gap_wording,
)
from shared.database.models import Keyframe
from tests.events.conftest import at


def test_parts_in_order_with_an_explicit_gap(session, add_segment, add_event):
    first = add_segment(0, 30)
    second = add_segment(342, 30)  # 312 s after the first ends
    event = add_event([(first, 10_000, 30_000), (second, 0, 12_000)], start_s=15, end_s=345)
    manifest = event_manifest(event, evidence_rows(session, event.id))
    assert [p.type for p in manifest.parts] == ["video", "gap", "video"]
    video, gap, last = manifest.parts
    assert (video.segment_id, video.source_start_ms, video.source_end_ms) == (
        first.id,
        10_000,
        30_000,
    )
    assert video.start_time == at(10) and video.end_time == at(30)
    assert gap.duration_ms == 312_000
    assert (last.segment_id, last.start_time, last.end_time) == (second.id, at(342), at(354))
    assert manifest.start_time == at(10) and manifest.end_time == at(354)


def test_back_to_back_segments_play_without_a_gap(session, add_segment, add_event):
    first, second = add_segment(0, 30), add_segment(30, 30)
    event = add_event([(first, 20_000, 30_000), (second, 0, 10_000)])
    assert [p.type for p in event_manifest(event, evidence_rows(session, event.id)).parts] == [
        "video",
        "video",
    ]


def test_overlapping_segments_never_play_twice(session, add_segment, add_event):
    first, overlapping = add_segment(0, 30), add_segment(25, 30)
    event = add_event([(first, 0, 30_000), (overlapping, 0, 20_000)])
    first_part, second_part = event_manifest(event, evidence_rows(session, event.id)).parts
    assert first_part.end_time == at(30) and second_part.start_time == at(30)
    assert second_part.source_start_ms == 5_000


def test_the_picture_is_the_middle_keyframe_of_the_deciding_window(
    session, add_segment, add_event, add_window
):
    segment = add_segment(0, 30)
    window = add_window(segment)
    event = add_event([(segment, 0, 30_000)], window=window)
    middle = session.scalars(
        select(Keyframe.id)
        .where(Keyframe.analysis_window_id == window.id)
        .order_by(Keyframe.timestamp)
    ).all()[1]
    assert best_keyframes(session, [event]) == {event.id: middle}
    no_window = add_event([(segment, 0, 30_000)])
    assert best_keyframes(session, [no_window]) == {}


@pytest.mark.parametrize(
    ("ms", "words"),
    [
        (312_000, "5 minutes 12 seconds"),
        (60_000, "1 minute"),
        (1_000, "1 second"),
        (3_725_000, "1 hour 2 minutes 5 seconds"),
        (0, "0 seconds"),
    ],
)
def test_gap_wording(ms, words):
    assert gap_wording(ms) == words


def test_the_export_file_name():
    name = export_file_name("Object left behind!", "CAM-01 (Front)", at(0))
    assert name.startswith("object-left-behind_cam-01-front_") and name.endswith(".mp4")
    assert export_file_name("", "Café", at(0)).startswith("event_cafe_")
