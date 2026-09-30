"""FAKE seed data for integration tests. Nothing here is a KA Nails business fact."""

import secrets
from dataclasses import dataclass
from uuid import UUID

import psycopg

from gorgona_booking.db.provisioning import owner_tenant_transaction, provision_tenant

# A real IANA zone with DST, used only as test data (not a KA Nails location).
FAKE_TIMEZONE = "America/New_York"


@dataclass(frozen=True, slots=True)
class Salon:
    tenant_id: UUID
    slug: str
    host: str
    location_id: UUID


def seed_salon(conn: psycopg.Connection, label: str) -> Salon:
    slug = f"fake-salon-{label}-{secrets.token_hex(4)}"
    host = f"{slug}.test"
    tenant_id = provision_tenant(
        conn, slug=slug, display_name=f"FAKE salon {label.upper()}", hosts=[host]
    )
    with owner_tenant_transaction(conn, tenant_id):
        row = conn.execute(
            "insert into gba.locations (tenant_id, name, timezone) values (%s, %s, %s) "
            "returning id",
            (tenant_id, f"FAKE location {label.upper()}", FAKE_TIMEZONE),
        ).fetchone()
    assert row is not None
    return Salon(tenant_id=tenant_id, slug=slug, host=host, location_id=row[0])


@dataclass(frozen=True, slots=True)
class FakeCatalog:
    """FAKE catalog: 60-minute base, 90-minute gel, unbookable unknown-duration variant."""

    base_variant_id: UUID
    gel_variant_id: UUID
    unknown_duration_variant_id: UUID
    massage_add_on_id: UUID
    french_add_on_id: UUID


FAKE_BASE_MINUTES = 60
FAKE_GEL_MINUTES = 90
FAKE_MASSAGE_MINUTES = 15


def seed_fake_catalog(conn: psycopg.Connection, salon: Salon) -> FakeCatalog:
    with owner_tenant_transaction(conn, salon.tenant_id):
        for code in ("FAKE_PEDICURE", "FAKE_GEL_COVERAGE", "FAKE_MASSAGE", "FAKE_FRENCH_DESIGN"):
            conn.execute(
                "insert into gba.service_components (tenant_id, code, name) values (%s, %s, %s)",
                (salon.tenant_id, code, code),
            )
        service = conn.execute(
            "insert into gba.services (tenant_id, code, name) "
            "values (%s, 'FAKE_SERVICE', 'FAKE service') returning id",
            (salon.tenant_id,),
        ).fetchone()
        assert service is not None

        def variant(code: str, minutes: int | None, cents: int, provides: list[str]) -> UUID:
            row = conn.execute(
                "insert into gba.service_variants (tenant_id, service_id, code, name, status, "
                "price_cents, currency, booking_duration_minutes, is_bookable) "
                "values (%s, %s, %s, %s, 'published', %s, 'USD', %s, %s) returning id",
                (
                    salon.tenant_id,
                    service[0],
                    code,
                    f"FAKE {code}",
                    cents,
                    minutes,
                    minutes is not None,
                ),
            ).fetchone()
            assert row is not None
            for component in provides:
                conn.execute(
                    "insert into gba.variant_components (tenant_id, variant_id, component_code) "
                    "values (%s, %s, %s)",
                    (salon.tenant_id, row[0], component),
                )
            return UUID(str(row[0]))

        def add_on(code: str, minutes: int, cents: int, rules: list[tuple[str, str]]) -> UUID:
            row = conn.execute(
                "insert into gba.add_ons (tenant_id, code, name, status, price_cents, currency, "
                "duration_delta_minutes, is_bookable) "
                "values (%s, %s, %s, 'published', %s, 'USD', %s, true) returning id",
                (salon.tenant_id, code, f"FAKE {code}", cents, minutes),
            ).fetchone()
            assert row is not None
            for relation, component in rules:
                conn.execute(
                    "insert into gba.add_on_rules (tenant_id, add_on_id, relation, component_code) "
                    "values (%s, %s, %s, %s)",
                    (salon.tenant_id, row[0], relation, component),
                )
            return UUID(str(row[0]))

        return FakeCatalog(
            base_variant_id=variant("FAKE_BASE", FAKE_BASE_MINUTES, 5000, ["FAKE_PEDICURE"]),
            gel_variant_id=variant(
                "FAKE_GEL", FAKE_GEL_MINUTES, 6500, ["FAKE_PEDICURE", "FAKE_GEL_COVERAGE"]
            ),
            unknown_duration_variant_id=variant("FAKE_UNKNOWN", None, 7000, ["FAKE_PEDICURE"]),
            massage_add_on_id=add_on(
                "FAKE_MASSAGE", FAKE_MASSAGE_MINUTES, 2000, [("PROVIDES", "FAKE_MASSAGE")]
            ),
            french_add_on_id=add_on(
                "FAKE_FRENCH",
                10,
                1500,
                [("PROVIDES", "FAKE_FRENCH_DESIGN"), ("REQUIRES", "FAKE_GEL_COVERAGE")],
            ),
        )


def seed_resource(conn: psycopg.Connection, salon: Salon, name: str = "FAKE artist") -> UUID:
    with owner_tenant_transaction(conn, salon.tenant_id):
        row = conn.execute(
            "insert into gba.resources (tenant_id, location_id, kind, display_name) "
            "values (%s, %s, 'artist', %s) returning id",
            (salon.tenant_id, salon.location_id, name),
        ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


def force_hold_expired(conn: psycopg.Connection, salon: Salon, booking_id: UUID) -> None:
    """Simulate the passage of time: move a hold's expiry into the past."""
    with owner_tenant_transaction(conn, salon.tenant_id):
        conn.execute(
            "update gba.bookings set hold_expires_at = now() - interval '1 minute' where id = %s",
            (booking_id,),
        )
