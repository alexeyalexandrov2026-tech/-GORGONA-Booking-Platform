"""Governed embedding allowlist: which web origins may frame a tenant's booking pages.

Operators manage it with the owner credential; the runtime role only reads it.
The HTTP framing policy (api/framing.py) emits it as CSP frame-ancestors.
"""

import re
from uuid import UUID

import psycopg

from gorgona_booking.db.pool import RuntimePool, tenant_transaction
from gorgona_booking.db.provisioning import owner_tenant_transaction
from gorgona_booking.errors import DomainError
from gorgona_booking.tenancy.resolver import TenantNotFoundError, resolve_tenant_by_host

# Mirrors the CHECK constraint in migration 0007.
_HTTPS_ORIGIN = re.compile(
    r"^https://[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+(:[0-9]{1,5})?$"
)
_LOOPBACK_ORIGIN = re.compile(r"^http://(127\.0\.0\.1|localhost)(:[0-9]{1,5})?$")


class InvalidEmbedOriginError(DomainError):
    code = "INVALID_EMBED_ORIGIN"


def normalize_origin(raw: str) -> str:
    """Scheme://host[:port], lowercase, with no path, query, wildcard or credentials."""
    origin = raw.strip().lower().removesuffix("/")
    if not (_HTTPS_ORIGIN.fullmatch(origin) or _LOOPBACK_ORIGIN.fullmatch(origin)):
        raise InvalidEmbedOriginError(
            "An embed origin must be https://host[:port] (loopback http only for local tests)"
        )
    return origin


def _set_actor(conn: psycopg.Connection, actor: str) -> None:
    conn.execute("select pg_catalog.set_config('gba.actor', %s, true)", (actor,))


def add_embed_origin(conn: psycopg.Connection, tenant_id: UUID, origin: str, *, actor: str) -> None:
    """Approve an origin (owner credential). Re-approving a revoked origin is audited too."""
    value = normalize_origin(origin)
    with owner_tenant_transaction(conn, tenant_id):
        _set_actor(conn, actor)
        conn.execute(
            "insert into gba.tenant_embed_origins (tenant_id, origin) values (%s, %s) "
            "on conflict (tenant_id, origin) do update "
            "set status = 'approved', updated_at = now() "
            "where gba.tenant_embed_origins.status <> 'approved'",
            (tenant_id, value),
        )


def revoke_embed_origin(
    conn: psycopg.Connection, tenant_id: UUID, origin: str, *, actor: str
) -> None:
    value = normalize_origin(origin)
    with owner_tenant_transaction(conn, tenant_id):
        _set_actor(conn, actor)
        conn.execute(
            "update gba.tenant_embed_origins set status = 'revoked', updated_at = now() "
            "where tenant_id = %s and origin = %s and status = 'approved'",
            (tenant_id, value),
        )


def list_embed_origins(conn: psycopg.Connection, tenant_id: UUID) -> list[tuple[str, str]]:
    with owner_tenant_transaction(conn, tenant_id):
        rows = conn.execute(
            "select origin, status from gba.tenant_embed_origins order by origin"
        ).fetchall()
    return [(str(origin), str(status)) for origin, status in rows]


async def approved_embed_origins(
    pool: RuntimePool, raw_host: str, *, allow_loopback: bool
) -> list[str]:
    """Approved origins for the live tenant behind `raw_host`; [] for unknown or not-live."""
    try:
        tenant_id = await resolve_tenant_by_host(pool, raw_host)
    except TenantNotFoundError:
        return []
    async with tenant_transaction(pool, tenant_id) as conn:
        rows = await (
            await conn.execute(
                "select origin from gba.tenant_embed_origins "
                "where status = 'approved' order by origin"
            )
        ).fetchall()
    origins = [str(row[0]) for row in rows]
    return [o for o in origins if allow_loopback or not _LOOPBACK_ORIGIN.fullmatch(o)]
