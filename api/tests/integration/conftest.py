"""Real PostgreSQL 18 only. No SQLite, no mocks.

Each test session creates a throwaway database, bootstraps dedicated test roles,
applies migrations as the owner role, and runs every assertion as the non-owner
runtime role. Without GBA_TEST_ADMIN_DSN these tests are skipped as BLOCKED;
with GBA_REQUIRE_POSTGRES=1 (CI) a missing server is a failure.
"""

import os
import secrets
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from gorgona_booking.db.bootstrap import BootstrapSpec, bootstrap
from gorgona_booking.db.migrate import apply_migrations
from gorgona_booking.db.pool import RuntimePool, create_runtime_pool
from tests.integration.booking_support import BookingWorld
from tests.integration.seed import Salon, seed_fake_catalog, seed_resource, seed_salon

TEST_OWNER_ROLE = "gba_test_owner"
TEST_APP_ROLE = "gba_test_app"


@dataclass(frozen=True, slots=True)
class ProvisionedDatabase:
    name: str
    admin_dsn: str
    owner_dsn: str
    app_dsn: str


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/integration" in item.nodeid.replace("\\", "/"):
            item.add_marker(pytest.mark.postgres)


@pytest.fixture(scope="session")
def test_database() -> Iterator[ProvisionedDatabase]:
    admin_dsn = os.environ.get("GBA_TEST_ADMIN_DSN")
    if not admin_dsn:
        reason = "BLOCKED: no PostgreSQL 18 server configured (set GBA_TEST_ADMIN_DSN)"
        if os.environ.get("GBA_REQUIRE_POSTGRES") == "1":
            pytest.fail(reason)
        pytest.skip(reason)

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        row = conn.execute("show server_version_num").fetchone()
        assert row is not None
        if int(row[0]) < 180000:
            pytest.fail(f"PostgreSQL 18+ is required; server_version_num={row[0]}")

    name = f"gba_test_{secrets.token_hex(6)}"
    owner_password, app_password = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    bootstrap(
        admin_dsn,
        BootstrapSpec(
            database=name,
            owner_role=TEST_OWNER_ROLE,
            owner_password=owner_password,
            app_role=TEST_APP_ROLE,
            app_password=app_password,
        ),
    )
    database = ProvisionedDatabase(
        name=name,
        admin_dsn=admin_dsn,
        owner_dsn=make_conninfo(
            admin_dsn, dbname=name, user=TEST_OWNER_ROLE, password=owner_password
        ),
        app_dsn=make_conninfo(admin_dsn, dbname=name, user=TEST_APP_ROLE, password=app_password),
    )
    apply_migrations(database.owner_dsn)
    try:
        yield database
    finally:
        if os.environ.get("GBA_TEST_KEEP_DB") != "1":
            with psycopg.connect(admin_dsn, autocommit=True) as conn:
                conn.execute(
                    sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(name))
                )


@pytest.fixture
def owner_conn(test_database: ProvisionedDatabase) -> Iterator[psycopg.Connection]:
    with psycopg.connect(test_database.owner_dsn, autocommit=True) as conn:
        yield conn


@pytest.fixture
async def app_pool(test_database: ProvisionedDatabase) -> AsyncIterator[RuntimePool]:
    pool = create_runtime_pool(test_database.app_dsn, min_size=1, max_size=10)
    await pool.open(wait=True, timeout=10.0)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
def salons(owner_conn: psycopg.Connection) -> tuple[Salon, Salon]:
    """Two fake salons with one fake location each."""
    return seed_salon(owner_conn, "a"), seed_salon(owner_conn, "b")


@pytest.fixture
def world(owner_conn: psycopg.Connection, salons: tuple[Salon, Salon]) -> BookingWorld:
    a, b = salons
    return BookingWorld(
        a=a,
        b=b,
        catalog_a=seed_fake_catalog(owner_conn, a),
        catalog_b=seed_fake_catalog(owner_conn, b),
        artist_a1=seed_resource(owner_conn, a, "FAKE artist A1"),
        artist_a2=seed_resource(owner_conn, a, "FAKE artist A2"),
        artist_b1=seed_resource(owner_conn, b, "FAKE artist B1"),
    )
