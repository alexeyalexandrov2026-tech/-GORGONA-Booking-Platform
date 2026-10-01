"""Durable job store: idempotent enqueue, leased claims, retries with backoff, dead letters.

A run is claimed in its own short transaction (so the lease is visible to other
workers), then its handler executes in one transaction together with the completion
update. A crash rolls the work back; the lease expires and another worker retries.
Handlers must be idempotent; the database constraints make them so.
"""

import json
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

log = logging.getLogger("gorgona_ai.jobs")

LEASE_SECONDS = 300
MAX_BACKOFF_SECONDS = 300


class PermanentJobError(Exception):
    """Non-retryable: the run is dead-lettered immediately."""


@dataclass(frozen=True, slots=True)
class JobRun:
    id: UUID
    kind: str
    tenant_id: UUID | None
    attempts: int
    max_attempts: int
    payload: dict[str, Any]


Handler = Callable[[psycopg.Connection, JobRun], dict[str, Any]]


def enqueue(
    conn: psycopg.Connection,
    kind: str,
    key: str,
    *,
    tenant_id: UUID | None = None,
    payload: Mapping[str, Any] | None = None,
    max_attempts: int = 5,
    delay_seconds: int = 0,
) -> UUID | None:
    """Insert a queued run unless the idempotency key exists. Returns its id if new."""
    row = conn.execute(
        "insert into ai.job_runs"
        " (kind, tenant_id, idempotency_key, payload, max_attempts, not_before)"
        " values (%s, %s, %s, %s, %s, now() + make_interval(secs => %s))"
        " on conflict (idempotency_key) do nothing returning id",
        (kind, tenant_id, key, Jsonb(dict(payload or {})), max_attempts, delay_seconds),
    ).fetchone()
    return row[0] if row else None


def claim(conn: psycopg.Connection, kinds: Iterable[str], worker: str) -> JobRun | None:
    """Lease the next due run (queued, or running with an expired lease)."""
    with conn.transaction():
        row = conn.execute(
            "update ai.job_runs set status = 'running', attempts = attempts + 1,"
            " lease_owner = %s, lease_until = now() + make_interval(secs => %s), updated_at = now()"
            " where id = (select id from ai.job_runs"
            "   where kind = any(%s)"
            "     and ((status = 'queued' and not_before <= now())"
            "          or (status = 'running' and lease_until < now()))"
            "   order by not_before, created_at for update skip locked limit 1)"
            " returning id, kind, tenant_id, attempts, max_attempts, payload",
            (worker, LEASE_SECONDS, list(kinds)),
        ).fetchone()
    if row is None:
        return None
    return JobRun(row[0], row[1], row[2], row[3], row[4], row[5])


def _finish(
    conn: psycopg.Connection, run: JobRun, status: str, result: Any, error: str | None
) -> None:
    conn.execute(
        "update ai.job_runs set status = %s, result = %s, last_error = %s, lease_owner = null,"
        " lease_until = null, updated_at = now(), finished_at = now() where id = %s",
        (status, Jsonb(result) if result is not None else None, error, run.id),
    )


def execute(conn: psycopg.Connection, run: JobRun, handler: Handler) -> str:
    """Run one claimed job. Returns the resulting status."""
    started = time.perf_counter()
    if run.attempts > run.max_attempts:
        with conn.transaction():
            _finish(conn, run, "dead", None, "AttemptsExhausted")
        _log(run, "dead", started, "AttemptsExhausted")
        return "dead"
    try:
        with conn.transaction():
            result = handler(conn, run)
            _finish(conn, run, "succeeded", result, None)
    except PermanentJobError as exc:
        with conn.transaction():
            _finish(conn, run, "dead", {"reason": str(exc)[:200]}, type(exc).__name__)
        _log(run, "dead", started, type(exc).__name__)
        return "dead"
    except Exception as exc:  # any other failure is retryable
        error = type(exc).__name__  # the type only: messages can carry data
        with conn.transaction():
            if run.attempts >= run.max_attempts:
                _finish(conn, run, "dead", None, error)
                status = "dead"
            else:
                backoff = min(2**run.attempts, MAX_BACKOFF_SECONDS)
                conn.execute(
                    "update ai.job_runs set status = 'queued', last_error = %s, lease_owner = null,"
                    " lease_until = null, not_before = now() + make_interval(secs => %s),"
                    " updated_at = now() where id = %s",
                    (error, backoff, run.id),
                )
                status = "queued"
        _log(run, "retry" if status == "queued" else "dead", started, error)
        return status
    _log(run, "succeeded", started, None)
    return "succeeded"


def drain(
    conn: psycopg.Connection,
    handlers: Mapping[str, Handler],
    *,
    worker: str,
    kinds: Iterable[str] | None = None,
    max_runs: int = 1000,
) -> dict[str, int]:
    """Process due runs until none remain (or max_runs). Returns counts by outcome."""
    wanted = list(kinds) if kinds is not None else list(handlers)
    counts: dict[str, int] = {}
    for _ in range(max_runs):
        run = claim(conn, wanted, worker)
        if run is None:
            break
        status = execute(conn, run, handlers[run.kind])
        counts[status] = counts.get(status, 0) + 1
    return counts


def _log(run: JobRun, outcome: str, started: float, error: str | None) -> None:
    log.info(
        "job finished",
        extra={
            "job_id": str(run.id),
            "kind": run.kind,
            "tenant_id": str(run.tenant_id) if run.tenant_id else None,
            "attempt": run.attempts,
            "outcome": outcome,
            "error_type": error,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        },
    )


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
