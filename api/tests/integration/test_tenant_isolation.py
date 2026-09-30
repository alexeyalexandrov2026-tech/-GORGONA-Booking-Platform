"""Salon isolation, proven with the real non-owner runtime role on real PostgreSQL."""

from uuid import uuid7

import psycopg
import pytest
from psycopg import errors

from gorgona_booking.db.pool import (
    RuntimePool,
    create_runtime_pool,
    tenant_transaction,
    unscoped_transaction,
)
from tests.integration.conftest import ProvisionedDatabase
from tests.integration.seed import Salon, seed_user

pytestmark = pytest.mark.anyio


async def test_select_sees_only_own_salon(
    app_pool: RuntimePool, salons: tuple[Salon, Salon]
) -> None:
    a, b = salons
    async with tenant_transaction(app_pool, a.tenant_id) as conn:
        tenants = await (await conn.execute("select id from gba.tenants")).fetchall()
        locations = await (await conn.execute("select tenant_id, id from gba.locations")).fetchall()
    assert tenants == [(a.tenant_id,)]
    assert (a.tenant_id, a.location_id) in locations
    assert all(tenant_id == a.tenant_id for tenant_id, _ in locations)
    assert (b.tenant_id, b.location_id) not in locations


async def test_insert_into_other_salon_is_rejected(
    app_pool: RuntimePool, salons: tuple[Salon, Salon]
) -> None:
    a, b = salons
    with pytest.raises(errors.InsufficientPrivilege):
        async with tenant_transaction(app_pool, a.tenant_id) as conn:
            await conn.execute(
                "insert into gba.locations (tenant_id, name, timezone) values (%s, 'x', 'UTC')",
                (b.tenant_id,),
            )


async def test_update_cannot_reach_or_move_rows_across_salons(
    app_pool: RuntimePool, salons: tuple[Salon, Salon]
) -> None:
    a, b = salons
    async with tenant_transaction(app_pool, a.tenant_id) as conn:
        cur = await conn.execute(
            "update gba.locations set name = 'hijacked' where id = %s", (b.location_id,)
        )
        assert cur.rowcount == 0

    with pytest.raises(errors.InsufficientPrivilege):
        async with tenant_transaction(app_pool, a.tenant_id) as conn:
            await conn.execute(
                "update gba.locations set tenant_id = %s where id = %s",
                (b.tenant_id, a.location_id),
            )

    async with tenant_transaction(app_pool, b.tenant_id) as conn:
        row = await (
            await conn.execute("select name from gba.locations where id = %s", (b.location_id,))
        ).fetchone()
    assert row == ("FAKE location B",)


async def test_delete_cannot_reach_other_salon(
    app_pool: RuntimePool, salons: tuple[Salon, Salon]
) -> None:
    a, b = salons
    async with tenant_transaction(app_pool, a.tenant_id) as conn:
        cur = await conn.execute("delete from gba.locations where id = %s", (b.location_id,))
        assert cur.rowcount == 0
    async with tenant_transaction(app_pool, b.tenant_id) as conn:
        row = await (
            await conn.execute("select count(*) from gba.locations where id = %s", (b.location_id,))
        ).fetchone()
    assert row == (1,)


async def test_without_tenant_context_nothing_is_visible_or_writable(
    app_pool: RuntimePool, salons: tuple[Salon, Salon]
) -> None:
    a, _ = salons
    async with unscoped_transaction(app_pool) as conn:
        for table in ("tenants", "locations", "memberships"):
            row = await (await conn.execute(f"select count(*) from gba.{table}")).fetchone()
            assert row == (0,), table
    with pytest.raises(errors.InsufficientPrivilege):
        async with unscoped_transaction(app_pool) as conn:
            await conn.execute(
                "insert into gba.locations (tenant_id, name, timezone) values (%s, 'x', 'UTC')",
                (a.tenant_id,),
            )


async def test_composite_foreign_key_blocks_cross_salon_reference(
    app_pool: RuntimePool, salons: tuple[Salon, Salon], owner_conn: psycopg.Connection
) -> None:
    a, b = salons
    user = seed_user(owner_conn, "fk")  # M2: memberships reference a user (owner decision 1)
    with pytest.raises(errors.ForeignKeyViolation):
        async with tenant_transaction(app_pool, a.tenant_id) as conn:
            await conn.execute(
                "insert into gba.memberships (tenant_id, user_id, role, location_id) "
                "values (%s, %s, 'artist', %s)",
                (a.tenant_id, user.user_id, b.location_id),
            )


async def test_tenant_context_does_not_leak_through_pooled_connections(
    test_database: ProvisionedDatabase, salons: tuple[Salon, Salon]
) -> None:
    a, _ = salons
    pool = create_runtime_pool(test_database.app_dsn, min_size=1, max_size=1)
    await pool.open(wait=True, timeout=10.0)
    try:
        async with tenant_transaction(pool, a.tenant_id) as conn:
            first_pid = conn.info.backend_pid
        # Same physical connection (max_size=1), new transaction, no context.
        async with unscoped_transaction(pool) as conn:
            assert conn.info.backend_pid == first_pid
            row = await (
                await conn.execute("select gba.current_tenant_id(), count(*) from gba.locations")
            ).fetchone()
        assert row == (None, 0)

        # Context is also dropped when the transaction fails.
        with pytest.raises(errors.DivisionByZero):
            async with tenant_transaction(pool, a.tenant_id) as conn:
                await conn.execute("select 1 / 0")
        async with unscoped_transaction(pool) as conn:
            row = await (await conn.execute("select gba.current_tenant_id()")).fetchone()
        assert row == (None,)
    finally:
        await pool.close()


async def test_runtime_cannot_create_tenants_or_write_host_routes(
    app_pool: RuntimePool, salons: tuple[Salon, Salon]
) -> None:
    a, b = salons
    new_tenant = uuid7()
    with pytest.raises(errors.InsufficientPrivilege):
        async with tenant_transaction(app_pool, new_tenant) as conn:
            await conn.execute(
                "insert into gba.tenants (id, slug, display_name) values (%s, 'rogue', 'x')",
                (new_tenant,),
            )
    with pytest.raises(errors.InsufficientPrivilege):
        async with tenant_transaction(app_pool, a.tenant_id) as conn:
            await conn.execute(
                "update gba.tenant_hosts set tenant_id = %s where host = %s", (a.tenant_id, b.host)
            )
    # Host routing is readable without context by design (hostnames and IDs only).
    async with unscoped_transaction(app_pool) as conn:
        row = await (
            await conn.execute("select tenant_id from gba.tenant_hosts where host = %s", (b.host,))
        ).fetchone()
    assert row == (b.tenant_id,)


@pytest.mark.parametrize("zone", ["Mars/Olympus_Mons", "EST", "+05:00", ""])
async def test_location_timezone_must_be_iana(
    app_pool: RuntimePool, salons: tuple[Salon, Salon], zone: str
) -> None:
    a, _ = salons
    with pytest.raises((errors.InvalidParameterValue, errors.CheckViolation)):
        async with tenant_transaction(app_pool, a.tenant_id) as conn:
            await conn.execute(
                "insert into gba.locations (tenant_id, name, timezone) values (%s, 'x', %s)",
                (a.tenant_id, zone),
            )


def test_every_tenant_owned_table_has_forced_rls(owner_conn: psycopg.Connection) -> None:
    rows = owner_conn.execute(
        """
        select c.relname, c.relrowsecurity, c.relforcerowsecurity
        from pg_catalog.pg_class c
        join pg_catalog.pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'gba' and c.relkind in ('r', 'p')
          and (c.relname = 'tenants' or exists (
                select 1 from pg_catalog.pg_attribute a
                where a.attrelid = c.oid and a.attname = 'tenant_id' and not a.attisdropped))
        order by c.relname
        """
    ).fetchall()
    assert {name for name, _, _ in rows} >= {"tenants", "tenant_hosts", "locations", "memberships"}
    unprotected = [name for name, enabled, forced in rows if not (enabled and forced)]
    assert unprotected == []
