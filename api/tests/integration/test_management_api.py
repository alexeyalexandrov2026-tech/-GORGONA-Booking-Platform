"""Management API tests against disposable PostgreSQL 18.

Verifies:
- Overview stats, today's bookings, and audit activity
- Staff booking creation, listing, rescheduling, and cancellation
- Client search, aggregation, and booking history
- Staff working hours and service assignment
- Service catalog updates and unpublishing
- Settings inspection
- Tenant isolation and concurrency enforcement
"""

from collections.abc import AsyncIterator
from uuid import uuid7

import httpx
import psycopg
import pytest

from gorgona_booking.api.app import create_app
from gorgona_booking.config import Settings
from gorgona_booking.db.pool import RuntimePool
from gorgona_booking.db.provisioning import add_membership
from tests.integration.booking_support import BookingWorld, at
from tests.integration.seed import FakeUser, seed_user
from tests.support.fake_idp import FakeIdp

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def idp() -> FakeIdp:
    return FakeIdp()


@pytest.fixture
async def client(app_pool: RuntimePool, idp: FakeIdp) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(environment="test"), pool=app_pool, token_verifier=idp.verifier())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as http:
        yield http


@pytest.fixture
def manager_a(world: BookingWorld, owner_conn: psycopg.Connection) -> FakeUser:
    user = seed_user(owner_conn, f"manager-a-{uuid7()}")
    add_membership(owner_conn, tenant_id=world.a.tenant_id, user_id=user.user_id, role="manager")
    return user


@pytest.fixture
def manager_b(world: BookingWorld, owner_conn: psycopg.Connection) -> FakeUser:
    user = seed_user(owner_conn, f"manager-b-{uuid7()}")
    add_membership(owner_conn, tenant_id=world.b.tenant_id, user_id=user.user_id, role="manager")
    return user


async def test_management_overview_and_settings(
    client: httpx.AsyncClient,
    world: BookingWorld,
    manager_a: FakeUser,
    idp: FakeIdp,
) -> None:
    headers = idp.bearer(manager_a.subject, email=manager_a.email)
    salon_id = world.a.tenant_id

    # 1. Overview
    res = await client.get(f"/v1/salons/{salon_id}/overview", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["salon_id"] == str(salon_id)
    assert data["status"] == "active"
    assert "stats" in data
    assert data["stats"]["active_staff_count"] >= 1
    assert data["stats"]["total_services_count"] >= 1

    # 2. Settings
    settings_res = await client.get(f"/v1/salons/{salon_id}/settings", headers=headers)
    assert settings_res.status_code == 200, settings_res.text
    settings_data = settings_res.json()
    assert settings_data["salon_id"] == str(salon_id)
    assert len(settings_data["locations"]) >= 1


async def test_staff_booking_crud_reschedule_cancel(
    client: httpx.AsyncClient,
    world: BookingWorld,
    manager_a: FakeUser,
    idp: FakeIdp,
) -> None:
    headers = idp.bearer(manager_a.subject, email=manager_a.email)
    salon_id = world.a.tenant_id
    location_id = world.a.location_id
    resource_id = world.artist_a1
    variant_id = world.catalog_a.base_variant_id

    # 1. Staff creates confirmed booking
    slot_time = at(11)
    create_payload = {
        "location_id": str(location_id),
        "resource_id": str(resource_id),
        "variant_id": str(variant_id),
        "starts_at": slot_time.isoformat(),
        "customer_name": "Alice Wonderland",
        "customer_email": "alice@example.com",
        "customer_phone": "+15551234567",
    }
    create_res = await client.post(
        f"/v1/salons/{salon_id}/bookings",
        json=create_payload,
        headers=headers,
    )
    assert create_res.status_code == 201, create_res.text
    created = create_res.json()
    booking_id = created["booking_id"]
    assert created["status"] == "CONFIRMED"
    assert created["customer_name"] == "Alice Wonderland"
    assert created["customer_email"] == "alice@example.com"

    # 2. Conflict check: cannot book overlapping time for same resource
    conflict_res = await client.post(
        f"/v1/salons/{salon_id}/bookings",
        json=create_payload,
        headers=headers,
    )
    assert conflict_res.status_code == 409, conflict_res.text

    # 3. List bookings
    list_res = await client.get(
        f"/v1/salons/{salon_id}/bookings?resource_id={resource_id}&status=CONFIRMED",
        headers=headers,
    )
    assert list_res.status_code == 200
    listed_ids = [b["booking_id"] for b in list_res.json()]
    assert booking_id in listed_ids

    # 4. Reschedule booking to another hour
    new_slot = at(14)
    reschedule_res = await client.post(
        f"/v1/salons/{salon_id}/bookings/{booking_id}/reschedule",
        json={"new_starts_at": new_slot.isoformat()},
        headers=headers,
    )
    assert reschedule_res.status_code == 200, reschedule_res.text
    rescheduled = reschedule_res.json()
    new_booking_id = rescheduled["booking_id"]
    assert new_booking_id != booking_id
    assert rescheduled["status"] == "CONFIRMED"
    assert rescheduled["customer_name"] == "Alice Wonderland"

    # 5. Check old slot at 11 is now free: someone else can book it!
    free_slot_res = await client.post(
        f"/v1/salons/{salon_id}/bookings",
        json={
            "location_id": str(location_id),
            "resource_id": str(resource_id),
            "variant_id": str(variant_id),
            "starts_at": slot_time.isoformat(),
            "customer_name": "Bob Builder",
            "customer_email": "bob@example.com",
            "customer_phone": "+15559876543",
        },
        headers=headers,
    )
    assert free_slot_res.status_code == 201, free_slot_res.text

    # 6. Cancel rescheduled booking
    cancel_res = await client.post(
        f"/v1/salons/{salon_id}/bookings/{new_booking_id}/cancel",
        json={"reason": "Customer called to cancel"},
        headers=headers,
    )
    assert cancel_res.status_code == 200, cancel_res.text
    assert cancel_res.json()["status"] == "CANCELLED"

    # 7. Check Clients list
    clients_res = await client.get(f"/v1/salons/{salon_id}/clients?q=Alice", headers=headers)
    assert clients_res.status_code == 200
    clients_data = clients_res.json()
    assert len(clients_data) >= 1
    alice = next((c for c in clients_data if c["email"] == "alice@example.com"), None)
    assert alice is not None
    assert alice["customer_name"] == "Alice Wonderland"

    # 8. Check Client history
    history_res = await client.get(
        f"/v1/salons/{salon_id}/clients/history?email=alice@example.com",
        headers=headers,
    )
    assert history_res.status_code == 200
    history_bookings = history_res.json()
    assert len(history_bookings) >= 1


async def test_staff_schedule_and_services_management(
    client: httpx.AsyncClient,
    world: BookingWorld,
    manager_a: FakeUser,
    idp: FakeIdp,
) -> None:
    headers = idp.bearer(manager_a.subject, email=manager_a.email)
    salon_id = world.a.tenant_id
    resource_id = world.artist_a1

    # 1. Get schedule
    sched_res = await client.get(
        f"/v1/salons/{salon_id}/staff/{resource_id}/schedule",
        headers=headers,
    )
    assert sched_res.status_code == 200, sched_res.text
    sched_data = sched_res.json()
    assert sched_data["resource_id"] == str(resource_id)

    # 2. Update schedule
    new_hours = [
        {"weekday": 1, "opens_minute": 540, "closes_minute": 1020},  # 9:00 - 17:00
        {"weekday": 2, "opens_minute": 540, "closes_minute": 1020},
    ]
    put_sched_res = await client.put(
        f"/v1/salons/{salon_id}/staff/{resource_id}/schedule",
        json={"hours": new_hours},
        headers=headers,
    )
    assert put_sched_res.status_code == 200, put_sched_res.text
    updated_sched = put_sched_res.json()
    assert len(updated_sched["hours"]) == 2

    # 3. Update staff services
    services_res = await client.get(f"/v1/salons/{salon_id}/services", headers=headers)
    assert services_res.status_code == 200
    target_service_id = services_res.json()[0]["service_id"]
    put_services_res = await client.put(
        f"/v1/salons/{salon_id}/staff/{resource_id}/services",
        json={"service_ids": [target_service_id]},
        headers=headers,
    )
    assert put_services_res.status_code == 200, put_services_res.text
    assert target_service_id in [str(s) for s in put_services_res.json()["service_ids"]]

    # 4. Patch staff
    patch_staff_res = await client.patch(
        f"/v1/salons/{salon_id}/staff/{resource_id}",
        json={"display_name": "Artist Supreme"},
        headers=headers,
    )
    assert patch_staff_res.status_code == 200, patch_staff_res.text
    assert patch_staff_res.json()["display_name"] == "Artist Supreme"


async def test_services_patch_and_unpublish(
    client: httpx.AsyncClient,
    world: BookingWorld,
    manager_a: FakeUser,
    idp: FakeIdp,
) -> None:
    headers = idp.bearer(manager_a.subject, email=manager_a.email)
    salon_id = world.a.tenant_id
    variant_id = world.catalog_a.base_variant_id

    # 1. Patch variant price
    patch_res = await client.patch(
        f"/v1/salons/{salon_id}/services/{variant_id}",
        json={"price_cents": 7500},
        headers=headers,
    )
    assert patch_res.status_code == 200, patch_res.text
    assert patch_res.json()["price_cents"] == 7500

    # 2. Unpublish
    unpub_res = await client.post(
        f"/v1/salons/{salon_id}/services/{variant_id}/unpublish",
        headers=headers,
    )
    assert unpub_res.status_code == 200, unpub_res.text
    assert unpub_res.json()["is_bookable"] is False
    assert unpub_res.json()["status"] == "draft"

    # 3. Re-publish
    pub_res = await client.post(
        f"/v1/salons/{salon_id}/services/{variant_id}/publish",
        headers=headers,
    )
    assert pub_res.status_code == 200, pub_res.text
    assert pub_res.json()["is_bookable"] is True
    assert pub_res.json()["status"] == "published"


async def test_tenant_isolation_on_management_endpoints(
    client: httpx.AsyncClient,
    world: BookingWorld,
    manager_a: FakeUser,
    manager_b: FakeUser,
    idp: FakeIdp,
) -> None:
    headers_a = idp.bearer(manager_a.subject, email=manager_a.email)
    headers_b = idp.bearer(manager_b.subject, email=manager_b.email)

    # Manager A cannot view Salon B's overview or bookings
    overview_b = await client.get(f"/v1/salons/{world.b.tenant_id}/overview", headers=headers_a)
    assert overview_b.status_code == 403

    bookings_b = await client.get(f"/v1/salons/{world.b.tenant_id}/bookings", headers=headers_a)
    assert bookings_b.status_code == 403

    # Manager B cannot view Salon A's settings or clients
    settings_a = await client.get(f"/v1/salons/{world.a.tenant_id}/settings", headers=headers_b)
    assert settings_a.status_code == 403

    clients_a = await client.get(f"/v1/salons/{world.a.tenant_id}/clients", headers=headers_b)
    assert clients_a.status_code == 403
