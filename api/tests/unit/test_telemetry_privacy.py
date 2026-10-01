"""Exported HTTP spans must not contain customer data or capability-like URL values."""

import io
import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from gorgona_booking.api.app import create_app
from gorgona_booking.config import Settings
from gorgona_booking.observability import start_telemetry

pytestmark = pytest.mark.anyio


@pytest.fixture
def exported_http(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[FastAPI, InMemorySpanExporter, TracerProvider]]:
    # Exercise the production setup with local SDK exporters; never contact Azure.
    import azure.monitor.opentelemetry.exporter as azure_exporters

    exporter = InMemorySpanExporter()
    tracer_providers: list[TracerProvider] = []
    meter_providers: list[MeterProvider] = []
    monkeypatch.setattr(
        azure_exporters, "AzureMonitorTraceExporter", lambda **_: exporter
    )
    monkeypatch.setattr(
        azure_exporters,
        "AzureMonitorMetricExporter",
        lambda **_: ConsoleMetricExporter(out=io.StringIO()),
    )
    monkeypatch.setattr(trace, "set_tracer_provider", tracer_providers.append)
    monkeypatch.setattr(metrics, "set_meter_provider", meter_providers.append)
    monkeypatch.setattr(PsycopgInstrumentor, "instrument", lambda *_, **__: None)
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    monkeypatch.setenv("OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_REQUEST", ".*")

    instrument = FastAPIInstrumentor.instrument_app

    def local_instrument(app: FastAPI, **kwargs: Any) -> None:
        # Providers stay local to this test instead of replacing global test telemetry.
        kwargs.setdefault("tracer_provider", tracer_providers[0])
        kwargs.setdefault("meter_provider", meter_providers[0])
        instrument(app, **kwargs)

    monkeypatch.setattr(FastAPIInstrumentor, "instrument_app", local_instrument)
    settings = Settings(
        environment="test",
        applicationinsights_connection_string=(
            "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
            "IngestionEndpoint=https://fake-ingestion.example.test/"
        ),
    )
    app = create_app(settings)

    @app.get("/v1/privacy-probe/{booking_id}")
    async def probe(booking_id: str) -> dict[str, str]:
        return {"result": "ok"}

    assert start_telemetry(app, settings) is True
    try:
        yield app, exporter, tracer_providers[0]
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
        tracer_providers[0].shutdown()
        meter_providers[0].shutdown()


def _server_span(exporter: InMemorySpanExporter) -> ReadableSpan:
    server_spans = [s for s in exporter.get_finished_spans() if s.kind is trace.SpanKind.SERVER]
    assert len(server_spans) == 1
    return server_spans[0]


@pytest.mark.parametrize(
    ("request_path", "safe_path", "expected_status"),
    [
        (
            "/v1/privacy-probe/private-booking-id",
            "/v1/privacy-probe/{booking_id}",
            200,
        ),
        ("/unmatched/private-booking-id", "/unmatched", 404),
    ],
)
async def test_exported_http_spans_redact_pii_and_keep_route_and_correlation(
    exported_http: tuple[FastAPI, InMemorySpanExporter, TracerProvider],
    request_path: str,
    safe_path: str,
    expected_status: int,
) -> None:
    app, exporter, provider = exported_http
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.example.test") as client:
        response = await client.get(
            request_path,
            params={
                "email": "fake-person@example.test",
                "phone": "5551234567",
                "code": "fake-authorization-code",
            },
            headers={
                "X-Request-ID": "privacy-request-12345678",
                "Authorization": "Bearer fake-sensitive-access-token",
                "Booking-Token": "fake-sensitive-capability",
            },
        )
    assert response.status_code == expected_status
    assert provider.force_flush() is True
    span = _server_span(exporter)
    assert span.attributes is not None
    assert span.attributes["http.url"] == f"https://api.example.test{safe_path}"
    assert span.attributes["http.target"] == safe_path
    assert span.attributes["url.full"] == f"https://api.example.test{safe_path}"
    assert span.attributes["url.path"] == safe_path
    assert span.attributes["http.method"] == "GET"
    assert span.attributes["gorgona.request_id"] == response.headers["x-request-id"]
    assert span.context is not None and span.context.is_valid
    emitted = json.dumps(
        [{"name": s.name, "attributes": dict(s.attributes or {})} for s in exporter.get_finished_spans()]
    )
    for private_value in (
        "private-booking-id",
        "fake-person@example.test",
        "5551234567",
        "fake-authorization-code",
        "fake-sensitive-access-token",
        "fake-sensitive-capability",
    ):
        assert private_value not in emitted
