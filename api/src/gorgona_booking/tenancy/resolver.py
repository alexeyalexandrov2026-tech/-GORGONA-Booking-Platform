"""Resolve the tenant from the request host. Never from a client-supplied tenant ID.

Only trustworthy behind a reverse proxy that accepts known hosts only; M1 is not
deployed anywhere.
"""

from uuid import UUID

from gorgona_booking.db.pool import RuntimePool, set_tenant_context, unscoped_transaction
from gorgona_booking.errors import NotFoundError


class TenantNotFoundError(NotFoundError):
    code = "TENANT_NOT_FOUND"


def normalize_host(raw: str) -> str:
    host = raw.strip().lower()
    return host[:-1] if host.endswith(".") else host


async def resolve_tenant_by_host(pool: RuntimePool, raw_host: str) -> UUID:
    host = normalize_host(raw_host)
    async with unscoped_transaction(pool) as conn:
        row = await (
            await conn.execute("select tenant_id from gba.tenant_hosts where host = %s", (host,))
        ).fetchone()
        if row is None:
            raise TenantNotFoundError("Unknown site")
        tenant_id = UUID(str(row[0]))
        await set_tenant_context(conn, tenant_id)
        status = await (
            await conn.execute("select status, booking_state from gba.tenants")
        ).fetchone()
    # A suspended or not-yet-live salon is indistinguishable from an unknown one (ADR-0010).
    if status is None or status[0] != "active" or status[1] != "live":
        raise TenantNotFoundError("Unknown site")
    return tenant_id
