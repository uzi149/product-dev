"""Check the VLM setup: send one picture and print who answered, how fast, and what it said.

    python -m shared.model_adapters.check_vlm                  # a generated test picture
    python -m shared.model_adapters.check_vlm photo.jpg        # your own picture(s)
    python -m shared.model_adapters.check_vlm --backend local  # force the in-process model

With an API key set (VLM_API_KEY or OPENROUTER_API_KEY) it uses the OpenAI-compatible server
(OpenRouter by default); without one, the local model, which takes minutes on a CPU (ADR 0019).
"""

import argparse
import sys

import cv2
import numpy as np

from shared.config import get_settings
from shared.model_adapters import VLMError, build_vlm, resolve_vlm_backend

PROMPT = "Describe this image in one sentence."


def sample_picture() -> np.ndarray:
    """A white picture with a red ball next to a blue box: easy to check the answer against."""
    frame = np.full((360, 640, 3), 255, np.uint8)
    cv2.circle(frame, (220, 190), 80, (0, 0, 220), -1)
    cv2.rectangle(frame, (380, 110), (540, 270), (200, 60, 0), -1)
    return frame


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("images", nargs="*", help="pictures to send (default: a generated one)")
    parser.add_argument("--backend", choices=["auto", "api", "local"], help="override VLM_BACKEND")
    parser.add_argument("--prompt", default=PROMPT)
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.backend:
        settings = settings.model_copy(update={"vlm_backend": args.backend})
    frames = []
    for path in args.images:
        frame = cv2.imread(path)
        if frame is None:
            print(f"could not read {path}", file=sys.stderr)
            return 2
        frames.append(frame)
    frames = frames or [sample_picture()]

    backend = resolve_vlm_backend(settings)
    where = settings.vlm_base_url if backend == "api" else "this process (CPU: minutes)"
    vlm = build_vlm(settings)
    print(f"backend: {backend}, model: {vlm.name}, at: {where}")
    print(f"sending {len(frames)} picture(s)...")
    try:
        reply = vlm.complete(frames, args.prompt, max_tokens=120)
    except VLMError as error:
        print(f"failed ({type(error).__name__}): {error}", file=sys.stderr)
        return 1
    tokens = ""
    if reply.prompt_tokens is not None:
        tokens = f", {reply.prompt_tokens} prompt + {reply.completion_tokens} answer tokens"
    print(f"answered by {reply.model} in {reply.latency_ms / 1000:.1f} s{tokens}:")
    print(reply.text.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
