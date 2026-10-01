"""Staff mutations must keep the same booking authority as customer mutations."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid7
from zoneinfo import ZoneInfo

import httpx
import psycopg
import pytest

from gorgona_booking.api.app import create_app
from gorgona_booking.config import Settings
from gorgona_booking.db.pool import RuntimePool
from gorgona_booking.db.provisioning import add_membership, owner_tenant_transaction
from gorgona_booking.tenancy.embedding import add_embed_origin
from tests.integration.booking_support import BookingWorld
from tests.integration.customer_support import customer_day, seed_customer_setup
from tests.integration.seed import seed_user
from tests.support.fake_idp import FakeIdp

pytestmark = pytest.mark.anyio


def start(hour: int, minute: int = 0) -> str:
    local = datetime.fromisoformat(f"{customer_day()}T{hour:02d}:{minute:02d}:00")
    return local.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(UTC).isoformat()


def payload(world: BookingWorld, hour: int = 11) -> dict[str, object]:
    return {
        "location_id": str(world.a.location_id),
        "resource_id": str(world.artist_a1),
        "variant_id": str(world.catalog_a.base_variant_id),
        "starts_at": start(hour),
        "customer_name": "FAKE Staff Guest",
        "customer_email": "staff-guest@example.test",
        "customer_phone": "+15551234567",
    }


@pytest.fixture
async def staff_client(
    world: BookingWorld, owner_conn: psycopg.Connection, app_pool: RuntimePool
) -> AsyncIterator[httpx.AsyncClient]:
    seed_customer_setup(owner_conn, world)
    user = seed_user(owner_conn, f"invariant-manager-{uuid7()}")
    add_membership(owner_conn, tenant_id=world.a.tenant_id, user_id=user.user_id, role="manager")
    idp = FakeIdp()
    app = create_app(Settings(environment="test"), pool=app_pool, token_verifier=idp.verifier())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=f"http://{world.a.host}",
        headers=idp.bearer(user.subject, email=user.email),
    ) as client:
        yield client


async def test_reschedule_preserves_quote_addons_and_duration_after_catalog_change(
    staff_client: httpx.AsyncClient, world: BookingWorld, owner_conn: psycopg.Connection
) -> None:
    base = f"/v1/salons/{world.a.tenant_id}/bookings"
    created = await staff_client.post(
        base, json={**payload(world), "add_on_ids": [str(world.catalog_a.massage_add_on_id)]}
    )
    assert created.status_code == 201, created.text
    old_id = created.json()["booking_id"]
    with owner_tenant_transaction(owner_conn, world.a.tenant_id):
        original = owner_conn.execute(
            "select quote, total_cents, ends_at - starts_at from gba.bookings where id = %s",
            (UUID(old_id),),
        ).fetchone()
        owner_conn.execute(
            "update gba.service_variants set price_cents = 9900, "
            "booking_duration_minutes = 120, revision = revision + 1 where id = %s",
            (world.catalog_a.base_variant_id,),
        )
    moved = await staff_client.post(
        f"{base}/{old_id}/reschedule", json={"new_starts_at": start(13)}
    )
    assert moved.status_code == 200, moved.text
    with owner_tenant_transaction(owner_conn, world.a.tenant_id):
        after = owner_conn.execute(
            "select quote, total_cents, ends_at - starts_at from gba.bookings where id = %s",
            (UUID(moved.json()["booking_id"]),),
        ).fetchone()
    assert after == original


@pytest.mark.parametrize(
    ("hour", "minute"), [(9, 0), (11, 1), (16, 0)], ids=["outside-hours", "off-grid", "closed"]
)
async def test_staff_create_rejects_unavailable_times(
    staff_client: httpx.AsyncClient, world: BookingWorld, hour: int, minute: int
) -> None:
    response = await staff_client.post(
        f"/v1/salons/{world.a.tenant_id}/bookings",
        json={**payload(world), "starts_at": start(hour, minute)},
    )
    assert response.status_code == 409, response.text


async def test_staff_create_rejects_ineligible_resource_and_wrong_location(
    staff_client: httpx.AsyncClient, world: BookingWorld, owner_conn: psycopg.Connection
) -> None:
    with owner_tenant_transaction(owner_conn, world.a.tenant_id):
        owner_conn.execute(
            "delete from gba.resource_services where resource_id = %s", (world.artist_a1,)
        )
    response = await staff_client.post(
        f"/v1/salons/{world.a.tenant_id}/bookings", json=payload(world)
    )
    assert response.status_code == 404, response.text
    response = await staff_client.post(
        f"/v1/salons/{world.a.tenant_id}/bookings",
        json={
            **payload(world),
            "resource_id": str(world.artist_a2),
            "location_id": str(world.b.location_id),
        },
    )
    assert response.status_code == 404, response.text


async def test_reschedule_excludes_itself_but_rejects_closed_time_without_cancelling(
    staff_client: httpx.AsyncClient, world: BookingWorld
) -> None:
    base = f"/v1/salons/{world.a.tenant_id}/bookings"
    created = await staff_client.post(base, json=payload(world))
    assert created.status_code == 201, created.text
    old_id = created.json()["booking_id"]
    failed = await staff_client.post(
        f"{base}/{old_id}/reschedule", json={"new_starts_at": start(9)}
    )
    assert failed.status_code == 409, failed.text
    unchanged = await staff_client.get(f"{base}/{old_id}")
    assert unchanged.json()["status"] == "CONFIRMED"
    moved = await staff_client.post(
        f"{base}/{old_id}/reschedule", json={"new_starts_at": start(11, 30)}
    )
    assert moved.status_code == 200, moved.text


async def test_management_mutations_replay_and_reject_reused_key(
    staff_client: httpx.AsyncClient, world: BookingWorld
) -> None:
    base = f"/v1/salons/{world.a.tenant_id}/bookings"
    headers = {"Idempotency-Key": str(uuid7())}
    first = await staff_client.post(base, json=payload(world), headers=headers)
    retry = await staff_client.post(base, json=payload(world), headers=headers)
    assert first.status_code == retry.status_code == 201
    assert first.json() == retry.json()
    changed = await staff_client.post(base, json=payload(world, 13), headers=headers)
    assert changed.status_code == 422
    assert changed.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    old_id = first.json()["booking_id"]
    move_headers = {"Idempotency-Key": str(uuid7())}
    body = {"new_starts_at": start(13)}
    move = await staff_client.post(f"{base}/{old_id}/reschedule", json=body, headers=move_headers)
    retry_move = await staff_client.post(
        f"{base}/{old_id}/reschedule", json=body, headers=move_headers
    )
    assert move.status_code == retry_move.status_code == 200
    assert move.json() == retry_move.json()
    cancel_headers = {"Idempotency-Key": str(uuid7())}
    url = f"{base}/{move.json()['booking_id']}/cancel"
    cancelled = await staff_client.post(
        url, json={"reason": "FAKE cancellation"}, headers=cancel_headers
    )
    retried_cancel = await staff_client.post(
        url, json={"reason": "FAKE cancellation"}, headers=cancel_headers
    )
    assert cancelled.json() == retried_cancel.json()


async def test_reschedule_cannot_confirm_a_guest_hold(
    staff_client: httpx.AsyncClient, world: BookingWorld
) -> None:
    selection = {
        "location_id": str(world.a.location_id),
        "variant_id": str(world.catalog_a.base_variant_id),
        "resource_id": str(world.artist_a1),
        "add_on_ids": [],
        "start_at": start(11),
    }
    held = await staff_client.post(
        "/v1/customer/holds",
        json=selection,
        headers={"Booking-Token": "a" * 43, "Idempotency-Key": str(uuid7())},
    )
    assert held.status_code == 201, held.text
    moved = await staff_client.post(
        f"/v1/salons/{world.a.tenant_id}/bookings/{held.json()['booking_id']}/reschedule",
        json={"new_starts_at": start(13)},
    )
    assert moved.status_code == 409, moved.text


async def test_opposite_resource_swaps_fail_cleanly_and_keep_both_bookings(
    staff_client: httpx.AsyncClient, world: BookingWorld
) -> None:
    base = f"/v1/salons/{world.a.tenant_id}/bookings"
    first = await staff_client.post(base, json=payload(world))
    second = await staff_client.post(
        base, json={**payload(world), "resource_id": str(world.artist_a2)}
    )
    assert first.status_code == second.status_code == 201
    requests = [
        staff_client.post(
            f"{base}/{response.json()['booking_id']}/reschedule",
            json={"new_starts_at": start(11), "new_resource_id": str(resource)},
        )
        for response, resource in ((first, world.artist_a2), (second, world.artist_a1))
    ]
    results = await asyncio.wait_for(asyncio.gather(*requests), timeout=10)
    assert [r.status_code for r in results] == [409, 409]
    for response in (first, second):
        current = await staff_client.get(f"{base}/{response.json()['booking_id']}")
        assert current.json()["status"] == "CONFIRMED"


async def test_management_responses_and_errors_are_not_cached(
    staff_client: httpx.AsyncClient, world: BookingWorld
) -> None:
    success = await staff_client.get(f"/v1/salons/{world.a.tenant_id}/clients")
    denied = await staff_client.get(f"/v1/salons/{world.b.tenant_id}/clients")
    assert success.status_code == 200
    assert denied.status_code == 403
    for response in (success, denied):
        assert response.headers["cache-control"] == "private, no-store"


async def test_management_availability_uses_preserved_quote_and_refuses_other_tenant(
    staff_client: httpx.AsyncClient, world: BookingWorld, owner_conn: psycopg.Connection
) -> None:
    base = f"/v1/salons/{world.a.tenant_id}"
    created = await staff_client.post(
        f"{base}/bookings",
        json={**payload(world), "add_on_ids": [str(world.catalog_a.massage_add_on_id)]},
    )
    assert created.status_code == 201, created.text
    booking_id = created.json()["booking_id"]
    with owner_tenant_transaction(owner_conn, world.a.tenant_id):
        owner_conn.execute(
            "update gba.service_variants set price_cents = 9999 where id = %s",
            (world.catalog_a.base_variant_id,),
        )
    query = {
        "location_id": str(world.a.location_id),
        "variant_id": str(world.catalog_a.base_variant_id),
        "resource_id": str(world.artist_a1),
        "day": customer_day(),
        "booking_id": booking_id,
    }
    available = await staff_client.post(f"{base}/availability", json=query)
    assert available.status_code == 200, available.text
    view = available.json()
    assert view["timezone"] == "America/New_York"
    assert view["quote"]["total_cents"] == created.json()["total_cents"]
    assert view["quote"]["booking_duration_minutes"] == 75
    assert any(datetime.fromisoformat(slot["start_at"]) == datetime.fromisoformat(start(11))
               for slot in view["slots"])
    denied = await staff_client.post(f"/v1/salons/{world.b.tenant_id}/availability", json=query)
    assert denied.status_code == 403


async def test_calendar_local_day_uses_each_location_timezone(
    staff_client: httpx.AsyncClient, world: BookingWorld, owner_conn: psycopg.Connection
) -> None:
    with owner_tenant_transaction(owner_conn, world.a.tenant_id):
        owner_conn.execute(
            "update gba.business_hours set opens_minute = 0, closes_minute = 1440"
        )
        owner_conn.execute(
            "update gba.resource_hours set opens_minute = 0, closes_minute = 1440"
        )
    base = f"/v1/salons/{world.a.tenant_id}/bookings"
    created = await staff_client.post(base, json=payload(world, 23))
    assert created.status_code == 201, created.text
    assert datetime.fromisoformat(start(23)).date().isoformat() != customer_day()
    listed = await staff_client.get(base, params={"local_day": customer_day()})
    assert [row["booking_id"] for row in listed.json()] == [created.json()["booking_id"]]
    next_day = datetime.fromisoformat(start(23)).date().isoformat()
    other = await staff_client.get(base, params={"local_day": next_day})
    assert other.json() == []


@pytest.mark.parametrize(
    "page", ["overview", "calendar", "bookings", "services", "staff", "clients", "settings"]
)
async def test_management_html_cannot_use_customer_embedding_allowlist(
    world: BookingWorld,
    owner_conn: psycopg.Connection,
    app_pool: RuntimePool,
    tmp_path: Path,
    page: str,
) -> None:
    add_embed_origin(
        owner_conn, world.a.tenant_id, "https://approved.example.test", actor="test:framing"
    )
    folder = tmp_path / page
    folder.mkdir()
    (folder / "index.html").write_text("<h1>FAKE management page</h1>", encoding="utf-8")
    app = create_app(Settings(environment="test", customer_web_dir=tmp_path), pool=app_pool)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        response = await client.get(f"http://{world.a.host}/{page}/")
    assert response.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "private, no-store"
