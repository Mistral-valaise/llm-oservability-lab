"""LLM providers: a realistic mock and any OpenAI-compatible endpoint (OpenAI, Ollama, vLLM)."""

from __future__ import annotations

import asyncio
import math
import random
import uuid
from dataclasses import dataclass

import httpx


@dataclass
class Completion:
    id: str
    text: str
    input_tokens: int
    output_tokens: int
    finish_reason: str
    response_model: str


class UpstreamError(Exception):
    def __init__(self, error_type: str, status: int, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.status = status


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 characters per token), never zero for non-empty text."""
    return max(1, math.ceil(len(text) / 4)) if text else 0


class Provider:
    name = "base"
    server_address = ""

    async def complete(self, model: str, messages: list[dict], max_tokens: int, temperature: float) -> Completion:
        raise NotImplementedError

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        return None


_MOCK_ANSWERS = (
    "Observability means you can explain any state of your system from the outside.",
    "An SLO is a promise with a budget: spend the error budget on change, not on surprises.",
    "Traces tell you where time went, metrics tell you how often, logs tell you why.",
    "Start with the four golden signals: latency, traffic, errors and saturation.",
    "Default-deny network policies turn implicit trust into explicit, reviewable rules.",
)


class MockProvider(Provider):
    """Simulates an LLM: log-normal latency that grows with output size, token usage and failures.

    ``latency_ms`` and ``error_rate`` are mutable so the chaos endpoint can change them at runtime.
    """

    name = "mock"
    server_address = "mock.local"

    def __init__(self, latency_ms: float, error_rate: float, rng: random.Random | None = None) -> None:
        self.latency_ms = latency_ms
        self.error_rate = error_rate
        self._rng = rng or random.Random()

    async def complete(self, model: str, messages: list[dict], max_tokens: int, temperature: float) -> Completion:
        prompt = " ".join(str(m.get("content", "")) for m in messages)
        input_tokens = estimate_tokens(prompt) + 4 * len(messages)
        size = 3 if model == "mock-large" else 1
        answer = " ".join(self._rng.choice(_MOCK_ANSWERS) for _ in range(self._rng.randint(1, 2 * size)))
        output_tokens = min(max_tokens, estimate_tokens(answer))
        finish = "length" if output_tokens == max_tokens else "stop"

        base = self.latency_ms * (2.5 if model == "mock-large" else 1.0)
        delay_ms = self._rng.lognormvariate(math.log(base), 0.35) + output_tokens * 2
        await asyncio.sleep(delay_ms / 1000)

        if self._rng.random() < self.error_rate:
            if self._rng.random() < 0.5:
                raise UpstreamError("rate_limited", 429, "upstream rate limit exceeded")
            raise UpstreamError("unavailable", 503, "upstream model overloaded")

        return Completion(
            id=f"chatcmpl-{uuid.uuid4().hex[:24]}",
            text=answer,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=finish,
            response_model=f"{model}-2026-01",
        )


class OpenAICompatibleProvider(Provider):
    name = "openai"

    def __init__(self, base_url: str, api_key: str, timeout_s: float) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout_s)
        self.server_address = httpx.URL(base_url).host

    async def complete(self, model: str, messages: list[dict], max_tokens: int, temperature: float) -> Completion:
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        try:
            resp = await self._client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise UpstreamError("timeout", 504, "upstream timed out") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(type(exc).__name__, 502, "upstream connection failed") from exc
        if resp.status_code >= 400:
            raise UpstreamError(
                str(resp.status_code),
                502 if resp.status_code >= 500 else resp.status_code,
                "upstream returned an error",
            )
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        text = (choice.get("message") or {}).get("content", "")
        return Completion(
            id=data.get("id", ""),
            text=text,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", estimate_tokens(text))),
            finish_reason=choice.get("finish_reason") or "stop",
            response_model=data.get("model", model),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
