"""Real OIDC code/PKCE + Chromium + management API + disposable PostgreSQL.

The identity provider is explicitly FAKE and exists only in this test fixture.
Its discovery, authorize, token, JWKS and logout requests are real loopback HTTP.
No login callback, token or management API response is mocked in the browser.
"""

import base64
import hashlib
import hmac
import os
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode
from uuid import uuid7

import psycopg
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import SecretStr

from gorgona_booking.api.app import create_app
from gorgona_booking.auth.verifier import OidcJwtVerifier, StaticJwksKeySource
from gorgona_booking.config import Settings
from gorgona_booking.db.provisioning import add_membership, owner_tenant_transaction
from tests.integration.booking_support import BookingWorld
from tests.integration.conftest import ProvisionedDatabase
from tests.integration.customer_support import customer_day, seed_customer_setup
from tests.integration.live_server import free_port, live_server
from tests.integration.seed import FakeUser, seed_user
from tests.support.fake_idp import FAKE_AUDIENCE, FakeIdp

CLIENT_ID = "gba-browser-test"
GUEST = "FAKE Management Browser Guest"


@dataclass(frozen=True, slots=True)
class AuthorizationCode:
    challenge: str
    nonce: str
    invalid_nonce: bool


def _provider(
    issuer: str, app_origin: str, idp: FakeIdp, user: FakeUser
) -> tuple[FastAPI, OidcJwtVerifier]:
    provider = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    provider.add_middleware(
        CORSMiddleware,
        allow_origins=[app_origin],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )
    verifier = OidcJwtVerifier(
        issuer=issuer, audience=FAKE_AUDIENCE,
        key_source=StaticJwksKeySource(idp.jwks), leeway_seconds=0,
    )
    redirect_uri = f"{app_origin}/auth/callback/"
    codes: dict[str, AuthorizationCode] = {}

    @provider.get("/.well-known/openid-configuration")
    async def discovery() -> dict[str, object]:
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/authorize",
            "token_endpoint": f"{issuer}/token",
            "jwks_uri": f"{issuer}/jwks",
            "userinfo_endpoint": f"{issuer}/userinfo",
            "end_session_endpoint": f"{issuer}/logout",
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        }

    @provider.get("/jwks")
    async def jwks() -> dict[str, object]:
        return idp.jwks

    @provider.get("/authorize")
    async def authorize(request: Request) -> RedirectResponse:
        query = request.query_params
        if (
            query.get("client_id") != CLIENT_ID
            or query.get("redirect_uri") != redirect_uri
            or query.get("response_type") != "code"
            or query.get("code_challenge_method") != "S256"
            or not query.get("code_challenge")
            or not query.get("state")
        ):
            raise HTTPException(400, "Invalid authorization request")
        code = secrets.token_urlsafe(32)
        codes[code] = AuthorizationCode(
            query["code_challenge"], query.get("nonce", ""),
            query.get("invalid_nonce") == "1",
        )
        return RedirectResponse(
            f"{redirect_uri}?{urlencode({'code': code, 'state': query['state']})}", status_code=302
        )

    @provider.post("/token")
    async def token(request: Request) -> JSONResponse:
        body = await request.body()
        if len(body) > 8192:
            raise HTTPException(400, "Invalid token request")
        values = parse_qs(body.decode("utf-8"))

        def value(name: str) -> str:
            entries = values.get(name, [])
            return entries[0] if len(entries) == 1 else ""

        record = codes.pop(value("code"), None)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(value("code_verifier").encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        if (
            value("grant_type") != "authorization_code"
            or value("client_id") != CLIENT_ID
            or value("redirect_uri") != redirect_uri
            or record is None
            or not hmac.compare_digest(record.challenge, challenge)
        ):
            raise HTTPException(400, "Invalid code or PKCE verifier")
        access_token = idp.token(
            user.subject,
            email=user.email,
            iss=issuer,
            name="FAKE Management Browser Manager",
        )
        id_token = idp.token(
            user.subject,
            email=user.email,
            iss=issuer,
            name="FAKE Management Browser Manager",
            aud=CLIENT_ID,
            nonce="invalid-nonce" if record.invalid_nonce else record.nonce,
        )
        return JSONResponse(
            {"access_token": access_token, "token_type": "Bearer", "expires_in": 300,
             "id_token": id_token, "scope": "openid profile email"},
            headers={"Cache-Control": "no-store"},
        )

    @provider.get("/userinfo")
    async def userinfo(request: Request) -> dict[str, object]:
        scheme, _, credential = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer":
            raise HTTPException(401, "Authentication required")
        verified = await verifier.verify(credential)
        return {"sub": verified.subject, "email": user.email,
                "email_verified": True, "name": "FAKE Management Browser Manager"}

    @provider.get("/logout")
    async def logout(request: Request) -> RedirectResponse:
        destination = request.query_params.get("post_logout_redirect_uri", "")
        if destination not in (f"{app_origin}/", f"{app_origin}/overview/"):
            raise HTTPException(400, "Invalid logout redirect")
        return RedirectResponse(destination, status_code=302)

    return provider, verifier


def test_real_management_browser_oidc_pkce_and_database(
    world: BookingWorld, owner_conn: psycopg.Connection, test_database: ProvisionedDatabase
) -> None:
    if os.environ.get("GBA_REQUIRE_BROWSER") != "1":
        pytest.skip("BLOCKED: management browser verification requires GBA_REQUIRE_BROWSER=1")
    web = Path(__file__).resolve().parents[3] / "web"
    assert (web / "out" / "auth" / "callback" / "index.html").exists(), "build web first"
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    assert npm is not None
    seed_customer_setup(owner_conn, world)
    user = seed_user(owner_conn, f"browser-manager-{uuid7()}")
    add_membership(owner_conn, tenant_id=world.a.tenant_id, user_id=user.user_id, role="manager")
    app_port, idp_port = free_port(), free_port()
    app_origin, issuer = f"http://127.0.0.1:{app_port}", f"http://127.0.0.1:{idp_port}"
    idp = FakeIdp()
    provider, verifier = _provider(issuer, app_origin, idp, user)
    app = create_app(
        Settings(environment="test", database_url=SecretStr(test_database.app_dsn)),
        token_verifier=verifier,
    )

    @app.get("/auth-config.json")
    async def auth_config() -> JSONResponse:
        return JSONResponse(
            {"authority": issuer, "client_id": CLIENT_ID, "scope": "openid profile email",
             "resource": FAKE_AUDIENCE}, headers={"Cache-Control": "no-store"},
        )

    # Test-only runtime route precedes the actual static export; APIs remain unchanged.
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=web / "out", html=True), name="management-test-web")
    blocked_keys = ("DSN", "DATABASE_URL", "SECRET", "PASSWORD", "TOKEN")
    env = {
        k: v for k, v in os.environ.items()
        if not any(part in k.upper() for part in blocked_keys)
    }
    env.update(
        GBA_MGMT_BROWSER_URL=app_origin,
        GBA_MGMT_BROWSER_DAY=customer_day(),
        GBA_TEST_OIDC_ISSUER=issuer,
    )
    with live_server(provider, idp_port), live_server(app, app_port):
        result = subprocess.run(  # noqa: S603 - fixed repository script and located npm executable
            [npm, "run", "test:management"], cwd=web, env=env,
            capture_output=True, text=True, timeout=180, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout)  # noqa: T201 - surface browser evidence under pytest -s
    with owner_tenant_transaction(owner_conn, world.a.tenant_id):
        rows = owner_conn.execute(
            "select b.status, count(*) from gba.bookings b join gba.booking_customers c "
            "on c.tenant_id = b.tenant_id and c.booking_id = b.id "
            "where c.customer_name = %s group by b.status", (GUEST,),
        ).fetchall()
    assert dict(rows) == {"CANCELLED": 2}, (
        "create -> reschedule -> cancel must persist in PostgreSQL"
    )
    with owner_tenant_transaction(owner_conn, world.b.tenant_id):
        count = owner_conn.execute("select count(*) from gba.bookings").fetchone()
    assert count is not None
    assert count[0] == 0
