"""The load-baseline tool against the real API and PostgreSQL (small, fast run).

Proves the tool measures what it claims and that the platform keeps its contention
invariant under concurrent holds: exactly one winner, every other attempt a clean
409 SLOT_CONFLICT, no 5xx. The staging baseline uses the same tool with larger numbers.
"""

import asyncio

import psycopg
import pytest
from pydantic import SecretStr
from tools import load_baseline

from gorgona_booking.api.app import create_app
from gorgona_booking.config import Settings
from gorgona_booking.db.provisioning import owner_tenant_transaction
from tests.integration.booking_support import BookingWorld
from tests.integration.conftest import ProvisionedDatabase
from tests.integration.customer_support import customer_day, seed_customer_setup
from tests.integration.live_server import free_port, live_server


def test_load_baseline_reports_latency_and_single_contention_winner(
    world: BookingWorld, owner_conn: psycopg.Connection, test_database: ProvisionedDatabase
) -> None:
    port = free_port()
    host = f"127.0.0.1:{port}"
    seed_customer_setup(owner_conn, world)
    with owner_tenant_transaction(owner_conn, world.a.tenant_id):
        owner_conn.execute(
            "insert into gba.tenant_hosts (host, tenant_id) values (%s, %s)",
            (host, world.a.tenant_id),
        )
    app = create_app(Settings(environment="test", database_url=SecretStr(test_database.app_dsn)))
    args = load_baseline.parse_args(
        ["--base-url", f"http://{host}", "--day", customer_day(), "--concurrency", "4",
         "--duration", "1.5", "--contenders", "6", "--bookings", "4"]
    )  # fmt: skip
    with live_server(app, port):
        report = asyncio.run(load_baseline.run(args))

    assert report["verdict"] == "PASS", report
    assert report["failures"] == 0
    assert report["contention"] == {
        "contenders": 6,
        "winners": 1,
        "slot_conflicts": 5,
        "other": {},
        "correct": True,
    }
    ops = report["operations"]
    for op in ("bootstrap", "availability", "hold", "confirm"):
        assert ops[op]["count"] > 0
        assert 0 < ops[op]["p50_ms"] <= ops[op]["p95_ms"] <= ops[op]["p99_ms"] <= ops[op]["max_ms"]
    assert report["bookings"]["confirmed"] >= 1
    with owner_tenant_transaction(owner_conn, world.a.tenant_id):
        confirmed = owner_conn.execute(
            "select count(*) from gba.bookings where status = 'CONFIRMED'"
        ).fetchone()
    assert confirmed == (report["bookings"]["confirmed"],)
    with owner_tenant_transaction(owner_conn, world.b.tenant_id):
        assert owner_conn.execute("select count(*) from gba.bookings").fetchone() == (0,)


def test_load_baseline_refuses_remote_targets_without_explicit_flag() -> None:
    with pytest.raises(SystemExit):
        load_baseline.parse_args(["--base-url", "https://staging.example.test", "--day", "x"])
    args = load_baseline.parse_args(
        ["--base-url", "https://staging.example.test", "--day", "x", "--allow-remote"]
    )
    assert args.allow_remote


def test_percentile_is_nearest_rank() -> None:
    values = [float(v) for v in range(1, 101)]
    assert load_baseline.percentile(values, 50) == 50.0
    assert load_baseline.percentile(values, 95) == 95.0
    assert load_baseline.percentile(values, 99) == 99.0
    assert load_baseline.percentile([], 95) == 0.0
