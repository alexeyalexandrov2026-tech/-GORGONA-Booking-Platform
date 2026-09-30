"""The 100-way race, on real PostgreSQL 18 with independent connections.

Every attempt targets the exact same salon, resource and time range, and all
attempts are released together through a barrier so they genuinely race.
"""

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import timedelta
from functools import partial

import psycopg
import pytest
from psycopg import errors

from gorgona_booking.booking.models import BookingResult, SlotConflictError
from gorgona_booking.booking.repository import is_slot_conflict
from gorgona_booking.booking.service import BookingService
from gorgona_booking.db.pool import (
    RuntimeConnection,
    RuntimePool,
    create_runtime_pool,
    set_tenant_context,
    tenant_transaction,
)
from tests.integration.booking_support import (
    BookingWorld,
    at,
    blocking_allocations,
    booking_status,
    overlapping_blocking_pairs,
)
from tests.integration.conftest import ProvisionedDatabase
from tests.integration.seed import force_hold_expired

pytestmark = pytest.mark.anyio

ATTEMPTS = 100
ACTOR = "test:race"


async def _open_pool(database: ProvisionedDatabase, size: int) -> RuntimePool:
    pool = create_runtime_pool(database.app_dsn, min_size=size, max_size=size)
    await pool.open(wait=True, timeout=30.0)
    return pool


async def _race(
    attempts: list[Callable[[], Awaitable[BookingResult]]],
) -> tuple[list[BookingResult], list[SlotConflictError], list[BaseException]]:
    """Release every attempt at once; classify outcomes without swallowing anything."""
    barrier = asyncio.Barrier(len(attempts))

    async def run(attempt: Callable[[], Awaitable[BookingResult]]) -> BookingResult:
        await barrier.wait()
        return await attempt()

    outcomes = await asyncio.gather(*(run(a) for a in attempts), return_exceptions=True)
    wins = [o for o in outcomes if isinstance(o, BookingResult)]
    conflicts = [o for o in outcomes if isinstance(o, SlotConflictError)]
    other = [
        o for o in outcomes if isinstance(o, BaseException) and not isinstance(o, SlotConflictError)
    ]
    return wins, conflicts, other


async def test_raw_sql_race_the_constraint_alone_admits_exactly_one(
    test_database: ProvisionedDatabase, world: BookingWorld, app_pool: RuntimePool
) -> None:
    """No application locking: 100 connections insert the same range at once."""
    tenant, resource = world.a.tenant_id, world.artist_a1
    start, end = at(10), at(11)
    connections = [
        await psycopg.AsyncConnection.connect(test_database.app_dsn, autocommit=True)
        for _ in range(ATTEMPTS)
    ]
    barrier = asyncio.Barrier(ATTEMPTS)

    async def attempt(conn: RuntimeConnection) -> str:
        try:
            async with conn.transaction():
                await set_tenant_context(conn, tenant)
                row = await (
                    await conn.execute(
                        "insert into gba.bookings (tenant_id, location_id, variant_id, status, "
                        "starts_at, ends_at, hold_expires_at, total_cents, currency, quote, "
                        "created_by) values (%s, %s, %s, 'HOLD', %s, %s, "
                        "now() + interval '10 minutes', 5000, 'USD', '{}'::jsonb, %s) "
                        "returning id",
                        (
                            tenant,
                            world.a.location_id,
                            world.catalog_a.base_variant_id,
                            start,
                            end,
                            ACTOR,
                        ),
                    )
                ).fetchone()
                assert row is not None
                await barrier.wait()  # every connection now inserts the same range
                await conn.execute(
                    "insert into gba.booking_allocations (tenant_id, booking_id, booking_status, "
                    "resource_id, during) values (%s, %s, 'HOLD', %s, tstzrange(%s, %s, '[)'))",
                    (tenant, row[0], resource, start, end),
                )
        except errors.ExclusionViolation as exc:
            return "conflict_23P01" if is_slot_conflict(exc) else "other_23P01"
        except errors.DeadlockDetected:
            # Possible without application-level ordering (see ADR-0003); the
            # transaction is aborted and reserves nothing.
            return "deadlock_40P01"
        return "committed"

    try:
        outcomes = Counter(await asyncio.gather(*(attempt(c) for c in connections)))
    finally:
        for conn in connections:
            await conn.close()

    assert outcomes["committed"] == 1, outcomes
    assert outcomes["conflict_23P01"] + outcomes["deadlock_40P01"] == ATTEMPTS - 1, outcomes
    assert set(outcomes) <= {"committed", "conflict_23P01", "deadlock_40P01"}, outcomes
    assert await blocking_allocations(app_pool, tenant, resource) == 1
    assert await overlapping_blocking_pairs(app_pool, tenant) == 0
    async with tenant_transaction(app_pool, tenant) as conn:
        row = await (
            await conn.execute("select count(*) from gba.bookings where starts_at = %s", (start,))
        ).fetchone()
    assert row == (1,)  # losing transactions left no booking rows behind


async def test_service_race_100_attempts_one_success_99_clean_conflicts(
    test_database: ProvisionedDatabase, world: BookingWorld, app_pool: RuntimePool
) -> None:
    pool = await _open_pool(test_database, ATTEMPTS)
    try:
        service = BookingService(pool)
        wins, conflicts, other = await _race(
            [
                partial(
                    service.create_hold,
                    world.a.tenant_id,
                    world.request(at(10)),
                    actor=ACTOR,
                    idempotency_key=f"race-key-{i:04d}",
                )
                for i in range(ATTEMPTS)
            ]
        )
    finally:
        await pool.close()

    assert other == [], other
    assert (len(wins), len(conflicts)) == (1, ATTEMPTS - 1)
    assert await blocking_allocations(app_pool, world.a.tenant_id, world.artist_a1) == 1
    assert await overlapping_blocking_pairs(app_pool, world.a.tenant_id) == 0
    async with tenant_transaction(app_pool, world.a.tenant_id) as conn:
        bookings = await (await conn.execute("select count(*) from gba.bookings")).fetchone()
        keys = await (
            await conn.execute(
                "select response_status, count(*) from gba.idempotency_keys "
                "where idempotency_key like 'race-key-%%' group by 1 order by 1"
            )
        ).fetchall()
    assert bookings == (1,)
    assert keys == [(201, 1), (409, ATTEMPTS - 1)]


async def test_two_service_instances_race_like_two_api_processes(
    test_database: ProvisionedDatabase, world: BookingWorld, app_pool: RuntimePool
) -> None:
    half = ATTEMPTS // 2
    pools = [await _open_pool(test_database, half) for _ in range(2)]
    try:
        instances = [BookingService(p) for p in pools]
        wins, conflicts, other = await _race(
            [
                partial(
                    instances[i % 2].create_hold,
                    world.a.tenant_id,
                    world.request(at(10)),
                    actor=ACTOR,
                    idempotency_key=None,
                )
                for i in range(ATTEMPTS)
            ]
        )
    finally:
        for pool in pools:
            await pool.close()
    assert other == [], other
    assert (len(wins), len(conflicts)) == (1, ATTEMPTS - 1)
    assert await blocking_allocations(app_pool, world.a.tenant_id, world.artist_a1) == 1


async def test_concurrent_hold_and_confirmed_follow_the_same_rule(
    test_database: ProvisionedDatabase, world: BookingWorld, app_pool: RuntimePool
) -> None:
    pool = await _open_pool(test_database, ATTEMPTS)
    try:
        service = BookingService(pool)

        def attempt(i: int) -> Callable[[], Awaitable[BookingResult]]:
            create = service.create_hold if i % 2 else service.create_confirmed_booking
            return lambda: create(
                world.a.tenant_id, world.request(at(10)), actor=ACTOR, idempotency_key=None
            )

        wins, conflicts, other = await _race([attempt(i) for i in range(ATTEMPTS)])
    finally:
        await pool.close()
    assert other == [], other
    assert (len(wins), len(conflicts)) == (1, ATTEMPTS - 1)
    assert wins[0].status in ("HOLD", "CONFIRMED")
    assert await overlapping_blocking_pairs(app_pool, world.a.tenant_id) == 0


async def test_race_over_an_expired_hold_expires_it_once_and_admits_one(
    test_database: ProvisionedDatabase,
    world: BookingWorld,
    app_pool: RuntimePool,
    owner_conn: psycopg.Connection,
) -> None:
    seed_service = BookingService(app_pool)
    stale = await seed_service.create_hold(
        world.a.tenant_id, world.request(at(10)), actor=ACTOR, idempotency_key=None
    )
    force_hold_expired(owner_conn, world.a, stale.booking_id)

    pool = await _open_pool(test_database, ATTEMPTS)
    try:
        service = BookingService(pool)
        wins, conflicts, other = await _race(
            [
                lambda: service.create_hold(
                    world.a.tenant_id, world.request(at(10, 30)), actor=ACTOR, idempotency_key=None
                )
                for _ in range(ATTEMPTS)
            ]
        )
    finally:
        await pool.close()

    assert other == [], other
    assert (len(wins), len(conflicts)) == (1, ATTEMPTS - 1)
    assert await booking_status(app_pool, world.a.tenant_id, stale.booking_id) == "EXPIRED"
    async with tenant_transaction(app_pool, world.a.tenant_id) as conn:
        row = await (
            await conn.execute(
                "select count(*) from gba.booking_events "
                "where booking_id = %s and to_status = 'EXPIRED'",
                (stale.booking_id,),
            )
        ).fetchone()
    assert row == (1,)
    assert await overlapping_blocking_pairs(app_pool, world.a.tenant_id) == 0


async def test_confirm_racing_new_holds_keeps_the_confirmed_booking(
    test_database: ProvisionedDatabase, world: BookingWorld, app_pool: RuntimePool
) -> None:
    held = await BookingService(app_pool).create_hold(
        world.a.tenant_id, world.request(at(10)), actor=ACTOR, idempotency_key=None
    )
    contenders = 20
    pool = await _open_pool(test_database, contenders + 1)
    try:
        service = BookingService(pool)
        confirm_first: list[Callable[[], Awaitable[BookingResult]]] = [
            lambda: service.confirm(world.a.tenant_id, held.booking_id, actor=ACTOR)
        ]
        wins, conflicts, other = await _race(
            confirm_first
            + [
                partial(
                    service.create_hold,
                    world.a.tenant_id,
                    world.request(at(10) + timedelta(minutes=5 * (i % 6))),
                    actor=ACTOR,
                    idempotency_key=None,
                )
                for i in range(contenders)
            ]
        )
    finally:
        await pool.close()
    assert other == [], other
    assert [w.booking_id for w in wins] == [held.booking_id]
    assert len(conflicts) == contenders
    assert await booking_status(app_pool, world.a.tenant_id, held.booking_id) == "CONFIRMED"


async def test_same_idempotency_key_racing_creates_one_booking(
    test_database: ProvisionedDatabase, world: BookingWorld, app_pool: RuntimePool
) -> None:
    racers = 20
    pool = await _open_pool(test_database, racers)
    try:
        service = BookingService(pool)
        wins, conflicts, other = await _race(
            [
                lambda: service.create_hold(
                    world.a.tenant_id,
                    world.request(at(10)),
                    actor=ACTOR,
                    idempotency_key="same-key-race-01",
                )
                for _ in range(racers)
            ]
        )
    finally:
        await pool.close()
    assert other == [], other
    assert conflicts == [], conflicts
    assert len({w.booking_id for w in wins}) == 1
    assert sum(not w.replayed for w in wins) == 1
    async with tenant_transaction(app_pool, world.a.tenant_id) as conn:
        row = await (await conn.execute("select count(*) from gba.bookings")).fetchone()
    assert row == (1,)


def test_race_helpers_do_not_hide_unexpected_errors() -> None:
    async def boom() -> BookingResult:
        raise RuntimeError("unexpected")

    async def conflict() -> BookingResult:
        raise SlotConflictError("taken")

    wins, conflicts, other = asyncio.run(_race([boom, conflict]))
    assert (wins, len(conflicts), [type(o) for o in other]) == ([], 1, [RuntimeError])
