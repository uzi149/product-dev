"""Evidence marks on a frame (step 5.7): where each track's box is at a moment, and drawing it.

`boxes_at` is the rule the player follows too (frontend `lib/marks.ts`): between two points of a
track the box slides linearly, when they are at most MAX_STEP_MS apart (further apart, the object
wasn't followed in between: nothing is drawn); just before the first point and after the last one
the box is held for HOLD_MS.

The drawing is adapted from the reference repository's video visualizer (box, label on a filled
background), on OpenCV frames.
"""

from bisect import bisect_right
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

MAX_STEP_MS = 600
HOLD_MS = 150

# BGR, the player's colours (frontend lib/marks.ts).
COLOURS = {
    "person": (0, 140, 255),  # orange
    "vehicle": (255, 170, 30),  # blue
    "portable_object": (0, 215, 255),  # yellow
    "animal": (200, 110, 255),  # pink
}
OTHER_COLOUR = (230, 230, 230)
NOT_PICKED_COLOUR = (150, 150, 150)


@dataclass(frozen=True)
class Shown:
    label: str
    kind: str | None
    strong: bool  # picked, or no pick was made
    box: tuple[float, float, float, float]  # 0-1


def _lerp(a: list, b: list, t: float) -> tuple[float, float, float, float]:
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(1, 5))  # type: ignore[return-value]


def track_box_at(points: list[list], ms: float) -> tuple[float, float, float, float] | None:
    """The box of one run (`[ms, x1, y1, x2, y2]` points in time order) at `ms`."""
    if not points:
        return None
    i = bisect_right([p[0] for p in points], ms)
    if i == 0:
        first = points[0]
        return tuple(first[1:5]) if first[0] - ms <= HOLD_MS else None  # type: ignore[return-value]
    if i == len(points):
        last = points[-1]
        return tuple(last[1:5]) if ms - last[0] <= HOLD_MS else None  # type: ignore[return-value]
    a, b = points[i - 1], points[i]
    if b[0] - a[0] > MAX_STEP_MS:
        return None
    return _lerp(a, b, (ms - a[0]) / (b[0] - a[0]) if b[0] > a[0] else 0.0)


def boxes_at(
    tracks: list[dict[str, Any]], pick_status: str | None, segment_id: str, ms: float
) -> list[Shown]:
    """Every track's box in `segment_id` at `ms` (milliseconds in the segment)."""
    picking = pick_status == "done"
    shown: list[Shown] = []
    for track in tracks:
        for run in track["runs"]:
            if str(run["segment_id"]) != str(segment_id):
                continue
            box = track_box_at(run["points"], ms)
            if box is not None:
                strong = track["picked"] or not picking
                shown.append(Shown(track["label"], track.get("kind"), strong, box))
    # The ones the clip is about are drawn last, on top.
    return sorted(shown, key=lambda s: s.strong)


def draw_marks(frame: np.ndarray, shown: list[Shown]) -> np.ndarray:
    """The boxes with their labels, drawn on the frame (in place); returns it."""
    h, w = frame.shape[:2]
    scale = max(0.4, min(w, h) / 900)
    for mark in shown:
        colour = COLOURS.get(mark.kind or "", OTHER_COLOUR) if mark.strong else NOT_PICKED_COLOUR
        thick = max(2, round(3 * scale)) if mark.strong else 1
        x1, y1, x2, y2 = (round(mark.box[0] * w), round(mark.box[1] * h),
                          round(mark.box[2] * w), round(mark.box[3] * h))  # fmt: skip
        cv2.rectangle(frame, (x1, y1), (x2, y2), colour, thick, cv2.LINE_AA)
        if not mark.strong:
            continue
        text_scale = 0.5 * scale + 0.2
        (tw, th), base = cv2.getTextSize(mark.label, cv2.FONT_HERSHEY_SIMPLEX, text_scale, 1)
        top = y1 - th - base - 4 if y1 - th - base - 4 >= 0 else y1
        cv2.rectangle(frame, (x1, top), (x1 + tw + 6, top + th + base + 4), colour, -1)
        cv2.putText(frame, mark.label, (x1 + 3, top + th + 2), cv2.FONT_HERSHEY_SIMPLEX,
                    text_scale, (20, 20, 20), 1, cv2.LINE_AA)  # fmt: skip
    return frame
