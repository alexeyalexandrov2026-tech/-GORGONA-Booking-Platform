"""Live Synchronization & Tenant Isolation Integration Suite.

Proves the non-negotiable contract between GORGONA Booking and KA Nails:
TEST 1: Change a service in GORGONA → verify KA Nails receives the change.
TEST 2: Change staff availability in GORGONA → verify KA Nails available slots change.
TEST 3: Create a booking from KA Nails → verify it appears in GORGONA Calendar/Bookings.
TEST 4: Create a booking → verify the occupied slot disappears from KA Nails availability.
TEST 5: Cancel and reschedule in GORGONA → verify KA Nails availability updates accordingly.
TEST 6: Tenant isolation: KA Nails never receives another tenant's services, staff,
        clients, or bookings.
"""

import secrets
from collections.abc import AsyncIterator
from datetime import datetime
from uuid import uuid7

import httpx
import psycopg
import pytest

from gorgona_booking.api.app import create_app
from gorgona_booking.config import Settings
from gorgona_booking.db.pool import RuntimePool
from gorgona_booking.db.provisioning import add_membership
from tests.integration.booking_support import BookingWorld
from tests.integration.customer_support import customer_day, seed_customer_setup
from tests.integration.seed import FakeUser, seed_user
from tests.support.fake_idp import FakeIdp

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def idp() -> FakeIdp:
    return FakeIdp()


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


@pytest.fixture
async def app_client(
    world: BookingWorld,
    owner_conn: psycopg.Connection,
    app_pool: RuntimePool,
    idp: FakeIdp,
) -> AsyncIterator[httpx.AsyncClient]:
    seed_customer_setup(owner_conn, world)
    app = create_app(
        Settings(environment="test"),
        pool=app_pool,
        token_verifier=idp.verifier(),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://platform.internal"
    ) as client:
        yield client


def ka_headers(world: BookingWorld) -> dict[str, str]:
    return {"Host": world.a.host}


def ka_selection(world: BookingWorld) -> dict[str, object]:
    return {
        "location_id": str(world.a.location_id),
        "variant_id": str(world.catalog_a.base_variant_id),
        "add_on_ids": [],
        "day": customer_day(),
        "resource_id": None,
    }


async def book_slot(
    client: httpx.AsyncClient,
    world: BookingWorld,
    slot: dict[str, str],
    customer_name: str,
    customer_email: str = "customer@example.test",
) -> str:
    body = ka_selection(world)
    body.pop("day")
    body.update({"resource_id": slot["resource_id"], "start_at": slot["start_at"]})
    token = secrets.token_urlsafe(32)
    key = secrets.token_urlsafe(16)
    hold_res = await client.post(
        "/v1/customer/holds",
        json=body,
        headers={**ka_headers(world), "Booking-Token": token, "Idempotency-Key": key},
    )
    assert hold_res.status_code == 201, hold_res.text
    booking_id = hold_res.json()["booking_id"]

    confirm_res = await client.post(
        f"/v1/customer/bookings/{booking_id}/confirm",
        json={
            "name": customer_name,
            "email": customer_email,
            "phone": "+1 555 234 5678",
            "accept_policy": True,
        },
        headers={**ka_headers(world), "Booking-Token": token, "Idempotency-Key": f"conf-{key}"},
    )
    assert confirm_res.status_code == 200, confirm_res.text
    return str(booking_id)


# ==============================================================================
# TEST 1: Change a service in GORGONA → verify KA Nails receives the change
# ==============================================================================
async def test_sync_1_service_update_propagates_to_ka_nails(
    app_client: httpx.AsyncClient,
    world: BookingWorld,
    manager_a: FakeUser,
    idp: FakeIdp,
) -> None:
    salon_id = world.a.tenant_id
    variant_id = world.catalog_a.base_variant_id
    staff_headers = idp.bearer(manager_a.subject, email=manager_a.email)

    # 1. Verify initial public catalog on KA Nails host
    initial_boot = await app_client.get("/v1/customer/bootstrap", headers=ka_headers(world))
    assert initial_boot.status_code == 200
    init_variant = next(v for v in initial_boot.json()["variants"] if v["id"] == str(variant_id))
    assert init_variant["price_cents"] == 5000  # $50.00 initial

    # 2. Update service variant in GORGONA management (change price to $85.00 and rename)
    update_res = await app_client.patch(
        f"/v1/salons/{salon_id}/services/{variant_id}",
        json={
            "name": "Luxury Silk Gel Manicure",
            "price_cents": 8500,
        },
        headers=staff_headers,
    )
    assert update_res.status_code == 200, update_res.text
    assert update_res.json()["price_cents"] == 8500
    assert update_res.json()["name"] == "Luxury Silk Gel Manicure"

    # 3. KA Nails customer immediately sees updated price and service name without cache stale
    updated_boot = await app_client.get("/v1/customer/bootstrap", headers=ka_headers(world))
    assert updated_boot.status_code == 200
    updated_variant = next(v for v in updated_boot.json()["variants"] if v["id"] == str(variant_id))
    assert updated_variant["price_cents"] == 8500
    assert updated_variant["name"] == "Luxury Silk Gel Manicure"

    # 4. Verify KA Nails availability quote reflects the updated price
    avail_res = await app_client.post(
        "/v1/customer/availability",
        json=ka_selection(world),
        headers=ka_headers(world),
    )
    assert avail_res.status_code == 200
    assert avail_res.json()["quote"]["total_cents"] == 8500


# ==============================================================================
# TEST 2: Change staff availability in GORGONA → verify KA Nails slots change
# ==============================================================================
async def test_sync_2_staff_availability_update_propagates_to_ka_nails(
    app_client: httpx.AsyncClient,
    world: BookingWorld,
    manager_a: FakeUser,
    idp: FakeIdp,
) -> None:
    salon_id = world.a.tenant_id
    artist_id = world.artist_a1
    day = customer_day()
    weekday = datetime.fromisoformat(f"{day}T00:00:00Z").isoweekday()  # 1=Mon .. 7=Sun
    staff_headers = idp.bearer(manager_a.subject, email=manager_a.email)

    # 1. Query initial KA Nails availability for artist A1
    query = ka_selection(world)
    query["resource_id"] = str(artist_id)
    initial_avail = await app_client.post(
        "/v1/customer/availability",
        json=query,
        headers=ka_headers(world),
    )
    assert initial_avail.status_code == 200
    init_slots = initial_avail.json()["slots"]
    assert len(init_slots) > 0
    earliest_slot_time = init_slots[0]["start_at"]

    # 2. Update staff working hours in GORGONA management:
    # change hours to start later (opens_minute: 840 = 2:00 PM local)
    schedule_update_res = await app_client.put(
        f"/v1/salons/{salon_id}/staff/{artist_id}/schedule",
        json={
            "hours": [
                {
                    "weekday": weekday,
                    "opens_minute": 840,
                    "closes_minute": 1020,
                }
            ]
        },
        headers=staff_headers,
    )
    assert schedule_update_res.status_code == 200, schedule_update_res.text

    # 3. Query KA Nails availability again: earlier slot must be gone
    updated_avail = await app_client.post(
        "/v1/customer/availability",
        json=query,
        headers=ka_headers(world),
    )
    assert updated_avail.status_code == 200
    updated_slots = updated_avail.json()["slots"]
    assert not any(s["start_at"] == earliest_slot_time for s in updated_slots), (
        f"Initial slot {earliest_slot_time} must no longer appear after schedule was reduced"
    )

    # 4. Deactivate the staff member in GORGONA:
    deactivate_res = await app_client.patch(
        f"/v1/salons/{salon_id}/staff/{artist_id}",
        json={"is_active": False},
        headers=staff_headers,
    )
    assert deactivate_res.status_code == 200
    assert deactivate_res.json()["is_active"] is False

    # 5. Query KA Nails: deactivated staff member returns 404 if requested specifically
    deactivated_avail = await app_client.post(
        "/v1/customer/availability",
        json=query,
        headers=ka_headers(world),
    )
    assert deactivated_avail.status_code == 404, "Inactive staff member cannot be requested"

    # 6. Query KA Nails with any artist: artist_a1 slots are completely omitted
    any_query = ka_selection(world)
    any_query["resource_id"] = None
    any_avail = await app_client.post(
        "/v1/customer/availability",
        json=any_query,
        headers=ka_headers(world),
    )
    assert any_avail.status_code == 200
    assert not any(s["resource_id"] == str(artist_id) for s in any_avail.json()["slots"]), (
        "Deactivated staff member's slots must not appear in any-artist availability"
    )


# ==============================================================================
# TEST 3: Create a booking from KA Nails → verify it appears in GORGONA Calendar
# ==============================================================================
async def test_sync_3_booking_from_ka_nails_appears_in_gorgona_calendar(
    app_client: httpx.AsyncClient,
    world: BookingWorld,
    manager_a: FakeUser,
    idp: FakeIdp,
) -> None:
    salon_id = world.a.tenant_id
    staff_headers = idp.bearer(manager_a.subject, email=manager_a.email)

    # 1. Customer on KA Nails website creates a booking
    avail_res = await app_client.post(
        "/v1/customer/availability",
        json=ka_selection(world),
        headers=ka_headers(world),
    )
    slot = avail_res.json()["slots"][0]
    guest_name = "KA Nails VIP Client"
    guest_email = "vip@kanails.test"
    created_booking_id = await book_slot(
        app_client, world, slot, customer_name=guest_name, customer_email=guest_email
    )

    # 2. Staff in GORGONA opens Calendar / Bookings register
    bookings_res = await app_client.get(
        f"/v1/salons/{salon_id}/bookings",
        headers=staff_headers,
    )
    assert bookings_res.status_code == 200, bookings_res.text
    b_list = bookings_res.json()
    matched = [b for b in b_list if b["booking_id"] == created_booking_id]
    assert len(matched) == 1, "The KA Nails customer appointment must appear in GORGONA bookings"
    booking_record = matched[0]
    assert booking_record["status"] == "CONFIRMED"
    assert booking_record["customer_name"] == guest_name
    assert booking_record["customer_email"] == guest_email
    assert datetime.fromisoformat(booking_record["starts_at"]) == datetime.fromisoformat(
        slot["start_at"]
    )

    # 3. Staff checks Calendar view for that appointment date
    booking_day = datetime.fromisoformat(slot["start_at"]).date().isoformat()
    cal_res = await app_client.get(
        f"/v1/salons/{salon_id}/bookings",
        params={
            "start_date": f"{booking_day}T00:00:00Z",
            "end_date": f"{booking_day}T23:59:59Z",
        },
        headers=staff_headers,
    )
    assert cal_res.status_code == 200
    cal_bookings = cal_res.json()
    assert any(b["booking_id"] == created_booking_id for b in cal_bookings), (
        "Booking must be returned when filtering for its scheduled date in Calendar"
    )

    # 4. Staff checks Clients directory in GORGONA: customer profile was automatically indexed
    clients_res = await app_client.get(
        f"/v1/salons/{salon_id}/clients",
        headers=staff_headers,
    )
    assert clients_res.status_code == 200
    clients_list = clients_res.json()
    assert any(c["email"] == guest_email for c in clients_list), (
        "Customer from KA Nails booking must be visible in GORGONA Clients directory"
    )

    # 5. Staff checks Salon Overview endpoint returns active salon status
    overview_res = await app_client.get(
        f"/v1/salons/{salon_id}/overview",
        headers=staff_headers,
    )
    assert overview_res.status_code == 200
    overview_data = overview_res.json()
    assert overview_data["status"].lower() == "active"


# ==============================================================================
# TEST 4: Create a booking → verify occupied slot disappears from KA Nails
# ==============================================================================
async def test_sync_4_booking_slot_disappears_from_ka_nails_availability(
    app_client: httpx.AsyncClient,
    world: BookingWorld,
) -> None:
    # 1. Find an available slot on KA Nails
    query = ka_selection(world)
    query["resource_id"] = str(world.artist_a1)
    avail_res1 = await app_client.post(
        "/v1/customer/availability",
        json=query,
        headers=ka_headers(world),
    )
    assert avail_res1.status_code == 200
    target_slot = avail_res1.json()["slots"][0]
    target_time = target_slot["start_at"]

    # 2. Book that specific slot
    await book_slot(
        app_client,
        world,
        target_slot,
        customer_name="First Customer",
        customer_email="first@example.test",
    )

    # 3. Query availability again for KA Nails: the slot MUST be gone!
    avail_res2 = await app_client.post(
        "/v1/customer/availability",
        json=query,
        headers=ka_headers(world),
    )
    assert avail_res2.status_code == 200
    remaining_slots = avail_res2.json()["slots"]
    assert not any(s["start_at"] == target_time for s in remaining_slots), (
        f"Slot {target_time} must not be bookable after being confirmed"
    )

    # 4. Attempting to hold that occupied slot directly returns 409 CONFLICT
    body = ka_selection(world)
    body.pop("day")
    body.update({"resource_id": target_slot["resource_id"], "start_at": target_time})
    conflict_hold = await app_client.post(
        "/v1/customer/holds",
        json=body,
        headers={
            **ka_headers(world),
            "Booking-Token": secrets.token_urlsafe(32),
            "Idempotency-Key": "double-book-key",
        },
    )
    assert conflict_hold.status_code == 409, conflict_hold.text


# ==============================================================================
# TEST 5: Cancel / reschedule in GORGONA → verify KA Nails availability changes
# ==============================================================================
async def test_sync_5_cancellation_and_rescheduling_updates_ka_nails_availability(
    app_client: httpx.AsyncClient,
    world: BookingWorld,
    manager_a: FakeUser,
    idp: FakeIdp,
) -> None:
    salon_id = world.a.tenant_id
    staff_headers = idp.bearer(manager_a.subject, email=manager_a.email)

    # --- Part A: Cancellation releases slot back to KA Nails ---
    query = ka_selection(world)
    query["resource_id"] = str(world.artist_a2)
    avail_res1 = await app_client.post(
        "/v1/customer/availability", json=query, headers=ka_headers(world)
    )
    slot1 = avail_res1.json()["slots"][0]
    time1 = slot1["start_at"]

    booking1_id = await book_slot(app_client, world, slot1, "Cancel Test Guest")

    # Verify slot is gone
    avail_res2 = await app_client.post(
        "/v1/customer/availability", json=query, headers=ka_headers(world)
    )
    assert not any(s["start_at"] == time1 for s in avail_res2.json()["slots"])

    # Staff cancels booking1 in GORGONA
    cancel_res = await app_client.post(
        f"/v1/salons/{salon_id}/bookings/{booking1_id}/cancel",
        json={"reason": "Customer called to cancel appointment"},
        headers=staff_headers,
    )
    assert cancel_res.status_code == 200, cancel_res.text
    assert cancel_res.json()["status"] == "CANCELLED"

    # Slot 1 MUST REAPPEAR on KA Nails
    avail_res3 = await app_client.post(
        "/v1/customer/availability", json=query, headers=ka_headers(world)
    )
    assert any(s["start_at"] == time1 for s in avail_res3.json()["slots"]), (
        f"Cancelled slot {time1} must become available again on KA Nails"
    )

    # --- Part B: Rescheduling releases old slot and occupies new slot ---
    # Find two available slots: slot_a and slot_b
    avail_res4 = await app_client.post(
        "/v1/customer/availability", json=query, headers=ka_headers(world)
    )
    available_now = avail_res4.json()["slots"]
    assert len(available_now) >= 2, "Need at least 2 slots for reschedule test"
    slot_a = available_now[0]
    dt_a = datetime.fromisoformat(slot_a["start_at"])
    disjoint_slots = [
        s
        for s in available_now
        if abs((datetime.fromisoformat(s["start_at"]) - dt_a).total_seconds()) >= 5400
    ]
    assert disjoint_slots, "Need at least one non-overlapping slot for reschedule test"
    slot_b = disjoint_slots[0]

    # Book slot_a
    booking2_id = await book_slot(app_client, world, slot_a, "Reschedule Test Guest")

    # Staff reschedules booking from slot_a to slot_b in GORGONA
    resched_res = await app_client.post(
        f"/v1/salons/{salon_id}/bookings/{booking2_id}/reschedule",
        json={
            "new_starts_at": slot_b["start_at"],
            "new_resource_id": slot_b["resource_id"],
        },
        headers=staff_headers,
    )
    assert resched_res.status_code == 200, resched_res.text
    new_booking_data = resched_res.json()
    assert datetime.fromisoformat(new_booking_data["starts_at"]) == datetime.fromisoformat(
        slot_b["start_at"]
    )
    assert new_booking_data["status"] == "CONFIRMED"

    # KA Nails availability check: slot_a should now be AVAILABLE, slot_b should now be OCCUPIED
    avail_res5 = await app_client.post(
        "/v1/customer/availability", json=query, headers=ka_headers(world)
    )
    current_slots = {s["start_at"] for s in avail_res5.json()["slots"]}
    assert slot_a["start_at"] in current_slots, "Old slot_a must be released after reschedule"
    assert slot_b["start_at"] not in current_slots, "New slot_b must be occupied after reschedule"


# ==============================================================================
# TEST 6: Strict Tenant Isolation
# ==============================================================================
async def test_sync_6_strict_tenant_isolation(
    app_client: httpx.AsyncClient,
    world: BookingWorld,
    manager_a: FakeUser,
    manager_b: FakeUser,
    idp: FakeIdp,
) -> None:
    salon_a = world.a.tenant_id
    salon_b = world.b.tenant_id
    headers_a = idp.bearer(manager_a.subject, email=manager_a.email)
    headers_b = idp.bearer(manager_b.subject, email=manager_b.email)

    # 1. Customer on KA Nails host (world.a.host) only gets Salon A catalog & staff
    ka_boot = await app_client.get("/v1/customer/bootstrap", headers=ka_headers(world))
    assert ka_boot.status_code == 200
    boot_data = ka_boot.json()
    tenant_a_variant_ids = {
        str(world.catalog_a.base_variant_id),
        str(world.catalog_a.gel_variant_id),
    }
    assert all(v["id"] in tenant_a_variant_ids for v in boot_data["variants"])
    # Ensure zero Tenant B catalog variants appear in KA Nails
    assert str(world.catalog_b.base_variant_id) not in [v["id"] for v in boot_data["variants"]]
    assert str(world.catalog_b.gel_variant_id) not in [v["id"] for v in boot_data["variants"]]

    # 2. Staff isolation: Manager A cannot read or write Tenant B
    assert (
        await app_client.get(f"/v1/salons/{salon_b}/overview", headers=headers_a)
    ).status_code == 403
    assert (
        await app_client.get(f"/v1/salons/{salon_b}/bookings", headers=headers_a)
    ).status_code == 403
    assert (
        await app_client.get(f"/v1/salons/{salon_b}/clients", headers=headers_a)
    ).status_code == 403
    assert (
        await app_client.get(f"/v1/salons/{salon_b}/staff", headers=headers_a)
    ).status_code == 403
    assert (
        await app_client.get(f"/v1/salons/{salon_b}/services", headers=headers_a)
    ).status_code == 403

    # 3. Manager B cannot read Tenant A
    assert (
        await app_client.get(f"/v1/salons/{salon_a}/overview", headers=headers_b)
    ).status_code == 403

    # 4. Cross-tenant booking attempt: KA Nails customer cannot hold or book Tenant B variant
    cross_tenant_selection = {
        "location_id": str(world.a.location_id),
        "variant_id": str(world.catalog_b.base_variant_id),  # Belongs to Tenant B!
        "add_on_ids": [],
        "resource_id": str(world.artist_a1),
        "start_at": "2026-10-05T10:00:00Z",
    }
    cross_hold = await app_client.post(
        "/v1/customer/holds",
        json=cross_tenant_selection,
        headers={
            **ka_headers(world),
            "Booking-Token": secrets.token_urlsafe(32),
            "Idempotency-Key": "cross-tenant-hold",
        },
    )
    # The platform refuses with 404 (variant not found for this tenant)
    assert cross_hold.status_code == 404, cross_hold.text
