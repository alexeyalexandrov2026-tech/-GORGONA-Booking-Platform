"""A tenant's independent public site embedding the hosted wizard, end to end.

The site (e.g. the separate KA-nails repository) is a black box. The platform starts
its real API and static export against disposable PostgreSQL 18 with FAKE tenants,
approves the site's origin through the governed embedding allowlist, and runs the
site's own public commands (`npm run build`, `npm run test:e2e`) with public
configuration only. Then it verifies the confirmed rows. The site never imports
platform code.

Enabled by GBA_REQUIRE_TENANT_SITE=1 and GBA_TENANT_SITE_DIR=<site checkout>.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import psycopg
import pytest
from pydantic import SecretStr

from gorgona_booking.api.app import create_app
from gorgona_booking.config import Settings
from gorgona_booking.db.provisioning import owner_tenant_transaction
from gorgona_booking.tenancy.embedding import add_embed_origin
from tests.integration.booking_support import BookingWorld
from tests.integration.conftest import ProvisionedDatabase
from tests.integration.customer_support import customer_day, seed_customer_setup
from tests.integration.live_server import free_port, live_server

WEB_OUT = Path(__file__).resolve().parents[3] / "web" / "out"
GUEST = "FAKE KA Website Guest"  # the name the site's own test types into the wizard


def _run(npm: str, site: Path, args: list[str], env: dict[str, str]) -> str:
    result = subprocess.run(  # noqa: S603 - fixed npm executable and site scripts
        [npm, *args],
        cwd=site,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output.encode("ascii", "replace").decode()
    return result.stdout


def test_tenant_site_embeds_the_real_wizard(
    world: BookingWorld, owner_conn: psycopg.Connection, test_database: ProvisionedDatabase
) -> None:
    if os.environ.get("GBA_REQUIRE_TENANT_SITE") != "1":
        pytest.skip("BLOCKED: tenant-site verification requires GBA_REQUIRE_TENANT_SITE=1")
    site_dir = os.environ.get("GBA_TENANT_SITE_DIR")
    if not site_dir:
        pytest.fail("GBA_REQUIRE_TENANT_SITE=1 needs GBA_TENANT_SITE_DIR")
    site = Path(site_dir).resolve()
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    assert npm is not None
    assert (WEB_OUT / "book" / "index.html").exists(), "build web/ first (npm run build)"

    api_port, site_port = free_port(), free_port()
    host = f"127.0.0.1:{api_port}"
    seed_customer_setup(owner_conn, world)
    with owner_tenant_transaction(owner_conn, world.a.tenant_id):
        owner_conn.execute(
            "insert into gba.tenant_hosts (host, tenant_id) values (%s, %s)",
            (host, world.a.tenant_id),
        )
    # The site's origin must be approved in the governed allowlist, or framing is refused.
    add_embed_origin(
        owner_conn, world.a.tenant_id, f"http://127.0.0.1:{site_port}", actor="test:tenant-site"
    )
    app = create_app(
        Settings(
            environment="test",
            database_url=SecretStr(test_database.app_dsn),
            customer_web_dir=WEB_OUT,
        )
    )
    booking_url = f"http://{host}/book/"
    public = {
        k: v
        for k, v in os.environ.items()
        if not any(w in k.upper() for w in ("DSN", "DATABASE_URL", "SECRET", "PASSWORD", "TOKEN"))
    }
    env = {
        **public,
        "NEXT_PUBLIC_GORGONA_BOOKING_URL": booking_url,
        "KA_BOOKING_TEST_URL": booking_url,
        "KA_BOOKING_TEST_DAY": customer_day(),
        "KA_SITE_PORT": str(site_port),
    }
    try:
        with live_server(app, api_port):
            _run(npm, site, ["run", "build"], env)
            report = _run(
                npm, site, ["run", "test:e2e", "--", "--grep-invert", "unconfigured"], env
            )
        summary = re.findall(r"^\s*(\d+ (?:passed|failed|skipped|flaky)\b.*)$", report, re.M)
        print("tenant site:", "; ".join(summary))  # noqa: T201
        assert summary, report.encode("ascii", "replace").decode()
        assert not any(re.search(r"failed|flaky|skipped", line) for line in summary), summary
        with owner_tenant_transaction(owner_conn, world.a.tenant_id):
            confirmed = owner_conn.execute(
                "select count(*) from gba.bookings b join gba.booking_customers c "
                "on c.tenant_id = b.tenant_id and c.booking_id = b.id "
                "where b.status = 'CONFIRMED' and c.customer_name = %s",
                (GUEST,),
            ).fetchone()
        assert confirmed == (2,), "each browser project confirms one real appointment"
        with owner_tenant_transaction(owner_conn, world.b.tenant_id):
            assert owner_conn.execute("select count(*) from gba.booking_customers").fetchone() == (
                0,
            )
    finally:
        # Never leave a test booking origin in the site's export.
        unset = {k: v for k, v in public.items() if k != "NEXT_PUBLIC_GORGONA_BOOKING_URL"}
        _run(npm, site, ["run", "build"], unset)
