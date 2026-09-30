"""Runtime connection pool, transaction-scoped tenant context and the runtime-role guard."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.pq import TransactionStatus
from psycopg.rows import TupleRow
from psycopg_pool import AsyncConnectionPool

type RuntimeConnection = AsyncConnection[TupleRow]
type RuntimePool = AsyncConnectionPool[RuntimeConnection]


class UnsafeDatabaseRoleError(RuntimeError):
    """The API was given a credential that could bypass tenant isolation."""


def create_runtime_pool(conninfo: str, *, min_size: int = 1, max_size: int = 10) -> RuntimePool:
    # Autocommit connections: every unit of work opens an explicit transaction.
    return AsyncConnectionPool(
        conninfo,
        min_size=min_size,
        max_size=max_size,
        kwargs={"autocommit": True},
        open=False,
        name="gba-runtime",
        check=AsyncConnectionPool.check_connection,
    )


async def set_tenant_context(conn: RuntimeConnection, tenant_id: UUID) -> None:
    """Scope RLS to `tenant_id` until the current transaction ends. Never session-wide."""
    if conn.info.transaction_status is not TransactionStatus.INTRANS:
        raise RuntimeError("tenant context may only be set inside an open transaction")
    await conn.execute("select pg_catalog.set_config('gba.tenant_id', %s, true)", (str(tenant_id),))


@asynccontextmanager
async def tenant_transaction(
    pool: RuntimePool, tenant_id: UUID
) -> AsyncIterator[RuntimeConnection]:
    async with pool.connection() as conn, conn.transaction():
        await set_tenant_context(conn, tenant_id)
        yield conn


@asynccontextmanager
async def unscoped_transaction(pool: RuntimePool) -> AsyncIterator[RuntimeConnection]:
    """A transaction with no tenant context: tenant-owned tables read as empty."""
    async with pool.connection() as conn, conn.transaction():
        yield conn


_ROLE_CHECK = """
select r.rolsuper,
       r.rolbypassrls,
       case when exists (select 1 from pg_catalog.pg_roles where rolname = 'gba_runtime')
            then pg_catalog.pg_has_role(current_user, 'gba_runtime', 'USAGE')
            else false end,
       exists (
           select 1
           from pg_catalog.pg_class c
           join pg_catalog.pg_namespace n on n.oid = c.relnamespace
           where n.nspname = 'gba'
             and pg_catalog.pg_has_role(current_user, c.relowner, 'MEMBER')
       ) or exists (
           select 1 from pg_catalog.pg_namespace n
           where n.nspname = 'gba' and pg_catalog.pg_has_role(current_user, n.nspowner, 'MEMBER')
       ),
       pg_catalog.to_regclass('gba.tenants') is not null
from pg_catalog.pg_roles r
where r.rolname = current_user
"""


async def assert_safe_runtime_role(conn: RuntimeConnection) -> None:
    row = await (await conn.execute(_ROLE_CHECK)).fetchone()
    if row is None:
        raise UnsafeDatabaseRoleError("current role not found")
    is_super, bypasses_rls, is_runtime, owns_schema_objects, schema_ready = row
    problems = [
        message
        for failed, message in (
            (is_super, "is a superuser"),
            (bypasses_rls, "has BYPASSRLS"),
            (not is_runtime, "is not a member of gba_runtime"),
            (owns_schema_objects, "owns (or is a member of the owner of) gba objects"),
            (not schema_ready, "cannot see the migrated gba schema"),
        )
        if failed
    ]
    if problems:
        raise UnsafeDatabaseRoleError("runtime database role " + "; ".join(problems))


def pool_readiness_probe(pool: RuntimePool, timeout: float = 2.0) -> Callable[[], Awaitable[None]]:
    async def probe() -> None:
        async with pool.connection(timeout=timeout) as conn:
            await assert_safe_runtime_role(conn)

    return probe
