"""Onboarding, readiness and go-live against real PostgreSQL 18 (ADR-0010).

FAKE salons only, except the KA Nails candidate spec, which contains only facts
already in the owner brief and leaves every unknown fact missing.
"""

import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest

from gorgona_booking.api.app import create_app
from gorgona_booking.config import Settings
from gorgona_booking.db.pool import RuntimePool, tenant_transaction
from gorgona_booking.db.provisioning import add_membership, grant_platform_admin
from gorgona_booking.onboarding.readiness import readiness_for_owner
from gorgona_booking.onboarding.service import (
    OnboardingConflictError,
    apply_onboarding,
    stable_id,
)
from gorgona_booking.onboarding.spec import OnboardingSpec, load_spec
from tests.integration.booking_support import at
from tests.integration.seed import seed_user
from tests.support.fake_idp import FakeIdp

pytestmark = pytest.mark.anyio

KA_NAILS = Path(__file__).resolve().parents[2] / "fixtures" / "ka_nails_onboarding.candidate.json"


def fake_spec(
    slug: str, *, status: str, owner_email: str | None = None, domains: list[str] | None = None
) -> OnboardingSpec:
    """A complete FAKE salon. Nothing here is a KA Nails fact."""

    def fact(value: object) -> dict[str, object]:
        return {"value": value, "status": status}

    spec: dict[str, Any] = {
        "spec_version": 1,
        "slug": slug,
        "display_name": f"FAKE {slug}",
        "location_name": "FAKE main location",
        "timezone": fact("America/New_York"),
        "domains": fact(domains or [f"{slug}.test"]),
        "business_hours": fact(
            [{"weekday": d, "opens": "09:00", "closes": "17:00"} for d in range(1, 6)]
        ),
        "staff": fact([{"display_name": "FAKE artist"}]),
        "catalog": fact(
            [
                {
                    "service_code": "FAKE_SERVICE",
                    "service_name": "FAKE service",
                    "code": "FAKE_VARIANT",
                    "name": "FAKE variant",
                    "price_cents": 5000,
                    "currency": "USD",
                    "booking_duration_minutes": 60,
                }
            ]
        ),
        "service_durations": {"status": status},
        "cancellation_policy": fact({"FAKE": "cancellation terms"}),
        "deposit_policy": fact({"FAKE": "no deposit"}),
        "booking_rules": fact({"FAKE": "rules"}),
    }
    if owner_email:
        spec["owner_email"] = fact(owner_email)
    return OnboardingSpec.model_validate(spec)


def _slug() -> str:
    return f"fake-onb-{secrets.token_hex(4)}"


@pytest.fixture(scope="module")
def idp() -> FakeIdp:
    return FakeIdp()


@pytest.fixture
async def client(app_pool: RuntimePool, idp: FakeIdp) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(environment="test"), pool=app_pool, token_verifier=idp.verifier())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as http:
        yield http


def _code(response: httpx.Response) -> str:
    return str(response.json()["error"]["code"])


def test_onboarding_is_idempotent(owner_conn: psycopg.Connection) -> None:
    tokens: list[str] = []
    spec = fake_spec(_slug(), status="unconfirmed", owner_email="owner@example.test")
    first = apply_onboarding(owner_conn, spec, actor="operator:test", on_invitation=tokens.append)
    second = apply_onboarding(owner_conn, spec, actor="operator:test", on_invitation=tokens.append)
    assert first.tenant_id == second.tenant_id == stable_id(None, "tenant", spec.slug)
    assert (first.changed, second.changed) == (True, False)
    assert len(tokens) == 1  # the owner invitation is created once, its token handed over once
    changed = fake_spec(spec.slug, status="confirmed", owner_email="owner@example.test")
    assert apply_onboarding(owner_conn, changed, actor="operator:test").changed is True


def test_readiness_distinguishes_missing_unconfirmed_and_confirmed(
    owner_conn: psycopg.Connection,
) -> None:
    spec = OnboardingSpec.model_validate(
        {
            "spec_version": 1,
            "slug": _slug(),
            "display_name": "FAKE partial",
            "location_name": "FAKE location",
            "timezone": {"value": "Europe/London", "status": "confirmed"},
            "domains": {"value": ["fake-partial.test"], "status": "unconfirmed"},
        }
    )
    result = apply_onboarding(owner_conn, spec, actor="operator:test")
    statuses = {i.fact: i.status for i in readiness_for_owner(owner_conn, result.tenant_id).items}
    assert statuses["timezone"] == "confirmed"
    assert statuses["domain"] == "unconfirmed"
    for missing in ("owner", "business_hours", "staff", "catalog", "cancellation_policy"):
        assert statuses[missing] == "missing", missing


def test_a_host_owned_by_another_salon_is_refused(owner_conn: psycopg.Connection) -> None:
    first = fake_spec(_slug(), status="unconfirmed")
    apply_onboarding(owner_conn, first, actor="operator:test")
    thief = fake_spec(_slug(), status="unconfirmed", domains=[f"{first.slug}.test"])
    with pytest.raises(OnboardingConflictError):
        apply_onboarding(owner_conn, thief, actor="operator:test")


async def test_go_live_requires_readiness_and_gates_public_booking(
    client: httpx.AsyncClient, owner_conn: psycopg.Connection, idp: FakeIdp
) -> None:
    tokens: list[str] = []
    slug = _slug()
    spec = fake_spec(slug, status="confirmed", owner_email=f"owner-{slug}@example.test")
    salon = apply_onboarding(owner_conn, spec, actor="operator:test", on_invitation=tokens.append)
    operator = seed_user(owner_conn, "operator")
    grant_platform_admin(owner_conn, user_id=operator.user_id, granted_by="test")
    platform = idp.bearer(operator.subject, email=operator.email)
    salon_path = f"/v1/salons/{salon.tenant_id}"
    hold = {
        "resource_id": str(stable_id(salon.tenant_id, "staff", "FAKE artist")),
        "variant_id": str(stable_id(salon.tenant_id, "variant", "FAKE_VARIANT")),
        "start_at": at(10).isoformat(),
    }

    refused = await client.post(f"/v1/platform/salons/{salon.tenant_id}/go-live", headers=platform)
    assert (refused.status_code, _code(refused)) == (409, "NOT_READY")
    assert refused.json()["error"]["details"]["missing"] == ["owner"]
    public = await client.post(
        "/v1/holds", json=hold, headers={"Idempotency-Key": f"{slug}-1", "Host": f"{slug}.test"}
    )
    assert (public.status_code, _code(public)) == (404, "TENANT_NOT_FOUND")  # not live yet

    owner = idp.bearer(f"fake-owner-{slug}", email=f"owner-{slug}@example.test")
    accepted = await client.post(
        "/v1/invitations/accept",
        json={"salon_id": str(salon.tenant_id), "token": tokens[0]},
        headers=owner,
    )
    assert accepted.status_code == 201, accepted.text
    readiness = await client.get(f"{salon_path}/readiness", headers=owner)
    assert readiness.json()["ready"] is True

    live = await client.post(f"/v1/platform/salons/{salon.tenant_id}/go-live", headers=platform)
    assert (live.status_code, live.json()["booking_state"]) == (200, "live")
    booked = await client.post(
        "/v1/holds", json=hold, headers={"Idempotency-Key": f"{slug}-2", "Host": f"{slug}.test"}
    )
    assert booked.status_code == 201, booked.text

    downgrade = await client.put(
        f"{salon_path}/facts/timezone", json={"status": "unconfirmed"}, headers=owner
    )
    assert (downgrade.status_code, _code(downgrade)) == (409, "SALON_IS_LIVE")
    by_owner = await client.post(f"/v1/platform/salons/{salon.tenant_id}/go-live", headers=owner)
    assert (by_owner.status_code, _code(by_owner)) == (403, "PERMISSION_DENIED")


async def test_salon_admin_settings_are_validated_governed_and_audited(
    client: httpx.AsyncClient,
    owner_conn: psycopg.Connection,
    idp: FakeIdp,
    app_pool: RuntimePool,
) -> None:
    # A salon that is not live yet: admin edits need a fresh confirmation.
    spec = OnboardingSpec.model_validate(
        {
            "spec_version": 1,
            "slug": _slug(),
            "display_name": "FAKE settings",
            "location_name": "FAKE location",
            "timezone": {"value": "America/New_York", "status": "unconfirmed"},
        }
    )
    tenant_id = apply_onboarding(owner_conn, spec, actor="operator:test").tenant_id
    location_id = stable_id(tenant_id, "location", "FAKE location")
    owner, artist = seed_user(owner_conn, "set-owner"), seed_user(owner_conn, "set-artist")
    add_membership(owner_conn, tenant_id=tenant_id, user_id=owner.user_id, role="owner")
    add_membership(owner_conn, tenant_id=tenant_id, user_id=artist.user_id, role="artist")
    as_owner = idp.bearer(owner.subject, email=owner.email)
    base = f"/v1/salons/{tenant_id}"

    overlapping = await client.put(
        f"{base}/business-hours",
        json={
            "location_id": str(location_id),
            "hours": [
                {"weekday": 1, "opens": "09:00", "closes": "13:00"},
                {"weekday": 1, "opens": "12:00", "closes": "17:00"},
            ],
        },
        headers=as_owner,
    )
    assert (overlapping.status_code, _code(overlapping)) == (422, "INVALID_BUSINESS_HOURS")
    ok = await client.put(
        f"{base}/business-hours",
        json={
            "location_id": str(location_id),
            "hours": [{"weekday": 1, "opens": "09:00", "closes": "17:00"}],
        },
        headers=as_owner,
    )
    assert ok.status_code == 200, ok.text
    await client.put(
        f"{base}/policies", json={"cancellation_policy": {"FAKE": "x"}}, headers=as_owner
    )

    def status(body: dict[str, Any], fact: str) -> str:
        return next(str(i["status"]) for i in body["items"] if i["fact"] == fact)

    readiness = (await client.get(f"{base}/readiness", headers=as_owner)).json()
    assert status(readiness, "business_hours") == "unconfirmed"
    assert status(readiness, "cancellation_policy") == "unconfirmed"
    assert status(readiness, "deposit_policy") == "missing"

    by_artist = await client.put(
        f"{base}/facts/business_hours",
        json={"status": "confirmed"},
        headers=idp.bearer(artist.subject, email=artist.email),
    )
    assert (by_artist.status_code, _code(by_artist)) == (403, "PERMISSION_DENIED")
    derived = await client.put(
        f"{base}/facts/owner", json={"status": "confirmed"}, headers=as_owner
    )
    assert derived.status_code == 422
    confirmed = await client.put(
        f"{base}/facts/business_hours",
        json={"status": "confirmed", "source_note": "FAKE owner call"},
        headers=as_owner,
    )
    assert confirmed.status_code == 200
    readiness = (await client.get(f"{base}/readiness", headers=as_owner)).json()
    assert status(readiness, "business_hours") == "confirmed"

    async with tenant_transaction(app_pool, tenant_id) as conn:
        fact = await (
            await conn.execute(
                "select id from gba.salon_fact_confirmations where fact_key = 'business_hours'"
            )
        ).fetchone()
        assert fact is not None
        events = await (
            await conn.execute(
                "select action, actor from gba.audit_events where target_id = %s "
                "order by occurred_at, id",
                (str(fact[0]),),
            )
        ).fetchall()
    owner_actor = f"user:{owner.user_id}"
    # Recorded unconfirmed by the owner's edit, then confirmed by the owner.
    assert events == [("salon_fact.created", owner_actor), ("salon_fact.updated", owner_actor)]


def test_ka_nails_candidate_reports_missing_owner_facts(owner_conn: psycopg.Connection) -> None:
    spec = load_spec(KA_NAILS)
    first = apply_onboarding(owner_conn, spec, actor="operator:m2-test")
    assert apply_onboarding(owner_conn, spec, actor="operator:m2-test").changed is False
    readiness = readiness_for_owner(owner_conn, first.tenant_id)
    statuses = {i.fact: i.status for i in readiness.items}
    assert readiness.ready is False
    for fact in (
        "owner",
        "timezone",
        "business_hours",
        "staff",
        "service_durations",
        "bookable_services",
        "cancellation_policy",
        "deposit_policy",
        "booking_rules",
        "domain",
    ):
        assert statuses[fact] == "missing", fact
    assert statuses["catalog"] == "unconfirmed"
    durations = next(i for i in readiness.items if i.fact == "service_durations")
    for code in ("HAMMAM_LUXURY", "HAMMAM_LUXURY_GEL", "HAMMAM_LUXURY_GEL_FRENCH"):
        assert code in durations.detail
