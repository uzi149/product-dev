"""Tracks of grounded boxes through a clip (step 5.7, ADR 0038).

Adapted from the reference repository's sparse tracker and optical-flow propagation:

- `SparseTracker` links keyframe boxes (about 1 s apart) into tracks. IoU matching breaks down at
  that spacing (a walking person moves more than their own width), so it matches on a
  constant-velocity (or optical-flow) prediction with a gate on the centre distance measured in
  box diagonals. Changed: the reference solves the assignment with scipy; here it is greedy in
  order of cost (a handful of objects per scene; no extra package).
- `Propagator` moves every live box between keyframes with sparse Lucas-Kanade flow on a
  downscaled grey frame (plain OpenCV, no model calls) and hands the flow positions to the
  tracker at each keyframe, so fast movers stay one track. Changed: frames are fed one at a time
  instead of read from one file, so contiguous parts from different segments are one clip.

Boxes here are in pixels of the video (x1, y1, x2, y2).
"""

from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass, field

import cv2
import numpy as np

_LK_WIN = (15, 15)
_LK_LEVELS = 3
_LK_CRITERIA = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 10, 0.03)
_MAX_POINTS = 40
_MIN_POINTS = 4
_RESEED_EVERY = 10  # frames
_FB_ERROR = 1.0  # most forward-backward flow error (px at flow size)
_MAX_SCALE_STEP = 0.05  # most box size change per frame
_MIN_VISIBLE = 0.3  # stop following a box once less of it is inside the picture
FLOW_SIZE = 640

Box = np.ndarray  # float [x1, y1, x2, y2], video pixels


def _centre(b: Sequence[float]) -> np.ndarray:
    return np.array([(b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0])


def _diag(b: Sequence[float]) -> float:
    return float(np.hypot(b[2] - b[0], b[3] - b[1])) or 1.0


def iou(a: Sequence[float], b: Sequence[float]) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


@dataclass
class Track:
    id: int
    label: str
    box: Box
    first_frame: int
    last_frame: int
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(2))
    missed: int = 0
    # (keyframe index, box) where the model saw it
    sightings: list[tuple[int, Box]] = field(default_factory=list)


class SparseTracker:
    """Label-aware tracker for keyframes about 1 s apart."""

    def __init__(self, max_missed: int = 2, distance_gate: float = 1.2, first_id: int = 1):
        """`max_missed`: keyframes a track may go unmatched before it ends. `distance_gate`: most
        centre distance (in box diagonals) between a prediction and a box for them to match."""
        self.max_missed = max_missed
        self.distance_gate = distance_gate
        self.next_id = first_id
        self.active: dict[int, Track] = {}
        self.tracks: dict[int, Track] = {}

    def _predict(self, track: Track, frame: int) -> Box:
        dx, dy = track.velocity * (frame - track.last_frame)
        return track.box + np.array([dx, dy, dx, dy])

    def update(
        self,
        detections: Sequence[tuple[str, Box]],
        frame: int,
        predicted: dict[int, Box] | None = None,
    ) -> list[int]:
        """Match a keyframe's (label, box) detections to the live tracks; returns each
        detection's track id. `predicted`: where each track is expected (optical flow); others
        use their constant-velocity prediction."""
        predicted = predicted or {}
        pairs: list[tuple[float, int, int]] = []
        for tid, track in self.active.items():
            pred = predicted.get(tid)
            if pred is None:
                pred = self._predict(track, frame)
            for j, (label, box) in enumerate(detections):
                if label != track.label:
                    continue
                dist = np.linalg.norm(_centre(pred) - _centre(box))
                norm = dist / max(_diag(pred), _diag(box))
                if norm <= self.distance_gate:
                    pairs.append((norm + (1.0 - iou(pred, box)), tid, j))
        matches: dict[int, int] = {}
        used: set[int] = set()
        for _, tid, j in sorted(pairs):
            if j not in matches and tid not in used:
                matches[j] = tid
                used.add(tid)

        ids: list[int] = []
        for j, (label, box) in enumerate(detections):
            box = np.asarray(box, dtype=float)
            tid = matches.get(j)
            if tid is None:
                tid = self.next_id
                self.next_id += 1
                track = Track(tid, label, box, frame, frame)
                self.active[tid] = self.tracks[tid] = track
            else:
                track = self.active[tid]
                dt = max(1, frame - track.last_frame)
                new_v = (_centre(box) - _centre(track.box)) / dt
                # Smoothed, so one loose box doesn't throw off the prediction.
                track.velocity = (
                    new_v if len(track.sightings) < 2 else 0.6 * new_v + 0.4 * track.velocity
                )
                track.box, track.last_frame, track.missed = box, frame, 0
            track.sightings.append((frame, box.copy()))
            ids.append(tid)

        for tid in list(self.active):
            if tid not in used and tid not in ids:
                self.active[tid].missed += 1
                if self.active[tid].missed > self.max_missed:
                    del self.active[tid]
        return ids


class _Flow:
    """Sparse LK flow on downscaled grey frames; boxes stay in video pixels."""

    def __init__(self, width: int, height: int, flow_size: int):
        self.width, self.height = width, height
        self.scale = min(1.0, flow_size / float(max(width, height)))

    def gray(self, frame: np.ndarray) -> np.ndarray:
        if self.scale < 1.0:
            frame = cv2.resize(
                frame, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_LINEAR
            )
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    def seed(self, gray: np.ndarray, box: Box) -> np.ndarray | None:
        """Corner points inside the box (its inner part, to keep off the background)."""
        x1, y1, x2, y2 = box * self.scale
        mx, my = (x2 - x1) * 0.15, (y2 - y1) * 0.15
        h, w = gray.shape
        ix1, iy1 = max(0, int(x1 + mx)), max(0, int(y1 + my))
        ix2, iy2 = min(w, int(np.ceil(x2 - mx))), min(h, int(np.ceil(y2 - my)))
        if ix2 - ix1 < 3 or iy2 - iy1 < 3:
            return None
        pts = cv2.goodFeaturesToTrack(gray[iy1:iy2, ix1:ix2], _MAX_POINTS, 0.01, 3)
        if pts is None or len(pts) < _MIN_POINTS:
            return None
        return (pts + np.array([ix1, iy1], dtype=np.float32)).astype(np.float32)

    @staticmethod
    def track(
        prev: np.ndarray, cur: np.ndarray, groups: list[np.ndarray]
    ) -> list[tuple[np.ndarray, np.ndarray] | None]:
        """Flow several point groups in one call: per group the (old, new) points that passed a
        forward-backward check, or None when too few did."""
        p0 = np.concatenate(groups).astype(np.float32)
        p1, st, _ = cv2.calcOpticalFlowPyrLK(prev, cur, p0, np.empty_like(p0), winSize=_LK_WIN,
                                             maxLevel=_LK_LEVELS, criteria=_LK_CRITERIA)  # fmt: skip
        back, st2, _ = cv2.calcOpticalFlowPyrLK(cur, prev, p1, np.empty_like(p1), winSize=_LK_WIN,
                                                maxLevel=_LK_LEVELS, criteria=_LK_CRITERIA)  # fmt: skip
        fb = np.linalg.norm((p0 - back).reshape(-1, 2), axis=1)
        ok = (st.ravel() == 1) & (st2.ravel() == 1) & (fb < _FB_ERROR)
        out: list[tuple[np.ndarray, np.ndarray] | None] = []
        start = 0
        for group in groups:
            sl = slice(start, start + len(group))
            start += len(group)
            good = ok[sl]
            if good.sum() < _MIN_POINTS:
                out.append(None)
            else:
                out.append((p0[sl][good].reshape(-1, 2), p1[sl][good].reshape(-1, 2)))
        return out

    def shift(self, box: Box, old: np.ndarray, new: np.ndarray) -> Box:
        """Move and rescale a box by the median motion of its points."""
        d = np.median(new - old, axis=0) / self.scale
        r0 = np.linalg.norm(old - old.mean(axis=0), axis=1)
        r1 = np.linalg.norm(new - new.mean(axis=0), axis=1)
        spread = r0 > 2
        s = float(np.median(r1[spread] / r0[spread])) if spread.sum() >= _MIN_POINTS else 1.0
        s = float(np.clip(s, 1 - _MAX_SCALE_STEP, 1 + _MAX_SCALE_STEP))
        cx, cy = (box[0] + box[2]) / 2 + d[0], (box[1] + box[3]) / 2 + d[1]
        hw, hh = (box[2] - box[0]) * s / 2, (box[3] - box[1]) * s / 2
        return np.array([cx - hw, cy - hh, cx + hw, cy + hh])

    def visible(self, box: Box) -> bool:
        area = (box[2] - box[0]) * (box[3] - box[1])
        ix = max(0.0, min(box[2], self.width) - max(box[0], 0.0))
        iy = max(0.0, min(box[3], self.height) - max(box[1], 0.0))
        return area > 0 and ix * iy / area >= _MIN_VISIBLE


@dataclass
class _Live:
    box: Box
    pts: np.ndarray | None
    age: int = 0


class Propagator:
    """Follows the tracker's boxes through every frame of one continuous clip.

    Feed the frames in order with `feed(index, frame, detections)`, where `detections` is the
    keyframe's (label, box) list or None for other frames, then call `boxes()`.
    """

    def __init__(self, width: int, height: int, step: int, tracker: SparseTracker,
                 flow_size: int = FLOW_SIZE):  # fmt: skip
        """`step`: frames between keyframes."""
        self.flow = _Flow(width, height, flow_size)
        self.width, self.height = width, height
        self.step = max(1, step)
        self.hold = max(1, self.step // 2)
        self.tracker = tracker
        self.live: dict[int, _Live] = {}
        self.trail: dict[int, dict[int, Box]] = defaultdict(dict)
        self.recent: deque[np.ndarray] = deque(maxlen=self.hold)
        self.prev: np.ndarray | None = None
        self.keyframes: list[int] = []
        self.last_frame = -1

    def feed(
        self, index: int, frame: np.ndarray, detections: Sequence[tuple[str, Box]] | None
    ) -> list[int] | None:
        """Returns the keyframe detections' track ids (None for other frames)."""
        flow = self.flow
        gray = flow.gray(frame)
        if self.prev is not None and self.live:
            ids = [t for t, s in self.live.items() if s.pts is not None]
            results = flow.track(self.prev, gray, [self.live[t].pts for t in ids]) if ids else []
            moved = dict(zip(ids, results, strict=True))
            for tid in list(self.live):
                state = self.live[tid]
                res = moved.get(tid)
                if res is not None:
                    state.box = flow.shift(state.box, *res)
                    state.pts = res[1].reshape(-1, 1, 2)
                else:
                    # No texture, or lost: coast on the keyframe velocity.
                    vx, vy = self.tracker.tracks[tid].velocity
                    state.box = state.box + np.array([vx, vy, vx, vy])
                    state.pts = None
                if not flow.visible(state.box):
                    del self.live[tid]
                    continue
                state.age += 1
                if state.pts is None or state.age >= _RESEED_EVERY:
                    state.pts = flow.seed(gray, state.box)
                    state.age = 0

        ids: list[int] | None = None
        if detections is not None:
            self.keyframes.append(index)
            predicted = {t: s.box for t, s in self.live.items()}
            ids = self.tracker.update(detections, index, predicted)
            self._backfill(gray, index, [(tid, np.asarray(b, dtype=float))
                                         for tid, (_, b) in zip(ids, detections, strict=True)
                                         if tid not in self.trail])  # fmt: skip
            for tid, (_, box) in zip(ids, detections, strict=True):
                box = np.asarray(box, dtype=float)
                self.live[tid] = _Live(box, flow.seed(gray, box))
            for tid in list(self.live):
                if tid not in self.tracker.active:
                    del self.live[tid]

        for tid, state in self.live.items():
            self.trail[tid][index] = state.box.copy()
        self.recent.append(gray)
        self.prev = gray
        self.last_frame = index
        return ids

    def _backfill(self, gray: np.ndarray, index: int, new: list[tuple[int, Box]]) -> None:
        """A new track's object was usually visible before the keyframe that found it: follow it
        backwards for up to half a keyframe interval."""
        flow = self.flow
        heads = [(tid, box, pts) for tid, box in new if (pts := flow.seed(gray, box)) is not None]
        cur = gray
        for k, earlier in enumerate(reversed(self.recent), 1):
            if not heads:
                return
            results = flow.track(cur, earlier, [h[2] for h in heads])
            still = []
            for (tid, box, _), res in zip(heads, results, strict=True):
                if res is None:
                    continue
                box = flow.shift(box, *res)
                if not flow.visible(box):
                    continue
                self.trail[tid][index - k] = box.copy()
                still.append((tid, box, res[1].reshape(-1, 1, 2)))
            heads, cur = still, earlier

    def boxes(self) -> dict[int, dict[int, Box]]:
        """track id -> frame index -> box, clipped to the picture.

        A track seen at a single keyframe inside the clip is left out: those are mostly the
        model's mistakes (boxes on an empty road, shadows). One at the first or last keyframe may
        be something entering or leaving. After its last sighting a track is followed only half
        a keyframe interval, unless it was still there at the last keyframe."""
        last_kf = self.keyframes[-1] if self.keyframes else 0
        first_kf = self.keyframes[0] if self.keyframes else 0
        out: dict[int, dict[int, Box]] = {}
        for tid, track in self.tracker.tracks.items():
            seen = [f for f, _ in track.sightings]
            if len(seen) == 1 and first_kf < seen[0] < last_kf:
                continue
            end = self.last_frame if seen[-1] >= last_kf else seen[-1] + self.hold
            frames: dict[int, Box] = {}
            for f, box in sorted(self.trail.get(tid, {}).items()):
                if f > end:
                    continue
                x1, x2 = max(0.0, box[0]), min(float(self.width), box[2])
                y1, y2 = max(0.0, box[1]), min(float(self.height), box[3])
                if x2 > x1 and y2 > y1:
                    frames[f] = np.array([x1, y1, x2, y2])
            if frames:
                out[tid] = frames
        return out
