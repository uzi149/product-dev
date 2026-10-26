"""VLM in this process with Hugging Face Transformers: the fallback without an API key (ADR 0019).

Adapted from the reference repository's shared Qwen-VL loader and its generate step: the model is
loaded once per process and shared, the device is CUDA, then Apple MPS, then CPU, and one
generation runs at a time. On CPU it uses bfloat16: the 3B model needs about 7.5 GB that way
instead of about 15 GB in float32, which matters on a 16 GB laptop. Expect a minute or more per
6-frame window on a CPU.

`torch` and `transformers` are imported only when this backend is used.
"""

import logging
import os
import threading
import time
from typing import Any

import cv2
import numpy as np

from shared.model_adapters.images import resize_max_side
from shared.model_adapters.vlm import VLMReply, VLMRequestRejected, VLMUnavailable

log = logging.getLogger(__name__)

# The shared model is not safe for two generate() calls at once.
GENERATION_LOCK = threading.Lock()
_LOAD_LOCK = threading.Lock()
# model name -> (model, processor), shared by every caller in the process
_CACHE: dict[str, tuple[Any, Any]] = {}

# Transformers class names to try, in order (they vary by release and by Qwen2 vs Qwen2.5-VL).
_MODEL_CLASS_CANDIDATES = [
    "Qwen2_5_VLForConditionalGeneration",
    "Qwen2VLForConditionalGeneration",
    "AutoModelForImageTextToText",
    "AutoModelForVision2Seq",
]
_INSTALL_HINT = 'the local VLM needs torch, torchvision and transformers: pip install -e ".[vlm]"'
# Qwen-VL's own default lower bound (56 x 56 pixels).
_MIN_PIXELS = 56 * 56


def select_device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        # Ops without an MPS kernel fall back to the CPU instead of failing.
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        return "mps"
    return "cpu"


def _load_model_class(model_name: str, load_kwargs: dict[str, Any]) -> Any:
    import transformers

    last_error: Exception | None = None
    for class_name in _MODEL_CLASS_CANDIDATES:
        model_class = getattr(transformers, class_name, None)
        if model_class is None:
            continue
        try:
            return model_class.from_pretrained(model_name, **load_kwargs)
        except Exception as error:  # noqa: BLE001 - try the next candidate
            last_error = error
            log.debug("%s could not load %s: %s", class_name, model_name, error)
    raise VLMRequestRejected(
        f"could not load {model_name} with any known Qwen-VL class "
        f"({', '.join(_MODEL_CLASS_CANDIDATES)}): {last_error}"
    )


def load_model(model_name: str, max_pixels: int) -> tuple[Any, Any]:
    """The cached (model, processor) for `model_name`, loaded on first use."""
    with _LOAD_LOCK:
        if model_name in _CACHE:
            return _CACHE[model_name]
        try:
            import torch
            from transformers import AutoProcessor
        except ImportError as error:
            raise VLMRequestRejected(_INSTALL_HINT) from error

        device = select_device()
        dtype = torch.bfloat16 if device == "cpu" else torch.float16
        log.info("loading %s on %s (%s); this takes a while", model_name, device, dtype)
        started = time.monotonic()
        model = _load_model_class(model_name, {"torch_dtype": dtype}).to(device)
        model.eval()
        # Caps the image tokens: about 640x360 per frame keeps 6 frames near 2,000 tokens. Qwen-VL
        # reads these "edges" as pixel counts; the older min/max_pixels arguments are deprecated.
        processor = AutoProcessor.from_pretrained(
            model_name, size={"shortest_edge": _MIN_PIXELS, "longest_edge": max_pixels}
        )
        _CACHE[model_name] = (model, processor)
        log.info("%s ready on %s after %.0f s", model_name, device, time.monotonic() - started)
        return _CACHE[model_name]


class LocalTransformersVLM:
    def __init__(self, model_name: str, image_max_side: int = 640):
        self.model_name = model_name
        self.image_max_side = image_max_side

    @property
    def name(self) -> str:
        return self.model_name

    def complete(
        self,
        frames: list[np.ndarray],
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 800,
    ) -> VLMReply:
        side = self.image_max_side
        model, processor = load_model(self.model_name, max_pixels=side * side * 9 // 16)
        try:
            import torch
            from PIL import Image
        except ImportError as error:
            raise VLMRequestRejected(_INSTALL_HINT) from error

        images = [
            Image.fromarray(cv2.cvtColor(resize_max_side(f, side), cv2.COLOR_BGR2RGB))
            for f in frames
        ]
        content: list[dict] = [{"type": "image"} for _ in images]
        content.append({"type": "text", "text": prompt})
        messages: list[dict] = (
            [{"role": "system", "content": [{"type": "text", "text": system}]}] if system else []
        )
        messages.append({"role": "user", "content": content})

        started = time.monotonic()
        try:
            text = processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            inputs = processor(text=[text], images=images or None, return_tensors="pt").to(
                model.device
            )
            with GENERATION_LOCK, torch.inference_mode():
                generated = model.generate(
                    **inputs, max_new_tokens=max_tokens, do_sample=False, repetition_penalty=1.05
                )
            trimmed = generated[:, inputs["input_ids"].shape[1] :]
            answer = processor.batch_decode(
                trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False
            )[0]
        except (RuntimeError, MemoryError) as error:
            raise VLMUnavailable(f"local model {self.model_name}: {error}") from error
        return VLMReply(
            text=answer,
            model=self.model_name,
            latency_ms=round((time.monotonic() - started) * 1000),
            prompt_tokens=int(inputs["input_ids"].shape[1]),
            completion_tokens=int(trimmed.shape[1]),
        )
