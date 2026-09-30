"""Explicit FAKE schedules/policies, shared by API and real browser tests."""

from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.types.json import Jsonb

from gorgona_booking.db.provisioning import owner_tenant_transaction
from tests.integration.booking_support import BookingWorld


def customer_day() -> str:
    return (datetime.now(UTC) + timedelta(days=2)).date().isoformat()


def seed_customer_setup(conn: psycopg.Connection, world: BookingWorld) -> None:
    for salon, artists in (
        (world.a, (world.artist_a1, world.artist_a2)),
        (world.b, (world.artist_b1,)),
    ):
        with owner_tenant_transaction(conn, salon.tenant_id):
            conn.execute(
                "insert into gba.salon_policies "
                "(tenant_id, booking_rules, deposit_policy, cancellation_policy) "
                "values (%s, %s, %s, %s)",
                (
                    salon.tenant_id,
                    Jsonb(
                        {
                            "version": 1,
                            "slot_interval_minutes": 30,
                            "advance_notice_minutes": 60,
                            "max_days_ahead": 60,
                        }
                    ),
                    Jsonb({"version": 1, "required": False}),
                    Jsonb({"version": 1, "summary": "FAKE cancellation terms for tests only"}),
                ),
            )
            for weekday in range(1, 8):
                conn.execute(
                    "insert into gba.business_hours "
                    "(tenant_id, location_id, weekday, opens_minute, closes_minute) "
                    "values (%s, %s, %s, 540, 1020)",
                    (salon.tenant_id, salon.location_id, weekday),
                )
            for artist in artists:
                conn.execute(
                    "insert into gba.resource_services (tenant_id, resource_id, service_id) "
                    "select %s, %s, id from gba.services",
                    (salon.tenant_id, artist),
                )
                for weekday in range(1, 8):
                    conn.execute(
                        "insert into gba.resource_hours "
                        "(tenant_id, resource_id, weekday, opens_minute, closes_minute) "
                        "values (%s, %s, %s, 600, 960)",
                        (salon.tenant_id, artist, weekday),
                    )
