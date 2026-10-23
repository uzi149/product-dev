"""Change detection on frames sampled at about 1 fps (scope §7.3, ADR 0010).

The detector is pure: frames and timestamps in, activity events out. File ingestion and
live ingestion both feed it.

Per sample, a pixel counts as changed when it differs from the background by more than
`pixel_delta` *and* its local structural similarity (the contrast/structure term of SSIM)
is below `ssim_threshold`. Lighting changes move many pixels but keep the structure, so
they don't count. The sample is a change when changed pixels cover `changed_fraction` of
the unmasked image.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import cv2
import numpy as np

from shared.config import Settings
from shared.schemas.detection import DetectionConfig, Point

# SSIM stabilising constant for the contrast/structure term, for 8-bit images.
_C2 = (0.03 * 255) ** 2
_SSIM_SIGMA = 2.0


@dataclass(frozen=True)
class DetectionParams:
    pixel_delta: int
    changed_fraction: float
    ssim_threshold: float
    activation_samples: int
    background_alpha: float
    stable_timeout_s: float
    width: int
    masks: tuple[tuple[Point, ...], ...] = ()


def resolve_params(config: DetectionConfig | dict[str, Any], settings: Settings) -> DetectionParams:
    """Camera overrides on top of the global defaults."""
    if isinstance(config, dict):
        config = DetectionConfig.model_validate(config)

    def pick(value, default):
        return default if value is None else value

    return DetectionParams(
        pixel_delta=pick(config.pixel_delta, settings.detect_pixel_delta),
        changed_fraction=pick(config.changed_fraction, settings.detect_changed_fraction),
        ssim_threshold=pick(config.ssim_threshold, settings.detect_ssim_threshold),
        activation_samples=pick(config.activation_samples, settings.detect_activation_samples),
        background_alpha=pick(config.background_alpha, settings.detect_background_alpha),
        stable_timeout_s=settings.stable_timeout_seconds,
        width=settings.detect_width,
        masks=tuple(tuple(polygon) for polygon in config.masks),
    )


def build_mask(masks, width: int, height: int) -> np.ndarray:
    """Boolean image of the pixels to measure: False inside the ignore polygons."""
    include = np.full((height, width), 255, np.uint8)
    polygons = [
        np.round(np.array(polygon, np.float64) * (width - 1, height - 1)).astype(np.int32)
        for polygon in masks
    ]
    if polygons:
        cv2.fillPoly(include, polygons, 0)
    return include.astype(bool)


class State(StrEnum):
    IDLE = "idle"
    ACTIVE = "active"


@dataclass(frozen=True)
class ActivityStarted:
    start: float


@dataclass(frozen=True)
class ActivityEnded:
    start: float
    end: float
    # Mean changed fraction over the interval's change samples (0-1).
    score: float


@dataclass(frozen=True)
class Sample:
    t: float
    changed: float  # share of unmasked pixels that changed against the background
    ssim: float  # mean structural similarity against the background, unmasked area
    is_change: bool
    state: State
    event: ActivityStarted | ActivityEnded | None = None


class ChangeDetector:
    def __init__(self, params: DetectionParams):
        self.params = params
        self.state = State.IDLE
        self._background: np.ndarray | None = None
        self._previous: np.ndarray | None = None
        self._include: np.ndarray | None = None
        self._run = 0  # consecutive change samples while idle
        self._run_start = 0.0
        self._start = 0.0
        self._last_motion = 0.0
        self._scores: list[float] = []

    def update(self, frame: np.ndarray, t: float) -> Sample:
        image = self._prepare(frame)
        if self._background is None or self._background.shape != image.shape:
            # First frame (or the resolution changed): it only seeds the background.
            self._seed(image)
            return Sample(t, 0.0, 1.0, False, self.state)

        changed, ssim = self._compare(image, self._background)
        is_change = changed >= self.params.changed_fraction
        event = (
            self._step_active(image, t, changed, is_change)
            if self.state == State.ACTIVE
            else self._step_idle(image, t, changed, is_change)
        )
        self._previous = image
        return Sample(t, changed, ssim, is_change, self.state, event)

    def finish(self) -> ActivityEnded | None:
        """Close an open interval at the end of the input (end of a file)."""
        if self.state != State.ACTIVE:
            return None
        return self._end()

    def _step_idle(self, image, t, changed, is_change) -> ActivityStarted | None:
        if not is_change:
            self._run = 0
            self._scores = []
            cv2.accumulateWeighted(image, self._background, self.params.background_alpha)
            return None
        if self._run == 0:
            self._run_start = t
        self._run += 1
        self._scores.append(changed)
        if self._run < self.params.activation_samples:
            return None
        # The interval starts at the first change sample, not at the one that confirmed it.
        self.state = State.ACTIVE
        self._start = self._run_start
        self._last_motion = t
        return ActivityStarted(self._start)

    def _step_active(self, image, t, changed, is_change) -> ActivityEnded | None:
        # The background stays frozen: someone standing still must not fade into it.
        if is_change:
            self._scores.append(changed)
        moved, _ = self._compare(image, self._previous)
        if moved >= self.params.changed_fraction:
            self._last_motion = t
            return None
        if t - self._last_motion < self.params.stable_timeout_s:
            return None
        event = self._end()
        # A lasting change (a bag left behind) becomes the new background.
        self._background = image.copy()
        return event

    def _end(self) -> ActivityEnded:
        score = float(np.mean(self._scores)) if self._scores else 0.0
        event = ActivityEnded(self._start, self._last_motion, score)
        self.state = State.IDLE
        self._run = 0
        self._scores = []
        return event

    def _seed(self, image: np.ndarray) -> None:
        self._background = image.copy()
        self._previous = image
        height, width = image.shape
        self._include = build_mask(self.params.masks, width, height)
        if not self._include.any():
            raise ValueError("the detection masks cover the whole image")

    def _prepare(self, frame: np.ndarray) -> np.ndarray:
        gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        height, width = gray.shape
        if width > self.params.width:
            size = (self.params.width, max(1, round(height * self.params.width / width)))
            gray = cv2.resize(gray, size, interpolation=cv2.INTER_AREA)
        return cv2.GaussianBlur(gray, (5, 5), 0).astype(np.float32)

    def _compare(self, image: np.ndarray, reference: np.ndarray) -> tuple[float, float]:
        """(changed fraction, mean structural similarity) over the unmasked pixels."""
        structure = _structure_map(image, reference)
        differs = np.abs(image - reference) > self.params.pixel_delta
        changed = differs & (structure < self.params.ssim_threshold) & self._include
        area = np.count_nonzero(self._include)
        return np.count_nonzero(changed) / area, float(structure[self._include].mean())


def _structure_map(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Per-pixel contrast/structure term of SSIM: (2·σxy + C2) / (σx² + σy² + C2)."""

    def blur(image):
        return cv2.GaussianBlur(image, (0, 0), _SSIM_SIGMA)

    mu_x, mu_y = blur(x), blur(y)
    var_x = blur(x * x) - mu_x * mu_x
    var_y = blur(y * y) - mu_y * mu_y
    cov = blur(x * y) - mu_x * mu_y
    return (2 * cov + _C2) / (var_x + var_y + _C2)
