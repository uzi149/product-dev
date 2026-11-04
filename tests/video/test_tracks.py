"""Tracking grounded boxes between keyframes about 1 s apart (step 5.7)."""

import cv2
import numpy as np
import pytest

from shared.video.tracks import Propagator, SparseTracker
from tests.marks.videos import FPS, Mover, write_clip
from tests.video.scenes import HEIGHT, WIDTH


def px(box01):
    return np.array([box01[0] * WIDTH, box01[1] * HEIGHT, box01[2] * WIDTH, box01[3] * HEIGHT])


def test_a_fast_walker_stays_one_track():
    """40 px between keyframes is more than the person's width: velocity keeps them one track."""
    tracker = SparseTracker()
    ids = set()
    for k in range(6):
        x = 10 + 40 * k
        ids.update(tracker.update([("person", np.array([x, 100, x + 30, 160]))], k * 10))
    assert ids == {1}
    assert len(tracker.tracks[1].sightings) == 6


def test_two_people_passing_keep_their_tracks():
    tracker = SparseTracker()
    first = second = None
    for k in range(5):
        a = np.array([20 + 30 * k, 100, 50 + 30 * k, 160])
        b = np.array([250 - 30 * k, 105, 280 - 30 * k, 165])
        ids = tracker.update([("person", a), ("person", b)], k * 10)
        if k == 0:
            first, second = ids
        else:
            assert ids == [first, second]


def test_labels_never_mix():
    tracker = SparseTracker()
    tracker.update([("person", np.array([100, 100, 130, 160]))], 0)
    assert tracker.update([("bag", np.array([100, 100, 130, 160]))], 10) == [2]


def test_a_track_ends_after_missed_keyframes():
    tracker = SparseTracker(max_missed=2)
    tracker.update([("person", np.array([100, 100, 130, 160]))], 0)
    for k in (1, 2, 3):
        tracker.update([], k * 10)
    assert tracker.active == {}
    assert tracker.update([("person", np.array([100, 100, 130, 160]))], 40) == [2]


def test_ids_start_where_asked():
    tracker = SparseTracker(first_id=7)
    assert tracker.update([("person", np.array([0, 0, 10, 10]))], 0) == [7]
    assert tracker.next_id == 8


def frames(path):
    capture = cv2.VideoCapture(str(path))
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        yield frame
    capture.release()


@pytest.fixture
def walker_clip(tmp_path):
    mover = Mover("person", seed=1, x0=10, speed=40)  # 4 px per frame, 40 per keyframe
    return write_clip(tmp_path / "walker.avi", 6, [mover]), mover


def test_flow_follows_the_walker_between_keyframes(walker_clip):
    path, mover = walker_clip
    tracker = SparseTracker()
    propagator = Propagator(WIDTH, HEIGHT, FPS, tracker)
    for n, frame in enumerate(frames(path)):
        t = n / FPS
        detections = [("person", px(mover.box(t)))] if n % FPS == 0 and mover.box(t) else None
        propagator.feed(n, frame, detections)
    boxes = propagator.boxes()
    assert list(boxes) == [1]
    # Between keyframes (frame 15 = 1.5 s) the box is where the walker is, within a few px.
    for n in (5, 15, 25, 35):
        truth = px(mover.box(n / FPS))
        assert np.abs(boxes[1][n] - truth).max() <= 4, n


def test_a_one_off_box_inside_the_clip_is_dropped(walker_clip):
    path, mover = walker_clip
    tracker = SparseTracker()
    propagator = Propagator(WIDTH, HEIGHT, FPS, tracker)
    for n, frame in enumerate(frames(path)):
        t = n / FPS
        detections = None
        if n % FPS == 0:
            detections = [("person", px(mover.box(t)))]
            if n == 20:  # the model "sees" a bag once, on the background
                detections.append(("bag", np.array([250.0, 20, 290, 60])))
        propagator.feed(n, frame, detections)
    assert set(propagator.boxes()) == {1}
