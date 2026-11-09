import random

import pytest
from fastapi.testclient import TestClient
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from gateway.config import Settings
from gateway.main import create_app
from gateway.providers import MockProvider

SPANS = InMemorySpanExporter()
READER = InMemoryMetricReader()

_tp = TracerProvider()
_tp.add_span_processor(SimpleSpanProcessor(SPANS))
trace.set_tracer_provider(_tp)
metrics.set_meter_provider(MeterProvider(metric_readers=[READER]))

KEY = "test-key-acme"


@pytest.fixture
def spans():
    SPANS.clear()
    return SPANS


def metric_points(name: str) -> list:
    data = READER.get_metrics_data()
    points = []
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == name:
                    points.extend(m.data.data_points)
    return points


def make_client(**overrides) -> TestClient:
    settings = Settings(
        api_keys={"acme": KEY}, admin_key="admin-secret", mock_latency_ms=1.0, mock_error_rate=0.0, **overrides
    )
    provider = MockProvider(settings.mock_latency_ms, settings.mock_error_rate, rng=random.Random(42))
    return TestClient(create_app(settings, provider, telemetry=False))


@pytest.fixture
def client() -> TestClient:
    return make_client()


AUTH = {"Authorization": f"Bearer {KEY}"}
