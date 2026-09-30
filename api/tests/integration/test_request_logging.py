"""Request logs carry the resolved tenant UUID (never its host or name), real PostgreSQL."""

import logging

import httpx
import psycopg
import pytest

from gorgona_booking.api.app import create_app
from gorgona_booking.config import Settings
from gorgona_booking.db.pool import RuntimePool
from tests.integration.booking_support import BookingWorld
from tests.integration.customer_support import seed_customer_setup

pytestmark = pytest.mark.anyio


async def test_access_log_records_tenant_uuid_only(
    world: BookingWorld,
    owner_conn: psycopg.Connection,
    app_pool: RuntimePool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    seed_customer_setup(owner_conn, world)
    caplog.set_level(logging.INFO, logger="gorgona_booking.access")
    app = create_app(Settings(environment="test"), pool=app_pool)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=f"http://{world.a.host}"
    ) as client:
        assert (await client.get("/v1/customer/bootstrap")).status_code == 200
    [record] = [r for r in caplog.records if r.name == "gorgona_booking.access"]
    assert getattr(record, "tenant_id", None) == str(world.a.tenant_id)
    assert getattr(record, "operation", None) == "GET /v1/customer/bootstrap"
    assert world.a.host not in record.getMessage()
