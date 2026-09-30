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
