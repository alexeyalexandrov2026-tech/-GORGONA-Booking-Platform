"""Governed per-tenant embedding allowlist and the HTTP framing policy (ADR-0012).

Real PostgreSQL 18. The allowlist lives in gba.tenant_embed_origins (migration 0007).
It is managed with the owner credential, audited, and read-only to the runtime role.
Customer-web HTML carries Content-Security-Policy frame-ancestors for its own tenant only.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import httpx
import psycopg
import pytest

from gorgona_booking.api.app import create_app
from gorgona_booking.config import Settings
from gorgona_booking.db import cli
from gorgona_booking.db.pool import RuntimePool, tenant_transaction
from gorgona_booking.db.provisioning import owner_tenant_transaction
from gorgona_booking.onboarding.service import stable_id
from gorgona_booking.tenancy.embedding import (
    InvalidEmbedOriginError,
    add_embed_origin,
    approved_embed_origins,
    list_embed_origins,
    revoke_embed_origin,
)
from tests.integration.booking_support import BookingWorld
from tests.integration.conftest import ProvisionedDatabase
from tests.integration.customer_support import seed_customer_setup

pytestmark = pytest.mark.anyio

A_SITE = "https://salon-a-site.example.test"
B_SITE = "https://salon-b-site.example.test"


@pytest.mark.parametrize(
    "origin",
    [
        "http://salon.example.test",
        "https://salon.example.test/book/",
        "https://*.example.test",
        "https://user@salon.example.test",
        "javascript:alert(1)",
        "https://salon.example.test?x=1",
        "'self'",
        "https://salon.example.test https://evil.test",
    ],
)
def test_database_rejects_non_origin_values(
    world: BookingWorld, owner_conn: psycopg.Connection, origin: str
) -> None:
    with (
        pytest.raises(psycopg.errors.CheckViolation),
        owner_tenant_transaction(owner_conn, world.a.tenant_id),
    ):
        owner_conn.execute(
            "insert into gba.tenant_embed_origins (tenant_id, origin) values (%s, %s)",
            (world.a.tenant_id, origin),
        )


@pytest.mark.parametrize("origin", ["https://Salon.Example.test/", "ftp://x.test", ""])
def test_service_normalises_or_rejects_before_the_database(
    world: BookingWorld, owner_conn: psycopg.Connection, origin: str
) -> None:
    if origin.startswith("https://"):
        add_embed_origin(owner_conn, world.a.tenant_id, origin, actor="operator:test")
        assert list_embed_origins(owner_conn, world.a.tenant_id) == [
            ("https://salon.example.test", "approved")
        ]
    else:
        with pytest.raises(InvalidEmbedOriginError):
            add_embed_origin(owner_conn, world.a.tenant_id, origin, actor="operator:test")


async def test_runtime_role_reads_own_tenant_only_and_cannot_write(
    world: BookingWorld, owner_conn: psycopg.Connection, app_pool: RuntimePool
) -> None:
    add_embed_origin(owner_conn, world.a.tenant_id, A_SITE, actor="operator:test")
    add_embed_origin(owner_conn, world.b.tenant_id, B_SITE, actor="operator:test")
    async with tenant_transaction(app_pool, world.a.tenant_id) as conn:
        rows = await (await conn.execute("select origin from gba.tenant_embed_origins")).fetchall()
    assert rows == [(A_SITE,)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with tenant_transaction(app_pool, world.a.tenant_id) as conn:
            await conn.execute(
                "insert into gba.tenant_embed_origins (tenant_id, origin) values (%s, %s)",
                (world.a.tenant_id, "https://attacker.example.test"),
            )


def test_add_and_revoke_are_audited_and_revoke_keeps_history(
    world: BookingWorld, owner_conn: psycopg.Connection
) -> None:
    add_embed_origin(owner_conn, world.a.tenant_id, A_SITE, actor="operator:alice")
    revoke_embed_origin(owner_conn, world.a.tenant_id, A_SITE, actor="operator:bob")
    assert list_embed_origins(owner_conn, world.a.tenant_id) == [(A_SITE, "revoked")]
    with owner_tenant_transaction(owner_conn, world.a.tenant_id):
        events = owner_conn.execute(
            "select action, actor from gba.audit_events "
            "where target_type = 'embed_origin' order by occurred_at, id"
        ).fetchall()
    assert events == [
        ("embed_origin.created", "operator:alice"),
        ("embed_origin.updated", "operator:bob"),
    ]


def test_cli_manages_origins_for_an_onboarded_slug(
    owner_conn: psycopg.Connection,
    test_database: ProvisionedDatabase,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    slug = "fake-embed-cli"
    tenant_id: UUID = stable_id(None, "tenant", slug)
    with owner_tenant_transaction(owner_conn, tenant_id):
        owner_conn.execute(
            "insert into gba.tenants (id, slug, display_name) values (%s, %s, %s) "
            "on conflict do nothing",
            (tenant_id, slug, "FAKE embed CLI salon"),
        )
    monkeypatch.setenv("GBA_MIGRATION_DATABASE_URL", test_database.owner_dsn)
    cli.main(["embed-origin", "add", slug, A_SITE])
    cli.main(["embed-origin", "list", slug])
    cli.main(["embed-origin", "revoke", slug, A_SITE])
    out = capsys.readouterr().out
    assert f"approved {A_SITE}" in out
    assert list_embed_origins(owner_conn, tenant_id) == [(A_SITE, "revoked")]


@pytest.fixture
async def web_client(
    world: BookingWorld, owner_conn: psycopg.Connection, app_pool: RuntimePool, tmp_path: Path
) -> AsyncIterator[httpx.AsyncClient]:
    seed_customer_setup(owner_conn, world)
    (tmp_path / "book").mkdir()
    (tmp_path / "book" / "index.html").write_text("<h1>export</h1>", encoding="utf-8")
    (tmp_path / "app.js").write_text("console.log(1)", encoding="utf-8")
    app = create_app(Settings(environment="test", customer_web_dir=tmp_path), pool=app_pool)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        yield client


async def test_customer_html_allows_only_its_own_approved_origins(
    web_client: httpx.AsyncClient, world: BookingWorld, owner_conn: psycopg.Connection
) -> None:
    add_embed_origin(owner_conn, world.a.tenant_id, A_SITE, actor="operator:test")
    add_embed_origin(owner_conn, world.a.tenant_id, "https://old.example.test", actor="x:test")
    revoke_embed_origin(owner_conn, world.a.tenant_id, "https://old.example.test", actor="x:test")
    add_embed_origin(owner_conn, world.b.tenant_id, B_SITE, actor="operator:test")

    a = await web_client.get(f"http://{world.a.host}/book/")
    b = await web_client.get(f"http://{world.b.host}/book/")
    assert a.status_code == b.status_code == 200
    assert a.headers["content-security-policy"] == f"frame-ancestors 'self' {A_SITE}"
    assert b.headers["content-security-policy"] == f"frame-ancestors 'self' {B_SITE}"
    assert "x-frame-options" not in a.headers


async def test_tenants_without_approvals_and_unknown_hosts_are_self_only(
    web_client: httpx.AsyncClient, world: BookingWorld, owner_conn: psycopg.Connection
) -> None:
    with owner_tenant_transaction(owner_conn, world.b.tenant_id):
        owner_conn.execute(
            "update gba.tenants set booking_state = 'not_live' where id = %s", (world.b.tenant_id,)
        )
    add_embed_origin(owner_conn, world.b.tenant_id, B_SITE, actor="operator:test")
    for host in (world.a.host, world.b.host, "unknown.example.test"):
        response = await web_client.get(f"http://{host}/book/")
        assert response.headers["content-security-policy"] == "frame-ancestors 'self'"
        assert response.headers["x-frame-options"] == "SAMEORIGIN"


async def test_api_and_asset_responses_do_not_query_or_leak_the_allowlist(
    web_client: httpx.AsyncClient, world: BookingWorld, owner_conn: psycopg.Connection
) -> None:
    add_embed_origin(owner_conn, world.a.tenant_id, A_SITE, actor="operator:test")
    api = await web_client.get(f"http://{world.a.host}/v1/customer/bootstrap")
    asset = await web_client.get(f"http://{world.a.host}/app.js")
    assert api.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert A_SITE not in str(asset.headers)


async def test_loopback_origins_are_honoured_only_in_development_environments(
    world: BookingWorld, owner_conn: psycopg.Connection, app_pool: RuntimePool
) -> None:
    seed_customer_setup(owner_conn, world)
    add_embed_origin(owner_conn, world.a.tenant_id, "http://127.0.0.1:4173", actor="x:test")
    add_embed_origin(owner_conn, world.a.tenant_id, A_SITE, actor="x:test")
    dev = await approved_embed_origins(app_pool, world.a.host, allow_loopback=True)
    deployed = await approved_embed_origins(app_pool, world.a.host, allow_loopback=False)
    assert dev == ["http://127.0.0.1:4173", A_SITE]
    assert deployed == [A_SITE]
