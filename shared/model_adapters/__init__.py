"""Model adapters: the vision-language model (ADR 0019) and the local embedding models (ADR 0020)."""

import logging
from functools import lru_cache

from shared.config import Settings, get_settings
from shared.model_adapters.embeddings import (
    EmbeddingError,
    ImageEmbedder,
    TextEmbedder,
    build_image_embedder,
    build_text_embedder,
    get_image_embedder,
    get_text_embedder,
)
from shared.model_adapters.vlm import (
    VisionLanguageModel,
    VLMError,
    VLMQuotaExhausted,
    VLMReply,
    VLMRequestRejected,
    VLMUnavailable,
)

__all__ = [
    "EmbeddingError",
    "ImageEmbedder",
    "TextEmbedder",
    "VLMError",
    "VLMQuotaExhausted",
    "VLMReply",
    "VLMRequestRejected",
    "VLMUnavailable",
    "VisionLanguageModel",
    "build_image_embedder",
    "build_text_embedder",
    "build_vlm",
    "get_image_embedder",
    "get_text_embedder",
    "get_vlm",
    "resolve_vlm_backend",
]

log = logging.getLogger(__name__)


def resolve_vlm_backend(settings: Settings) -> str:
    """'api' or 'local'. 'auto' picks the API when a key is set, as in the reference code."""
    if settings.vlm_backend != "auto":
        return settings.vlm_backend
    return "api" if settings.vlm_key else "local"


def build_vlm(settings: Settings) -> VisionLanguageModel:
    if resolve_vlm_backend(settings) == "api":
        from shared.model_adapters.openai_compatible import OpenAICompatibleVLM

        return OpenAICompatibleVLM(
            settings.vlm_base_url,
            settings.vlm_model,
            settings.vlm_key,
            timeout=settings.vlm_timeout_seconds,
            max_retries=settings.vlm_max_retries,
            image_max_side=settings.vlm_image_max_side,
        )
    from shared.model_adapters.local_transformers import LocalTransformersVLM

    return LocalTransformersVLM(settings.vlm_local_model, settings.vlm_image_max_side)


@lru_cache
def get_vlm() -> VisionLanguageModel:
    """The process's VLM, built once from the settings."""
    settings = get_settings()
    vlm = build_vlm(settings)
    where = settings.vlm_base_url if resolve_vlm_backend(settings) == "api" else "this process"
    log.info("VLM: %s (%s)", vlm.name, where)
    return vlm
