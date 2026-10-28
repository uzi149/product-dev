"""Frames nearest to given times, read in one forward pass.

Like `sample_frames`, this goes forward through the file and uses the stream timestamps instead
of seeking: seeking in compressed or variable-frame-rate video lands on the wrong frame. One
pass serves every window of a segment, and frames are handed out as soon as they are found, so
a long segment never holds all its keyframes in memory.
"""

from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np

from shared.video.frames import FrameReadError

# Frames further than this before a wanted time can't be the nearest; they aren't decoded.
_LOOKBACK_SECONDS = 1.0

Found = tuple[float, float, np.ndarray]  # (wanted time, the frame's time, BGR frame)


def iter_frames_at(path: str | Path, times: list[float]) -> Iterator[Found]:
    """The nearest frame for each distinct time, in time order.

    Times after the last frame get the last frame. Raises FrameReadError when the file can't be
    opened or has no frames.
    """
    wanted = sorted(set(times))
    capture = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
    if not capture.isOpened():
        raise FrameReadError(f"could not open {path}")
    previous: tuple[float, np.ndarray] | None = None
    last_t: float | None = None
    index = 0
    try:
        while index < len(wanted) and capture.grab():
            t = last_t = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000
            if t < wanted[index] - _LOOKBACK_SECONDS:
                continue
            ok, frame = capture.retrieve()
            if not ok:
                raise FrameReadError(f"could not decode the frame at {t:.2f} s in {path}")
            # Every wanted time this frame has reached: the nearer of it and the one before.
            while index < len(wanted) and t >= wanted[index]:
                target = wanted[index]
                if previous is not None and target - previous[0] < t - target:
                    yield target, *previous
                else:
                    yield target, t, frame
                index += 1
            previous = (t, frame)
    finally:
        capture.release()
    if last_t is None:
        raise FrameReadError(f"no frames in {path}")
    if index < len(wanted):
        # Times after the last frame (the stored duration was a little long): the last frame,
        # read in a second pass when it wasn't decoded, as frames far before a wanted time aren't.
        if previous is not None and previous[0] == last_t:
            t, frame = previous
        else:
            _, t, frame = next(iter_frames_at(path, [last_t]))
        for target in wanted[index:]:
            yield target, t, frame


def frames_at(path: str | Path, times: list[float]) -> list[tuple[float, np.ndarray]]:
    """For each time, in the given order, the nearest frame as (its time, BGR frame)."""
    found = {target: (t, frame) for target, t, frame in iter_frames_at(path, times)}
    return [found[t] for t in times]
