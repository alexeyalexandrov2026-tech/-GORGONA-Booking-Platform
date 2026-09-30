"""The runtime credential cannot bypass isolation; migrations are owner-only and checksummed."""

from dataclasses import replace

import httpx
import psycopg
import pytest
from psycopg import errors

from gorgona_booking.api.app import create_app
from gorgona_booking.config import Settings
from gorgona_booking.db.migrate import MigrationError, apply_migrations, load_migrations
from gorgona_booking.db.pool import (
    RuntimePool,
    UnsafeDatabaseRoleError,
    assert_safe_runtime_role,
    unscoped_transaction,
)
from tests.integration.conftest import TEST_OWNER_ROLE, ProvisionedDatabase

pytestmark = pytest.mark.anyio


async def test_runtime_role_passes_the_guard(app_pool: RuntimePool) -> None:
    async with app_pool.connection() as conn:
        await assert_safe_runtime_role(conn)


async def test_owner_role_is_refused_as_runtime(test_database: ProvisionedDatabase) -> None:
    async with await psycopg.AsyncConnection.connect(test_database.owner_dsn) as conn:
        with pytest.raises(UnsafeDatabaseRoleError, match="owns"):
            await assert_safe_runtime_role(conn)


async def test_superuser_is_refused_as_runtime(test_database: ProvisionedDatabase) -> None:
    async with await psycopg.AsyncConnection.connect(
        psycopg.conninfo.make_conninfo(test_database.admin_dsn, dbname=test_database.name)
    ) as conn:
        with pytest.raises(UnsafeDatabaseRoleError, match="superuser"):
            await assert_safe_runtime_role(conn)


@pytest.mark.parametrize(
    "statement",
    [
        "alter table gba.locations disable row level security",
        "alter table gba.locations no force row level security",
        "drop policy locations_tenant_isolation on gba.locations",
        f"set role {TEST_OWNER_ROLE}",
        "select * from public.gba_schema_migrations",
        "create table gba.rogue (id int)",
    ],
)
async def test_runtime_role_cannot_escalate(app_pool: RuntimePool, statement: str) -> None:
    with pytest.raises(errors.InsufficientPrivilege):
        async with unscoped_transaction(app_pool) as conn:
            await conn.execute(statement.encode())


async def test_readiness_uses_the_real_runtime_role(app_pool: RuntimePool) -> None:
    app = create_app(Settings(environment="test"), pool=app_pool)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok"}}


def test_reapplying_migrations_is_a_no_op(test_database: ProvisionedDatabase) -> None:
    assert apply_migrations(test_database.owner_dsn) == []


def test_edited_migration_is_detected(test_database: ProvisionedDatabase) -> None:
    migrations = load_migrations()
    edited = [replace(migrations[0], sql=migrations[0].sql + "\n-- edited\n"), *migrations[1:]]
    with pytest.raises(MigrationError, match="edited"):
        apply_migrations(test_database.owner_dsn, edited)


def test_migrating_as_superuser_is_refused(test_database: ProvisionedDatabase) -> None:
    dsn = psycopg.conninfo.make_conninfo(test_database.admin_dsn, dbname=test_database.name)
    with pytest.raises(MigrationError, match="superuser"):
        apply_migrations(dsn)
