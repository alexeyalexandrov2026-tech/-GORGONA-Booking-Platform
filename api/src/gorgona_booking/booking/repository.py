"""SQL for bookings and occupancy. Every function runs inside the caller's tenant transaction."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from gorgona_booking.booking.models import (
    BookingResult,
    BookingStatus,
    ResourceUnavailableError,
)
from gorgona_booking.catalog.models import Quote
from gorgona_booking.db.pool import RuntimeConnection
from gorgona_booking.errors import NotFoundError

NO_OVERLAP_CONSTRAINT = "booking_allocations_no_overlap"
EXCLUSION_VIOLATION = "23P01"


def is_slot_conflict(exc: object) -> bool:
    """True only for the occupancy exclusion constraint, never for other 23P01s."""
    diag = getattr(exc, "diag", None)
    return (
        getattr(exc, "sqlstate", None) == EXCLUSION_VIOLATION
        and getattr(diag, "constraint_name", None) == NO_OVERLAP_CONSTRAINT
    )


async def set_audit_context(
    conn: RuntimeConnection, *, actor: str, reason: str, request_id: str | None
) -> None:
    """Transaction-local values read by the booking_events trigger."""
    await conn.execute(
        "select pg_catalog.set_config('gba.actor', %s, true), "
        "pg_catalog.set_config('gba.reason', %s, true), "
        "pg_catalog.set_config('gba.request_id', %s, true)",
        (actor, reason, request_id or ""),
    )


async def active_resource_location(conn: RuntimeConnection, resource_id: UUID) -> UUID:
    row = await (
        await conn.execute(
            "select location_id, is_active from gba.resources where id = %s", (resource_id,)
        )
    ).fetchone()
    if row is None:
        raise NotFoundError("Resource not found", resource_id=str(resource_id))
    if not row[1]:
        raise ResourceUnavailableError("Resource is not available", resource_id=str(resource_id))
    return UUID(str(row[0]))


async def lock_resource_schedule(
    conn: RuntimeConnection, tenant_id: UUID, resource_id: UUID
) -> None:
    """Serialize occupancy changes for one resource until this transaction ends.

    Contention management only; the exclusion constraint stays the authority.
    Without it, two concurrent inserters of overlapping ranges can each wait on
    the other's uncommitted index entry, and PostgreSQL resolves that cycle with
    40P01 (deadlock) instead of a clean 23P01 conflict.
    """
    await conn.execute(
        "select pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s, 0))",
        (f"gba:resource:{tenant_id}:{resource_id}",),
    )


async def booking_resource_ids(conn: RuntimeConnection, booking_id: UUID) -> list[UUID]:
    rows = await (
        await conn.execute(
            "select resource_id from gba.booking_allocations where booking_id = %s "
            "order by resource_id",
            (booking_id,),
        )
    ).fetchall()
    return [UUID(str(row[0])) for row in rows]


async def expire_stale_holds_overlapping(
    conn: RuntimeConnection, resource_id: UUID, starts_at: datetime, ends_at: datetime
) -> int:
    """Move holds past their expiry that overlap the range out of the blocking set.

    Rows are locked in id order; a hold another transaction already moved on is
    re-checked after the lock and skipped.
    """
    cur = await conn.execute(
        """
        with stale as (
            select b.tenant_id, b.id
            from gba.bookings b
            join gba.booking_allocations a
              on a.tenant_id = b.tenant_id and a.booking_id = b.id
            where a.resource_id = %s
              and a.during && tstzrange(%s, %s, '[)')
              and b.status = 'HOLD'
              and b.hold_expires_at <= now()
            order by b.id
            for update of b
        )
        update gba.bookings b
        set status = 'EXPIRED'
        from stale
        where b.tenant_id = stale.tenant_id and b.id = stale.id
        """,
        (resource_id, starts_at, ends_at),
    )
    return cur.rowcount


async def insert_booking(
    conn: RuntimeConnection,
    *,
    tenant_id: UUID,
    location_id: UUID,
    resource_id: UUID,
    variant_id: UUID,
    status: BookingStatus,
    starts_at: datetime,
    ends_at: datetime,
    hold_ttl_seconds: int,
    quote: Quote,
    created_by: str,
) -> UUID:
    row = await (
        await conn.execute(
            """
            insert into gba.bookings (tenant_id, location_id, variant_id, status, starts_at,
                                      ends_at, hold_expires_at, total_cents, currency, quote,
                                      created_by)
            values (%(tenant_id)s, %(location_id)s, %(variant_id)s, %(status)s, %(starts_at)s,
                    %(ends_at)s,
                    case when %(status)s = 'HOLD'
                         then now() + make_interval(secs => %(ttl)s) end,
                    %(total)s, %(currency)s, %(quote)s, %(created_by)s)
            returning id
            """,
            {
                "tenant_id": tenant_id,
                "location_id": location_id,
                "variant_id": variant_id,
                "status": status,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "ttl": hold_ttl_seconds,
                "total": quote.total_cents,
                "currency": quote.currency,
                "quote": Jsonb(quote.snapshot()),
                "created_by": created_by,
            },
        )
    ).fetchone()
    assert row is not None  # noqa: S101 - INSERT ... RETURNING always yields a row
    booking_id = UUID(str(row[0]))
    # The exclusion constraint decides here; 23P01 aborts the caller's savepoint.
    await conn.execute(
        """
        insert into gba.booking_allocations (tenant_id, booking_id, booking_status, resource_id,
                                             during)
        values (%s, %s, %s, %s, tstzrange(%s, %s, '[)'))
        """,
        (tenant_id, booking_id, status, resource_id, starts_at, ends_at),
    )
    return booking_id


async def lock_booking(conn: RuntimeConnection, booking_id: UUID) -> tuple[BookingStatus, bool]:
    """Row-lock a booking. Returns (status, hold_has_expired)."""
    row = await (
        await conn.execute(
            "select status, coalesce(hold_expires_at <= now(), false) "
            "from gba.bookings where id = %s for update",
            (booking_id,),
        )
    ).fetchone()
    if row is None:
        raise NotFoundError("Booking not found", booking_id=str(booking_id))
    return row[0], bool(row[1])


async def set_status(conn: RuntimeConnection, booking_id: UUID, status: BookingStatus) -> None:
    await conn.execute("update gba.bookings set status = %s where id = %s", (status, booking_id))


async def load_booking(conn: RuntimeConnection, booking_id: UUID) -> BookingResult:
    row = await (
        await conn.execute(
            """
            select b.id, b.status, a.resource_id, b.starts_at, b.ends_at, b.hold_expires_at,
                   b.total_cents, b.currency, b.quote
            from gba.bookings b
            join gba.booking_allocations a
              on a.tenant_id = b.tenant_id and a.booking_id = b.id
            where b.id = %s
            order by a.id
            limit 1
            """,
            (booking_id,),
        )
    ).fetchone()
    if row is None:
        raise NotFoundError("Booking not found", booking_id=str(booking_id))
    quote: dict[str, Any] = row[8]
    return BookingResult(
        booking_id=UUID(str(row[0])),
        status=row[1],
        resource_id=UUID(str(row[2])),
        starts_at=row[3].astimezone(UTC),
        ends_at=row[4].astimezone(UTC),
        hold_expires_at=row[5].astimezone(UTC) if row[5] else None,
        total_cents=row[6],
        currency=row[7],
        quote=quote,
    )


async def expire_due_holds(conn: RuntimeConnection, limit: int) -> int:
    cur = await conn.execute(
        """
        with due as (
            select id from gba.bookings
            where status = 'HOLD' and hold_expires_at <= now()
            order by hold_expires_at
            limit %s
            for update skip locked
        )
        update gba.bookings b set status = 'EXPIRED'
        from due
        where b.id = due.id
        """,
        (limit,),
    )
    return cur.rowcount
