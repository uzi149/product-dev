"""The OpenRouter key check (`python -m shared.model_adapters.check_openrouter`), with a fake server."""

import json

import httpx
import pytest

from shared.config import get_settings
from shared.model_adapters import check_openrouter

KEY = {
    "label": "sk-or-v1-abc...123",
    "limit": 10.0,
    "limit_remaining": 7.5,
    "limit_reset": "monthly",
    "usage": 2.5,
    "usage_daily": 0.25,
    "usage_weekly": 1.0,
    "usage_monthly": 2.5,
    "is_free_tier": False,
    "free_model_daily_requests": {"used": 12, "limit": 50, "remaining": 38},
    "budgets": [],
}
MODELS = [
    {
        "id": "qwen/qwen3-vl-30b-a3b-instruct",
        "name": "Qwen3 VL",
        "context_length": 262144,
        "architecture": {"input_modalities": ["text", "image"]},
        "pricing": {
            "prompt": "0.00000015",
            "completion": "0.0000006",
            "image": "0",
            "request": "0",
        },
    },
    {
        "id": "google/gemma-4-31b-it:free",
        "architecture": {"input_modalities": ["text", "image"]},
        "pricing": {"prompt": "0", "completion": "0"},
    },
    {
        "id": "some/text-only",
        "architecture": {"input_modalities": ["text"]},
        "pricing": {"prompt": "0.000001", "completion": "0.000002"},
    },
]


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    for name in ("VLM_API_KEY", "OPENROUTER_API_KEY", "VLM_BASE_URL", "VLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("VLM_MODEL", "qwen/qwen3-vl-30b-a3b-instruct")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def server(key=KEY, key_status=200, seen=None):
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path.endswith("/key"):
            if key_status != 200:
                return httpx.Response(key_status, json={"error": {"message": "User not found."}})
            return httpx.Response(200, json={"data": key})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": MODELS})
        return httpx.Response(404)

    return httpx.MockTransport(handle)


def run(capsys, *args, **kw):
    code = check_openrouter.main(list(args), transport=server(**kw))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_it_prints_the_key_usage_and_the_configured_model_price(capsys):
    seen = []
    code, out, _ = run(capsys, seen=seen)
    assert code == 0
    assert seen[0].headers["Authorization"] == "Bearer sk-or-test"
    assert str(seen[0].url) == "https://openrouter.ai/api/v1/key"
    assert "$10.00, resets monthly" in out and "$7.50 (25% used)" in out
    assert "today $0.2500" in out and "in total $2.50" in out
    assert "12 used of 50, 38 left" in out
    assert "Model qwen/qwen3-vl-30b-a3b-instruct" in out
    assert "$0.1500 per 1M input tokens, $0.6000 per 1M output tokens" in out


def test_the_model_comes_from_the_command_line(capsys):
    code, out, _ = run(capsys, "--model", "google/gemma-4-31b-it:free", "--model", "some/text-only")
    assert code == 0
    assert "qwen3-vl" not in out
    assert "price:            free" in out and "calls left today: 38" in out
    assert "NO, it cannot be the VLM" in out


def test_it_estimates_the_cost_of_a_call(capsys):
    code, out, _ = run(capsys, "--estimate", "2000", "500")
    assert code == 0
    # 2000 * 0.15e-6 + 500 * 0.6e-6 = 0.0006 a call; 7.5 / 0.0006 = 12500 calls
    assert "about $0.0006" in out and "about 12,500 on the key's cap" in out


def test_a_key_without_a_cap_or_free_count(capsys):
    key = {**KEY, "limit": None, "limit_remaining": None, "free_model_daily_requests": None}
    code, out, _ = run(capsys, key=key)
    assert code == 0
    assert "none on this key" in out
    assert "not reported; the limit is 50 a day" in out


def test_an_unknown_model_fails(capsys):
    code, out, _ = run(capsys, "--model", "nope/nothing")
    assert code == 1
    assert "not found on OpenRouter" in out


def test_a_refused_key_fails(capsys):
    code, _, err = run(capsys, key_status=401)
    assert code == 1
    assert "refused the key (HTTP 401)" in err


def test_no_key(monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    get_settings.cache_clear()
    code, _, err = run(capsys)
    assert code == 2 and "no key" in err


def test_json_prints_the_raw_answers(capsys):
    code, out, _ = run(capsys, "--json")
    assert code == 0
    data = json.loads(out)
    assert data["key"]["usage"] == 2.5
    assert data["models"]["qwen/qwen3-vl-30b-a3b-instruct"]["name"] == "Qwen3 VL"


def test_a_local_vlm_server_still_checks_openrouter(monkeypatch, capsys):
    monkeypatch.setenv("VLM_BASE_URL", "http://vllm.local:8000/v1")
    get_settings.cache_clear()
    seen = []
    code, out, _ = run(capsys, seen=seen)
    assert code == 0
    assert "not OpenRouter" in out
    assert seen[0].url.host == "openrouter.ai"
