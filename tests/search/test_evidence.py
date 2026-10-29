"""Evidence results grouped by time (step 4.3, scope §11)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from services.query_engine.evidence import (
    SegmentSpan,
    Window,
    build_manifest,
    evidence_results,
    expand,
    group_results,
    load_neighbours,
    merge_sequences,
)
from services.query_engine.retrieval import WindowHit
from shared.config import Settings
from shared.database.models import AnalysisWindow, Camera, VideoSegment

MERGE = timedelta(seconds=2)
GROUP = timedelta(minutes=10)
CAM02, CAM01 = uuid4(), uuid4()
SEG100, SEG108 = uuid4(), uuid4()


def at(hms: str) -> datetime:
    h, m, s = (int(x) for x in hms.split(":"))
    return datetime(2026, 3, 7, h, m, s, tzinfo=UTC)


def window(start: str, end: str, *, camera=CAM02, segment=SEG100, score=0.5) -> Window:
    return Window(uuid4(), camera, segment, at(start), at(end), f"{start}-{end}", score)


# Scope §11: three overlapping windows, then the same camera again 5 min 12 s later.
ARRIVES = window("14:20:51", "14:21:12", score=0.6)
PLACES = window("14:21:09", "14:21:31", score=0.9)
LEAVES = window("14:21:30", "14:21:48", score=0.7)
LATER = window("14:27:00", "14:27:29", segment=SEG108, score=0.4)
SEGMENTS = {
    SEG100: SegmentSpan(SEG100, CAM02, at("14:20:39"), at("14:22:00")),
    SEG108: SegmentSpan(SEG108, CAM02, at("14:27:00"), at("14:27:40")),
}


def results(windows, group=GROUP):
    return group_results(merge_sequences(windows, MERGE), group)


def test_overlapping_windows_merge_into_one_sequence():
    (sequence,) = merge_sequences([LEAVES, ARRIVES, PLACES], MERGE)
    assert (sequence.start, sequence.end) == (at("14:20:51"), at("14:21:48"))
    assert [w.id for w in sequence.windows] == [ARRIVES.id, PLACES.id, LEAVES.id]
    assert sequence.score == 0.9


def test_a_later_clip_on_the_same_camera_is_the_same_result_after_a_gap():
    (result,) = results([ARRIVES, PLACES, LEAVES, LATER])
    assert len(result.sequences) == 2
    manifest = build_manifest(result, SEGMENTS)
    kinds = [p.type for p in manifest.parts]
    assert kinds == ["video", "gap", "video"]
    first, gap, second = manifest.parts
    assert gap.duration_ms == 312_000  # "5 minutes 12 seconds not shown"
    assert (first.segment_id, first.source_start_ms, first.source_end_ms) == (
        SEG100,
        12_000,
        69_000,
    )
    assert (second.segment_id, second.source_start_ms, second.source_end_ms) == (SEG108, 0, 29_000)
    assert (manifest.start_time, manifest.end_time) == (at("14:20:51"), at("14:27:29"))
    assert manifest.result_id == result.id and manifest.camera_id == CAM02


def test_clips_further_apart_than_the_group_gap_are_separate_results():
    found = results([ARRIVES, PLACES, LEAVES, LATER], group=timedelta(minutes=5))
    assert len(found) == 2
    assert found[0].score == 0.9 and found[1].score == 0.4  # best first


def test_cameras_never_merge():
    other = window("14:21:00", "14:21:20", camera=CAM01, score=0.95)
    found = results([ARRIVES, PLACES, other])
    assert [r.camera_id for r in found] == [CAM01, CAM02]


def test_windows_nearly_touching_merge_and_further_apart_do_not():
    a = window("10:00:00", "10:00:12")
    near = window("10:00:13", "10:00:25")  # 1 s after: merges (gap 2 s)
    far = window("10:00:30", "10:00:42")  # 5 s after: a new sequence
    sequences = merge_sequences([a, near, far], MERGE)
    assert [(s.start, s.end) for s in sequences] == [
        (at("10:00:00"), at("10:00:25")),
        (at("10:00:30"), at("10:00:42")),
    ]


def test_back_to_back_segments_play_as_two_parts_without_a_gap():
    seg_a, seg_b = uuid4(), uuid4()
    spans = {
        seg_a: SegmentSpan(seg_a, CAM02, at("09:00:00"), at("09:00:30")),
        seg_b: SegmentSpan(seg_b, CAM02, at("09:00:30"), at("09:01:00")),
    }
    first = window("09:00:18", "09:00:30", segment=seg_a)
    second = window("09:00:30", "09:00:42", segment=seg_b)
    (result,) = results([first, second])
    parts = build_manifest(result, spans).parts
    assert [p.type for p in parts] == ["video", "video"]
    assert (parts[0].source_start_ms, parts[0].source_end_ms) == (18_000, 30_000)
    assert (parts[1].source_start_ms, parts[1].source_end_ms) == (0, 12_000)


def test_a_join_under_a_second_is_not_a_gap():
    seg_a, seg_b = uuid4(), uuid4()
    half = timedelta(milliseconds=500)
    spans = {
        seg_a: SegmentSpan(seg_a, CAM02, at("09:00:00"), at("09:00:30")),
        seg_b: SegmentSpan(seg_b, CAM02, at("09:00:30") + half, at("09:01:00")),
    }
    first = window("09:00:18", "09:00:30", segment=seg_a)
    second = Window(uuid4(), CAM02, seg_b, at("09:00:30") + half, at("09:00:42"), None, 0.5)
    (result,) = results([first, second])
    assert [p.type for p in build_manifest(result, spans).parts] == ["video", "video"]


def test_overlapping_segments_never_play_the_same_footage_twice():
    seg_a, seg_b = uuid4(), uuid4()
    spans = {
        seg_a: SegmentSpan(seg_a, CAM02, at("09:00:00"), at("09:00:40")),
        seg_b: SegmentSpan(seg_b, CAM02, at("09:00:30"), at("09:01:00")),
    }
    (result,) = results(
        [
            window("09:00:20", "09:00:40", segment=seg_a),
            window("09:00:30", "09:00:50", segment=seg_b),
        ]
    )
    first, second = build_manifest(result, spans).parts
    assert first.end_time == second.start_time == at("09:00:40")
    assert second.source_start_ms == 10_000


def test_expansion_adds_neighbours_once_and_keeps_a_hits_score():
    before = window("14:20:42", "14:20:54", score=None)
    neighbours = {ARRIVES.id: [before, PLACES], PLACES.id: [ARRIVES, LEAVES]}
    hits = [ARRIVES, PLACES]
    expanded = expand(hits, neighbours)
    assert sorted(w.id for w in expanded) == sorted([before.id, ARRIVES.id, PLACES.id, LEAVES.id])
    assert next(w for w in expanded if w.id == PLACES.id).score == 0.9


def test_a_result_scores_by_its_best_hit_not_its_neighbours():
    context = window("14:21:40", "14:21:52", score=None)
    (result,) = results([ARRIVES, context])
    assert result.score == 0.6


# The database part: neighbours across a segment boundary, and the whole chain from hits.


@pytest.fixture
def footage(session):
    """A camera with two back-to-back 30 s segments and windows 0-12, 9-21, 18-30 in the first
    and 30-42 in the second; another camera with a window at the same time."""
    camera = Camera(display_name="Evidence cam", source_type="rtsp")
    other = Camera(display_name="Other cam", source_type="file")
    session.add_all([camera, other])
    session.flush()
    start = at("12:00:00")
    segments = []
    for n, cam in ((0, camera), (1, camera), (0, other)):
        s = VideoSegment(
            camera_id=cam.id,
            start_time=start + timedelta(seconds=30 * n),
            end_time=start + timedelta(seconds=30 * (n + 1)),
            duration_ms=30_000,
            storage_uri="s3://sentinel/x.mp4",
            status="done",
        )
        session.add(s)
        segments.append(s)
    session.flush()
    windows = {}
    for name, segment, offset in (
        ("w0", segments[0], 0),
        ("w9", segments[0], 9),
        ("w18", segments[0], 18),
        ("w30", segments[1], 30),
        ("x9", segments[2], 9),
    ):
        w = AnalysisWindow(
            segment_id=segment.id,
            camera_id=segment.camera_id,
            start_time=start + timedelta(seconds=offset),
            end_time=start + timedelta(seconds=offset + 12),
            vlm_description=name,
            processing_status="failed" if name == "w0" else "done",
        )
        session.add(w)
        windows[name] = w
    session.commit()
    return camera, segments, windows


def as_window(row, score=None) -> Window:
    return Window(row.id, row.camera_id, row.segment_id, row.start_time, row.end_time, None, score)


def test_neighbours_are_the_closest_before_and_after_even_across_segments(session, footage):
    _, _, w = footage
    found = load_neighbours(session, [as_window(w["w18"], 0.8)], MERGE)
    assert [n.description for n in found[w["w18"].id]] == ["w9", "w30"]
    # A failed window is still footage, so it can be context.
    assert [
        n.description for n in load_neighbours(session, [as_window(w["w9"])], MERGE)[w["w9"].id]
    ] == [
        "w0",
        "w18",
    ]
    # The camera's last window has nothing after it, and other cameras never count.
    assert [
        n.description for n in load_neighbours(session, [as_window(w["w30"])], MERGE)[w["w30"].id]
    ] == ["w18"]


def test_hits_become_results_with_manifests(session, footage):
    camera, segments, w = footage

    def hit(row, score):
        return WindowHit(
            window_id=row.id,
            segment_id=row.segment_id,
            camera_id=row.camera_id,
            camera_name="",
            start_time=row.start_time,
            end_time=row.end_time,
            segment_start_seconds=0,
            segment_end_seconds=12,
            description=row.vlm_description,
            fused_score=score,
        )

    found = evidence_results(
        session, [hit(w["w18"], 0.9), hit(w["x9"], 0.5)], Settings(_env_file=None)
    )
    assert [r.camera_id for r in found] == [camera.id, w["x9"].camera_id]
    best = found[0]
    # w18 with its neighbours w9 and w30: 12:00:09-12:00:42 over both segments, no gap.
    assert (best.start, best.end) == (at("12:00:09"), at("12:00:42"))
    parts = best.manifest.parts
    assert [(p.segment_id, p.source_start_ms, p.source_end_ms) for p in parts] == [
        (segments[0].id, 9_000, 30_000),
        (segments[1].id, 0, 12_000),
    ]
    assert evidence_results(session, [], Settings(_env_file=None)) == []
