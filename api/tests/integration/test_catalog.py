"""Bookability invariants in the database, and catalog reads under tenant isolation."""

import psycopg
import pytest
from psycopg import errors, sql

from gorgona_booking.catalog.quote import ServiceNotBookableError, build_quote
from gorgona_booking.catalog.repository import load_add_ons, load_variant
from gorgona_booking.db.pool import RuntimePool, tenant_transaction
from gorgona_booking.errors import NotFoundError
from tests.integration.seed import (
    FAKE_GEL_MINUTES,
    FAKE_MASSAGE_MINUTES,
    FakeCatalog,
    Salon,
    seed_fake_catalog,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def catalogs(
    owner_conn: psycopg.Connection, salons: tuple[Salon, Salon]
) -> tuple[FakeCatalog, FakeCatalog]:
    a, b = salons
    return seed_fake_catalog(owner_conn, a), seed_fake_catalog(owner_conn, b)


async def _service_id(pool: RuntimePool, salon: Salon) -> object:
    async with tenant_transaction(pool, salon.tenant_id) as conn:
        row = await (await conn.execute("select id from gba.services limit 1")).fetchone()
    assert row is not None
    return row[0]


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        (
            {"status": "published", "booking_duration_minutes": None, "is_bookable": True},
            "service_variants_bookable_requires_duration",
        ),
        (
            {"status": "draft", "booking_duration_minutes": 60, "is_bookable": True},
            "service_variants_bookable_requires_published",
        ),
        ({"booking_duration_minutes": 0}, "service_variants_booking_duration_range"),
        (
            {"display_duration_min_minutes": 145, "display_duration_max_minutes": 120},
            "service_variants_display_range",
        ),
        ({"price_cents": -1}, "service_variants_price_nonnegative"),
        ({"currency": "usd"}, "service_variants_currency_iso"),
    ],
)
async def test_variant_invariants_are_enforced_by_the_database(
    app_pool: RuntimePool,
    salons: tuple[Salon, Salon],
    catalogs: tuple[FakeCatalog, FakeCatalog],
    overrides: dict[str, object],
    constraint: str,
) -> None:
    a, _ = salons
    values: dict[str, object] = {
        "tenant_id": a.tenant_id,
        "service_id": await _service_id(app_pool, a),
        "code": "FAKE_BAD",
        "name": "FAKE bad",
        "price_cents": 100,
        "currency": "USD",
    } | overrides
    statement = sql.SQL("insert into gba.service_variants ({}) values ({})").format(
        sql.SQL(", ").join(map(sql.Identifier, values)),
        sql.SQL(", ").join(sql.Placeholder() * len(values)),
    )
    with pytest.raises(errors.CheckViolation) as info:
        async with tenant_transaction(app_pool, a.tenant_id) as conn:
            await conn.execute(statement, list(values.values()))
    assert info.value.diag.constraint_name == constraint


async def test_making_an_unknown_duration_variant_bookable_is_rejected(
    app_pool: RuntimePool, salons: tuple[Salon, Salon], catalogs: tuple[FakeCatalog, FakeCatalog]
) -> None:
    a, _ = salons
    with pytest.raises(errors.CheckViolation) as info:
        async with tenant_transaction(app_pool, a.tenant_id) as conn:
            await conn.execute(
                "update gba.service_variants set is_bookable = true where id = %s",
                (catalogs[0].unknown_duration_variant_id,),
            )
    assert info.value.diag.constraint_name == "service_variants_bookable_requires_duration"


async def test_add_on_bookable_requires_duration(
    app_pool: RuntimePool, salons: tuple[Salon, Salon], catalogs: tuple[FakeCatalog, FakeCatalog]
) -> None:
    a, _ = salons
    with pytest.raises(errors.CheckViolation) as info:
        async with tenant_transaction(app_pool, a.tenant_id) as conn:
            await conn.execute(
                "update gba.add_ons set duration_delta_minutes = null where id = %s",
                (catalogs[0].massage_add_on_id,),
            )
    assert info.value.diag.constraint_name == "add_ons_bookable_requires_duration"


async def test_revision_increments_on_change(
    app_pool: RuntimePool, salons: tuple[Salon, Salon], catalogs: tuple[FakeCatalog, FakeCatalog]
) -> None:
    a, _ = salons
    async with tenant_transaction(app_pool, a.tenant_id) as conn:
        before = (await load_variant(conn, catalogs[0].base_variant_id)).revision
        await conn.execute(
            "update gba.service_variants set price_cents = price_cents + 100 where id = %s",
            (catalogs[0].base_variant_id,),
        )
        after = (await load_variant(conn, catalogs[0].base_variant_id)).revision
    assert after == before + 1


async def test_repository_round_trip_feeds_the_quote_engine(
    app_pool: RuntimePool, salons: tuple[Salon, Salon], catalogs: tuple[FakeCatalog, FakeCatalog]
) -> None:
    a, _ = salons
    fake = catalogs[0]
    async with tenant_transaction(app_pool, a.tenant_id) as conn:
        gel = await load_variant(conn, fake.gel_variant_id)
        add_ons = await load_add_ons(conn, [fake.massage_add_on_id, fake.french_add_on_id])
        unknown = await load_variant(conn, fake.unknown_duration_variant_id)
    quote = build_quote(gel, add_ons)
    assert quote.booking_duration_minutes == FAKE_GEL_MINUTES + FAKE_MASSAGE_MINUTES + 10
    assert quote.total_cents == 6500 + 2000 + 1500
    assert add_ons[1].requires == frozenset({"FAKE_GEL_COVERAGE"})
    with pytest.raises(ServiceNotBookableError):
        build_quote(unknown)


async def test_catalog_rows_of_another_salon_are_invisible(
    app_pool: RuntimePool, salons: tuple[Salon, Salon], catalogs: tuple[FakeCatalog, FakeCatalog]
) -> None:
    a, _ = salons
    other = catalogs[1]
    async with tenant_transaction(app_pool, a.tenant_id) as conn:
        with pytest.raises(NotFoundError):
            await load_variant(conn, other.base_variant_id)
        with pytest.raises(NotFoundError):
            await load_add_ons(conn, [other.massage_add_on_id])


async def test_cannot_attach_components_to_another_salons_variant(
    app_pool: RuntimePool, salons: tuple[Salon, Salon], catalogs: tuple[FakeCatalog, FakeCatalog]
) -> None:
    a, _ = salons
    with pytest.raises(errors.ForeignKeyViolation):
        async with tenant_transaction(app_pool, a.tenant_id) as conn:
            await conn.execute(
                "insert into gba.variant_components (tenant_id, variant_id, component_code) "
                "values (%s, %s, 'FAKE_MASSAGE')",
                (a.tenant_id, catalogs[1].base_variant_id),
            )
