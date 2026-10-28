"""The real models (marked `models`): only with RUN_MODEL_TESTS=1, never in CI.

- The API test calls the configured server (OpenRouter with a key in .env): a fraction of a cent.
- The tiny-model test downloads a few MB and checks the local code path in about 20 s.
- The local test loads Qwen2.5-VL-3B on the CPU: about 7.5 GB of free memory and several
  minutes.
"""

import pytest

from shared.config import Settings
from shared.model_adapters import build_vlm
from shared.model_adapters.check_vlm import sample_picture

pytestmark = pytest.mark.models

PROMPT = "What two shapes are in this picture, and what colour is each? Answer in one sentence."


def check(answer: str):
    text = answer.lower()
    assert "red" in text and "blue" in text, answer


def test_the_api_backend_describes_a_picture():
    settings = Settings()
    if not settings.vlm_key:
        pytest.skip("no VLM_API_KEY / OPENROUTER_API_KEY in .env")
    reply = build_vlm(settings.model_copy(update={"vlm_backend": "api"})).complete(
        [sample_picture()], PROMPT, max_tokens=80
    )
    check(reply.text)
    assert reply.latency_ms < 60_000


def test_the_local_code_path_runs_with_a_tiny_random_model():
    """Loading, chat template, generation and decoding with the installed Transformers version,
    on a few-MB random-weight Qwen-VL (its answer is gibberish; only the path is checked)."""
    from shared.model_adapters.local_transformers import LocalTransformersVLM

    vlm = LocalTransformersVLM("hf-internal-testing/tiny-random-Qwen2VLForConditionalGeneration")
    reply = vlm.complete(
        [sample_picture(), sample_picture()], "Describe.", system="x", max_tokens=5
    )
    assert reply.completion_tokens == 5
    # Two pictures capped at 640x360 are about 300 image tokens each.
    assert 500 < reply.prompt_tokens < 800


def test_the_local_backend_describes_a_picture():
    settings = Settings().model_copy(update={"vlm_backend": "local"})
    reply = build_vlm(settings).complete([sample_picture()], PROMPT, max_tokens=60)
    check(reply.text)
    assert reply.completion_tokens and reply.completion_tokens <= 60
