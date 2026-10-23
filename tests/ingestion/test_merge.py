import pytest

from services.ingestion.file_ingest import Interval, pad_and_merge, static_gaps


def spans(intervals):
    return [(i.start, i.end) for i in intervals]


def test_pre_and_post_roll_are_added():
    assert spans(pad_and_merge([Interval(20, 25)], 10, 10, 90)) == [(10, 35)]


def test_padding_is_clamped_to_the_file():
    assert spans(pad_and_merge([Interval(3, 5), Interval(85, 88)], 10, 10, 90)) == [
        (0, 15),
        (75, 90),
    ]


def test_overlapping_after_padding_merge():
    # 20-25 -> 10-35 and 40-45 -> 30-55 overlap.
    assert spans(pad_and_merge([Interval(40, 45), Interval(20, 25)], 10, 10, 90)) == [(10, 55)]


def test_touching_parts_merge():
    # 10-35 and 35-60 touch at 35.
    assert spans(pad_and_merge([Interval(20, 25), Interval(45, 50)], 10, 10, 90)) == [(10, 60)]


def test_separate_parts_stay_separate():
    assert spans(pad_and_merge([Interval(20, 25), Interval(60, 65)], 10, 10, 90)) == [
        (10, 35),
        (50, 75),
    ]


def test_merged_score_is_weighted_by_length():
    merged = pad_and_merge([Interval(20, 30, 0.1), Interval(32, 34, 0.4)], 5, 5, 90)
    assert len(merged) == 1
    # Lengths 10 s and 2 s: (0.1 * 10 + 0.4 * 2) / 12
    assert merged[0].score == pytest.approx(0.15)


def test_a_point_interval_counts_as_one_second():
    merged = pad_and_merge([Interval(20, 20, 0.2), Interval(21, 21, 0.4)], 1, 1, 90)
    assert merged[0].score == pytest.approx(0.3)


def test_nothing_in_nothing_out():
    assert pad_and_merge([], 10, 10, 90) == []


def test_static_gaps_between_and_around_parts():
    parts = [Interval(10, 35), Interval(50, 75)]
    assert spans(static_gaps(parts, 90)) == [(0, 10), (35, 50), (75, 90)]


def test_static_gaps_without_activity_is_the_whole_file():
    assert spans(static_gaps([], 42.5)) == [(0, 42.5)]


def test_static_gaps_when_everything_is_active():
    assert static_gaps([Interval(0, 30)], 30) == []


def test_gaps_shorter_than_a_second_are_dropped():
    parts = [Interval(0.5, 20), Interval(20.6, 40)]
    assert spans(static_gaps(parts, 40.4)) == []
