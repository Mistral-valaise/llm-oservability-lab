"""Runtime configuration, read once from environment variables.

Secrets (API keys) are read from files so they can be mounted from
Kubernetes Secrets instead of being passed as plain environment variables.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# Demo price table in USD per 1M tokens. The mock models are fictional;
# override with PRICE_TABLE_JSON for real upstream models.
DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "mock-small": {"input": 0.15, "output": 0.60},
    "mock-large": {"input": 2.50, "output": 10.00},
}


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _read_secret(env_file: str, env_value: str) -> str:
    """Prefer a mounted file; fall back to an env var (dev only)."""
    path = os.getenv(env_file)
    if path and Path(path).is_file():
        return Path(path).read_text(encoding="utf-8").strip()
    return os.getenv(env_value, "").strip()


def parse_api_keys(raw: str) -> dict[str, str]:
    """Parse ``tenant=key`` pairs separated by newlines or commas."""
    keys: dict[str, str] = {}
    for item in raw.replace(",", "\n").splitlines():
        item = item.strip()
        if not item or item.startswith("#") or "=" not in item:
            continue
        tenant, key = item.split("=", 1)
        if tenant.strip() and key.strip():
            keys[tenant.strip()] = key.strip()
    return keys


@dataclass
class Settings:
    provider: str = "mock"
    upstream_base_url: str = "http://ollama:11434/v1"
    upstream_api_key: str = ""
    upstream_timeout_s: float = 30.0
    api_keys: dict[str, str] = field(default_factory=dict)
    admin_key: str = ""
    rate_limit_rps: float = 5.0
    rate_limit_burst: int = 10
    max_prompt_chars: int = 8000
    capture_content: bool = False
    mock_latency_ms: float = 350.0
    mock_error_rate: float = 0.01
    prices: dict[str, dict[str, float]] = field(default_factory=lambda: dict(DEFAULT_PRICES))
    allowed_models: tuple[str, ...] = ("mock-small", "mock-large")

    @classmethod
    def from_env(cls) -> Settings:
        prices = dict(DEFAULT_PRICES)
        if raw := os.getenv("PRICE_TABLE_JSON"):
            prices.update(json.loads(raw))
        models = tuple(m.strip() for m in os.getenv("ALLOWED_MODELS", "mock-small,mock-large").split(",") if m.strip())
        return cls(
            provider=os.getenv("PROVIDER", "mock").lower(),
            upstream_base_url=os.getenv("UPSTREAM_BASE_URL", cls.upstream_base_url).rstrip("/"),
            upstream_api_key=_read_secret("UPSTREAM_API_KEY_FILE", "UPSTREAM_API_KEY"),
            upstream_timeout_s=_float("UPSTREAM_TIMEOUT_S", 30.0),
            api_keys=parse_api_keys(_read_secret("GATEWAY_API_KEYS_FILE", "GATEWAY_API_KEYS")),
            admin_key=_read_secret("GATEWAY_ADMIN_KEY_FILE", "GATEWAY_ADMIN_KEY"),
            rate_limit_rps=_float("RATE_LIMIT_RPS", 5.0),
            rate_limit_burst=int(os.getenv("RATE_LIMIT_BURST", "10")),
            max_prompt_chars=int(os.getenv("MAX_PROMPT_CHARS", "8000")),
            capture_content=_bool("CAPTURE_CONTENT", False),
            mock_latency_ms=_float("MOCK_LATENCY_MS", 350.0),
            mock_error_rate=_float("MOCK_ERROR_RATE", 0.01),
            prices=prices,
            allowed_models=models,
        )
