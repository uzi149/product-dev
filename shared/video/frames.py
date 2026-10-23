"""Reading frames from a video file at a fixed sampling rate."""

from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np


class FrameReadError(Exception):
    pass


def sample_frames(path: str | Path, fps: float = 1.0) -> Iterator[tuple[float, np.ndarray]]:
    """Yield (seconds from the start, BGR frame), one frame per 1/fps seconds.

    Uses the stream timestamps, so variable frame rate files are sampled correctly.
    Skipped frames are grabbed without being decoded.
    """
    if fps <= 0:
        raise ValueError("fps must be positive")
    capture = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
    if not capture.isOpened():
        raise FrameReadError(f"could not open {path}")
    step = 1.0 / fps
    next_t = 0.0
    try:
        while capture.grab():
            t = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000
            # Half a millisecond of slack so 0.9999 s still counts as 1 s.
            if t + 0.0005 < next_t:
                continue
            ok, frame = capture.retrieve()
            if not ok:
                raise FrameReadError(f"could not decode the frame at {t:.2f} s in {path}")
            yield t, frame
            # After a gap in the stream, continue from the next step after t.
            next_t += step * (1 + int((t + 0.0005 - next_t) // step))
    finally:
        capture.release()
