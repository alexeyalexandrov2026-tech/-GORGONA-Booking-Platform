import asyncio
import secrets
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from uuid import UUID

import httpx
import psycopg
import pytest
from psycopg.types.json import Jsonb

from gorgona_booking.api.app import create_app
from gorgona_booking.config import Settings
from gorgona_booking.db.pool import RuntimePool, tenant_transaction
from gorgona_booking.db.provisioning import owner_tenant_transaction
from tests.integration.booking_support import BookingWorld, overlapping_blocking_pairs
from tests.integration.customer_support import customer_day, seed_customer_setup
from tests.integration.seed import force_hold_expired

pytestmark = pytest.mark.anyio
DETAILS = {
    "name": "FAKE Customer",
    "email": "fake@example.test",
    "phone": "+1 555 010 1234",
    "accept_policy": True,
}


@pytest.fixture
async def customer_client(
    world: BookingWorld, owner_conn: psycopg.Connection, app_pool: RuntimePool
) -> AsyncIterator[httpx.AsyncClient]:
    seed_customer_setup(owner_conn, world)
    app = create_app(Settings(environment="test"), pool=app_pool)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=f"http://{world.a.host}"
    ) as client:
        yield client


def selection(world: BookingWorld) -> dict[str, object]:
    return {
        "location_id": str(world.a.location_id),
        "variant_id": str(world.catalog_a.base_variant_id),
        "add_on_ids": [],
        "day": customer_day(),
        "resource_id": None,
    }


async def first_slot(client: httpx.AsyncClient, world: BookingWorld) -> dict[str, str]:
    response = await client.post("/v1/customer/availability", json=selection(world))
    assert response.status_code == 200, response.text
    slot: dict[str, str] = response.json()["slots"][0]
    return slot


async def hold(
    client: httpx.AsyncClient, world: BookingWorld, slot: dict[str, str], token: str, key: str
) -> httpx.Response:
    body = selection(world)
    body.pop("day")
    body.update({"resource_id": slot["resource_id"], "start_at": slot["start_at"]})
    return await client.post(
        "/v1/customer/holds", json=body, headers={"Booking-Token": token, "Idempotency-Key": key}
    )


async def test_bootstrap_filters_and_quotes(
    customer_client: httpx.AsyncClient, world: BookingWorld
) -> None:
    response = await customer_client.get("/v1/customer/bootstrap")
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == 1
    assert len(data["variants"]) == 2  # unknown duration is never public/bookable
    assert len(data["artists"]) == 2
    query = selection(world)
    query["add_on_ids"] = [str(world.catalog_a.massage_add_on_id)]
    response = await customer_client.post("/v1/customer/availability", json=query)
    assert response.status_code == 200
    assert response.json()["quote"]["total_cents"] == 7000
    assert response.json()["quote"]["booking_duration_minutes"] == 75
    assert {s["resource_id"] for s in response.json()["slots"]} == {
        str(world.artist_a1),
        str(world.artist_a2),
    }


async def test_hold_confirm_retry_and_capability(
    customer_client: httpx.AsyncClient, world: BookingWorld, app_pool: RuntimePool
) -> None:
    slot = await first_slot(customer_client, world)
    token = secrets.token_urlsafe(32)
    response = await hold(customer_client, world, slot, token, "fake-hold-01")
    assert response.status_code == 201, response.text
    booking_id = response.json()["booking_id"]
    again = await hold(customer_client, world, slot, token, "fake-hold-01")
    assert again.json() == response.json()
    url = f"/v1/customer/bookings/{booking_id}/confirm"
    denied = await customer_client.post(
        url,
        json=DETAILS,
        headers={"Booking-Token": secrets.token_urlsafe(32), "Idempotency-Key": "fake-confirm-01"},
    )
    assert denied.status_code == 404
    headers = {"Booking-Token": token, "Idempotency-Key": "fake-confirm-01"}
    confirmed = await customer_client.post(url, json=DETAILS, headers=headers)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "CONFIRMED"
    assert "email" not in confirmed.text
    assert (
        await customer_client.post(url, json=DETAILS, headers=headers)
    ).json() == confirmed.json()
    changed = await customer_client.post(
        url, json={**DETAILS, "name": "different"}, headers=headers
    )
    assert changed.status_code == 422
    async with tenant_transaction(app_pool, world.a.tenant_id) as conn:
        row = await (
            await conn.execute(
                "select capability_hash, customer_name, email from gba.booking_customers "
                "where booking_id = %s",
                (UUID(booking_id),),
            )
        ).fetchone()
    assert row is not None
    assert row[0] != token
    assert row[1:] == (DETAILS["name"], DETAILS["email"])


async def test_slot_race_has_one_winner(
    customer_client: httpx.AsyncClient, world: BookingWorld, app_pool: RuntimePool
) -> None:
    slot = await first_slot(customer_client, world)
    results = await asyncio.gather(
        *(
            hold(customer_client, world, slot, secrets.token_urlsafe(32), f"fake-race-{i}")
            for i in range(8)
        )
    )
    assert sorted(r.status_code for r in results) == [201] + [409] * 7
    assert all(
        r.json()["error"]["code"] == "SLOT_CONFLICT" for r in results if r.status_code == 409
    )
    assert await overlapping_blocking_pairs(app_pool, world.a.tenant_id) == 0


async def test_expired_hold_releases_slot(
    customer_client: httpx.AsyncClient, world: BookingWorld, owner_conn: psycopg.Connection
) -> None:
    slot = await first_slot(customer_client, world)
    token = secrets.token_urlsafe(32)
    response = await hold(customer_client, world, slot, token, "fake-expire-01")
    assert response.status_code == 201
    booking_id = UUID(response.json()["booking_id"])
    force_hold_expired(owner_conn, world.a, booking_id)
    response = await customer_client.post(
        f"/v1/customer/bookings/{booking_id}/confirm",
        json=DETAILS,
        headers={"Booking-Token": token, "Idempotency-Key": "fake-confirm-01"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "HOLD_EXPIRED"
    assert (
        await hold(customer_client, world, slot, secrets.token_urlsafe(32), "new-hold-01")
    ).status_code == 201


async def test_tenant_injection_and_not_live(
    customer_client: httpx.AsyncClient, world: BookingWorld, owner_conn: psycopg.Connection
) -> None:
    response = await customer_client.post(
        "/v1/customer/availability",
        json={**selection(world), "resource_id": str(world.artist_b1)},
        headers={"X-Tenant-ID": str(world.b.tenant_id)},
    )
    assert response.status_code == 404
    response = await customer_client.post(
        "/v1/customer/availability", json={**selection(world), "tenant_id": str(world.b.tenant_id)}
    )
    assert response.status_code == 422
    with owner_tenant_transaction(owner_conn, world.a.tenant_id):
        owner_conn.execute(
            "update gba.tenants set booking_state = 'not_live' where id = %s", (world.a.tenant_id,)
        )
    assert (await customer_client.get("/v1/customer/bootstrap")).status_code == 404
    assert (
        await customer_client.post("/v1/customer/availability", json=selection(world))
    ).status_code == 404


async def test_missing_settings_and_required_deposit_fail_closed(
    customer_client: httpx.AsyncClient, world: BookingWorld, owner_conn: psycopg.Connection
) -> None:
    with owner_tenant_transaction(owner_conn, world.a.tenant_id):
        owner_conn.execute("update gba.salon_policies set booking_rules = null")
    assert (await customer_client.get("/v1/customer/bootstrap")).status_code == 422
    with owner_tenant_transaction(owner_conn, world.a.tenant_id):
        owner_conn.execute(
            "update gba.salon_policies set booking_rules = %s, deposit_policy = %s",
            (
                Jsonb(
                    {
                        "version": 1,
                        "slot_interval_minutes": 30,
                        "advance_notice_minutes": 60,
                        "max_days_ahead": 60,
                    }
                ),
                Jsonb({"version": 1, "required": True}),
            ),
        )
    response = await customer_client.post("/v1/customer/availability", json=selection(world))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PAYMENT_REQUIRED"


async def test_artist_schedule_blocks_and_off_grid(
    customer_client: httpx.AsyncClient, world: BookingWorld, owner_conn: psycopg.Connection
) -> None:
    slot = await first_slot(customer_client, world)
    off_grid = {
        **slot,
        "start_at": (datetime.fromisoformat(slot["start_at"]) + timedelta(minutes=1)).isoformat(),
    }
    response = await hold(
        customer_client, world, off_grid, secrets.token_urlsafe(32), "off-grid-01"
    )
    assert response.status_code == 409
    with owner_tenant_transaction(owner_conn, world.a.tenant_id):
        owner_conn.execute(
            "insert into gba.resource_blocks "
            "(tenant_id, resource_id, starts_at, ends_at) values (%s, %s, %s, %s)",
            (world.a.tenant_id, UUID(slot["resource_id"]), slot["start_at"], slot["end_at"]),
        )
    response = await hold(customer_client, world, slot, secrets.token_urlsafe(32), "blocked-01")
    assert response.status_code == 409
    with owner_tenant_transaction(owner_conn, world.a.tenant_id):
        owner_conn.execute("delete from gba.resource_hours")
    response = await customer_client.post("/v1/customer/availability", json=selection(world))
    assert response.status_code == 200
    assert response.json()["slots"] == []


async def test_expired_unconfirmed_hold_can_be_replaced(
    customer_client: httpx.AsyncClient, world: BookingWorld, owner_conn: psycopg.Connection
) -> None:
    slot = await first_slot(customer_client, world)
    old = await hold(customer_client, world, slot, secrets.token_urlsafe(32), "old-hold-01")
    force_hold_expired(owner_conn, world.a, UUID(old.json()["booking_id"]))
    new = await hold(customer_client, world, slot, secrets.token_urlsafe(32), "new-hold-02")
    assert new.status_code == 201
    assert new.json()["booking_id"] != old.json()["booking_id"]


async def test_customer_validation_and_no_contact_before_confirm(
    customer_client: httpx.AsyncClient, world: BookingWorld, app_pool: RuntimePool
) -> None:
    slot = await first_slot(customer_client, world)
    token = secrets.token_urlsafe(32)
    response = await hold(customer_client, world, slot, token, "validation-hold")
    booking_id = response.json()["booking_id"]
    for details in (
        {**DETAILS, "accept_policy": False},
        {**DETAILS, "name": " "},
        {**DETAILS, "email": "invalid"},
        {**DETAILS, "phone": "((((((("},
    ):
        invalid = await customer_client.post(
            f"/v1/customer/bookings/{booking_id}/confirm",
            json=details,
            headers={"Booking-Token": token, "Idempotency-Key": "invalid-contact"},
        )
        assert invalid.status_code == 422
    async with tenant_transaction(app_pool, world.a.tenant_id) as conn:
        row = await (
            await conn.execute(
                "select customer_name, email, phone "
                "from gba.booking_customers where booking_id = %s",
                (UUID(booking_id),),
            )
        ).fetchone()
    assert row == (None, None, None)


async def test_cross_tenant_confirmation_and_live_recheck(
    customer_client: httpx.AsyncClient, world: BookingWorld, owner_conn: psycopg.Connection
) -> None:
    slot = await first_slot(customer_client, world)
    token = secrets.token_urlsafe(32)
    response = await hold(customer_client, world, slot, token, "cross-tenant-hold")
    url = f"/v1/customer/bookings/{response.json()['booking_id']}/confirm"
    headers = {"Booking-Token": token, "Idempotency-Key": "cross-tenant-confirm"}
    denied = await customer_client.post(
        f"http://{world.b.host}{url}", json=DETAILS, headers=headers
    )
    assert denied.status_code == 404
    with owner_tenant_transaction(owner_conn, world.a.tenant_id):
        owner_conn.execute(
            "update gba.tenants set booking_state = 'not_live' where id = %s", (world.a.tenant_id,)
        )
    assert (await customer_client.post(url, json=DETAILS, headers=headers)).status_code == 404


async def test_concurrent_confirmation_is_idempotent(
    customer_client: httpx.AsyncClient, world: BookingWorld
) -> None:
    slot = await first_slot(customer_client, world)
    token = secrets.token_urlsafe(32)
    response = await hold(customer_client, world, slot, token, "confirm-race-hold")
    url = f"/v1/customer/bookings/{response.json()['booking_id']}/confirm"
    results = await asyncio.gather(
        *(
            customer_client.post(
                url,
                json=DETAILS,
                headers={"Booking-Token": token, "Idempotency-Key": f"confirm-race-{i}"},
            )
            for i in range(4)
        )
    )
    assert all(r.status_code == 200 for r in results)
    assert len({r.json()["booking_id"] for r in results}) == 1
