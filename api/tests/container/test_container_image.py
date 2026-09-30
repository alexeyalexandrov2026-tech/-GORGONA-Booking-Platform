"""The production image, run for real: jobs, API, booking flow, runtime hardening.

Uses a disposable official postgres:18 container on a private Docker network; the
developer's PostgreSQL is not touched. Enabled by GBA_REQUIRE_CONTAINER=1 and
GBA_CONTAINER_IMAGE=<image>; otherwise reported as BLOCKED (skipped).

Note: the postgres image's bootstrap user is a real superuser. Bootstrap against the
Azure admin (not a superuser) remains a staging acceptance item.
"""

import os
import secrets
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID

import httpx
import psycopg
import pytest

from gorgona_booking.db.provisioning import owner_tenant_transaction
from tests.integration.booking_support import BookingWorld
from tests.integration.customer_support import customer_day, seed_customer_setup
from tests.integration.seed import seed_fake_catalog, seed_resource, seed_salon

POSTGRES_IMAGE = (
    "postgres:18@sha256:5a5a84b19854a9ffaa54082c166ff4ec27473a361e496e5ea167f298f2da9722"
)
DETAILS = {
    "name": "FAKE Container Guest",
    "email": "fake-container@example.test",
    "phone": "+1 555 010 7777",
    "accept_policy": True,
}


@dataclass(frozen=True, slots=True)
class Stack:
    image: str
    network: str
    owner_dsn_host: str  # owner DSN reachable from the test process
    app_dsn_net: str  # runtime DSN reachable inside the Docker network
    owner_dsn_net: str


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    docker = shutil.which("docker")
    assert docker is not None, "docker CLI not found"
    return subprocess.run(  # noqa: S603 - fixed docker CLI with test-controlled arguments
        [docker, *args], capture_output=True, text=True, timeout=300, check=check
    )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def stack() -> Iterator[Stack]:
    if os.environ.get("GBA_REQUIRE_CONTAINER") != "1":
        pytest.skip("BLOCKED: container verification requires GBA_REQUIRE_CONTAINER=1")
    image = os.environ.get("GBA_CONTAINER_IMAGE")
    if not image:
        pytest.fail("GBA_REQUIRE_CONTAINER=1 needs GBA_CONTAINER_IMAGE")
    suffix = secrets.token_hex(4)
    network, pg = f"gba-smoke-{suffix}", f"gba-smoke-pg-{suffix}"
    admin_pw, owner_pw, app_pw = (secrets.token_urlsafe(24) for _ in range(3))
    pg_port = _free_port()
    _docker("network", "create", network)
    try:
        _docker(
            "run", "-d", "--rm", "--name", pg, "--network", network, "--network-alias", "pg",
            "-e", f"POSTGRES_PASSWORD={admin_pw}", "-p", f"127.0.0.1:{pg_port}:5432",
            POSTGRES_IMAGE,
        )  # fmt: skip
        for _ in range(60):
            if _docker("exec", pg, "pg_isready", "-h", "127.0.0.1", check=False).returncode == 0:
                break
            time.sleep(1)
        else:
            pytest.fail("disposable postgres:18 did not become ready")
        net = "pg:5432/gorgona_booking?sslmode=disable"
        stack = Stack(
            image=image,
            network=network,
            owner_dsn_host=f"postgresql://gba_owner:{owner_pw}@127.0.0.1:{pg_port}/gorgona_booking",
            app_dsn_net=f"postgresql://gba_app:{app_pw}@{net}",
            owner_dsn_net=f"postgresql://gba_owner:{owner_pw}@{net}",
        )
        bootstrap = _docker(
            "run", "--rm", "--network", network,
            "-e", f"GBA_ADMIN_DATABASE_URL=postgresql://postgres:{admin_pw}@pg:5432/postgres",
            "-e", f"GBA_OWNER_PASSWORD={owner_pw}", "-e", f"GBA_APP_PASSWORD={app_pw}",
            "-e", "GBA_DATABASE_NAME=gorgona_booking",
            image, "gba-db", "bootstrap",
        )  # fmt: skip
        assert "bootstrapped database gorgona_booking" in bootstrap.stdout
        yield stack
    finally:
        _docker("rm", "-f", pg, check=False)
        _docker("network", "rm", network, check=False)


def _migrate(stack: Stack) -> str:
    result = _docker(
        "run", "--rm", "--network", stack.network,
        "-e", f"GBA_MIGRATION_DATABASE_URL={stack.owner_dsn_net}",
        stack.image, "gba-db", "migrate",
    )  # fmt: skip
    return result.stdout


def test_migration_job_is_explicit_and_idempotent(stack: Stack) -> None:
    first, second = _migrate(stack), _migrate(stack)
    assert "0001_tenancy" in first
    assert "0007_tenant_embed_origins" in first
    assert "nothing to apply" in second


def test_image_runs_non_root_without_baked_secrets(stack: Stack) -> None:
    config = _docker("image", "inspect", stack.image, "--format", "{{json .Config}}").stdout
    for forbidden in ("DATABASE_URL", "PASSWORD", "SECRET", "postgresql://"):
        assert forbidden not in config
    user = _docker("run", "--rm", "--entrypoint", "id", stack.image, "-u").stdout.strip()
    assert user == "10001"


def test_api_container_serves_health_web_and_a_real_booking(stack: Stack) -> None:
    _migrate(stack)
    with psycopg.connect(stack.owner_dsn_host, autocommit=True) as owner:
        a, b = seed_salon(owner, "a"), seed_salon(owner, "b")
        world = BookingWorld(
            a=a,
            b=b,
            catalog_a=seed_fake_catalog(owner, a),
            catalog_b=seed_fake_catalog(owner, b),
            artist_a1=seed_resource(owner, a, "FAKE artist A1"),
            artist_a2=seed_resource(owner, a, "FAKE artist A2"),
            artist_b1=seed_resource(owner, b, "FAKE artist B1"),
        )
        seed_customer_setup(owner, world)

    api_port = _free_port()
    name = f"gba-smoke-api-{secrets.token_hex(4)}"
    _docker(
        "run", "-d", "--name", name, "--network", stack.network,
        "-p", f"127.0.0.1:{api_port}:8000",
        "-e", "GBA_ENV=local", "-e", f"GBA_DATABASE_URL={stack.app_dsn_net}",
        stack.image,
    )  # fmt: skip
    try:
        base = f"http://127.0.0.1:{api_port}"
        with httpx.Client(base_url=base, timeout=10) as client:
            for _ in range(60):
                try:
                    if client.get("/health/live").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.5)
            assert client.get("/health/ready").json()["checks"]["database"] == "ok"

            host = {"host": world.a.host}
            page = client.get("/book/", headers=host)
            assert page.status_code == 200
            assert page.headers["content-security-policy"] == "frame-ancestors 'self'"

            day = customer_day()
            available = client.post(
                "/v1/customer/availability",
                headers=host,
                json={
                    "location_id": str(world.a.location_id),
                    "variant_id": str(world.catalog_a.base_variant_id),
                    "add_on_ids": [],
                    "day": day,
                    "resource_id": None,
                },
            )
            assert available.status_code == 200, available.text
            slot = available.json()["slots"][0]
            token = secrets.token_urlsafe(32)
            held = client.post(
                "/v1/customer/holds",
                headers={**host, "Booking-Token": token, "Idempotency-Key": "container-hold-1"},
                json={
                    "location_id": str(world.a.location_id),
                    "variant_id": str(world.catalog_a.base_variant_id),
                    "add_on_ids": [],
                    "resource_id": slot["resource_id"],
                    "start_at": slot["start_at"],
                },
            )
            assert held.status_code == 201, held.text
            booking_id = UUID(held.json()["booking_id"])
            confirmed = client.post(
                f"/v1/customer/bookings/{booking_id}/confirm",
                headers={**host, "Booking-Token": token, "Idempotency-Key": "container-confirm-1"},
                json=DETAILS,
            )
            assert confirmed.status_code == 200, confirmed.text

        with (
            psycopg.connect(stack.owner_dsn_host, autocommit=True) as owner,
            owner_tenant_transaction(owner, world.a.tenant_id),
        ):
            row = owner.execute(
                "select status from gba.bookings where id = %s", (booking_id,)
            ).fetchone()
        assert row == ("CONFIRMED",)

        started = time.monotonic()
        _docker("stop", "--time", "30", name)
        assert time.monotonic() - started < 25, "graceful shutdown should not need SIGKILL"
        state = _docker("inspect", name, "--format", "{{.State.ExitCode}}").stdout.strip()
        assert state == "0", f"unclean exit {state}"
    finally:
        _docker("rm", "-f", name, check=False)
