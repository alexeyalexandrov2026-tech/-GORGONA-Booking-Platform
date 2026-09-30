"""Idempotency keys (ADR-0004), claimed in the same transaction as the work they guard."""

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from gorgona_booking.booking.models import InvalidIdempotencyKeyError
from gorgona_booking.db.pool import RuntimeConnection

_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,255}$")


@dataclass(frozen=True, slots=True)
class IdempotencyScope:
    tenant_id: UUID
    actor_key: str
    operation: str
    key: str

    def __post_init__(self) -> None:
        if not _KEY.fullmatch(self.key):
            raise InvalidIdempotencyKeyError(
                "Idempotency key must be 8-255 characters of A-Z a-z 0-9 . _ : -"
            )


@dataclass(frozen=True, slots=True)
class StoredResponse:
    request_hash: str
    status_code: int
    body: dict[str, Any]


async def claim(
    conn: RuntimeConnection, scope: IdempotencyScope, request_hash: str
) -> StoredResponse | None:
    """Claim the key, or return the response already stored under it.

    A concurrent transaction holding the same key makes this wait on the primary
    key until it commits (then we read its response) or rolls back (then we
    claim it). An expired key is reclaimed.
    """
    cur = await conn.execute(
        """
        insert into gba.idempotency_keys (tenant_id, actor_key, operation, idempotency_key,
                                          request_hash)
        values (%s, %s, %s, %s, %s)
        on conflict (tenant_id, actor_key, operation, idempotency_key) do update
            set request_hash = excluded.request_hash,
                response_status = null,
                response_body = null,
                created_at = now(),
                expires_at = now() + interval '24 hours'
            where gba.idempotency_keys.expires_at <= now()
        """,
        (scope.tenant_id, scope.actor_key, scope.operation, scope.key, request_hash),
    )
    if cur.rowcount == 1:
        return None
    row = await (
        await conn.execute(
            "select request_hash, response_status, response_body from gba.idempotency_keys "
            "where tenant_id = %s and actor_key = %s and operation = %s and idempotency_key = %s",
            (scope.tenant_id, scope.actor_key, scope.operation, scope.key),
        )
    ).fetchone()
    if row is None or row[1] is None:
        # Unreachable while claims and completions share one transaction.
        raise RuntimeError("idempotency record is missing or incomplete")
    return StoredResponse(request_hash=row[0], status_code=row[1], body=row[2])


async def complete(
    conn: RuntimeConnection, scope: IdempotencyScope, status_code: int, body: dict[str, Any]
) -> None:
    await conn.execute(
        "update gba.idempotency_keys set response_status = %s, response_body = %s "
        "where tenant_id = %s and actor_key = %s and operation = %s and idempotency_key = %s",
        (status_code, Jsonb(body), scope.tenant_id, scope.actor_key, scope.operation, scope.key),
    )
