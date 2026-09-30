import httpx
import pytest
from fastapi import FastAPI

from gorgona_booking.api.app import create_app
from gorgona_booking.config import Settings
from gorgona_booking.errors import NotFoundError

pytestmark = pytest.mark.anyio


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_live_reports_ok_and_request_id() -> None:
    async with _client(create_app(Settings())) as client:
        response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert len(response.headers["x-request-id"]) >= 8


async def test_safe_incoming_request_id_is_echoed_and_unsafe_one_replaced() -> None:
    async with _client(create_app(Settings())) as client:
        kept = await client.get("/health/live", headers={"x-request-id": "abc12345-req"})
        replaced = await client.get("/health/live", headers={"x-request-id": "bad id\n"})
    assert kept.headers["x-request-id"] == "abc12345-req"
    assert replaced.headers["x-request-id"] != "bad id\n"


async def test_ready_is_unavailable_without_a_database() -> None:
    async with _client(create_app(Settings())) as client:
        response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "not_configured"


async def test_ready_reflects_probe_success_and_failure() -> None:
    async def ok() -> None:
        return None

    async def broken() -> None:
        raise ConnectionError("password authentication failed for user secret_user")

    async with _client(create_app(Settings(), readiness_probe=ok)) as client:
        assert (await client.get("/health/ready")).status_code == 200
    async with _client(create_app(Settings(), readiness_probe=broken)) as client:
        response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "failed"
    assert "secret_user" not in response.text


async def test_domain_and_unhandled_errors_use_the_envelope_without_leaking() -> None:
    app = create_app(Settings())

    @app.get("/boom-domain")
    async def boom_domain() -> None:
        raise NotFoundError("Booking not found", booking_id="b-1")

    @app.get("/boom-internal")
    async def boom_internal() -> None:
        raise RuntimeError("connection to 10.0.0.5 refused, password=hunter2")

    async with _client(app) as client:
        domain = await client.get("/boom-domain")
        internal = await client.get("/boom-internal")
        missing = await client.get("/no-such-route")

    assert domain.status_code == 404
    assert domain.json()["error"]["code"] == "NOT_FOUND"
    assert domain.json()["error"]["details"] == {"booking_id": "b-1"}
    assert internal.status_code == 500
    assert internal.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "hunter2" not in internal.text
    assert "10.0.0.5" not in internal.text
    assert missing.json()["error"]["code"] == "NOT_FOUND"
