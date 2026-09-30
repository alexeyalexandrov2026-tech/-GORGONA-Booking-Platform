"""Tenant context from identity -> active membership -> tenant (ADR-0009).

The salon named in a URL is only a request. Access is decided here, inside the
same transaction as the operation, so authorization and work cannot diverge.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from psycopg.types.json import Jsonb

from gorgona_booking.auth.permissions import (
    PLATFORM_ADMIN_PERMISSIONS,
    ROLE_PERMISSIONS,
    Permission,
)
from gorgona_booking.auth.principal import Principal, set_user_context
from gorgona_booking.db.pool import RuntimeConnection, RuntimePool, set_tenant_context
from gorgona_booking.errors import DomainError

_DENIED = "You do not have access to this salon"


class TenantAccessDeniedError(DomainError):
    code = "TENANT_ACCESS_DENIED"


class TenantSuspendedError(DomainError):
    code = "TENANT_SUSPENDED"


class PermissionDeniedError(DomainError):
    code = "PERMISSION_DENIED"


@dataclass(frozen=True, slots=True)
class TenantAccess:
    conn: RuntimeConnection
    tenant_id: UUID
    principal: Principal
    role: str | None
    via: Literal["membership", "platform"]


@asynccontextmanager
async def authorized_tenant(
    pool: RuntimePool,
    principal: Principal,
    salon_id: UUID,
    permission: Permission,
    *,
    request_id: str | None = None,
    exclusive: str | None = None,
) -> AsyncIterator[TenantAccess]:
    """Yield a connection scoped to `salon_id` only if `principal` may use `permission`.

    Any failure raises before the caller runs and rolls the transaction back, so the
    candidate tenant context never outlives the check.

    `exclusive` names a per-salon critical section (e.g. "members"). Its advisory
    lock is taken *before* the membership row lock, so two requests that change each
    other's memberships queue instead of deadlocking.
    """
    async with pool.connection() as conn, conn.transaction():
        if exclusive is not None:
            await conn.execute(
                "select pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s, 0))",
                (f"gba:{exclusive}:{salon_id}",),
            )
        await set_tenant_context(conn, salon_id)
        await set_user_context(conn, principal.user_id, request_id=request_id)
        yield await _authorize(conn, principal, salon_id, permission)


async def _authorize(
    conn: RuntimeConnection, principal: Principal, salon_id: UUID, permission: Permission
) -> TenantAccess:
    # FOR SHARE: a concurrent suspend/revoke waits until this request finishes.
    membership = await (
        await conn.execute(
            "select role from gba.memberships "
            "where tenant_id = %s and user_id = %s and status = 'active' for share",
            (salon_id, principal.user_id),
        )
    ).fetchone()
    tenant = await (
        await conn.execute("select status from gba.tenants where id = %s", (salon_id,))
    ).fetchone()
    platform_admin = principal.is_platform_admin and await _is_platform_admin(conn, principal)
    platform_may = platform_admin and permission in PLATFORM_ADMIN_PERMISSIONS

    if membership is not None and tenant is not None:
        role = str(membership[0])
        if tenant[0] != "active" and not platform_may:
            raise TenantSuspendedError("This salon is suspended")
        if permission in ROLE_PERMISSIONS[role] and tenant[0] == "active":
            return TenantAccess(conn, salon_id, principal, role, "membership")
        if not platform_may:
            raise PermissionDeniedError("Your role does not allow this action")
    elif tenant is None or not platform_admin:
        raise TenantAccessDeniedError(_DENIED)
    elif not platform_may:
        raise PermissionDeniedError("Platform support access is read-only")

    await conn.execute(
        "insert into gba.audit_events (tenant_id, actor, action, target_type, target_id, "
        "details, request_id) values (%s, %s, 'platform.tenant_access', 'tenant', %s, %s, "
        "nullif(pg_catalog.current_setting('gba.request_id', true), ''))",
        (salon_id, principal.actor, str(salon_id), Jsonb({"permission": str(permission)})),
    )
    return TenantAccess(
        conn, salon_id, principal, str(membership[0]) if membership else None, "platform"
    )


async def _is_platform_admin(conn: RuntimeConnection, principal: Principal) -> bool:
    """Re-checked in the request transaction: a revoked grant stops working at once."""
    row = await (
        await conn.execute(
            "select 1 from gba.platform_roles "
            "where user_id = %s and role = 'platform_admin' and revoked_at is null",
            (principal.user_id,),
        )
    ).fetchone()
    return row is not None
