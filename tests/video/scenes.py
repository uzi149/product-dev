"""Synthetic camera frames for change detection tests: no video files needed."""

import cv2
import numpy as np

HEIGHT, WIDTH = 240, 320


def background(seed: int = 0) -> np.ndarray:
    """A textured static scene (blobs on a gradient), grey levels kept well inside 0-255."""
    rng = np.random.default_rng(seed)
    scene = np.zeros((HEIGHT, WIDTH), np.float32)
    for _ in range(25):
        centre = (int(rng.integers(0, WIDTH)), int(rng.integers(0, HEIGHT)))
        cv2.circle(scene, centre, int(rng.integers(5, 40)), float(rng.integers(40, 200)), -1)
    scene = cv2.GaussianBlur(scene, (0, 0), 2) + np.linspace(30, 90, WIDTH, dtype=np.float32)
    return scene * 0.6


def person_patch(seed: int = 1) -> np.ndarray:
    """A textured 30 x 60 'person' (clothing has texture, unlike a flat block)."""
    rng = np.random.default_rng(seed)
    coarse = rng.integers(0, 255, (20, 10)).astype(np.float32)
    return cv2.resize(coarse, (30, 60), interpolation=cv2.INTER_NEAREST)


def with_object(scene: np.ndarray, x: int, y: int = 100, patch: np.ndarray | None = None):
    patch = person_patch() if patch is None else patch
    out = scene.copy()
    h, w = patch.shape
    out[y : y + h, x : x + w] = patch
    return out


class Camera:
    """Adds sensor noise to every frame, like a real camera."""

    def __init__(self, seed: int = 42, noise: float = 3.0):
        self.rng = np.random.default_rng(seed)
        self.noise = noise

    def __call__(self, scene: np.ndarray) -> np.ndarray:
        noisy = scene + self.rng.normal(0, self.noise, scene.shape)
        return np.clip(noisy, 0, 255).astype(np.uint8)
