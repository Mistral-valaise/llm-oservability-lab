"""OpenTelemetry setup: traces, metrics (with exemplars) and logs over OTLP.

Exporters are configured entirely through the standard OTEL_* environment
variables, including mTLS:

    OTEL_EXPORTER_OTLP_ENDPOINT=https://otel-collector:4317
    OTEL_EXPORTER_OTLP_CERTIFICATE=/etc/otel-tls/ca.crt
    OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE=/etc/otel-tls/tls.crt
    OTEL_EXPORTER_OTLP_CLIENT_KEY=/etc/otel-tls/tls.key
"""

from __future__ import annotations

import json
import logging
import os
import sys
from importlib.metadata import PackageNotFoundError, version

from opentelemetry import metrics, trace
from opentelemetry.sdk.resources import Resource

# GenAI semantic conventions recommend these bucket boundaries.
TOKEN_BUCKETS = (1, 4, 16, 64, 256, 1024, 4096, 16384, 65536, 262144, 1048576, 4194304, 16777216, 67108864)
DURATION_BUCKETS = (0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48, 40.96, 81.92)


def _service_version() -> str:
    try:
        return version("ai-gateway")
    except PackageNotFoundError:
        return os.getenv("SERVICE_VERSION", "0.0.0-dev")


class JsonFormatter(logging.Formatter):
    """One JSON object per line on stdout, with trace context for correlation."""

    _skip = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        span_ctx = trace.get_current_span().get_span_context()
        doc = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if span_ctx.is_valid:
            doc["trace_id"] = format(span_ctx.trace_id, "032x")
            doc["span_id"] = format(span_ctx.span_id, "016x")
        doc.update({k: v for k, v in record.__dict__.items() if k not in self._skip and not k.startswith("_")})
        return json.dumps(doc, default=str)


def setup_telemetry(service_name: str) -> None:
    """Install SDK providers unless exporting is disabled (tests, local runs without a collector)."""
    root = logging.getLogger()
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    stdout = logging.StreamHandler(sys.stdout)
    stdout.setFormatter(JsonFormatter())
    root.handlers[:] = [stdout]

    if os.getenv("OTEL_SDK_DISABLED", "false").lower() == "true":
        return

    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": service_name, "service.version": _service_version()})

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)

    views = [
        View(
            instrument_name="gen_ai.client.token.usage", aggregation=ExplicitBucketHistogramAggregation(TOKEN_BUCKETS)
        ),
        View(
            instrument_name="gen_ai.client.operation.duration",
            aggregation=ExplicitBucketHistogramAggregation(DURATION_BUCKETS),
        ),
    ]
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(), export_interval_millis=int(os.getenv("OTEL_METRIC_EXPORT_INTERVAL", "15000"))
    )
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader], views=views))

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(logger_provider)
    root.addHandler(LoggingHandler(level=logging.INFO, logger_provider=logger_provider))


def shutdown_telemetry() -> None:
    for provider in (trace.get_tracer_provider(), metrics.get_meter_provider()):
        if hasattr(provider, "shutdown"):
            provider.shutdown()
    from opentelemetry._logs import get_logger_provider

    lp = get_logger_provider()
    if hasattr(lp, "shutdown"):
        lp.shutdown()


class GatewayInstruments:
    """All custom instruments in one place. Names follow the OTel GenAI semantic conventions."""

    def __init__(self) -> None:
        meter = metrics.get_meter("ai-gateway")
        self.token_usage = meter.create_histogram(
            "gen_ai.client.token.usage", unit="{token}", description="Tokens used per request, by token type"
        )
        self.duration = meter.create_histogram(
            "gen_ai.client.operation.duration", unit="s", description="GenAI operation duration"
        )
        self.cost = meter.create_counter("llm_gateway.cost", unit="{usd}", description="Estimated spend in US dollars")
        self.guardrail_blocks = meter.create_counter(
            "llm_gateway.guardrail.blocks", unit="{request}", description="Requests rejected by a guardrail"
        )
        self.pii_redactions = meter.create_counter(
            "llm_gateway.pii.redactions", unit="{item}", description="PII items redacted before leaving the gateway"
        )
        self.ratelimit_rejections = meter.create_counter(
            "llm_gateway.ratelimit.rejections", unit="{request}", description="Requests rejected by the rate limiter"
        )
        self.auth_failures = meter.create_counter(
            "llm_gateway.auth.failures", unit="{request}", description="Requests with a missing or invalid API key"
        )
