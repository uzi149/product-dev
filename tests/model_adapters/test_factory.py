"""Which backend the settings pick (ADR 0019): the API when a key is set, else the local model."""

import sys

import pytest

import shared.model_adapters as adapters
from shared.config import Settings
from shared.model_adapters import build_vlm, resolve_vlm_backend
from shared.model_adapters.local_transformers import LocalTransformersVLM
from shared.model_adapters.openai_compatible import OpenAICompatibleVLM


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


@pytest.fixture(autouse=True)
def no_key_from_the_environment(monkeypatch):
    for name in ("VLM_API_KEY", "OPENROUTER_API_KEY", "VLM_BACKEND"):
        monkeypatch.delenv(name, raising=False)


def test_auto_uses_the_api_when_a_key_is_set():
    s = settings(vlm_api_key="sk-test")
    assert resolve_vlm_backend(s) == "api"
    vlm = build_vlm(s)
    assert isinstance(vlm, OpenAICompatibleVLM)
    assert (vlm.base_url, vlm.name) == (
        "https://openrouter.ai/api/v1",
        "qwen/qwen3-vl-30b-a3b-instruct",
    )


def test_auto_falls_back_to_the_local_model_without_a_key():
    s = settings()
    assert resolve_vlm_backend(s) == "local"
    vlm = build_vlm(s)
    assert isinstance(vlm, LocalTransformersVLM)
    assert vlm.name == "Qwen/Qwen2.5-VL-3B-Instruct"


def test_the_openrouter_key_name_works_too(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-1")
    assert resolve_vlm_backend(settings()) == "api"
    assert settings().vlm_key == "sk-or-1"
    monkeypatch.setenv("VLM_API_KEY", "")  # an empty line copied from .env.example doesn't hide it
    assert settings().vlm_key == "sk-or-1"
    monkeypatch.setenv("VLM_API_KEY", "sk-2")  # the project's own name wins
    assert settings().vlm_key == "sk-2"


def test_api_and_local_force_their_backend():
    assert resolve_vlm_backend(settings(vlm_backend="local", vlm_api_key="sk-test")) == "local"
    vlm = build_vlm(settings(vlm_backend="api", vlm_base_url="http://127.0.0.1:8001/v1"))
    assert isinstance(vlm, OpenAICompatibleVLM)
    assert vlm.base_url == "http://127.0.0.1:8001/v1"  # a local vLLM needs no key


def test_the_keys_are_hidden_in_the_settings_repr():
    s = settings(vlm_api_key="sk-hidden", openrouter_api_key="sk-or-hidden")
    assert "sk-hidden" not in repr(s) and "sk-or-hidden" not in repr(s)


def test_building_the_local_backend_imports_no_model_library():
    before = set(sys.modules)
    build_vlm(settings())
    new = {name.split(".")[0] for name in set(sys.modules) - before}
    assert not new & {"torch", "transformers", "PIL"}  # they load on the first request only


def test_get_vlm_builds_once_per_process(monkeypatch):
    adapters.get_vlm.cache_clear()
    monkeypatch.setattr(adapters, "get_settings", lambda: settings(vlm_api_key="sk-test"))
    try:
        assert adapters.get_vlm() is adapters.get_vlm()
    finally:
        adapters.get_vlm.cache_clear()
