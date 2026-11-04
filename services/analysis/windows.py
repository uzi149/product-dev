"""Analysis windows of one segment and their keyframe times (scope §7.2, §9.3, ADR 0021).

Windows are ANALYSIS_WINDOW_SECONDS long (12) and overlap by WINDOW_OVERLAP_SECONDS (3), so a
new one starts every 9 s. Footage left after the last full window gets one more window that
ends at the segment's end, so the end is never cut off. A segment no longer than one window is
one window. Windows never cross into the next segment.
"""

from dataclasses import dataclass
from uuid import UUID, uuid5

# A remainder shorter than this stretches the last window instead of adding a nearly identical
# one (a 30.02 s live segment is 0-12, 9-21, 18-30.02, not four windows).
MIN_EXTRA_SECONDS = 1.0


@dataclass(frozen=True)
class Window:
    number: int  # 0, 1, 2, ... in the segment
    start: float  # seconds into the segment
    end: float

    @property
    def seconds(self) -> float:
        return self.end - self.start


def plan_windows(duration: float, length: float, overlap: float) -> list[Window]:
    if duration <= 0:
        raise ValueError("a segment must have a positive duration")
    if duration <= length + MIN_EXTRA_SECONDS:
        return [Window(0, 0.0, duration)]
    step = length - overlap
    windows: list[Window] = []
    start = 0.0
    while start + length <= duration:
        windows.append(Window(len(windows), start, start + length))
        start += step
    left = duration - windows[-1].end
    if left >= MIN_EXTRA_SECONDS:
        windows.append(Window(len(windows), duration - length, duration))
    elif left > 0:
        last = windows[-1]
        windows[-1] = Window(last.number, last.start, duration)
    return windows


def keyframe_times(window: Window, count: int) -> list[float]:
    """The middle of each of `count` equal parts of the window, in seconds into the segment.

    Evenly spaced like the reference code's scene frames; the middles keep keyframes off the
    window edges, where the overlapping neighbour has its own.
    """
    part = window.seconds / count
    return [window.start + part * (n + 0.5) for n in range(count)]


def window_id(segment_id: UUID, number: int) -> UUID:
    """The same id on every run, so a retried or repeated task never duplicates a window."""
    return uuid5(segment_id, f"window/{number}")


def keyframe_id(window_id: UUID, number: int) -> UUID:
    return uuid5(window_id, f"keyframe/{number}")
