"""The embedding models in this process with Hugging Face Transformers (ADR 0020).

- Text: `BAAI/bge-base-en-v1.5`, the [CLS] token's last hidden state, normalised. Queries get
  bge's instruction prefix; stored descriptions don't, as its model card advises for retrieval.
- Pictures, and text compared with pictures: `google/siglip2-base-patch16-224`. Its image and
  text towers map into one space. Text is lowercased and padded to 64 tokens, as SigLIP 2 was
  trained.

Like the local VLM, each model is loaded once per process, on first use, and shared. They run on
the CPU in float32: about 0.4 GB (bge) and 1.5 GB (SigLIP 2) of weights. `torch` and
`transformers` are imported only when a model is used.
"""

import logging
import threading
import time
from typing import Any

import cv2
import numpy as np

from shared.database.models import EMBEDDING_DIM
from shared.model_adapters.embeddings import EmbeddingError, checked

log = logging.getLogger(__name__)

# Items per forward pass.
BATCH_SIZE = 32
TEXT_MAX_TOKENS = 512
# SigLIP models were trained with text padded to 64 tokens; other lengths give worse vectors.
SIGLIP_TEXT_TOKENS = 64
# bge's instruction for short queries that search longer passages (its model card).
BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
QUERY_INSTRUCTIONS = {
    "BAAI/bge-small-en-v1.5": BGE_QUERY_INSTRUCTION,
    "BAAI/bge-base-en-v1.5": BGE_QUERY_INSTRUCTION,
    "BAAI/bge-large-en-v1.5": BGE_QUERY_INSTRUCTION,
}
_INSTALL_HINT = "the embedding models need torch and transformers: pip install -e ."

_LOAD_LOCK = threading.Lock()
# model name -> (model, tokenizer or processor), shared by every caller in the process
_CACHE: dict[str, tuple[Any, Any]] = {}


def load_model(model_name: str, processor_class: str, threads: int) -> tuple[Any, Any]:
    """The cached (model, processor) for `model_name`, loaded on first use."""
    with _LOAD_LOCK:
        if model_name in _CACHE:
            return _CACHE[model_name]
        try:
            import torch
            import transformers
        except ImportError as error:
            raise EmbeddingError(_INSTALL_HINT) from error

        # Process-wide: leaves the other half of the cores to FFmpeg and the API.
        torch.set_num_threads(threads)
        log.info("loading %s on cpu (%d threads)", model_name, threads)
        started = time.monotonic()
        processor = getattr(transformers, processor_class).from_pretrained(model_name)
        model = transformers.AutoModel.from_pretrained(model_name, dtype=torch.float32)
        model.eval()
        _CACHE[model_name] = (model, processor)
        log.info("%s ready after %.1f s", model_name, time.monotonic() - started)
        return _CACHE[model_name]


def weight_bytes(model_name: str) -> int | None:
    """Memory held by a loaded model's weights, or None when it isn't loaded."""
    if model_name not in _CACHE:
        return None
    model = _CACHE[model_name][0]
    return sum(p.numel() * p.element_size() for p in model.parameters())


def _empty() -> np.ndarray:
    return np.empty((0, EMBEDDING_DIM), np.float32)


def _pooled(output: Any) -> Any:
    """Transformers 5 returns the features in a model output; 4.x returns the tensor itself."""
    return getattr(output, "pooler_output", output)


class LocalTextEmbedder:
    def __init__(self, model_name: str, threads: int = 1):
        self.model_name = model_name
        self.threads = threads
        self.query_instruction = QUERY_INSTRUCTIONS.get(model_name, "")

    @property
    def name(self) -> str:
        return self.model_name

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return self._embed(list(texts))

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([self.query_instruction + text])[0]

    def _embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return _empty()
        model, tokenizer = load_model(self.model_name, "AutoTokenizer", self.threads)
        import torch

        parts = []
        for start in range(0, len(texts), BATCH_SIZE):
            inputs = tokenizer(
                texts[start : start + BATCH_SIZE],
                padding=True,
                truncation=True,
                max_length=TEXT_MAX_TOKENS,
                return_tensors="pt",
            )
            with torch.inference_mode():
                cls = model(**inputs).last_hidden_state[:, 0]
            parts.append(torch.nn.functional.normalize(cls, dim=-1).numpy())
        return checked(np.concatenate(parts), len(texts), self.model_name)


class LocalImageEmbedder:
    def __init__(self, model_name: str, threads: int = 1):
        self.model_name = model_name
        self.threads = threads

    @property
    def name(self) -> str:
        return self.model_name

    def embed_images(self, frames: list[np.ndarray]) -> np.ndarray:
        if not frames:
            return _empty()
        model, processor = load_model(self.model_name, "AutoProcessor", self.threads)
        import torch
        from PIL import Image

        parts = []
        for start in range(0, len(frames), BATCH_SIZE):
            images = [
                Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                for frame in frames[start : start + BATCH_SIZE]
            ]
            inputs = processor(images=images, return_tensors="pt")
            with torch.inference_mode():
                features = _pooled(model.get_image_features(**inputs))
            parts.append(torch.nn.functional.normalize(features, dim=-1).numpy())
        return checked(np.concatenate(parts), len(frames), self.model_name)

    def embed_text(self, text: str) -> np.ndarray:
        model, processor = load_model(self.model_name, "AutoProcessor", self.threads)
        import torch

        inputs = processor(
            text=[text.lower()],
            padding="max_length",
            max_length=SIGLIP_TEXT_TOKENS,
            truncation=True,
            return_tensors="pt",
        )
        with torch.inference_mode():
            features = _pooled(model.get_text_features(**inputs))
        vector = torch.nn.functional.normalize(features, dim=-1).numpy()
        return checked(vector, 1, self.model_name)[0]
