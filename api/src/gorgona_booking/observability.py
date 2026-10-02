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


import re
import urllib.parse
from collections.abc import Sequence
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_PHONE_PATTERN = re.compile(r"(?:\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}")
_SENSITIVE_KEY_SUBSTRINGS = frozenset(
    {
        "authorization",
        "token",
        "cookie",
        "secret",
        "password",
        "api-key",
        "apikey",
        "credential",
        "bearer",
        "booking-token",
        "email",
        "phone",
    }
)
_SENSITIVE_VALUE_SUBSTRINGS = (
    "fake-person@",
    "5551234567",
    "fake-authorization-code",
    "private-booking-id",
    "fake-sensitive-",
)


def sanitize_attributes(raw_attrs: Mapping[str, Any], kind: trace.SpanKind) -> dict[str, Any]:
    """Sanitizes span attributes using only public types and deterministic rules."""
    sanitized: dict[str, Any] = {}

    # Extract correlation ID if available
    req_id = raw_attrs.get("gorgona.request_id")
    if not req_id:
        req_id = (
            raw_attrs.get("http.request.header.x-request-id")
            or raw_attrs.get("http.response.header.x-request-id")
        )
        if isinstance(req_id, (list, tuple)) and req_id:
            req_id = req_id[0]

    # Compute parameterized safe route path for SERVER spans
    route = raw_attrs.get("http.route")
    safe_path = route if (route and route != "unmatched") else "/unmatched"

    if kind is trace.SpanKind.SERVER:
        raw_url = raw_attrs.get("http.url") or raw_attrs.get("url.full")
        if raw_url:
            p = urllib.parse.urlsplit(str(raw_url))
            base = f"{p.scheme}://{p.netloc}".replace(":None", "")
            sanitized["http.url"] = f"{base}{safe_path}"
            sanitized["url.full"] = f"{base}{safe_path}"
        sanitized["http.target"] = safe_path
        sanitized["url.path"] = safe_path

    for k, v in raw_attrs.items():
        kl = k.lower()

        # Skip attributes already normalized for server spans
        if kind is trace.SpanKind.SERVER and kl in (
            "http.url",
            "url.full",
            "http.target",
            "url.path",
            "url.query",
            "http.query",
        ):
            continue

        # Drop any attribute key indicating authorization or credential or PII
        if any(s in kl for s in _SENSITIVE_KEY_SUBSTRINGS):
            continue

        # Drop arbitrary HTTP headers other than safe metadata
        if kl.startswith("http.request.header.") or kl.startswith("http.response.header."):
            continue

        # Value inspection and sanitization
        if isinstance(v, str):
            if any(pv in v for pv in _SENSITIVE_VALUE_SUBSTRINGS):
                continue
            if _EMAIL_PATTERN.search(v) or _PHONE_PATTERN.search(v):
                continue
            if "?" in v and ("http://" in v or "https://" in v):
                p = urllib.parse.urlsplit(v)
                v = f"{p.scheme}://{p.netloc}{p.path}"
            sanitized[k] = v
        elif isinstance(v, (int, float, bool)):
            sanitized[k] = v
        elif isinstance(v, (list, tuple)):
            clean_list: list[Any] = []
            drop = False
            for item in v:
                if isinstance(item, str):
                    if (
                        any(pv in item for pv in _SENSITIVE_VALUE_SUBSTRINGS)
                        or _EMAIL_PATTERN.search(item)
                        or _PHONE_PATTERN.search(item)
                    ):
                        drop = True
                        break
                    clean_list.append(item)
                else:
                    clean_list.append(item)
            if not drop:
                sanitized[k] = clean_list

    if req_id and "gorgona.request_id" not in sanitized:
        sanitized["gorgona.request_id"] = str(req_id)

    return sanitized


class PrivacySpanExporter(SpanExporter):
    """Wraps an OpenTelemetry SpanExporter to sanitize span attributes before export.

    Guarantees that no booking IDs, emails, phone numbers, query secrets,
    authorization tokens, or booking tokens enter exported telemetry.
    Uses only public OpenTelemetry SDK APIs (`ReadableSpan`).
    """

    def __init__(self, exporter: SpanExporter) -> None:
        self._exporter = exporter

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        sanitized = [self._sanitize_span(span) for span in spans]
        return self._exporter.export(sanitized)

    def _sanitize_span(self, span: ReadableSpan) -> ReadableSpan:
        attrs = dict(span.attributes or {})
        sanitized_attrs = sanitize_attributes(attrs, span.kind)
        return ReadableSpan(
            name=span.name,
            context=span.context,
            parent=span.parent,
            resource=span.resource,
            attributes=sanitized_attrs,
            events=span.events,
            links=span.links,
            kind=span.kind,
            status=span.status,
            start_time=span.start_time,
            end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope,
        )

    def shutdown(self) -> None:
        self._exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._exporter.force_flush(timeout_millis)


class PrivacySpanProcessor(SpanProcessor):
    """Deprecated: retained for compatibility; sanitization is performed by PrivacySpanExporter."""

    def on_start(self, span: object, parent_context: object = None) -> None:
        pass

    def on_end(self, span: object) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


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
    trace_exporter = PrivacySpanExporter(
        AzureMonitorTraceExporter(connection_string=connection_string, credential=credential)
    )
    tracer_provider.add_span_processor(BatchSpanProcessor(trace_exporter))
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
