"""Check the embedding models: vector sizes, memory, speed, and that similar things rank first.

    python -m shared.model_adapters.check_embeddings

Text: a query has to rank the matching description first. Pictures: each caption has to pick
its own generated picture. Exits with 1 when a ranking is wrong. The models download on first
use (about 2 GB, see `download_models`).
"""

import sys
import time

import cv2
import numpy as np

from shared.config import get_settings
from shared.model_adapters.embeddings import build_image_embedder, build_text_embedder

QUERY = "someone rings the doorbell"
DESCRIPTIONS = [
    "A person presses the bell at the front door and waits.",
    "A red car parks in the street.",
    "Two people walk a dog along the pavement.",
    "A delivery van stops and the driver unloads boxes.",
    "The camera shows an empty driveway at night.",
]
# A window has 6 keyframes; timing a batch of them shows the cost of one window.
WINDOW_KEYFRAMES = 6


def sample_pictures() -> dict[str, np.ndarray]:
    """Generated pictures, keyed by the caption that should find each one."""
    red = np.full((360, 640, 3), 255, np.uint8)
    cv2.rectangle(red, (220, 80), (420, 280), (0, 0, 220), -1)
    blue = np.full((360, 640, 3), 255, np.uint8)
    cv2.rectangle(blue, (220, 80), (420, 280), (200, 60, 0), -1)
    person = np.full((360, 640, 3), 170, np.uint8)
    cv2.circle(person, (320, 80), 30, (60, 80, 110), -1)
    cv2.rectangle(person, (285, 115), (355, 240), (90, 50, 40), -1)
    cv2.rectangle(person, (290, 240), (315, 340), (40, 40, 40), -1)
    cv2.rectangle(person, (325, 240), (350, 340), (40, 40, 40), -1)
    return {"a red square": red, "a blue square": blue, "a person standing": person}


def _ms_each(started: float, count: int) -> float:
    return (time.monotonic() - started) * 1000 / count


def _gb(model) -> str:
    from shared.model_adapters.local_embeddings import weight_bytes

    size = weight_bytes(model.name)
    return "" if size is None else f", weights {size / 1e9:.2f} GB"


def check_text(embedder) -> bool:
    started = time.monotonic()
    documents = embedder.embed_documents(DESCRIPTIONS)
    print(
        f"{embedder.name}: {documents.shape[1]} values, first call {time.monotonic() - started:.1f} s (loads the model){_gb(embedder)}"
    )
    started = time.monotonic()
    embedder.embed_documents(DESCRIPTIONS)
    print(f"  {_ms_each(started, len(DESCRIPTIONS)):.0f} ms per description")
    scores = documents @ embedder.embed_query(QUERY)
    print(f'  query "{QUERY}":')
    for index in np.argsort(-scores):
        print(f"    {scores[index]:.3f}  {DESCRIPTIONS[index]}")
    ok = int(np.argmax(scores)) == 0
    print(f"  ranking: {'ok' if ok else 'WRONG (the doorbell description should be first)'}")
    return ok


def check_pictures(embedder) -> bool:
    pictures = sample_pictures()
    captions = list(pictures)
    started = time.monotonic()
    images = embedder.embed_images(list(pictures.values()))
    print(
        f"{embedder.name}: {images.shape[1]} values, first call {time.monotonic() - started:.1f} s (loads the model){_gb(embedder)}"
    )
    frames = [pictures[captions[0]]] * WINDOW_KEYFRAMES
    started = time.monotonic()
    embedder.embed_images(frames)
    window_seconds = time.monotonic() - started
    print(
        f"  {window_seconds * 1000 / WINDOW_KEYFRAMES:.0f} ms per picture, {window_seconds:.1f} s per window of {WINDOW_KEYFRAMES}"
    )
    ok = True
    for index, caption in enumerate(captions):
        started = time.monotonic()
        scores = images @ embedder.embed_text(caption)
        best = int(np.argmax(scores))
        ok &= best == index
        shown = ", ".join(f"{c} {s:.3f}" for c, s in zip(captions, scores, strict=True))
        print(f'  "{caption}" ({_ms_each(started, 1):.0f} ms) -> {captions[best]}  [{shown}]')
    print(f"  ranking: {'ok' if ok else 'WRONG (each caption should pick its own picture)'}")
    return ok


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    text_ok = check_text(build_text_embedder(settings))
    pictures_ok = check_pictures(build_image_embedder(settings))
    return 0 if text_ok and pictures_ok else 1


if __name__ == "__main__":
    sys.exit(main())
