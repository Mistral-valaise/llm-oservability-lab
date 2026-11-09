"""AI Gateway: an OpenAI-compatible proxy that makes every LLM call observable and safe.

Request flow: authenticate -> rate limit -> guardrails (injection, size) -> PII redaction
-> provider call (span ``chat {model}``) -> usage, cost and duration metrics -> response.
"""

from __future__ import annotations

import hmac
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from pydantic import BaseModel, Field

from .config import Settings
from .guardrails import GuardrailViolation, check_prompt, redact_pii
from .providers import MockProvider, OpenAICompatibleProvider, Provider, UpstreamError
from .ratelimit import TokenBucketLimiter
from .telemetry import GatewayInstruments, setup_telemetry, shutdown_telemetry

log = logging.getLogger("ai_gateway")
tracer = trace.get_tracer("ai-gateway")


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=100_000)


class ChatRequest(BaseModel):
    model: str
    messages: list[Message] = Field(min_length=1, max_length=50)
    max_tokens: int = Field(default=256, ge=1, le=4096)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    stream: bool = False


class ChaosRequest(BaseModel):
    error_rate: float = Field(ge=0.0, le=1.0)
    latency_ms: float = Field(ge=1.0, le=60_000)


def build_provider(settings: Settings) -> Provider:
    if settings.provider == "mock":
        return MockProvider(settings.mock_latency_ms, settings.mock_error_rate)
    if settings.provider == "openai":
        return OpenAICompatibleProvider(
            settings.upstream_base_url, settings.upstream_api_key, settings.upstream_timeout_s
        )
    raise ValueError(f"unknown PROVIDER {settings.provider!r}")


def _error(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"error": {"type": code, "message": message}}, status_code=status, headers=headers)


def create_app(
    settings: Settings | None = None, provider: Provider | None = None, *, telemetry: bool = True
) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await app.state.provider.aclose()
        if telemetry:
            shutdown_telemetry()

    if telemetry:
        setup_telemetry("ai-gateway")

    app = FastAPI(title="AI Gateway", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.provider = provider or build_provider(settings)
    app.state.limiter = TokenBucketLimiter(settings.rate_limit_rps, settings.rate_limit_burst)
    inst = GatewayInstruments()

    if telemetry:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz")
        HTTPXClientInstrumentor().instrument()

    if not settings.api_keys:
        log.warning("no API keys configured: every request will be rejected")

    def authenticate(authorization: str | None) -> str | None:
        if not authorization or not authorization.lower().startswith("bearer "):
            return None
        presented = authorization[7:].strip().encode()
        for tenant, key in settings.api_keys.items():
            if hmac.compare_digest(presented, key.encode()):
                return tenant
        return None

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict:
        return {"status": "ready", "provider": app.state.provider.name}

    @app.get("/v1/models")
    async def list_models() -> dict:
        return {"object": "list", "data": [{"id": m, "object": "model"} for m in settings.allowed_models]}

    @app.post("/admin/chaos")
    async def chaos(body: ChaosRequest, x_admin_key: str | None = Header(default=None)) -> dict:
        """Change mock latency and error rate at runtime to demo SLO burn and alerting."""
        if not settings.admin_key or not x_admin_key or not hmac.compare_digest(x_admin_key, settings.admin_key):
            raise HTTPException(status_code=403, detail="forbidden")
        prov = app.state.provider
        if not isinstance(prov, MockProvider):
            raise HTTPException(status_code=409, detail="chaos is only available with the mock provider")
        prov.error_rate, prov.latency_ms = body.error_rate, body.latency_ms
        log.warning("chaos settings changed", extra={"error_rate": body.error_rate, "latency_ms": body.latency_ms})
        return {"error_rate": prov.error_rate, "latency_ms": prov.latency_ms}

    @app.post("/v1/chat/completions")
    async def chat(body: ChatRequest, request: Request, authorization: str | None = Header(default=None)):
        server_span = trace.get_current_span()
        tenant = authenticate(authorization)
        if tenant is None:
            inst.auth_failures.add(1)
            return _error(401, "unauthorized", "missing or invalid API key")
        server_span.set_attribute("llm_gateway.tenant", tenant)

        allowed, retry_after = app.state.limiter.acquire(tenant)
        if not allowed:
            inst.ratelimit_rejections.add(1, {"llm_gateway.tenant": tenant})
            log.info("rate limited", extra={"tenant": tenant})
            return _error(
                429, "rate_limited", "tenant rate limit exceeded", {"Retry-After": str(max(1, round(retry_after)))}
            )

        if body.stream:
            return _error(400, "unsupported", "streaming is not supported yet")
        if body.model not in settings.allowed_models:
            return _error(400, "model_not_allowed", f"model {body.model!r} is not allowed")

        prompt = "\n".join(m.content for m in body.messages)
        try:
            check_prompt(prompt, settings.max_prompt_chars)
        except GuardrailViolation as v:
            inst.guardrail_blocks.add(1, {"llm_gateway.tenant": tenant, "llm_gateway.guardrail.reason": v.reason})
            server_span.add_event("guardrail.blocked", {"llm_gateway.guardrail.reason": v.reason})
            log.warning("guardrail blocked request", extra={"tenant": tenant, "reason": v.reason})
            return _error(400, "guardrail_blocked", v.message)

        messages = []
        for m in body.messages:
            red = redact_pii(m.content)
            for pii_type, n in red.counts.items():
                inst.pii_redactions.add(n, {"llm_gateway.pii.type": pii_type})
            messages.append({"role": m.role, "content": red.text})

        provider: Provider = app.state.provider
        attrs = {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": provider.name,
            "gen_ai.request.model": body.model,
            "llm_gateway.tenant": tenant,
        }
        start = time.perf_counter()
        with tracer.start_as_current_span(
            f"chat {body.model}", kind=SpanKind.CLIENT, record_exception=False, set_status_on_exception=False
        ) as span:
            span.set_attributes(
                attrs
                | {
                    "gen_ai.request.max_tokens": body.max_tokens,
                    "gen_ai.request.temperature": body.temperature,
                    "server.address": provider.server_address,
                }
            )
            if settings.capture_content:
                # Opt-in only, and only after PII redaction. The collector can still drop it.
                span.set_attribute("gen_ai.input.messages", json.dumps(messages))
            try:
                result = await provider.complete(body.model, messages, body.max_tokens, body.temperature)
            except UpstreamError as err:
                elapsed = time.perf_counter() - start
                span.set_attribute("error.type", err.error_type)
                span.set_status(Status(StatusCode.ERROR, str(err)))
                inst.duration.record(elapsed, attrs | {"error.type": err.error_type})
                log.error(
                    "upstream call failed",
                    extra={
                        "tenant": tenant,
                        "model": body.model,
                        "error_type": err.error_type,
                        "duration_ms": round(elapsed * 1000),
                    },
                )
                return _error(err.status if err.status in (429, 502, 503, 504) else 502, "upstream_error", str(err))

            elapsed = time.perf_counter() - start
            resp_attrs = attrs | {"gen_ai.response.model": result.response_model}
            span.set_attributes(
                {
                    "gen_ai.response.id": result.id,
                    "gen_ai.response.model": result.response_model,
                    "gen_ai.response.finish_reasons": [result.finish_reason],
                    "gen_ai.usage.input_tokens": result.input_tokens,
                    "gen_ai.usage.output_tokens": result.output_tokens,
                }
            )
            inst.duration.record(elapsed, resp_attrs)
            inst.token_usage.record(result.input_tokens, resp_attrs | {"gen_ai.token.type": "input"})
            inst.token_usage.record(result.output_tokens, resp_attrs | {"gen_ai.token.type": "output"})
            price = settings.prices.get(body.model, {"input": 0.0, "output": 0.0})
            cost = (result.input_tokens * price["input"] + result.output_tokens * price["output"]) / 1_000_000
            inst.cost.add(cost, {"llm_gateway.tenant": tenant, "gen_ai.request.model": body.model})
            span.set_attribute("llm_gateway.cost_usd", cost)
            trace_id = format(span.get_span_context().trace_id, "032x")

        log.info(
            "chat completed",
            extra={
                "tenant": tenant,
                "model": body.model,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cost_usd": round(cost, 8),
                "duration_ms": round(elapsed * 1000),
                "finish_reason": result.finish_reason,
            },
        )
        return JSONResponse(
            {
                "id": result.id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": result.response_model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": result.text},
                        "finish_reason": result.finish_reason,
                    }
                ],
                "usage": {
                    "prompt_tokens": result.input_tokens,
                    "completion_tokens": result.output_tokens,
                    "total_tokens": result.input_tokens + result.output_tokens,
                },
            },
            headers={"x-trace-id": trace_id},
        )

    return app


def app_factory() -> FastAPI:
    """Entry point for uvicorn: ``uvicorn gateway.main:app_factory --factory``."""
    return create_app()
