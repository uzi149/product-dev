"""VLM over an OpenAI-compatible chat completions API: OpenRouter, or a vLLM server (ADR 0019).

Adapted from the reference repository's API client: retries with backoff and jitter, a
cooldown shared by every request to the same endpoint and model after a rate limit, and an
immediate stop when the provider's daily quota is used up.
"""

import logging
import random
import threading
import time
from collections.abc import Callable

import httpx
import numpy as np

from shared.model_adapters.images import jpeg_data_url, resize_max_side
from shared.model_adapters.vlm import (
    VLMQuotaExhausted,
    VLMReply,
    VLMRequestRejected,
    VLMUnavailable,
)

log = logging.getLogger(__name__)

_RETRY_STATUS = {408, 429, 500, 502, 503, 504}
# 402: OpenRouter's "insufficient credits".
_QUOTA_STATUS = {402}
_MAX_ERROR_CHARS = 300

# Per (endpoint, model), shared by every client in the process, so parallel requests back off
# together after a rate limit instead of each one hammering the server.
_COOLDOWN_LOCK = threading.Lock()
_COOLDOWN_UNTIL: dict[str, float] = {}


class OpenAICompatibleVLM:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        *,
        timeout: float = 180,
        max_retries: int = 6,
        image_max_side: int = 640,
        extra_body: dict | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_retries = max_retries
        self.image_max_side = image_max_side
        self._sleep = sleep
        openrouter = "openrouter.ai" in self.base_url
        if extra_body is None and openrouter:
            # Hybrid "thinking" models otherwise spend the token budget on reasoning first.
            extra_body = {"reasoning": {"enabled": False}}
        self.extra_body = extra_body or {}
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if openrouter:
            headers["X-Title"] = "Sentinel"
        self._client = httpx.Client(
            base_url=self.base_url, headers=headers, timeout=timeout, transport=transport
        )

    @property
    def name(self) -> str:
        return self.model

    def complete(
        self,
        frames: list[np.ndarray],
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 800,
    ) -> VLMReply:
        content: list[dict] = [
            {
                "type": "image_url",
                "image_url": {"url": jpeg_data_url(resize_max_side(f, self.image_max_side))},
            }
            for f in frames
        ]
        content.append({"type": "text", "text": prompt})
        messages: list[dict] = [{"role": "system", "content": system}] if system else []
        messages.append({"role": "user", "content": content})
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
            **self.extra_body,
        }
        started = time.monotonic()
        data = self._post(payload)
        message = data["choices"][0].get("message") or {}
        usage = data.get("usage") or {}
        return VLMReply(
            text=message.get("content") or "",
            model=data.get("model") or self.model,
            latency_ms=round((time.monotonic() - started) * 1000),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )

    def _post(self, payload: dict) -> dict:
        last_error = ""
        for attempt in range(self.max_retries + 1):
            self._wait_for_cooldown()
            try:
                response = self._client.post("/chat/completions", json=payload)
            except httpx.TimeoutException:
                last_error = "the request timed out"
            except httpx.TransportError as error:
                last_error = f"could not reach the server ({type(error).__name__})"
            else:
                if response.status_code == 200:
                    data = _json(response)
                    if data and data.get("choices"):
                        return data
                    # Some providers answer 200 with an error body when an upstream fails.
                    last_error = _short(str((data or {}).get("error") or response.text))
                else:
                    last_error = _describe_error(response)
                    if response.status_code in _QUOTA_STATUS or _daily_limit_hit(response):
                        raise VLMQuotaExhausted(f"{self.base_url}: {last_error}")
                    if response.status_code not in _RETRY_STATUS:
                        raise VLMRequestRejected(f"{self.base_url}: {last_error}")

            if attempt < self.max_retries:
                # Jitter keeps parallel requests from retrying in lockstep.
                delay = round(min(30, 2**attempt * 2) * random.uniform(0.7, 1.3), 1)
                if last_error.startswith("HTTP 429"):
                    # A rate limit applies to every request in flight: pause them all.
                    self._start_cooldown(delay)
                log.warning(
                    "VLM %s: %s; retry %d/%d in %.1f s",
                    self.model,
                    last_error,
                    attempt + 1,
                    self.max_retries,
                    delay,
                )
                self._sleep(delay)

        raise VLMUnavailable(f"{self.base_url}: {last_error}")

    @property
    def _cooldown_key(self) -> str:
        return f"{self.base_url}|{self.model}"

    def _start_cooldown(self, seconds: float) -> None:
        with _COOLDOWN_LOCK:
            until = time.monotonic() + seconds
            _COOLDOWN_UNTIL[self._cooldown_key] = max(
                _COOLDOWN_UNTIL.get(self._cooldown_key, 0.0), until
            )

    def _wait_for_cooldown(self) -> None:
        with _COOLDOWN_LOCK:
            remaining = _COOLDOWN_UNTIL.get(self._cooldown_key, 0.0) - time.monotonic()
        if remaining > 0:
            self._sleep(remaining + random.uniform(0, 0.5))


def _json(response: httpx.Response) -> dict | None:
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _short(text: str) -> str:
    return " ".join(text.split())[:_MAX_ERROR_CHARS]


def _describe_error(response: httpx.Response) -> str:
    message = None
    data = _json(response)
    if data and isinstance(data.get("error"), dict):
        error = data["error"]
        message = (error.get("metadata") or {}).get("raw") or error.get("message")
    message = _short(str(message or response.text or response.reason_phrase))
    if "rate-limited upstream" in message:
        message = "the free model is rate-limited upstream"
    return f"HTTP {response.status_code}: {message}"


def _daily_limit_hit(response: httpx.Response) -> bool:
    # Retrying can't help once the free per-day quota is used up.
    return response.status_code == 429 and "per-day" in response.text.lower()
