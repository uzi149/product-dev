"""The embedding interfaces for the two search indexes (scope §9.2, ADR 0020).

- Text index: a `TextEmbedder` turns VLM descriptions and typed queries into vectors.
- Visual index: an `ImageEmbedder` turns keyframes into vectors, and typed text into vectors in
  the same space, so a query can be compared with pictures directly.

Every vector has EMBEDDING_DIM values and length 1, so cosine similarity is a dot product
(pgvector's `<=>` gives 1 - similarity). The models always run locally and are fixed per
installation; `index_settings` guards against mixing models in one index.
"""

import logging
import os
from functools import lru_cache
from typing import Protocol

import numpy as np

from shared.config import Settings, get_settings
from shared.database.models import EMBEDDING_DIM

log = logging.getLogger(__name__)

# How far a vector's length may be from 1 after normalising (float32 rounding).
NORM_TOLERANCE = 1e-3


class EmbeddingError(Exception):
    """A model returned something that can't go into an index (wrong size, NaN, not normalised)."""


class TextEmbedder(Protocol):
    @property
    def name(self) -> str:
        """The model id, recorded in `index_settings`."""
        ...

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        """Texts to store (VLM descriptions): array (n, EMBEDDING_DIM), float32, normalised."""
        ...

    def embed_query(self, text: str) -> np.ndarray:
        """A search query, with the model's query instruction: array (EMBEDDING_DIM,)."""
        ...


class ImageEmbedder(Protocol):
    @property
    def name(self) -> str: ...

    def embed_images(self, frames: list[np.ndarray]) -> np.ndarray:
        """Pictures (numpy BGR arrays): array (n, EMBEDDING_DIM), float32, normalised."""
        ...

    def embed_text(self, text: str) -> np.ndarray:
        """Text in the same space as the pictures: array (EMBEDDING_DIM,)."""
        ...


def checked(vectors: np.ndarray, count: int, model: str) -> np.ndarray:
    """`vectors` as float32 (count, EMBEDDING_DIM), or EmbeddingError if it can't be indexed."""
    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.shape != (count, EMBEDDING_DIM):
        raise EmbeddingError(
            f"{model} gave vectors of shape {vectors.shape}, expected ({count}, {EMBEDDING_DIM})"
        )
    if not np.isfinite(vectors).all():
        raise EmbeddingError(f"{model} gave a vector with NaN or infinite values")
    off = np.abs(np.linalg.norm(vectors, axis=1) - 1)
    if count and off.max() > NORM_TOLERANCE:
        raise EmbeddingError(
            f"{model} gave a vector that is not normalised (length off by {off.max():.4f})"
        )
    return vectors


def embedding_threads(settings: Settings) -> int:
    """EMBEDDING_THREADS, or half the CPU cores when it is 0."""
    return settings.embedding_threads or max(1, (os.cpu_count() or 2) // 2)


def build_text_embedder(settings: Settings) -> TextEmbedder:
    from shared.model_adapters.local_embeddings import LocalTextEmbedder

    return LocalTextEmbedder(settings.text_embedding_model, embedding_threads(settings))


def build_image_embedder(settings: Settings) -> ImageEmbedder:
    from shared.model_adapters.local_embeddings import LocalImageEmbedder

    return LocalImageEmbedder(settings.image_embedding_model, embedding_threads(settings))


@lru_cache
def get_text_embedder() -> TextEmbedder:
    """The process's text embedder, built once (the model loads on first use)."""
    embedder = build_text_embedder(get_settings())
    log.info("text embeddings: %s", embedder.name)
    return embedder


@lru_cache
def get_image_embedder() -> ImageEmbedder:
    """The process's image embedder, built once (the model loads on first use)."""
    embedder = build_image_embedder(get_settings())
    log.info("image embeddings: %s", embedder.name)
    return embedder
