"""Structured logs, request logging and domain metrics (AZURE_ARCHITECTURE section 10).

Logs are JSON with an allowlist of fields; free-form extras, exception messages, raw
paths, query strings and headers never reach them.
"""

import json
import logging

import httpx
import pytest
from fastapi import FastAPI
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, NumberDataPoint

from gorgona_booking.api.app import create_app
from gorgona_booking.config import Settings
from gorgona_booking.errors import NotFoundError
from gorgona_booking.observability import JsonFormatter, start_telemetry

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def metric_reader() -> InMemoryMetricReader:
    reader = InMemoryMetricReader()
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
    return reader


def _format(record: logging.LogRecord) -> dict[str, object]:
    parsed: dict[str, object] = json.loads(JsonFormatter().format(record))
    return parsed


def test_json_logs_keep_only_allowlisted_fields() -> None:
    record = logging.LogRecord("gorgona_booking.x", logging.INFO, __file__, 1, "done", None, None)
    record.request_id = "req-12345678"
    record.tenant_id = "0190a0a0-0000-7000-8000-000000000001"
    record.email = "customer@example.test"
    record.dsn = "postgresql://gba_app:hunter2@db/x"
    payload = _format(record)
    assert payload["message"] == "done"
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "req-12345678"
    assert payload["tenant_id"] == "0190a0a0-0000-7000-8000-000000000001"
    text = json.dumps(payload)
    assert "customer@example.test" not in text
    assert "hunter2" not in text


def test_exception_logs_carry_type_and_frames_but_not_the_message() -> None:
    try:
        raise RuntimeError("password authentication failed for user secret_user")
    except RuntimeError:
        import sys

        record = logging.LogRecord(
            "gorgona_booking.api", logging.ERROR, __file__, 1, "boom", None, sys.exc_info()
        )
    payload = _format(record)
    assert payload["exception_type"] == "RuntimeError"
    assert "secret_user" not in json.dumps(payload)
    assert payload["exception_frames"]


def _app() -> FastAPI:
    app = create_app(Settings(environment="test"))

    @app.get("/v1/things/{thing_id}")
    async def thing(thing_id: str) -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/v1/boom")
    async def boom() -> None:
        raise NotFoundError("Nothing here")

    return app


async def test_request_log_uses_route_templates_not_raw_paths(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="gorgona_booking.access")
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://salon.test") as client:
        await client.get("/v1/things/0190-private-id?email=customer@example.test")
        await client.get("/health/live")
    records = [r for r in caplog.records if r.name == "gorgona_booking.access"]
    assert len(records) == 1, "health probes are not access-logged"
    record = records[0]
    assert getattr(record, "operation", None) == "GET /v1/things/{thing_id}"
    assert getattr(record, "status", None) == 200
    duration = getattr(record, "duration_ms", None)
    assert isinstance(duration, float)
    assert duration >= 0
    assert len(str(getattr(record, "request_id", ""))) >= 8
    rendered = json.dumps(_format(record))
    assert "0190-private-id" not in rendered
    assert "customer@example.test" not in rendered


async def test_domain_errors_are_counted_by_code_and_route(
    metric_reader: InMemoryMetricReader,
) -> None:
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://salon.test") as client:
        assert (await client.get("/v1/boom")).status_code == 404
    data = metric_reader.get_metrics_data()
    assert data is not None
    points = [
        point
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
        if metric.name == "gorgona.domain_errors"
        for point in metric.data.data_points
        if isinstance(point, NumberDataPoint)
    ]
    assert any(
        p.attributes == {"code": "NOT_FOUND", "route": "/v1/boom"} and p.value >= 1 for p in points
    )


def test_telemetry_is_off_without_a_connection_string() -> None:
    app = create_app(Settings(environment="test"))
    assert start_telemetry(app, Settings(environment="test")) is False


def test_connection_string_is_read_from_env_and_kept_secret() -> None:
    value = (
        "InstrumentationKey=00000000-0000-0000-0000-000000000000;IngestionEndpoint=https://x.test/"
    )
    settings = Settings.from_env({"APPLICATIONINSIGHTS_CONNECTION_STRING": value})
    assert settings.applicationinsights_connection_string is not None
    assert settings.applicationinsights_connection_string.get_secret_value() == value
    assert "InstrumentationKey" not in repr(settings)
