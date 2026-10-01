"""JSON logs (allowlisted fields, no payload content) and health reporting from the job store."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

import psycopg

_FIELDS = (
    "job_id",
    "kind",
    "tenant_id",
    "attempt",
    "outcome",
    "error_type",
    "duration_ms",
    "count",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def health(
    conn: psycopg.Connection, *, stale_minutes: int = 60, window_hours: int = 24
) -> dict[str, Any]:
    """Job-store health. Unhealthy when runs died recently, the backlog is old, or a
    periodic job has not succeeded within stale_minutes."""
    by_status = {
        (kind, status): count
        for kind, status, count in conn.execute(
            "select kind, status, count(*) from ai.job_runs"
            " where created_at > now() - make_interval(hours => %s) group by kind, status",
            (window_hours,),
        ).fetchall()
    }
    oldest_due = conn.execute(
        "select extract(epoch from now() - min(not_before)) from ai.job_runs"
        " where status = 'queued' and not_before <= now()"
    ).fetchone()[0]  # type: ignore[index]
    last_success: dict[str, Any] = dict(
        conn.execute(
            "select kind, extract(epoch from now() - max(finished_at)) from ai.job_runs"
            " where status = 'succeeded' group by kind"
        ).fetchall()
    )
    dead = sum(c for (_, s), c in by_status.items() if s == "dead")
    problems = []
    if dead:
        problems.append(f"{dead} dead run(s) in the last {window_hours} h")
    if oldest_due is not None and float(oldest_due) > stale_minutes * 60:
        problems.append("backlog older than threshold")
    for kind in ("dataset_cut", "rag_refresh"):
        age = last_success.get(kind)
        if age is None or float(age) > stale_minutes * 60:
            problems.append(f"{kind} has not succeeded within {stale_minutes} min")
    return {
        "healthy": not problems,
        "problems": problems,
        "runs": {f"{k}:{s}": c for (k, s), c in sorted(by_status.items())},
        "oldest_due_seconds": float(oldest_due) if oldest_due is not None else None,
    }
