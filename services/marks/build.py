"""Marks for one clip (step 5.7, ADR 0038): keyframes → grounding → tracks → pick.

The clip is the playback manifest: video parts in order, with gap parts between separated
footage. Contiguous parts (no gap) are one run: one tracker and one optical-flow pass, so a
person walking across a segment boundary stays one track. A gap ends every track.

1. Keyframes about every `marks_keyframe_seconds`, further apart when the clip would need more
   than `marks_max_keyframes`; read forward by timestamp (no seeking).
2. Each keyframe is grounded by the VLM, `marks_concurrency` at a time. A failed keyframe is
   skipped (the tracks bridge it); when every one fails the clip fails.
3. Each run is read once more, frame by frame, for the tracker and the optical flow.
4. With two or more tracks, one call per batch picks the ones the question is about.
"""

import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import cv2
import numpy as np

from shared.config import Settings
from shared.model_adapters.grounding import Detection, ground, pick_tracks
from shared.model_adapters.images import resize_max_side
from shared.model_adapters.vlm import VisionLanguageModel, VLMError
from shared.schemas.entities import entity_class
from shared.schemas.query import PlaybackManifest, VideoPart
from shared.video.frames import frame_rate, frames_between
from shared.video.keyframes import iter_frames_at
from shared.video.tracks import Propagator, SparseTracker

log = logging.getLogger(__name__)

POINT_SPACING_MS = 200  # about 5 points per second per track
_DEFAULT_FPS = 25.0


class MarksError(Exception):
    """The clip couldn't be marked (no footage, or every keyframe failed)."""


@dataclass
class Built:
    tracks: list[dict]
    pick_status: str  # done, failed, none
    keyframes: int
    grounded: int  # keyframes the VLM answered


@dataclass
class _Keyframe:
    part: int
    t: float  # the frame's time in its segment, s
    image: np.ndarray  # scaled to the grounding size
    detections: list[Detection] | None = None


@dataclass
class _Run:
    parts: list[int] = field(default_factory=list)


def _runs(manifest: PlaybackManifest) -> tuple[list[VideoPart], list[_Run]]:
    parts: list[VideoPart] = []
    runs: list[_Run] = [_Run()]
    for part in manifest.parts:
        if isinstance(part, VideoPart):
            runs[-1].parts.append(len(parts))
            parts.append(part)
        elif runs[-1].parts:
            runs.append(_Run())
    return parts, [r for r in runs if r.parts]


def keyframe_interval(parts: list[VideoPart], every: float, most: int) -> float:
    """Seconds between keyframes: `every`, or longer when the clip would need more than `most`."""
    total = sum(max(0, p.source_end_ms - p.source_start_ms) for p in parts) / 1000
    return max(every, total / most)


def keyframe_times(parts: list[VideoPart], interval: float) -> list[list[float]]:
    """Per part, its keyframe times in its segment (s): evenly spaced about `interval` apart, in
    the middle of equal slices so none sits on the part's edge. At least one per part."""
    times: list[list[float]] = []
    for part in parts:
        start = part.source_start_ms / 1000
        length = max(0.0, (part.source_end_ms - part.source_start_ms) / 1000)
        n = max(1, round(length / interval))
        times.append([start + (k + 0.5) * length / n for k in range(n)])
    return times


def build_marks(
    manifest: PlaybackManifest,
    sources: dict[UUID, Path],
    vlm: VisionLanguageModel,
    labels: list[str],
    question: str,
    settings: Settings,
) -> Built:
    """Marks for the clip; `sources` holds every part's segment file. Raises MarksError, or the
    VLM's error when no keyframe could be grounded."""
    parts, runs = _runs(manifest)
    if not parts:
        raise MarksError("the clip has no stored footage")
    interval = keyframe_interval(
        parts, settings.marks_keyframe_seconds, settings.marks_max_keyframes
    )
    times = keyframe_times(parts, interval)

    # 1. Keyframes.
    keyframes: list[_Keyframe] = []
    for n, part in enumerate(parts):
        for _, t, frame in iter_frames_at(sources[part.segment_id], times[n]):
            if part.source_start_ms / 1000 - 0.5 <= t <= part.source_end_ms / 1000 + 0.5:
                keyframes.append(
                    _Keyframe(n, t, resize_max_side(frame, settings.marks_image_max_side))
                )
    if not keyframes:
        raise MarksError("no frames could be read from the clip")

    # 2. Grounding.
    errors: list[Exception] = []

    def grounded(keyframe: _Keyframe) -> list[Detection] | None:
        try:
            return ground(vlm, keyframe.image, labels, image_max_side=settings.marks_image_max_side,
                          bbox_format=settings.vlm_bbox_format)  # fmt: skip
        except VLMError as error:
            log.warning("keyframe at %.1f s skipped: %s", keyframe.t, error)
            errors.append(error)
            return None

    with ThreadPoolExecutor(max_workers=settings.marks_concurrency) as pool:
        for keyframe, found in zip(keyframes, pool.map(grounded, keyframes), strict=True):
            keyframe.detections = found
    answered = sum(1 for k in keyframes if k.detections is not None)
    if answered == 0:
        raise errors[-1] if errors else MarksError("no keyframe could be grounded")

    # 3. Tracks, run by run.
    by_part: dict[int, list[_Keyframe]] = {}
    for keyframe in keyframes:
        if keyframe.detections is not None:
            by_part.setdefault(keyframe.part, []).append(keyframe)
    tracks: list[dict] = []
    sightings: dict[int, list[tuple[int, tuple[float, float, float, float]]]] = {}
    images: dict[int, np.ndarray] = {}
    next_id = 1
    for run in runs:
        built, next_id = _track_run(
            run, parts, sources, by_part, interval, next_id, sightings, images
        )
        tracks += built

    # 4. The pick.
    pick_status = "none"
    if len(tracks) >= 2:
        candidates = {t["id"]: sightings.get(t["id"], []) for t in tracks}
        picked = pick_tracks(vlm, question, candidates, images.get)
        if picked is None:
            pick_status = "failed"
        else:
            pick_status = "done"
            for track in tracks:
                track["picked"] = track["id"] in picked
    return Built(tracks, pick_status, len(keyframes), answered)


def _track_run(
    run: _Run,
    parts: list[VideoPart],
    sources: dict[UUID, Path],
    by_part: dict[int, list[_Keyframe]],
    interval: float,
    first_id: int,
    sightings: dict[int, list],
    images: dict[int, np.ndarray],
) -> tuple[list[dict], int]:
    """One run's tracks, as stored; fills `sightings` (track id -> (frame index, 0-1 box)) and
    `images` (keyframe frame index -> picture) for the pick."""
    tracker = SparseTracker(first_id=first_id)
    propagator: Propagator | None = None
    # The model names one object differently from picture to picture ("bag", "backpack"): tracks
    # are matched on the class, and shown with the word the model used most.
    words: dict[int, Counter[str]] = {}
    where: dict[int, tuple[UUID, int]] = {}  # frame index -> (segment, ms in it)
    size = (0, 0)
    index = 0
    for n in run.parts:
        part = parts[n]
        path = sources[part.segment_id]
        pending = sorted(by_part.get(n, []), key=lambda k: k.t)
        for t, frame in frames_between(
            path, part.source_start_ms / 1000, part.source_end_ms / 1000
        ):
            if propagator is None:
                size = (frame.shape[1], frame.shape[0])
                step = round(interval * frame_rate(path, _DEFAULT_FPS))
                propagator = Propagator(size[0], size[1], step, tracker)
            elif (frame.shape[1], frame.shape[0]) != size:
                frame = cv2.resize(frame, size)
            detections = None
            found: list[Detection] = []
            if pending and t >= pending[0].t - 0.001:
                keyframe = pending.pop(0)
                found = keyframe.detections or []
                w, h = size
                detections = [
                    (entity_class(d.label) or d.label,
                     np.array([d.box[0] * w, d.box[1] * h, d.box[2] * w, d.box[3] * h]))
                    for d in found
                ]  # fmt: skip
                images[index] = keyframe.image
            ids = propagator.feed(index, frame, detections)
            for tid, detection in zip(ids or [], found, strict=True):
                words.setdefault(tid, Counter())[detection.label] += 1
            where[index] = (part.segment_id, round(t * 1000))
            index += 1
    if propagator is None:
        return [], first_id

    w, h = size
    for track in tracker.tracks.values():
        sightings[track.id] = [
            (f, (b[0] / w, b[1] / h, b[2] / w, b[3] / h)) for f, b in track.sightings
        ]
    out: list[dict] = []
    for tid, frames in propagator.boxes().items():
        track = tracker.tracks[tid]
        runs: list[dict] = []
        last_ms: int | None = None
        for f in sorted(frames):
            segment_id, ms = where[f]
            if not runs or runs[-1]["segment_id"] != str(segment_id):
                runs.append({"segment_id": str(segment_id), "points": []})
                last_ms = None
            is_last = f == max(frames)
            if last_ms is not None and ms - last_ms < POINT_SPACING_MS and not is_last:
                continue
            b = frames[f]
            runs[-1]["points"].append(
                [ms, round(b[0] / w, 4), round(b[1] / h, 4), round(b[2] / w, 4), round(b[3] / h, 4)]
            )
            last_ms = ms
        out.append(
            {
                "id": tid,
                "label": words[tid].most_common(1)[0][0] if words.get(tid) else track.label,
                # The tracker's label is the class ("portable_object") when the word has one.
                "kind": entity_class(track.label),
                "picked": False,
                "runs": runs,
            }
        )
    return out, tracker.next_id
