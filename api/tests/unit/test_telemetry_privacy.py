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
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.example.test"
    ) as client:
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
    assert span.context is not None
    assert span.context.is_valid
    emitted = json.dumps(
        [
            {"name": s.name, "attributes": dict(s.attributes or {})}
            for s in exporter.get_finished_spans()
        ]
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


def test_privacy_span_exporter_purges_pii_from_attributes_events_exceptions_and_links() -> None:
    """Regression test: PrivacySpanExporter purges PII from attributes, events, and links."""
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.trace import Link, SpanContext, TraceFlags

    from gorgona_booking.observability import PrivacySpanExporter

    raw_exporter = InMemorySpanExporter()
    privacy_exporter = PrivacySpanExporter(raw_exporter)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(privacy_exporter))
    tracer = provider.get_tracer("test_privacy")

    link_ctx = SpanContext(trace_id=1, span_id=2, is_remote=False, trace_flags=TraceFlags(1))
    link = Link(
        context=link_ctx,
        attributes={
            "link.email": "leak-email@example.test",
            "link.phone": "+1-555-123-4567",
            "link.booking_id": "link-booking-id-9999",
            "link.safe": "safe-link-metadata",
        },
    )

    with tracer.start_as_current_span("parent_span", links=[link]) as span:
        # 1. Inject sensitive data into span attributes
        span.set_attribute("customer.email", "fake-person@example.test")
        span.set_attribute("customer.phone", "5551234567")
        span.set_attribute("booking.id", "private-booking-id-1234")
        span.set_attribute("authorization", "Bearer fake-sensitive-access-token")
        span.set_attribute("booking_token", "fake-sensitive-capability")
        span.set_attribute("secret_query", "secret_val_xyz")
        span.set_attribute("safe_attribute", "keep-me")

        # 2. Inject sensitive data into a normal event's attributes
        span.add_event(
            "custom_event",
            attributes={
                "event.email": "event-person@example.test",
                "event.phone": "555-987-6543",
                "event.booking_id": "event-booking-id-5678",
                "event.token": "event-secret-token",
                "event.safe": "safe-event-detail",
            },
        )

        # 3. Inject sensitive data into an exception event (message + stacktrace simulation)
        try:
            raise ValueError(
                "Customer customer@example.test failed payment for booking-token "
                "cap_token_abc and private-booking-id 12345 with secret query pass_123"
            )
        except ValueError as err:
            span.record_exception(err)

    provider.force_flush()
    spans = raw_exporter.get_finished_spans()
    assert len(spans) == 1
    exported_span = spans[0]

    # Verify attributes
    assert exported_span.attributes is not None
    assert exported_span.attributes.get("safe_attribute") == "keep-me"

    # Verify exception event preserved exception.type but dropped message and stacktrace
    exc_events = [e for e in exported_span.events if e.name == "exception"]
    assert len(exc_events) == 1
    assert exc_events[0].attributes is not None
    assert exc_events[0].attributes.get("exception.type") == "ValueError"
    assert "exception.message" not in exc_events[0].attributes
    assert "exception.stacktrace" not in exc_events[0].attributes

    # Verify normal event kept safe fields
    normal_events = [e for e in exported_span.events if e.name == "custom_event"]
    assert len(normal_events) == 1
    assert normal_events[0].attributes is not None
    assert normal_events[0].attributes.get("event.safe") == "safe-event-detail"

    # Verify link kept safe fields
    assert len(exported_span.links) == 1
    assert exported_span.links[0].attributes is not None
    assert exported_span.links[0].attributes.get("link.safe") == "safe-link-metadata"

    # Serialize all exported spans and verify NONE of the sensitive values leaked
    emitted = json.dumps(
        [
            {
                "name": s.name,
                "attributes": dict(s.attributes or {}),
                "events": [
                    {"name": e.name, "attributes": dict(e.attributes or {})}
                    for e in s.events
                ],
                "links": [{"attributes": dict(lnk.attributes or {})} for lnk in s.links],
            }
            for s in spans
        ]
    )

    sensitive_test_values = (
        "fake-person@example.test",
        "leak-email@example.test",
        "event-person@example.test",
        "customer@example.test",
        "5551234567",
        "+1-555-123-4567",
        "555-987-6543",
        "private-booking-id-1234",
        "link-booking-id-9999",
        "event-booking-id-5678",
        "fake-sensitive-access-token",
        "event-secret-token",
        "fake-sensitive-capability",
        "cap_token_abc",
        "secret_val_xyz",
        "pass_123",
    )
    for sensitive_value in sensitive_test_values:
        assert (
            sensitive_value not in emitted
        ), f"Leaked sensitive value in telemetry: {sensitive_value}"
