"""The OpenAI-compatible VLM client, against a mocked server (no network)."""

import base64
import json
import logging

import cv2
import httpx
import numpy as np
import pytest

from shared.model_adapters import openai_compatible
from shared.model_adapters.openai_compatible import OpenAICompatibleVLM
from shared.model_adapters.vlm import VLMQuotaExhausted, VLMRequestRejected, VLMUnavailable

KEY = "sk-or-secret-key-123"
OPENROUTER = "https://openrouter.ai/api/v1"


def answer(text="A red ball next to a blue box.", model="qwen/qwen3-vl-30b-a3b-instruct"):
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 512, "completion_tokens": 12},
        },
    )


class Server:
    """Answers the queued responses in order and keeps every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def body(self, n=0) -> dict:
        return json.loads(self.requests[n].content)


@pytest.fixture(autouse=True)
def no_cooldowns():
    openai_compatible._COOLDOWN_UNTIL.clear()
    yield
    openai_compatible._COOLDOWN_UNTIL.clear()


def client(server, base_url=OPENROUTER, api_key=KEY, sleeps=None, **kw):
    return OpenAICompatibleVLM(
        base_url,
        "qwen/qwen3-vl-30b-a3b-instruct",
        api_key,
        transport=httpx.MockTransport(server),
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
        **kw,
    )


def frame(height=720, width=1280):
    return np.zeros((height, width, 3), np.uint8)


def test_the_request_has_the_model_images_text_and_temperature_0():
    server = Server(answer())
    reply = client(server).complete(
        [frame(), frame()], "Describe this.", system="Be factual.", max_tokens=300
    )
    body = server.body()
    assert body["model"] == "qwen/qwen3-vl-30b-a3b-instruct"
    assert body["temperature"] == 0 and body["max_tokens"] == 300
    assert body["messages"][0] == {"role": "system", "content": "Be factual."}
    content = body["messages"][1]["content"]
    assert [c["type"] for c in content] == ["image_url", "image_url", "text"]
    assert content[2]["text"] == "Describe this."
    assert content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert server.requests[0].url.path == "/api/v1/chat/completions"
    assert reply.text == "A red ball next to a blue box."
    assert (reply.prompt_tokens, reply.completion_tokens) == (512, 12)
    assert reply.model == "qwen/qwen3-vl-30b-a3b-instruct" and reply.latency_ms >= 0


def test_images_are_scaled_to_the_longest_side():
    server = Server(answer(), answer())
    client(server, image_max_side=640).complete([frame(1080, 1920)], "x")
    client(server, image_max_side=640).complete([frame(240, 320)], "x")

    def sent_shape(n):
        url = server.body(n)["messages"][0]["content"][0]["image_url"]["url"]
        data = np.frombuffer(base64.b64decode(url.split(",", 1)[1]), np.uint8)
        return cv2.imdecode(data, 1).shape[:2]

    assert sent_shape(0) == (360, 640)
    assert sent_shape(1) == (240, 320)  # smaller frames are not enlarged


def test_text_only_requests_work():
    server = Server(answer("{}"))
    assert client(server).complete([], "Compile this rule.").text == "{}"
    assert [c["type"] for c in server.body()["messages"][0]["content"]] == ["text"]


def test_openrouter_gets_its_title_header_and_reasoning_off():
    server = Server(answer())
    client(server).complete([], "x")
    request = server.requests[0]
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["x-title"] == "Sentinel"
    assert server.body()["reasoning"] == {"enabled": False}


def test_a_local_server_without_a_key_gets_no_auth_header_and_no_extras():
    server = Server(answer())
    client(server, base_url="http://127.0.0.1:8001/v1", api_key="").complete([], "x")
    request = server.requests[0]
    assert "authorization" not in request.headers and "x-title" not in request.headers
    assert "reasoning" not in server.body()
    assert str(request.url) == "http://127.0.0.1:8001/v1/chat/completions"


def test_a_rate_limit_is_retried_and_cools_down_other_clients_too():
    sleeps: list[float] = []
    server = Server(
        httpx.Response(429, json={"error": {"message": "Rate limit exceeded"}}), answer()
    )
    assert client(server, sleeps=sleeps).complete([], "x").text
    assert len(server.requests) == 2
    assert 1.4 <= sleeps[0] <= 2.6  # first wait: 2 s ± 30 %

    # Another client of the same endpoint and model waits for the cooldown before its request.
    other_sleeps: list[float] = []
    client(Server(answer()), sleeps=other_sleeps).complete([], "x")
    assert other_sleeps and other_sleeps[0] > 0


def test_server_errors_are_retried_with_growing_waits_then_unavailable():
    sleeps: list[float] = []
    server = Server(*[httpx.Response(503, text="Service Unavailable")] * 7)
    with pytest.raises(VLMUnavailable, match="HTTP 503"):
        client(server, sleeps=sleeps, max_retries=6).complete([], "x")
    assert len(server.requests) == 7
    centres = [2, 4, 8, 16, 30, 30]
    assert all(0.69 * c <= s <= 1.31 * c for s, c in zip(sleeps, centres, strict=True))


def test_network_errors_and_timeouts_are_retried():
    server = Server(httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), answer())
    assert client(server).complete([], "x").text
    assert len(server.requests) == 3


def test_timeouts_every_time_become_unavailable():
    server = Server(*[httpx.ReadTimeout("slow")] * 3)
    with pytest.raises(VLMUnavailable, match="timed out"):
        client(server, max_retries=2).complete([], "x")


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413])
def test_rejected_requests_fail_at_once(status):
    server = Server(
        httpx.Response(status, json={"error": {"message": "No auth credentials found"}})
    )
    with pytest.raises(VLMRequestRejected, match=f"HTTP {status}: No auth credentials found"):
        client(server).complete([], "x")
    assert len(server.requests) == 1


def test_a_used_up_daily_quota_stops_at_once():
    server = Server(
        httpx.Response(429, json={"error": {"message": "Rate limit exceeded: free-models-per-day"}})
    )
    with pytest.raises(VLMQuotaExhausted):
        client(server).complete([], "x")
    assert len(server.requests) == 1


def test_no_credits_left_stops_at_once():
    server = Server(httpx.Response(402, json={"error": {"message": "Insufficient credits"}}))
    with pytest.raises(VLMQuotaExhausted, match="Insufficient credits"):
        client(server).complete([], "x")


def test_an_error_inside_a_200_answer_is_retried():
    server = Server(httpx.Response(200, json={"error": {"message": "upstream failed"}}), answer())
    assert client(server).complete([], "x").text
    assert len(server.requests) == 2


def test_the_upstream_message_is_used_when_openrouter_gives_one():
    body = {"error": {"message": "Provider returned error", "metadata": {"raw": "image too large"}}}
    with pytest.raises(VLMRequestRejected, match="image too large"):
        client(Server(httpx.Response(400, json=body))).complete([], "x")


def test_the_key_never_appears_in_logs_or_errors(caplog):
    caplog.set_level(logging.DEBUG)
    server = Server(*[httpx.Response(500, text="boom")] * 2)
    with pytest.raises(VLMUnavailable) as error:
        client(server, max_retries=1).complete([frame()], "x")
    assert KEY not in str(error.value)
    assert KEY not in caplog.text


def test_long_error_bodies_are_cut():
    server = Server(httpx.Response(400, text="x" * 5000))
    with pytest.raises(VLMRequestRejected) as error:
        client(server).complete([], "x")
    assert len(str(error.value)) < 400
