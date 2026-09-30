"""Structured logs, request logging, domain metrics and optional Azure Monitor export.

- Logs are JSON lines with an allowlist of fields. Free-form extras, exception
  messages, raw paths, query strings, headers, DSNs and customer data never reach
  them; exceptions contribute only their type and stack frames.
- One access log line per request (probes excluded), carrying the route template,
  status, duration, request ID and the resolved tenant UUID.
- Domain error codes are counted (`gorgona.domain_errors`, by code and route
  template) through the OpenTelemetry API; they cost nothing when no provider is set.
- `start_telemetry` exports traces and metrics to Application Insights only when
  APPLICATIONINSIGHTS_CONNECTION_STRING is set. The Azure resource disables local
  (key) auth, so in Azure the exporters authenticate with the user-assigned managed
  identity named by AZURE_CLIENT_ID. It is called by the process entrypoint, never by
  tests.
"""

import json
import logging
import os
import sys
import time
import traceback
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import FastAPI
from opentelemetry import metrics, trace
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from gorgona_booking.config import Settings

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential

_ALLOWED_FIELDS = (
    "request_id",
    "tenant_id",
    "operation",
    "method",
    "status",
    "duration_ms",
    "code",
    "reason",
    "error_type",
)
_PROBE_PATHS = frozenset({"/health/live", "/health/ready"})

access_logger = logging.getLogger("gorgona_booking.access")
_meter = metrics.get_meter("gorgona_booking")
_domain_errors = _meter.create_counter(
    "gorgona.domain_errors",
    description="Domain errors returned to clients, by error code and route template",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _ALLOWED_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            payload["trace_id"] = format(context.trace_id, "032x")
            payload["span_id"] = format(context.span_id, "016x")
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__
            payload["exception_frames"] = [
                f"{frame.filename}:{frame.lineno} {frame.name}"
                for frame in traceback.extract_tb(record.exc_info[2])
            ]
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_logging(level: str = "INFO") -> None:
    """JSON logs on stdout for the whole process (container entrypoint only)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def record_domain_error(code: str, route: str) -> None:
    _domain_errors.add(1, {"code": code, "route": route})


def route_template(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


class AccessLogMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in _PROBE_PATHS:
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = 500

        async def capture(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            state = scope.get("state", {})
            tenant_id = state.get("tenant_id")
            access_logger.info(
                "request completed",
                extra={
                    "request_id": state.get("request_id"),
                    "tenant_id": str(tenant_id) if tenant_id is not None else None,
                    "operation": f"{scope['method']} {route_template(scope)}",
                    "method": scope["method"],
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                },
            )


def exporter_credential(environ: Mapping[str, str]) -> TokenCredential | None:
    """Entra credential for ingestion: the user-assigned identity from AZURE_CLIENT_ID."""
    client_id = environ.get("AZURE_CLIENT_ID", "").strip()
    if not client_id:
        return None
    from azure.identity import ManagedIdentityCredential

    return ManagedIdentityCredential(client_id=client_id)


def start_telemetry(app: FastAPI, settings: Settings) -> bool:
    """Export traces and metrics to Application Insights when configured."""
    secret = settings.applicationinsights_connection_string
    if secret is None:
        return False
    from azure.monitor.opentelemetry.exporter import (
        AzureMonitorMetricExporter,
        AzureMonitorTraceExporter,
    )
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    connection_string = secret.get_secret_value()
    credential = exporter_credential(os.environ)
    resource = Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", "gorgona-api")})
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            AzureMonitorTraceExporter(connection_string=connection_string, credential=credential)
        )
    )
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(
        MeterProvider(
            resource=resource,
            metric_readers=[
                PeriodicExportingMetricReader(
                    AzureMonitorMetricExporter(
                        connection_string=connection_string, credential=credential
                    )
                )
            ],
        )
    )
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health/live,health/ready")
    # Query text only (with placeholders); parameters are never captured.
    PsycopgInstrumentor().instrument(enable_commenter=False)
    return True
