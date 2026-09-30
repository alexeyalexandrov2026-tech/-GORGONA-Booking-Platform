"""Trusted Front Door host boundary (ADR-0012, AZURE_ARCHITECTURE section 4).

Front Door sends the origin FQDN as Host, overwrites X-Forwarded-Host with the host the
client asked for, and adds X-Azure-FDID. Only in the explicit trusted mode, and only
with the configured profile ID, may X-Forwarded-Host become the effective Host.
"""

import httpx
import pytest
from fastapi import FastAPI, Request
from pydantic import ValidationError

from gorgona_booking.api.app import create_app
from gorgona_booking.config import Settings

pytestmark = pytest.mark.anyio

FDID = "a0a0a0a0-bbbb-cccc-dddd-e1e1e1e1e1e1"
ORIGIN = "ca-gorgona-staging-api.example.azurecontainerapps.io"


def _app(settings: Settings) -> FastAPI:
    app = create_app(settings)

    @app.get("/echo")
    async def echo(request: Request) -> dict[str, str]:
        return {"host": request.headers["host"], "scheme": request.url.scheme}

    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url=f"http://{ORIGIN}")


def _front_door() -> Settings:
    return Settings(environment="test", trusted_proxy="azure_front_door", front_door_id=FDID)


def test_settings_default_trusts_no_proxy() -> None:
    settings = Settings.from_env({})
    assert settings.trusted_proxy == "none"
    assert settings.front_door_id is None


def test_settings_read_front_door_mode_from_env() -> None:
    settings = Settings.from_env(
        {"GBA_TRUSTED_PROXY": "azure_front_door", "GBA_FRONT_DOOR_ID": FDID.upper()}
    )
    assert settings.trusted_proxy == "azure_front_door"
    assert settings.front_door_id == FDID


@pytest.mark.parametrize(
    "environ",
    [
        {"GBA_TRUSTED_PROXY": "azure_front_door"},
        {"GBA_TRUSTED_PROXY": "azure_front_door", "GBA_FRONT_DOOR_ID": "not-a-uuid"},
        {"GBA_TRUSTED_PROXY": "cloudflare", "GBA_FRONT_DOOR_ID": FDID},
        {"GBA_FRONT_DOOR_ID": FDID},
    ],
)
def test_incomplete_or_unknown_proxy_settings_are_rejected(environ: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        Settings.from_env(environ)


async def test_default_mode_ignores_forwarded_headers() -> None:
    async with _client(_app(Settings(environment="test"))) as client:
        response = await client.get(
            "/echo",
            headers={"x-forwarded-host": "victim.test", "x-azure-fdid": FDID},
        )
    assert response.json() == {"host": ORIGIN, "scheme": "http"}


async def test_front_door_mode_uses_forwarded_host_only_with_the_profile_id() -> None:
    async with _client(_app(_front_door())) as client:
        response = await client.get(
            "/echo",
            headers={
                "x-azure-fdid": FDID,
                "x-forwarded-host": "Booking.Salon-A.test",
                "x-forwarded-proto": "https",
            },
        )
    assert response.status_code == 200
    assert response.json() == {"host": "booking.salon-a.test", "scheme": "https"}


@pytest.mark.parametrize(
    "headers",
    [
        {"x-forwarded-host": "salon-a.test"},
        {"x-azure-fdid": "b0b0b0b0-bbbb-cccc-dddd-e1e1e1e1e1e1", "x-forwarded-host": "a.test"},
        {"x-azure-fdid": FDID},
        {"x-azure-fdid": FDID, "x-forwarded-host": "a.test, b.test"},
        {"x-azure-fdid": FDID, "x-forwarded-host": "a.test/evil"},
        {"x-azure-fdid": FDID, "x-forwarded-host": "a.test@b.test"},
        {"x-azure-fdid": FDID, "x-forwarded-host": ""},
    ],
    ids=[
        "no-profile-id",
        "wrong-profile-id",
        "no-forwarded-host",
        "two-forwarded-hosts",
        "path-in-host",
        "userinfo-in-host",
        "empty-forwarded-host",
    ],
)
async def test_front_door_mode_rejects_anything_not_from_our_profile(
    headers: dict[str, str],
) -> None:
    async with _client(_app(_front_door())) as client:
        response = await client.get("/echo", headers=headers)
    assert response.status_code == 404
    body = response.json()["error"]
    assert body["code"] == "TENANT_NOT_FOUND"
    assert body["request_id"] == response.headers["x-request-id"]
    assert "a.test" not in response.text


async def test_front_door_mode_rejects_duplicate_profile_id_headers() -> None:
    async with _client(_app(_front_door())) as client:
        response = await client.get(
            "/echo",
            headers=[
                ("x-azure-fdid", FDID),
                ("x-azure-fdid", FDID),
                ("x-forwarded-host", "salon-a.test"),
            ],
        )
    assert response.status_code == 404


async def test_health_probes_are_exempt_in_front_door_mode() -> None:
    async with _client(_app(_front_door())) as client:
        assert (await client.get("/health/live")).status_code == 200
        assert (await client.get("/health/ready")).status_code == 503  # no database here
