import psycopg
import pytest
from psycopg import errors

from gorgona_booking.db.pool import RuntimePool, tenant_transaction
from gorgona_booking.db.provisioning import owner_tenant_transaction
from tests.integration.booking_support import BookingWorld

pytestmark = pytest.mark.anyio


async def test_customer_tables_force_rls(owner_conn: psycopg.Connection) -> None:
    rows = owner_conn.execute(
        "select relname, relrowsecurity, relforcerowsecurity from pg_class "
        "join pg_namespace n on n.oid = relnamespace where n.nspname = 'gba' "
        "and relname in ('resource_services', 'resource_hours', 'resource_blocks', "
        "'booking_customers') order by relname"
    ).fetchall()
    assert len(rows) == 4
    assert all(enabled and forced for _, enabled, forced in rows)


async def test_schedule_cross_tenant_fk_and_rls(
    world: BookingWorld, owner_conn: psycopg.Connection, app_pool: RuntimePool
) -> None:
    with owner_tenant_transaction(owner_conn, world.a.tenant_id):
        owner_conn.execute(
            "insert into gba.resource_hours "
            "(tenant_id, resource_id, weekday, opens_minute, closes_minute) "
            "values (%s, %s, 1, 540, 1020)",
            (world.a.tenant_id, world.artist_a1),
        )
    async with tenant_transaction(app_pool, world.b.tenant_id) as conn:
        assert await (await conn.execute("select * from gba.resource_hours")).fetchall() == []
    with pytest.raises(errors.ForeignKeyViolation):
        async with tenant_transaction(app_pool, world.b.tenant_id) as conn:
            await conn.execute(
                "insert into gba.resource_hours "
                "(tenant_id, resource_id, weekday, opens_minute, closes_minute) "
                "values (%s, %s, 1, 540, 1020)",
                (world.b.tenant_id, world.artist_a1),
            )
