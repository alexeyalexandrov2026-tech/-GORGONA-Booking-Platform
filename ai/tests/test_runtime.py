"""Durable job runtime against real PostgreSQL: idempotency, leases, retries, dead letters."""

import threading
from typing import Any

import psycopg
import pytest

from gorgona_ai.jobs.runtime import JobRun, PermanentJobError, claim, drain, enqueue
from tests.conftest import AiDatabase


def _status(conn: psycopg.Connection, key: str) -> tuple[str, int, str | None]:
    row = conn.execute(
        "select status, attempts, last_error from ai.job_runs where idempotency_key = %s", (key,)
    ).fetchone()
    assert row is not None
    return row[0], row[1], row[2]


def test_enqueue_is_idempotent(conn: psycopg.Connection) -> None:
    first = enqueue(conn, "noop", "k1")
    assert first is not None
    assert enqueue(conn, "noop", "k1") is None
    assert conn.execute("select count(*) from ai.job_runs").fetchone() == (1,)


def test_success_records_result(conn: psycopg.Connection) -> None:
    enqueue(conn, "noop", "ok", payload={"n": 2})
    counts = drain(conn, {"noop": lambda c, run: {"double": run.payload["n"] * 2}}, worker="w")
    assert counts == {"succeeded": 1}
    row = conn.execute("select status, result from ai.job_runs").fetchone()
    assert row == ("succeeded", {"double": 4})


def test_transient_failures_retry_with_backoff_then_succeed(conn: psycopg.Connection) -> None:
    calls = {"n": 0}

    def flaky(c: psycopg.Connection, run: JobRun) -> dict[str, Any]:
        calls["n"] += 1
        c.execute("insert into ai.tenant_activity (tenant_id) values (gen_random_uuid())")
        if calls["n"] < 3:
            raise TimeoutError("transient")
        return {"ok": True}

    enqueue(conn, "flaky", "f")
    assert drain(conn, {"flaky": flaky}, worker="w") == {"queued": 1}
    assert _status(conn, "f") == ("queued", 1, "TimeoutError")
    # Backoff: not due yet; make it due to simulate the passage of time.
    assert drain(conn, {"flaky": flaky}, worker="w") == {}
    for _ in range(2):
        conn.execute("update ai.job_runs set not_before = now()")
        drain(conn, {"flaky": flaky}, worker="w")
    assert _status(conn, "f") == ("succeeded", 3, None)
    # The failed attempts rolled back their writes; only the successful one persisted.
    assert conn.execute("select count(*) from ai.tenant_activity").fetchone() == (1,)


def test_permanent_errors_dead_letter_immediately(conn: psycopg.Connection) -> None:
    def bad(c: psycopg.Connection, run: JobRun) -> dict[str, Any]:
        raise PermanentJobError("bad input")

    enqueue(conn, "bad", "b")
    assert drain(conn, {"bad": bad}, worker="w") == {"dead": 1}
    assert _status(conn, "b") == ("dead", 1, "PermanentJobError")


def test_exhausted_attempts_dead_letter(conn: psycopg.Connection) -> None:
    def always(c: psycopg.Connection, run: JobRun) -> dict[str, Any]:
        raise RuntimeError("boom")

    enqueue(conn, "x", "x", max_attempts=2)
    drain(conn, {"x": always}, worker="w")
    conn.execute("update ai.job_runs set not_before = now()")
    drain(conn, {"x": always}, worker="w")
    assert _status(conn, "x") == ("dead", 2, "RuntimeError")


def test_expired_lease_is_reclaimed_after_a_worker_crash(conn: psycopg.Connection) -> None:
    enqueue(conn, "x", "crash", max_attempts=3)
    run = claim(conn, ["x"], "crashed-worker")
    assert run is not None
    assert claim(conn, ["x"], "other") is None  # leased
    conn.execute("update ai.job_runs set lease_until = now() - interval '1 second'")
    assert drain(conn, {"x": lambda c, r: {"by": "survivor"}}, worker="survivor") == {
        "succeeded": 1
    }
    assert _status(conn, "crash")[:2] == ("succeeded", 2)


def test_a_run_is_claimed_by_exactly_one_worker(
    ai_db: AiDatabase, conn: psycopg.Connection
) -> None:
    for i in range(40):
        enqueue(conn, "x", f"c{i}")
    claimed: list[str] = []
    lock = threading.Lock()

    def worker(name: str) -> None:
        with psycopg.connect(ai_db.worker_dsn, autocommit=True) as c:
            while (run := claim(c, ["x"], name)) is not None:
                with lock:
                    claimed.append(str(run.id))

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert len(claimed) == 40
    assert len(set(claimed)) == 40


def test_worker_role_cannot_bypass_tenant_isolation(conn: psycopg.Connection) -> None:
    row = conn.execute(
        "select rolsuper, rolbypassrls from pg_roles where rolname = current_user"
    ).fetchone()
    assert row == (False, False)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("drop table ai.evidence")
