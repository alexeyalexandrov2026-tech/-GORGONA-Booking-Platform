"""Occupancy rules, lifecycle and idempotency against real PostgreSQL 18 (FAKE data)."""

from datetime import datetime
from uuid import UUID

import httpx
import psycopg
import pytest
from psycopg import errors

from gorgona_booking.api.app import create_app
from gorgona_booking.booking.models import (
    BookingResult,
    HoldExpiredError,
    IdempotencyKeyReusedError,
    InvalidTransitionError,
    SlotConflictError,
)
from gorgona_booking.booking.service import BookingService
from gorgona_booking.catalog.quote import ServiceNotBookableError
from gorgona_booking.config import Settings
from gorgona_booking.db.pool import RuntimePool, tenant_transaction
from gorgona_booking.errors import NotFoundError
from tests.integration.booking_support import (
    BookingWorld,
    at,
    blocking_allocations,
    booking_status,
    overlapping_blocking_pairs,
)
from tests.integration.seed import FAKE_BASE_MINUTES, force_hold_expired

pytestmark = pytest.mark.anyio
ACTOR = "test:actor"


@pytest.fixture
def service(app_pool: RuntimePool) -> BookingService:
    return BookingService(app_pool, hold_ttl_seconds=600)


async def _hold(
    service: BookingService,
    world: BookingWorld,
    start: datetime,
    *,
    tenant: UUID | None = None,
    resource: UUID | None = None,
    variant: UUID | None = None,
    add_ons: tuple[UUID, ...] = (),
    key: str | None = None,
) -> BookingResult:
    return await service.create_hold(
        tenant or world.a.tenant_id,
        world.request(start, resource=resource, variant=variant, add_ons=add_ons),
        actor=ACTOR,
        idempotency_key=key,
    )


async def test_adjacent_half_open_intervals_are_allowed(
    service: BookingService, world: BookingWorld
) -> None:
    first = await _hold(service, world, start=at(10))
    second = await _hold(service, world, start=at(11))
    assert first.ends_at == second.starts_at == at(11)
    assert first.ends_at - first.starts_at == at(10, FAKE_BASE_MINUTES) - at(10)


async def test_partial_overlap_is_rejected(service: BookingService, world: BookingWorld) -> None:
    await _hold(service, world, start=at(10))
    with pytest.raises(SlotConflictError):
        await _hold(service, world, start=at(10, 30))
    with pytest.raises(SlotConflictError):
        await _hold(service, world, start=at(9, 30))


async def test_containment_overlap_is_rejected_both_ways(
    service: BookingService, world: BookingWorld
) -> None:
    gel = world.catalog_a.gel_variant_id
    await _hold(service, world, start=at(10), variant=gel)  # FAKE 90 min: [10:00, 11:30)
    with pytest.raises(SlotConflictError):
        await _hold(service, world, start=at(10, 15))  # [10:15, 11:15) inside it
    await _hold(service, world, start=at(14, 15), resource=world.artist_a2)  # [14:15, 15:15)
    with pytest.raises(SlotConflictError):
        await _hold(service, world, start=at(14), resource=world.artist_a2, variant=gel)


async def test_same_time_other_resource_and_other_salon_are_allowed(
    service: BookingService, world: BookingWorld
) -> None:
    await _hold(service, world, start=at(10))
    await _hold(service, world, start=at(10), resource=world.artist_a2)
    await _hold(
        service,
        world,
        start=at(10),
        tenant=world.b.tenant_id,
        resource=world.artist_b1,
        variant=world.catalog_b.base_variant_id,
    )


async def test_cancelled_booking_no_longer_blocks(
    service: BookingService, world: BookingWorld, app_pool: RuntimePool
) -> None:
    held = await _hold(service, world, start=at(10))
    cancelled = await service.cancel(world.a.tenant_id, held.booking_id, actor=ACTOR)
    assert cancelled.status == "CANCELLED"
    assert await blocking_allocations(app_pool, world.a.tenant_id, world.artist_a1) == 0
    await _hold(service, world, start=at(10))


async def test_expired_hold_no_longer_blocks_and_is_expired_transactionally(
    service: BookingService,
    world: BookingWorld,
    app_pool: RuntimePool,
    owner_conn: psycopg.Connection,
) -> None:
    stale = await _hold(service, world, start=at(10))
    force_hold_expired(owner_conn, world.a, stale.booking_id)
    # Still blocking until a transaction moves it: no now() in the constraint.
    assert await blocking_allocations(app_pool, world.a.tenant_id, world.artist_a1) == 1

    fresh = await _hold(service, world, start=at(10, 30))
    assert await booking_status(app_pool, world.a.tenant_id, stale.booking_id) == "EXPIRED"
    assert fresh.status == "HOLD"
    async with tenant_transaction(app_pool, world.a.tenant_id) as conn:
        rows = await (
            await conn.execute(
                "select from_status, to_status, actor, reason from gba.booking_events "
                "where booking_id = %s order by id",
                (stale.booking_id,),
            )
        ).fetchall()
    assert rows == [
        (None, "HOLD", ACTOR, "created_hold"),
        ("HOLD", "EXPIRED", "system:hold-expiry", "expired_on_contention"),
    ]


async def test_sweeper_expires_due_holds(
    service: BookingService,
    world: BookingWorld,
    app_pool: RuntimePool,
    owner_conn: psycopg.Connection,
) -> None:
    due = await _hold(service, world, start=at(10))
    live = await _hold(service, world, start=at(12))
    force_hold_expired(owner_conn, world.a, due.booking_id)
    assert await service.expire_due_holds(world.a.tenant_id) == 1
    assert await booking_status(app_pool, world.a.tenant_id, due.booking_id) == "EXPIRED"
    assert await booking_status(app_pool, world.a.tenant_id, live.booking_id) == "HOLD"


async def test_confirm_checks_expiry_under_lock(
    service: BookingService,
    world: BookingWorld,
    app_pool: RuntimePool,
    owner_conn: psycopg.Connection,
) -> None:
    ok = await _hold(service, world, start=at(10))
    confirmed = await service.confirm(world.a.tenant_id, ok.booking_id, actor=ACTOR)
    assert confirmed.status == "CONFIRMED"
    again = await service.confirm(world.a.tenant_id, ok.booking_id, actor=ACTOR)
    assert again.status == "CONFIRMED"
    with pytest.raises(SlotConflictError):
        await _hold(service, world, start=at(10, 30))

    late = await _hold(service, world, start=at(13))
    force_hold_expired(owner_conn, world.a, late.booking_id)
    with pytest.raises(HoldExpiredError):
        await service.confirm(world.a.tenant_id, late.booking_id, actor=ACTOR)
    assert await booking_status(app_pool, world.a.tenant_id, late.booking_id) == "EXPIRED"
    with pytest.raises(InvalidTransitionError):
        await service.cancel(world.a.tenant_id, late.booking_id, actor=ACTOR)


async def test_hold_and_confirmed_follow_the_same_database_rule(
    service: BookingService, world: BookingWorld
) -> None:
    await service.create_confirmed_booking(
        world.a.tenant_id, world.request(at(10)), actor=ACTOR, idempotency_key=None
    )
    with pytest.raises(SlotConflictError):
        await _hold(service, world, start=at(10, 30))
    await _hold(service, world, start=at(12))
    with pytest.raises(SlotConflictError):
        await service.create_confirmed_booking(
            world.a.tenant_id, world.request(at(12, 45)), actor=ACTOR, idempotency_key=None
        )


async def test_database_enforces_lifecycle_even_for_direct_sql(
    service: BookingService, world: BookingWorld, app_pool: RuntimePool
) -> None:
    held = await _hold(service, world, start=at(10))
    await service.cancel(world.a.tenant_id, held.booking_id, actor=ACTOR)
    with pytest.raises(errors.CheckViolation) as info:
        async with tenant_transaction(app_pool, world.a.tenant_id) as conn:
            await conn.execute(
                "update gba.bookings set status = 'CONFIRMED' where id = %s", (held.booking_id,)
            )
    assert info.value.diag.constraint_name == "bookings_terminal_immutable"

    live = await _hold(service, world, start=at(12))
    with pytest.raises(errors.CheckViolation) as info:
        async with tenant_transaction(app_pool, world.a.tenant_id) as conn:
            await conn.execute(
                "update gba.bookings set starts_at = starts_at + interval '1 hour' where id = %s",
                (live.booking_id,),
            )
    assert info.value.diag.constraint_name == "bookings_identity_immutable"

    for statement in (
        "update gba.booking_allocations set booking_status = 'CANCELLED'",
        "delete from gba.booking_allocations",
        "delete from gba.bookings",
    ):
        with pytest.raises(errors.InsufficientPrivilege):
            async with tenant_transaction(app_pool, world.a.tenant_id) as conn:
                await conn.execute(statement.encode())


async def test_unknown_duration_service_cannot_be_held(
    service: BookingService, world: BookingWorld, app_pool: RuntimePool
) -> None:
    with pytest.raises(ServiceNotBookableError):
        await _hold(
            service, world, start=at(10), variant=world.catalog_a.unknown_duration_variant_id
        )
    assert await blocking_allocations(app_pool, world.a.tenant_id, world.artist_a1) == 0


async def test_add_on_extends_the_reserved_interval(
    service: BookingService, world: BookingWorld
) -> None:
    held = await _hold(service, world, start=at(10), add_ons=(world.catalog_a.massage_add_on_id,))
    assert held.ends_at == at(11, 15)
    with pytest.raises(SlotConflictError):
        await _hold(service, world, start=at(11), resource=world.artist_a1)


async def test_salons_cannot_touch_each_others_bookings(
    service: BookingService, world: BookingWorld
) -> None:
    held = await _hold(service, world, start=at(10))
    with pytest.raises(NotFoundError):
        await service.cancel(world.b.tenant_id, held.booking_id, actor=ACTOR)
    with pytest.raises(NotFoundError):
        await service.confirm(world.b.tenant_id, held.booking_id, actor=ACTOR)
    with pytest.raises(NotFoundError):  # salon B's artist is invisible to salon A
        await _hold(service, world, start=at(10), resource=world.artist_b1)


async def test_idempotent_retry_recovers_the_same_result(
    service: BookingService, world: BookingWorld, app_pool: RuntimePool
) -> None:
    first = await _hold(service, world, start=at(10), key="retry-key-0001")
    retry = await _hold(service, world, start=at(10), key="retry-key-0001")
    assert retry.booking_id == first.booking_id
    assert (first.replayed, retry.replayed) == (False, True)
    assert await blocking_allocations(app_pool, world.a.tenant_id, world.artist_a1) == 1

    with pytest.raises(IdempotencyKeyReusedError):
        await _hold(service, world, start=at(12), key="retry-key-0001")


async def test_idempotent_conflict_replays_as_conflict(
    service: BookingService, world: BookingWorld
) -> None:
    holder = await _hold(service, world, start=at(10), key="holder-key-001")
    with pytest.raises(SlotConflictError):
        await _hold(service, world, start=at(10), key="loser-key-0001")
    await service.cancel(world.a.tenant_id, holder.booking_id, actor=ACTOR)
    # Same key, same request: the recorded outcome, not a fresh attempt.
    with pytest.raises(SlotConflictError):
        await _hold(service, world, start=at(10), key="loser-key-0001")


async def test_http_hold_flow_against_the_database(
    app_pool: RuntimePool, world: BookingWorld
) -> None:
    app = create_app(Settings(environment="test"), pool=app_pool)
    body = {
        "resource_id": str(world.artist_a1),
        "variant_id": str(world.catalog_a.base_variant_id),
        "start_at": at(10).isoformat(),
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=f"http://{world.a.host}") as client:
        created = await client.post(
            "/v1/holds", json=body, headers={"Idempotency-Key": "http-0001"}
        )
        replay = await client.post("/v1/holds", json=body, headers={"Idempotency-Key": "http-0001"})
        clash = await client.post("/v1/holds", json=body, headers={"Idempotency-Key": "http-0002"})
    async with httpx.AsyncClient(transport=transport, base_url="http://unknown.test") as client:
        unknown = await client.post(
            "/v1/holds", json=body, headers={"Idempotency-Key": "http-0003"}
        )

    assert created.status_code == 201
    assert replay.status_code == 201
    assert replay.headers["idempotent-replayed"] == "true"
    assert replay.json()["booking_id"] == created.json()["booking_id"]
    assert clash.status_code == 409
    assert clash.json()["error"]["code"] == "SLOT_CONFLICT"
    assert "booking_allocations" not in clash.text  # no raw database detail
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "TENANT_NOT_FOUND"
    assert await overlapping_blocking_pairs(app_pool, world.a.tenant_id) == 0
