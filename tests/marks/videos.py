"""Generated clips with moving textured objects, and a fake VLM that "grounds" them (step 5.7).

The fake finds the objects by comparing the frame it is sent with the clip's known background,
and answers like a Qwen3 model (a 0-1000 grid).
"""

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from tests.video.scenes import HEIGHT, WIDTH, background, person_patch

FPS = 10


@dataclass(frozen=True)
class Mover:
    label: str
    seed: int
    x0: float  # left edge at t = 0, px
    speed: float  # px per second (negative: to the left)
    y: int = 100
    start: float = 0.0  # seconds in the clip it appears
    end: float = 1e9

    def x(self, t: float) -> float:
        return self.x0 + self.speed * t

    def box(self, t: float) -> tuple[float, float, float, float] | None:
        """Where it is at t, 0-1 of the picture; None when not in the picture."""
        if not self.start <= t < self.end:
            return None
        x = round(self.x(t))
        if x < 0 or x + 30 > WIDTH:
            return None
        return (x / WIDTH, self.y / HEIGHT, (x + 30) / WIDTH, (self.y + 60) / HEIGHT)


def scene_background() -> np.ndarray:
    return np.clip(background(), 0, 255).astype(np.uint8)


def write_clip(path: Path, seconds: float, movers: list[Mover]) -> Path:
    base = background()
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (WIDTH, HEIGHT))
    for n in range(round(seconds * FPS)):
        t = n / FPS
        scene = base.copy()
        for mover in movers:
            box = mover.box(t)
            if box is not None:
                x = round(box[0] * WIDTH)
                scene[mover.y : mover.y + 60, x : x + 30] = person_patch(mover.seed)
        grey = np.clip(scene, 0, 255).astype(np.uint8)
        writer.write(cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR))
    writer.release()
    return path


def grounder(movers: list[Mover], pick: str = "[]"):
    """A FakeVLM reply: the boxes of the movers in the frame, each labelled as the mover on the
    nearest row (movers with different labels are on different rows); `pick` answers a pick."""
    base = scene_background()

    def reply(frames, prompt) -> str:
        if "candidate" in prompt:
            return pick
        frame = cv2.cvtColor(frames[0], cv2.COLOR_BGR2GRAY)
        if frame.shape != base.shape:
            frame = cv2.resize(frame, (base.shape[1], base.shape[0]))
        changed = (cv2.absdiff(frame, base) > 40).astype(np.uint8)
        # The patch's texture has pixels close to the background: close the holes.
        changed = cv2.morphologyEx(changed, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        count, _, stats, _ = cv2.connectedComponentsWithStats(changed)
        items = []
        for k in range(1, count):
            x, y, w, h, area = stats[k]
            if area < 200:
                continue
            label = min(movers, key=lambda m: abs(m.y + 30 - (y + h / 2))).label
            items.append(
                {
                    "bbox_2d": [
                        round(x / WIDTH * 1000),
                        round(y / HEIGHT * 1000),
                        round((x + w) / WIDTH * 1000),
                        round((y + h) / HEIGHT * 1000),
                    ],
                    "label": label,
                }
            )
        return json.dumps(items)

    return reply
