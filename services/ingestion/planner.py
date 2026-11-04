"""Which fragments of a live camera are stored, and as which segments (ADR 0014, 0015).

A pure state machine on stream time (seconds since the FFmpeg run started): change detector
samples and finished fragments go in, actions come out. It does no I/O, so it is tested directly;
the camera worker carries the actions out.

- Activity at s: the segment begins with the fragment containing s - pre_roll (never before the
  end of the previous stored segment, so nothing is stored twice).
- Activity ends at e: the segment ends with the fragment containing e + post_roll. Activity that
  starts again before that continues the same segment.
- A segment that reaches max_segment while still active is stored, and the next one begins with
  the next fragment (back to back).
- Time outside segments is a static period, closed when a segment begins, every health interval,
  and when the stream ends.
- Fragments older than the rolling buffer are deleted unless an open segment needs them.
"""

from collections import deque
from dataclasses import dataclass

from shared.config import Settings
from shared.video.change_detector import ActivityEnded, ActivityStarted, Sample
from shared.video.stream import Fragment

# Static periods shorter than this are not worth a row (as for uploads).
MIN_STATIC_SECONDS = 1.0
# Fragment times are the stream's own timestamps: six "5 s" fragments can add up to 29.96 s.
# A part this close to max_segment counts as full.
SPLIT_SLACK_SECONDS = 0.5


@dataclass(frozen=True)
class KeepSegment:
    fragments: tuple[Fragment, ...]
    # Mean changed fraction of the change samples inside the segment (0-1).
    score: float

    @property
    def start(self) -> float:
        return self.fragments[0].start

    @property
    def end(self) -> float:
        return self.fragments[-1].end


@dataclass(frozen=True)
class KeepStatic:
    start: float
    end: float


@dataclass(frozen=True)
class DropFragments:
    fragments: tuple[Fragment, ...]


Action = KeepSegment | KeepStatic | DropFragments


@dataclass(frozen=True)
class PlannerConfig:
    pre_roll: float
    post_roll: float
    max_segment: float
    rolling_buffer: float
    health_interval: float

    @classmethod
    def from_settings(cls, settings: Settings) -> "PlannerConfig":
        return cls(
            pre_roll=settings.pre_roll_seconds,
            post_roll=settings.post_roll_seconds,
            max_segment=settings.max_segment_seconds,
            rolling_buffer=settings.rolling_buffer_seconds,
            health_interval=settings.health_snapshot_interval_seconds,
        )


class SegmentPlanner:
    def __init__(self, config: PlannerConfig):
        self.config = config
        self._buffer: list[Fragment] = []  # finished fragments still on disk, in order
        self._next_index = 0  # the fragment FFmpeg is writing now
        self._last_end = 0.0  # end of the newest finished fragment
        self._open_from: int | None = None  # first fragment of the open segment
        self._until: float | None = None  # the open segment ends at the fragment containing this
        self._kept_until = 0.0  # end of the last stored segment
        self._static_from: float | None = 0.0  # start of the open static period
        self._changes: deque[tuple[float, float]] = deque()  # (t, changed) of change samples
        self._latest = 0.0

    @property
    def active(self) -> bool:
        """A segment is open (activity, or waiting for its post-roll)."""
        return self._open_from is not None

    def on_sample(self, sample: Sample) -> list[Action]:
        self._latest = max(self._latest, sample.t)
        if sample.is_change:
            self._changes.append((sample.t, sample.changed))
        actions: list[Action] = []
        if isinstance(sample.event, ActivityStarted):
            actions += self._begin(sample.event.start)
        elif isinstance(sample.event, ActivityEnded) and self._open_from is not None:
            self._until = sample.event.end + self.config.post_roll
        actions += self._progress()
        actions += self._health_chunk()
        return actions + self._trim()

    def on_fragments(self, fragments: list[Fragment]) -> list[Action]:
        for fragment in fragments:
            self._buffer.append(fragment)
            self._next_index = fragment.index + 1
            self._last_end = fragment.end
        return self._progress() + self._trim()

    def finish(self, partial: Fragment | None = None) -> list[Action]:
        """The stream ended: an open segment keeps what was recorded; the buffer is dropped."""
        actions = self.on_fragments([partial]) if partial is not None else []
        if self._open_from is not None:
            actions += self._keep(self._open_fragments())
            self._open_from = self._until = None
            self._static_from = self._kept_until
        actions += self._close_static(max(self._latest, self._last_end))
        if self._buffer:
            actions.append(DropFragments(tuple(self._buffer)))
            self._buffer = []
        return actions

    # --- internals ------------------------------------------------------------------------

    def _begin(self, start: float) -> list[Action]:
        if self._open_from is not None:
            # Still waiting for the previous segment's post-roll: the two become one segment.
            self._until = None
            return []
        want = max(start - self.config.pre_roll, self._kept_until)
        first = next((f for f in self._buffer if f.end > want), None)
        if first is None:  # it falls in the fragment FFmpeg is writing now
            self._open_from, begins = self._next_index, self._last_end
        else:
            self._open_from, begins = first.index, first.start
        self._until = None
        return self._close_static(begins)

    def _progress(self) -> list[Action]:
        """Store the open segment, or its first part, once the detector has seen far enough.

        Fragments can be listed before the detector reaches them (FFmpeg runs ahead while
        frames wait in the queue), so only time the detector has seen is decided on.
        """
        actions: list[Action] = []
        while self._open_from is not None:
            fragments = self._open_fragments()
            last = None
            if self._until is not None and self._latest >= self._until:
                last = next((f for f in fragments if f.end >= self._until), None)
            if last is not None:
                usable = [f for f in fragments if f.index <= last.index]
            else:
                usable = [f for f in fragments if f.end <= self._latest]
            part = self._full_part(usable)
            closes_with_part = last is not None and part and part[-1].index == last.index
            if part and not closes_with_part:
                # Long activity: store max_segment of it and carry on (ADR 0015).
                actions += self._keep(part)
                self._open_from = part[-1].index + 1
                continue
            if last is not None:
                actions += self._keep(usable)
                self._open_from = self._until = None
                self._static_from = self._kept_until
            break
        return actions

    def _full_part(self, fragments: list[Fragment]) -> list[Fragment]:
        """The first fragments that together reach max_segment, or [] if they don't."""
        for n, fragment in enumerate(fragments):
            span = fragment.end - fragments[0].start
            if span >= self.config.max_segment - SPLIT_SLACK_SECONDS:
                return fragments[: n + 1]
        return []

    def _open_fragments(self) -> list[Fragment]:
        return [f for f in self._buffer if f.index >= self._open_from]

    def _keep(self, fragments: list[Fragment]) -> list[Action]:
        if not fragments:
            return []
        kept = {f.index for f in fragments}
        self._buffer = [f for f in self._buffer if f.index not in kept]
        start, end = fragments[0].start, fragments[-1].end
        scores = [changed for t, changed in self._changes if start <= t <= end]
        self._kept_until = end
        return [KeepSegment(tuple(fragments), sum(scores) / len(scores) if scores else 0.0)]

    def _close_static(self, end: float) -> list[Action]:
        start, self._static_from = self._static_from, None
        if start is None or end - start < MIN_STATIC_SECONDS:
            return []
        return [KeepStatic(start, end)]

    def _health_chunk(self) -> list[Action]:
        """Close the static period every health interval, so the camera shows it is alive.

        The chunk ends a rolling buffer behind the newest frame: a segment beginning later, with
        its pre-roll, can never start before that point, so the two never overlap.
        """
        if self._static_from is None:
            return []
        end = self._latest - self.config.rolling_buffer
        if end - self._static_from < self.config.health_interval:
            return []
        actions = self._close_static(end)
        self._static_from = end
        return actions

    def _trim(self) -> list[Action]:
        horizon = self._latest - self.config.rolling_buffer
        old = [
            f
            for f in self._buffer
            if f.end < horizon and (self._open_from is None or f.index < self._open_from)
        ]
        if old:
            dropped = {f.index for f in old}
            self._buffer = [f for f in self._buffer if f.index not in dropped]
        # Change samples only matter for segments still to be stored.
        keep_from = (
            self._kept_until if self._open_from is not None else max(self._kept_until, horizon)
        )
        while self._changes and self._changes[0][0] < keep_from:
            self._changes.popleft()
        return [DropFragments(tuple(old))] if old else []
