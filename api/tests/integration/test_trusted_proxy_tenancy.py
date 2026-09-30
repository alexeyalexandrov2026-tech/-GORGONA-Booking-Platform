"""Host-resolved tenancy behind Azure Front Door, against real PostgreSQL 18.

The effective tenant host is X-Forwarded-Host only when X-Azure-FDID matches the
configured profile; otherwise requests are refused. The default mode keeps the M1
behaviour (Host only, forwarded headers ignored).
"""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import psycopg
import pytest

from gorgona_booking.api.app import create_app
from gorgona_booking.config import Settings
from gorgona_booking.db.pool import RuntimePool
from tests.integration.booking_support import BookingWorld
from tests.integration.customer_support import seed_customer_setup

pytestmark = pytest.mark.anyio

FDID = "a0a0a0a0-bbbb-cccc-dddd-e1e1e1e1e1e1"
ORIGIN = "ca-gorgona-staging-api.example.azurecontainerapps.io"


@pytest.fixture
async def front_door_client(
    world: BookingWorld, owner_conn: psycopg.Connection, app_pool: RuntimePool, tmp_path: Path
) -> AsyncIterator[httpx.AsyncClient]:
    seed_customer_setup(owner_conn, world)
    (tmp_path / "book").mkdir()
    (tmp_path / "book" / "index.html").write_text("<h1>export</h1>", encoding="utf-8")
    settings = Settings(
        environment="test",
        trusted_proxy="azure_front_door",
        front_door_id=FDID,
        customer_web_dir=tmp_path,
    )
    app = create_app(settings, pool=app_pool)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=f"http://{ORIGIN}"
    ) as client:
        yield client


def via_front_door(host: str) -> dict[str, str]:
    return {"x-azure-fdid": FDID, "x-forwarded-host": host, "x-forwarded-proto": "https"}


async def test_forwarded_host_selects_each_tenant_without_leaking(
    front_door_client: httpx.AsyncClient, world: BookingWorld
) -> None:
    a = await front_door_client.get("/v1/customer/bootstrap", headers=via_front_door(world.a.host))
    b = await front_door_client.get("/v1/customer/bootstrap", headers=via_front_door(world.b.host))
    assert a.status_code == 200, a.text
    assert b.status_code == 200, b.text
    assert a.json()["name"] == "FAKE salon A"
    assert b.json()["name"] == "FAKE salon B"
    assert {v["id"] for v in a.json()["variants"]}.isdisjoint(
        {v["id"] for v in b.json()["variants"]}
    )


async def test_spoofed_host_or_forwarded_host_without_our_profile_is_refused(
    front_door_client: httpx.AsyncClient, world: BookingWorld
) -> None:
    spoofed_host = await front_door_client.get(
        "/v1/customer/bootstrap", headers={"host": world.a.host}
    )
    spoofed_forward = await front_door_client.get(
        "/v1/customer/bootstrap",
        headers={"x-forwarded-host": world.a.host, "x-azure-fdid": FDID.replace("a", "c")},
    )
    unknown = await front_door_client.get(
        "/v1/customer/bootstrap", headers=via_front_door("unknown.example.test")
    )
    for response in (spoofed_host, spoofed_forward, unknown):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "TENANT_NOT_FOUND"
        assert "FAKE salon" not in response.text


async def test_body_query_and_header_tenant_ids_still_cannot_override(
    front_door_client: httpx.AsyncClient, world: BookingWorld
) -> None:
    response = await front_door_client.get(
        f"/v1/customer/bootstrap?tenant_id={world.b.tenant_id}",
        headers={**via_front_door(world.a.host), "x-tenant-id": str(world.b.tenant_id)},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "FAKE salon A"


async def test_static_redirect_keeps_the_tenant_host_and_https(
    front_door_client: httpx.AsyncClient, world: BookingWorld
) -> None:
    response = await front_door_client.get("/book", headers=via_front_door(world.a.host))
    assert response.status_code in (307, 308)
    assert response.headers["location"] == f"https://{world.a.host}/book/"


async def test_default_mode_resolves_by_host_and_ignores_forwarded_host(
    world: BookingWorld, owner_conn: psycopg.Connection, app_pool: RuntimePool
) -> None:
    seed_customer_setup(owner_conn, world)
    app = create_app(Settings(environment="test"), pool=app_pool)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=f"http://{world.a.host}"
    ) as client:
        response = await client.get("/v1/customer/bootstrap", headers=via_front_door(world.b.host))
    assert response.status_code == 200
    assert response.json()["name"] == "FAKE salon A"
