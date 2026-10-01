"""FAKE salon for staging acceptance (operator path, owner credential).

Staging databases are private, so acceptance data is created by a migration-style job
running this seed rather than from a laptop. Everything written here is FAKE: the slug
must start with `fake-`, every name says FAKE, the owner is a placeholder identity no
real IdP issues, and the seed refuses to run when GBA_ENV is production. Re-running it
changes nothing (stable ids, `on conflict do nothing`).
"""

from dataclasses import dataclass
from uuid import UUID

import psycopg

from gorgona_booking.db.provisioning import (
    add_membership,
    owner_tenant_transaction,
    provision_user,
)
from gorgona_booking.onboarding.readiness import readiness_for_owner
from gorgona_booking.onboarding.service import apply_onboarding, go_live_owner, stable_id
from gorgona_booking.onboarding.spec import OnboardingSpec

FAKE_ISSUER = "https://fake-staging-owner.invalid"  # reserved TLD: no IdP issues it
FAKE_ARTISTS = ("FAKE artist one", "FAKE artist two")
FAKE_SOURCE = "FAKE staging acceptance seed"


class FakeSeedRefusedError(Exception):
    """The seed was asked to write somewhere it must never write."""


@dataclass(frozen=True, slots=True)
class FakeSeedResult:
    tenant_id: UUID
    live: bool


def fake_staging_spec(slug: str, host: str) -> OnboardingSpec:
    def fact(value: object) -> dict[str, object]:
        return {"value": value, "status": "confirmed", "source": FAKE_SOURCE}

    return OnboardingSpec.model_validate(
        {
            "spec_version": 1,
            "slug": slug,
            "display_name": f"FAKE {slug}",
            "location_name": "FAKE main location",
            "timezone": fact("America/Chicago"),
            "domains": fact([host]),
            "business_hours": fact(
                [{"weekday": d, "opens": "09:00", "closes": "19:00"} for d in range(1, 8)]
            ),
            "staff": fact([{"display_name": name} for name in FAKE_ARTISTS]),
            "catalog": fact(
                [
                    {
                        "service_code": "FAKE_MANICURE",
                        "service_name": "FAKE manicure",
                        "code": "FAKE_MANICURE_STD",
                        "name": "FAKE manicure (standard)",
                        "price_cents": 3000,
                        "currency": "USD",
                        "booking_duration_minutes": 60,
                    },
                    {
                        "service_code": "FAKE_PEDICURE",
                        "service_name": "FAKE pedicure",
                        "code": "FAKE_PEDICURE_STD",
                        "name": "FAKE pedicure (standard)",
                        "price_cents": 4500,
                        "currency": "USD",
                        "booking_duration_minutes": 90,
                    },
                ]
            ),
            "service_durations": {"status": "confirmed", "source": FAKE_SOURCE},
            "cancellation_policy": fact(
                {"version": 1, "summary": "FAKE cancellation terms for staging only"}
            ),
            "deposit_policy": fact({"version": 1, "required": False}),
            "booking_rules": fact(
                {
                    "version": 1,
                    "slot_interval_minutes": 30,
                    "advance_notice_minutes": 60,
                    "max_days_ahead": 60,
                }
            ),
        }
    )


def seed_fake_salon(
    conn: psycopg.Connection, *, slug: str, host: str, environment: str, actor: str
) -> FakeSeedResult:
    if environment == "production":
        raise FakeSeedRefusedError("the FAKE seed never runs in production")
    if not slug.startswith("fake-"):
        raise FakeSeedRefusedError("FAKE salons must have a slug starting with 'fake-'")
    spec = fake_staging_spec(slug, host)
    tenant_id = apply_onboarding(conn, spec, actor=actor).tenant_id
    _ensure_owner(conn, tenant_id, slug, actor)
    _ensure_schedules(conn, tenant_id)
    if readiness_for_owner(conn, tenant_id).ready:
        go_live_owner(conn, tenant_id, actor=actor)
    state = _booking_state(conn, tenant_id)
    return FakeSeedResult(tenant_id=tenant_id, live=state == "live")


def _ensure_owner(conn: psycopg.Connection, tenant_id: UUID, slug: str, actor: str) -> None:
    subject = f"fake-owner-{slug}"
    with conn.transaction():
        conn.execute(
            "select pg_catalog.set_config('gba.auth_issuer', %s, true), "
            "pg_catalog.set_config('gba.auth_subject', %s, true)",
            (FAKE_ISSUER, subject),
        )
        row = conn.execute(
            "select user_id from gba.user_identities where issuer = %s and subject = %s",
            (FAKE_ISSUER, subject),
        ).fetchone()
    user_id = (
        UUID(str(row[0]))
        if row is not None
        else provision_user(
            conn,
            display_name=f"FAKE owner of {slug}",
            email=None,
            issuer=FAKE_ISSUER,
            subject=subject,
            actor=actor,
        )
    )
    with owner_tenant_transaction(conn, tenant_id):
        exists = conn.execute(
            "select 1 from gba.memberships where user_id = %s and role = 'owner'", (user_id,)
        ).fetchone()
    if exists is None:
        add_membership(conn, tenant_id=tenant_id, user_id=user_id, role="owner", actor=actor)


def _ensure_schedules(conn: psycopg.Connection, tenant_id: UUID) -> None:
    """Every FAKE artist performs every service, 10:00-18:00 every day."""
    with owner_tenant_transaction(conn, tenant_id):
        for name in FAKE_ARTISTS:
            artist = stable_id(tenant_id, "staff", name)
            conn.execute(
                "insert into gba.resource_services (tenant_id, resource_id, service_id) "
                "select %s, %s, id from gba.services on conflict do nothing",
                (tenant_id, artist),
            )
            has_hours = conn.execute(
                "select 1 from gba.resource_hours where resource_id = %s", (artist,)
            ).fetchone()
            if has_hours is None:
                for weekday in range(1, 8):
                    conn.execute(
                        "insert into gba.resource_hours "
                        "(tenant_id, resource_id, weekday, opens_minute, closes_minute) "
                        "values (%s, %s, %s, 600, 1080)",
                        (tenant_id, artist, weekday),
                    )


def _booking_state(conn: psycopg.Connection, tenant_id: UUID) -> str:
    with owner_tenant_transaction(conn, tenant_id):
        row = conn.execute(
            "select booking_state from gba.tenants where id = %s", (tenant_id,)
        ).fetchone()
    assert row is not None  # noqa: S101 - the tenant was just onboarded
    return str(row[0])
