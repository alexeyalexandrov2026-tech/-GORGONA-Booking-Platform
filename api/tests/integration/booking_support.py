"""Shared fixtures and helpers for booking integration tests (FAKE data only)."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from gorgona_booking.booking.models import ReservationRequest
from gorgona_booking.db.pool import RuntimePool, tenant_transaction
from tests.integration.seed import FakeCatalog, Salon

# Far enough in the future that holds are always valid.
DAY = datetime(2031, 6, 2, tzinfo=UTC)


def at(hour: int, minute: int = 0) -> datetime:
    return DAY + timedelta(hours=hour, minutes=minute)


@dataclass(frozen=True, slots=True)
class BookingWorld:
    a: Salon
    b: Salon
    catalog_a: FakeCatalog
    catalog_b: FakeCatalog
    artist_a1: UUID
    artist_a2: UUID
    artist_b1: UUID

    def request(
        self,
        start: datetime,
        *,
        resource: UUID | None = None,
        variant: UUID | None = None,
        add_ons: tuple[UUID, ...] = (),
    ) -> ReservationRequest:
        return ReservationRequest(
            resource_id=resource or self.artist_a1,
            variant_id=variant or self.catalog_a.base_variant_id,
            starts_at=start,
            add_on_ids=add_ons,
        )


async def blocking_allocations(pool: RuntimePool, tenant_id: UUID, resource_id: UUID) -> int:
    async with tenant_transaction(pool, tenant_id) as conn:
        row = await (
            await conn.execute(
                "select count(*) from gba.booking_allocations "
                "where resource_id = %s and booking_status in ('HOLD', 'CONFIRMED')",
                (resource_id,),
            )
        ).fetchone()
    assert row is not None
    return int(row[0])


async def overlapping_blocking_pairs(pool: RuntimePool, tenant_id: UUID) -> int:
    """Independent consistency check: must always be zero."""
    async with tenant_transaction(pool, tenant_id) as conn:
        row = await (
            await conn.execute(
                """
                select count(*)
                from gba.booking_allocations x
                join gba.booking_allocations y
                  on x.tenant_id = y.tenant_id
                 and x.resource_id = y.resource_id
                 and x.id < y.id
                 and x.during && y.during
                where x.booking_status in ('HOLD', 'CONFIRMED')
                  and y.booking_status in ('HOLD', 'CONFIRMED')
                """
            )
        ).fetchone()
    assert row is not None
    return int(row[0])


async def booking_status(pool: RuntimePool, tenant_id: UUID, booking_id: UUID) -> str:
    async with tenant_transaction(pool, tenant_id) as conn:
        row = await (
            await conn.execute("select status from gba.bookings where id = %s", (booking_id,))
        ).fetchone()
    assert row is not None
    return str(row[0])
