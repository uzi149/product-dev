"""Frames nearest to given times, in one forward pass."""

import cv2
import numpy as np
import pytest

from shared.video.frames import FrameReadError
from shared.video.keyframes import frames_at, iter_frames_at

FPS = 10
FRAMES = 60  # 6 s


@pytest.fixture(scope="module")
def counter_video(tmp_path_factory):
    """Frame n is a flat grey of 4 * n, so a frame tells its own number."""
    path = tmp_path_factory.mktemp("keyframes") / "counter.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (64, 48))
    for n in range(FRAMES):
        writer.write(np.full((48, 64, 3), 4 * n, np.uint8))
    writer.release()
    return path


def number(frame: np.ndarray) -> int:
    return round(float(frame.mean()) / 4)


def test_each_time_gets_its_nearest_frame(counter_video):
    times = [0.0, 1.0, 2.04, 2.06, 3.33, 5.9]
    found = frames_at(counter_video, times)
    assert [number(f) for _, f in found] == [0, 10, 20, 21, 33, 59]
    assert [t for t, _ in found] == pytest.approx([0.0, 1.0, 2.0, 2.1, 3.3, 5.9])


def test_the_given_order_and_repeats_are_kept(counter_video):
    found = frames_at(counter_video, [4.0, 1.0, 4.0])
    assert [number(f) for _, f in found] == [40, 10, 40]


def test_times_after_the_end_get_the_last_frame(counter_video):
    found = frames_at(counter_video, [1.0, 7.5, 20.0])
    assert [number(f) for _, f in found] == [10, 59, 59]


def test_frames_come_in_time_order_as_they_are_found(counter_video):
    found = list(iter_frames_at(counter_video, [3.0, 0.5, 2.0]))
    assert [target for target, _, _ in found] == [0.5, 2.0, 3.0]


def test_an_unreadable_file_is_an_error(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(FrameReadError):
        frames_at(bad, [1.0])
