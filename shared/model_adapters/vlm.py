"""The vision-language model interface every feature uses (scope §8.1, ADR 0019).

A backend takes a few frames and a prompt and answers in text. Which model answers, and where it
runs (OpenRouter, a local vLLM server, or in this process), is configuration. The product
prompts (describe a window, verify a result, compile a rule) live with the features that use
them, not here.
"""

from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class VLMReply:
    text: str
    model: str  # the exact model id, stored with what it wrote (analysis_windows.vlm_model)
    latency_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class VLMError(Exception):
    """Base class: the model could not answer."""


class VLMUnavailable(VLMError):
    """Temporary: network error, timeout, 5xx or rate limit after the retries. Retry later."""


class VLMQuotaExhausted(VLMError):
    """The provider's daily or credit limit is used up; retrying today won't help."""


class VLMRequestRejected(VLMError):
    """The request itself is refused (wrong key, unknown model, image too large). Don't retry."""


class VisionLanguageModel(Protocol):
    @property
    def name(self) -> str:
        """The model id this backend asks."""
        ...

    def complete(
        self,
        frames: list[np.ndarray],
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 800,
    ) -> VLMReply:
        """Answer `prompt` about `frames` (BGR images, in order; may be empty). Temperature 0."""
        ...
