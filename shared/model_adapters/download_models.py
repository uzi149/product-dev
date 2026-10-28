"""Download the embedding models ahead of time, so they later load without a network.

    python -m shared.model_adapters.download_models

The models (about 2 GB) go to the usual Hugging Face cache (HF_HOME). Each one is loaded once and
runs one item, so a broken download shows up here. Afterwards a machine without internet access
can run with HF_HUB_OFFLINE=1.
"""

import sys
import time

import numpy as np

from shared.config import get_settings
from shared.model_adapters.embeddings import build_image_embedder, build_text_embedder


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    text = build_text_embedder(settings)
    image = build_image_embedder(settings)
    steps = [
        (text.name, lambda: text.embed_documents(["a person walks to the door"])),
        (image.name, lambda: image.embed_images([np.zeros((224, 224, 3), np.uint8)])),
    ]
    for name, run in steps:
        print(f"{name}: downloading if needed and loading...", flush=True)
        started = time.monotonic()
        try:
            run()
        except Exception as error:  # noqa: BLE001 - report any failure and stop
            print(f"{name} failed ({type(error).__name__}): {error}", file=sys.stderr)
            return 1
        print(f"{name}: ready ({time.monotonic() - started:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
