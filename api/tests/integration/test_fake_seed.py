"""The staging FAKE seed against real PostgreSQL 18: refuses unsafe targets, is
idempotent, and yields a live salon whose public booking flow works end to end."""

import secrets
from collections.abc import AsyncIterator

import httpx
import psycopg
import pytest

from gorgona_booking.api.app import create_app
from gorgona_booking.config import Settings
from gorgona_booking.db.pool import RuntimePool
from gorgona_booking.onboarding.fake_seed import FakeSeedRefusedError, seed_fake_salon
from tests.integration.customer_support import customer_day

pytestmark = pytest.mark.anyio


def _slug() -> str:
    return f"fake-stg-{secrets.token_hex(4)}"


@pytest.fixture
async def client(app_pool: RuntimePool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(environment="test"), pool=app_pool)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as http:
        yield http


@pytest.mark.parametrize(
    ("slug", "environment"), [("fake-ok", "production"), ("real-salon", "staging")]
)
def test_seed_refuses_production_and_non_fake_slugs(
    owner_conn: psycopg.Connection, slug: str, environment: str
) -> None:
    with pytest.raises(FakeSeedRefusedError):
        seed_fake_salon(
            owner_conn, slug=slug, host=f"{slug}.test", environment=environment, actor="test"
        )
    assert (
        owner_conn.execute("select 1 from gba.tenants where slug = %s", (slug,)).fetchone() is None
    )


async def test_seeded_salon_is_live_idempotent_and_bookable(
    owner_conn: psycopg.Connection, client: httpx.AsyncClient
) -> None:
    slug = _slug()
    host = f"{slug}.test"
    first = seed_fake_salon(owner_conn, slug=slug, host=host, environment="staging", actor="test")
    second = seed_fake_salon(owner_conn, slug=slug, host=host, environment="staging", actor="test")
    assert first == second
    assert first.live is True

    headers = {"Host": host}
    boot = await client.get("/v1/customer/bootstrap", headers=headers)
    assert boot.status_code == 200, boot.text
    data = boot.json()
    assert {v["name"] for v in data["variants"]} == {
        "FAKE manicure (standard)",
        "FAKE pedicure (standard)",
    }
    assert len(data["artists"]) == 2
    query = {
        "location_id": data["locations"][0]["id"],
        "variant_id": data["variants"][0]["id"],
        "add_on_ids": [],
        "day": customer_day(),
        "resource_id": None,
    }
    slots = await client.post("/v1/customer/availability", json=query, headers=headers)
    assert slots.status_code == 200, slots.text
    slot = slots.json()["slots"][0]
    token = secrets.token_urlsafe(32)
    body = {k: v for k, v in query.items() if k != "day"}
    body.update({"resource_id": slot["resource_id"], "start_at": slot["start_at"]})
    held = await client.post(
        "/v1/customer/holds",
        json=body,
        headers={**headers, "Booking-Token": token, "Idempotency-Key": f"{slug}-hold"},
    )
    assert held.status_code == 201, held.text
    confirmed = await client.post(
        f"/v1/customer/bookings/{held.json()['booking_id']}/confirm",
        json={
            "name": "FAKE Customer",
            "email": "fake@example.test",
            "phone": "+1 555 010 1234",
            "accept_policy": True,
        },
        headers={**headers, "Booking-Token": token, "Idempotency-Key": f"{slug}-confirm"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "CONFIRMED"
