"""Optional static export must not become a dependency of the API."""

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


async def test_api_only_mode_has_no_customer_web_route(
    world: BookingWorld, owner_conn: psycopg.Connection, app_pool: RuntimePool
) -> None:
    assert Settings(environment="test").customer_web_dir is None
    seed_customer_setup(owner_conn, world)
    app = create_app(Settings(environment="test"), pool=app_pool)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=f"http://{world.a.host}"
    ) as client:
        assert (await client.get("/book/")).status_code == 404
        response = await client.get("/v1/customer/bootstrap")
        assert response.status_code == 200
        assert response.json()["version"] == 1


async def test_optional_export_does_not_shadow_host_resolved_api(
    world: BookingWorld, owner_conn: psycopg.Connection, app_pool: RuntimePool, tmp_path: Path
) -> None:
    seed_customer_setup(owner_conn, world)
    book = tmp_path / "book"
    book.mkdir()
    (book / "index.html").write_text("<h1>Reusable booking export</h1>", encoding="utf-8")
    app = create_app(Settings(environment="test", customer_web_dir=tmp_path), pool=app_pool)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=f"http://{world.a.host}"
    ) as client:
        response = await client.get("/book/")
        assert response.status_code == 200
        assert "Reusable booking export" in response.text
        bootstrap = await client.get("/v1/customer/bootstrap")
        assert bootstrap.status_code == 200
        assert bootstrap.headers["cache-control"] == "no-store"
        assert bootstrap.json()["version"] == 1
        unknown = await client.get(
            "/v1/customer/bootstrap", headers={"Host": "unknown.example.test"}
        )
        assert unknown.status_code == 404
        assert unknown.json()["error"]["code"] == "TENANT_NOT_FOUND"
