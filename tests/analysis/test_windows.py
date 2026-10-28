"""Window planning and keyframe times (ADR 0021)."""

import itertools
from uuid import uuid4

import pytest

from services.analysis.windows import Window, keyframe_id, keyframe_times, plan_windows, window_id


def spans(duration: float) -> list[tuple[float, float]]:
    return [(w.start, w.end) for w in plan_windows(duration, length=12, overlap=3)]


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        (1, [(0, 1)]),
        (5, [(0, 5)]),
        (12, [(0, 12)]),
        (12.5, [(0, 12.5)]),
        (20, [(0, 12), (8, 20)]),
        (30, [(0, 12), (9, 21), (18, 30)]),
        (30.02, [(0, 12), (9, 21), (18, 30.02)]),
        (35, [(0, 12), (9, 21), (18, 30), (23, 35)]),
        (50, [(0, 12), (9, 21), (18, 30), (27, 39), (36, 48), (38, 50)]),
        (61, [(0, 12), (9, 21), (18, 30), (27, 39), (36, 48), (45, 57), (49, 61)]),
    ],
)
def test_windows_of_a_segment(duration, expected):
    assert spans(duration) == pytest.approx(expected)


@pytest.mark.parametrize("duration", [1, 7.3, 12, 13.4, 20, 29.9, 30, 47, 61, 600.5])
def test_windows_cover_every_second_and_never_pass_the_end(duration):
    windows = plan_windows(duration, 12, 3)
    assert windows[0].start == 0
    assert windows[-1].end == pytest.approx(duration)
    for before, after in itertools.pairwise(windows):
        assert after.start <= before.end  # no gap
    starts = [w.start for w in windows[:-1]]
    assert all(b - a == pytest.approx(9) for a, b in itertools.pairwise(starts))
    assert all(w.seconds <= 12 + 1 for w in windows)
    assert [w.number for w in windows] == list(range(len(windows)))


def test_a_segment_needs_a_duration():
    with pytest.raises(ValueError):
        plan_windows(0, 12, 3)


def test_keyframes_are_the_middles_of_six_parts():
    assert keyframe_times(Window(0, 0, 12), 6) == pytest.approx([1, 3, 5, 7, 9, 11])
    assert keyframe_times(Window(2, 18, 30), 6) == pytest.approx([19, 21, 23, 25, 27, 29])
    assert keyframe_times(Window(0, 0, 3), 6) == pytest.approx([0.25, 0.75, 1.25, 1.75, 2.25, 2.75])


def test_ids_are_the_same_on_every_run():
    segment = uuid4()
    assert window_id(segment, 1) == window_id(segment, 1)
    assert window_id(segment, 1) != window_id(segment, 2)
    assert window_id(segment, 1) != window_id(uuid4(), 1)
    wid = window_id(segment, 0)
    assert keyframe_id(wid, 1) == keyframe_id(wid, 1) != keyframe_id(wid, 2)
