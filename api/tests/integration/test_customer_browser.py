"""Real Chromium + exported web + FastAPI + disposable PostgreSQL 18. No mocked API."""

import asyncio
import os
import selectors
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import psycopg
import pytest
import uvicorn
from pydantic import SecretStr

from gorgona_booking.api.app import create_app
from gorgona_booking.config import Settings
from gorgona_booking.db.provisioning import owner_tenant_transaction
from gorgona_booking.tenancy.embedding import add_embed_origin
from tests.integration.booking_support import BookingWorld
from tests.integration.conftest import ProvisionedDatabase
from tests.integration.customer_support import customer_day, seed_customer_setup


def test_real_customer_browser(
    world: BookingWorld, owner_conn: psycopg.Connection, test_database: ProvisionedDatabase
) -> None:
    if os.environ.get("GBA_REQUIRE_BROWSER") != "1":
        pytest.skip("BLOCKED: browser verification requires built web and GBA_REQUIRE_BROWSER=1")
    web = Path(__file__).resolve().parents[3] / "web"
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    assert npm is not None
    seed_customer_setup(owner_conn, world)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        approved_embed_port = sock.getsockname()[1]
    host = f"127.0.0.1:{port}"
    # The governed allowlist approves exactly one FAKE embedder origin for tenant A.
    add_embed_origin(
        owner_conn,
        world.a.tenant_id,
        f"http://127.0.0.1:{approved_embed_port}",
        actor="test:browser-bridge",
    )
    with owner_tenant_transaction(owner_conn, world.a.tenant_id):
        owner_conn.execute(
            "insert into gba.tenant_hosts (host, tenant_id) values (%s, %s)",
            (host, world.a.tenant_id),
        )
    with owner_tenant_transaction(owner_conn, world.b.tenant_id):
        owner_conn.execute(
            "insert into gba.tenant_hosts (host, tenant_id) values (%s, %s)",
            (f"localhost:{port}", world.b.tenant_id),
        )
        owner_conn.execute(
            "update gba.tenants set booking_state = 'not_live' where id = %s", (world.b.tenant_id,)
        )
    app = create_app(
        Settings(
            environment="test",
            database_url=SecretStr(test_database.app_dsn),
            customer_web_dir=web / "out",
        )
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
            proxy_headers=False,
        )
    )

    def run_server() -> None:
        if os.name == "nt":
            asyncio.run(
                server.serve(),
                loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
            )
        else:
            asyncio.run(server.serve())

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started, "local browser fixture server did not start"
        env = {k: v for k, v in os.environ.items() if "DSN" not in k and "DATABASE_URL" not in k}
        env.update(
            GBA_BROWSER_URL=f"http://{host}",
            GBA_BROWSER_DAY=customer_day(),
            GBA_APPROVED_EMBED_PORT=str(approved_embed_port),
        )
        result = subprocess.run(  # noqa: S603 - fixed repository script, located npm executable
            [npm, "run", "test:e2e"],
            cwd=web,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout)  # noqa: T201 - surface real browser evidence under pytest -s
        with owner_tenant_transaction(owner_conn, world.a.tenant_id):
            rows = owner_conn.execute(
                "select c.customer_name, count(*) from gba.bookings b "
                "join gba.booking_customers c on c.tenant_id = b.tenant_id and c.booking_id = b.id "
                "where b.status = 'CONFIRMED' group by c.customer_name"
            ).fetchall()
        assert dict(rows) == {"FAKE Browser Customer": 2, "FAKE Retry Guest": 2}
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive(), "browser fixture did not shut down"
