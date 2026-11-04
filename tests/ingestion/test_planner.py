"""SegmentPlanner: which fragments of a live camera become segments (no I/O, no FFmpeg)."""

from pathlib import Path

import pytest

from services.ingestion.planner import (
    DropFragments,
    KeepSegment,
    KeepStatic,
    PlannerConfig,
    SegmentPlanner,
)
from shared.video.change_detector import ActivityEnded, ActivityStarted, Sample, State
from shared.video.stream import Fragment

CONFIG = PlannerConfig(
    pre_roll=10, post_roll=10, max_segment=30, rolling_buffer=60, health_interval=600
)
STABLE_TIMEOUT = 10  # the detector's: ActivityEnded comes this long after the last motion


def fragment(index: int, length: float = 5.0, offset: float = 0.0) -> Fragment:
    start = offset + index * length
    return Fragment(index, Path(f"frag-{index:06d}.ts"), start, start + length)


def simulate(
    planner: SegmentPlanner,
    seconds: int,
    activities: list[tuple[int, int]] = (),
    lag: float = 0.0,
    fragment_length: float = 5.0,
    finish: bool = True,
    partial: Fragment | None = None,
    timeline: list | None = None,
) -> list:
    """Feed one sample per second, as the detector would report the activities.

    A fragment is listed once its end has passed; with `lag`, that many seconds *earlier* than
    the detector reaches it (FFmpeg runs ahead while frames wait in the queue).
    """
    events, changes = {}, set()
    for start, end in activities:
        events[start + 1] = ActivityStarted(start)  # confirmed by the second change sample
        events[end + STABLE_TIMEOUT] = ActivityEnded(start, end, 0.02)
        changes.update(range(start, end + 1))
    actions, index = [], 0
    for t in range(seconds + 1):
        changed = 0.02 if t in changes else 0.0
        step = planner.on_sample(Sample(t, changed, 0.9, t in changes, State.IDLE, events.get(t)))
        listed = []
        while (index + 1) * fragment_length <= min(t + lag, seconds):
            listed.append(fragment(index, fragment_length))
            index += 1
        step += planner.on_fragments(listed)
        actions += step
        if timeline is not None:
            timeline += [(t, action) for action in step]
    if finish:
        actions += planner.finish(partial)
    return actions


def segments(actions) -> list[tuple[float, float]]:
    return [(a.start, a.end) for a in actions if isinstance(a, KeepSegment)]


def statics(actions) -> list[tuple[float, float]]:
    return [(a.start, a.end) for a in actions if isinstance(a, KeepStatic)]


def dropped(actions) -> set[int]:
    return {f.index for a in actions if isinstance(a, DropFragments) for f in a.fragments}


def kept(actions) -> set[int]:
    return {f.index for a in actions if isinstance(a, KeepSegment) for f in a.fragments}


def test_one_activity_keeps_ten_seconds_before_and_after():
    actions = simulate(SegmentPlanner(CONFIG), 90, [(20, 25)])
    assert segments(actions) == [(10, 35)]
    assert statics(actions) == [(0, 10), (35, 90)]
    # Every fragment is either stored or deleted, exactly once.
    assert kept(actions) == {2, 3, 4, 5, 6}
    assert kept(actions) | dropped(actions) == set(range(18))
    assert not kept(actions) & dropped(actions)


def test_two_activities_become_two_segments_like_an_upload():
    actions = simulate(SegmentPlanner(CONFIG), 90, [(20, 25), (60, 65)])
    assert segments(actions) == [(10, 35), (50, 75)]
    assert statics(actions) == [(0, 10), (35, 50), (75, 90)]


def test_the_segment_is_handed_over_when_its_post_roll_fragment_is_finished():
    timeline = []
    simulate(SegmentPlanner(CONFIG), 90, [(20, 25)], timeline=timeline)
    # Last motion at 25, post-roll to 35: stored as soon as the 30-35 s fragment is listed.
    assert [t for t, action in timeline if isinstance(action, KeepSegment)] == [35]


def test_pre_roll_is_whole_fragments_so_it_can_be_longer():
    # Fragments of 4 s: 20 - 10 = 10 s falls in 8-12, so the segment starts at 8 (12 s before).
    actions = simulate(SegmentPlanner(CONFIG), 60, [(20, 25)], fragment_length=4)
    assert segments(actions) == [(8, 36)]


def test_activity_right_at_the_start_begins_with_the_first_fragment():
    actions = simulate(SegmentPlanner(CONFIG), 40, [(3, 6)])
    assert segments(actions) == [(0, 20)]
    assert statics(actions) == [(20, 40)]  # nothing before it: no static period under 1 s


def test_a_later_pre_roll_never_reaches_back_into_the_previous_segment():
    actions = simulate(SegmentPlanner(CONFIG), 90, [(20, 25), (47, 50)])
    assert segments(actions) == [(10, 35), (35, 60)]
    assert statics(actions) == [(0, 10), (60, 90)]


def test_activity_during_the_post_roll_continues_the_same_segment():
    planner = SegmentPlanner(
        PlannerConfig(
            pre_roll=10, post_roll=20, max_segment=300, rolling_buffer=60, health_interval=600
        )
    )
    # The first activity ends at 25 (until 45); the next one starts at 38, before that.
    actions = simulate(planner, 90, [(20, 25), (38, 40)])
    assert segments(actions) == [(10, 60)]


def test_long_activity_is_stored_as_back_to_back_segments_of_at_most_30_s():
    actions = simulate(SegmentPlanner(CONFIG), 150, [(20, 100)])
    parts = segments(actions)
    assert parts == [(10, 40), (40, 70), (70, 100), (100, 110)]
    assert all(end - start <= 30 for start, end in parts)
    assert statics(actions) == [(0, 10), (110, 150)]


def test_fragments_listed_before_the_detector_reaches_them_change_nothing():
    for activities in ([(20, 25), (60, 65)], [(20, 100)]):
        on_time = simulate(SegmentPlanner(CONFIG), 150, activities)
        ahead = simulate(SegmentPlanner(CONFIG), 150, activities, lag=30)
        assert segments(ahead) == segments(on_time)
        assert statics(ahead) == statics(on_time)


def test_stream_ending_mid_activity_keeps_what_was_recorded():
    partial = Fragment(6, Path("frag-000006.ts"), 30, 33)
    actions = simulate(SegmentPlanner(CONFIG), 32, [(20, 40)], partial=partial)
    assert segments(actions) == [(10, 33)]
    assert 6 in kept(actions)


def test_the_activity_score_is_the_mean_of_the_change_samples_in_the_segment():
    actions = simulate(SegmentPlanner(CONFIG), 90, [(20, 25)])
    (segment,) = [a for a in actions if isinstance(a, KeepSegment)]
    assert segment.score == pytest.approx(0.02)


def test_static_periods_are_closed_every_health_interval_without_overlapping_a_segment():
    actions = simulate(SegmentPlanner(CONFIG), 1500, [(665, 670)])
    # Each chunk ends a rolling buffer (60 s) behind the newest frame when it is closed.
    assert statics(actions) == [(0, 600), (600, 655), (680, 1280), (1280, 1500)]
    assert segments(actions) == [(655, 680)]


def test_old_fragments_are_deleted_while_idle():
    planner = SegmentPlanner(CONFIG)
    actions = simulate(planner, 100, finish=False)
    # At t = 100 the buffer holds the last 60 s; fragments ending before 40 are gone.
    assert dropped(actions) == set(range(7))


def test_fragments_of_an_open_segment_are_never_deleted():
    planner = SegmentPlanner(
        PlannerConfig(
            pre_roll=10, post_roll=10, max_segment=1000, rolling_buffer=60, health_interval=600
        )
    )
    actions = simulate(planner, 300, [(20, 200)])
    assert segments(actions) == [(10, 210)]
    assert not dropped(actions) & kept(actions)


def test_fragments_a_little_shorter_than_5_s_still_split_at_about_30_s():
    # Stream timestamps: 4.99 s fragments add up to 29.94 s after six.
    actions = simulate(SegmentPlanner(CONFIG), 150, [(20, 100)], fragment_length=4.99)
    parts = segments(actions)
    assert len(parts) == 4
    assert all(end - start <= 30.5 for start, end in parts)
    assert [round(end - start) for start, end in parts[:3]] == [30, 30, 30]


def test_fragments_listed_late_still_split_at_30_s():
    # Fragments listed 4 s after the detector has passed them (a slow segment list).
    actions = simulate(SegmentPlanner(CONFIG), 90, [(24, 32)], lag=-4)
    assert segments(actions) == [(10, 40), (40, 45)]


def test_a_segment_that_would_close_longer_than_the_maximum_is_split_first():
    planner = SegmentPlanner(CONFIG)
    actions = simulate(planner, 44, [(24, 32)], finish=False, lag=-10)  # up to 30 s listed
    assert segments(actions) == []
    # The post-roll is reached (until 42) and the last three fragments arrive together.
    late = planner.on_fragments([fragment(6), fragment(7), fragment(8)])
    assert segments(late) == [(10, 40), (40, 45)]
