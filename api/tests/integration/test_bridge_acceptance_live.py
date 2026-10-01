"""The bridge acceptance runner against the real app and PostgreSQL 18 (loopback).

Two FAKE salons seeded exactly as staging seeds them (`seed-fake`), one with an approved
embed origin, served by the real ASGI app. Proves the runner's booking, framing and
tenant-isolation gates measure the real application, not only a simulation. Front Door,
WAF, Private Link and Azure configuration gates need the deployed environment.
"""

from pathlib import Path

import psycopg
from pydantic import SecretStr
from tools import bridge_acceptance as ba

from gorgona_booking.api.app import create_app
from gorgona_booking.config import Settings
from gorgona_booking.onboarding.fake_seed import seed_fake_salon
from gorgona_booking.tenancy.embedding import add_embed_origin
from tests.integration.conftest import ProvisionedDatabase
from tests.integration.customer_support import customer_day
from tests.integration.live_server import free_port, live_server

KA = "https://ka-nails.example.test"


def test_runner_gates_against_the_real_application(
    owner_conn: psycopg.Connection, test_database: ProvisionedDatabase, tmp_path: Path
) -> None:
    port = free_port()
    first = seed_fake_salon(
        owner_conn, slug="fake-bridge-a", host=f"127.0.0.1:{port}", environment="test", actor="t"
    )
    seed_fake_salon(
        owner_conn, slug="fake-bridge-b", host=f"localhost:{port}", environment="test", actor="t"
    )
    add_embed_origin(owner_conn, first.tenant_id, KA, actor="t")
    (tmp_path / "book").mkdir()
    (tmp_path / "book" / "index.html").write_text("<!doctype html><title>FAKE</title>", "utf-8")
    app = create_app(
        Settings(
            environment="test",
            database_url=SecretStr(test_database.app_dsn),
            customer_web_dir=tmp_path,
        )
    )
    args = ba.parse_args(
        [
            "http",
            *("--front-door-url", f"http://127.0.0.1:{port}"),
            *("--second-tenant-url", f"http://localhost:{port}"),
            *("--origin-fqdn", "127.0.0.1:1", "--postgres-fqdn", "db.invalid"),
            *("--authorized-origin", KA, "--day", customer_day()),
            *("--image-digest", "sha256:local", "--timeout", "5"),
        ]
    )
    with live_server(app, port):
        doc = ba.run_http(args, pg_connect=lambda host, timeout: "dns: gaierror")
    summary = ba.merge([doc])["summary"]
    for gate in (
        "azure_e2e_booking",
        "csp_frame_ancestors",
        "authorized_ka_origin",
        "tenant_isolation",
        "direct_origin_bypass",
        "private_postgresql",
    ):
        assert summary[gate] in ("pass", "blocked"), (gate, doc["gates"][gate])
    for gate in ("azure_e2e_booking", "csp_frame_ancestors", "authorized_ka_origin"):
        assert summary[gate] == "pass", (gate, doc["gates"][gate])
    checks = {c["check"]: c["ok"] for c in doc["gates"]["tenant_isolation"]}
    assert checks == {"tenants_resolve_separately": True, "cross_tenant_booking_refused": True}
    # Without Front Door in front, the WAF gate cannot pass: the app itself never 403s.
    assert summary["waf_rate_limiting"] in ("fail", "blocked")
