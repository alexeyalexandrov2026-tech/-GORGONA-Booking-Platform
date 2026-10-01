"""The AI worker image, run for real against a disposable pgvector PostgreSQL 18.

Proves the deployable artifact (not only the source) bootstraps the AI database, applies
migrations idempotently, executes the scheduled loop (`tick`) end to end, reports health,
runs as non-root and carries no secrets. Enabled by GBA_REQUIRE_AI_CONTAINER=1 and
GBA_AI_IMAGE=<image>; otherwise BLOCKED (skipped).
"""

import json
import os
import secrets
import shutil
import subprocess
import time
from collections.abc import Iterator
from uuid import uuid4

import pytest

from tests.conftest import PGVECTOR_IMAGE, envelope


def _docker(
    *args: str, check: bool = True, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    docker = shutil.which("docker")
    assert docker is not None
    return subprocess.run(  # noqa: S603 - fixed docker CLI with test-controlled arguments
        [docker, *args], capture_output=True, text=True, timeout=300, check=check, input=stdin
    )


@pytest.fixture(scope="module")
def stack() -> Iterator[dict[str, str]]:
    if os.environ.get("GBA_REQUIRE_AI_CONTAINER") != "1":
        pytest.skip("BLOCKED: AI container verification requires GBA_REQUIRE_AI_CONTAINER=1")
    image = os.environ.get("GBA_AI_IMAGE")
    if not image:
        pytest.fail("GBA_REQUIRE_AI_CONTAINER=1 needs GBA_AI_IMAGE")
    suffix = secrets.token_hex(4)
    net, pg = f"gai-net-{suffix}", f"gai-pg-{suffix}"
    admin_pw, owner_pw, worker_pw = (secrets.token_urlsafe(24) for _ in range(3))
    _docker("network", "create", net)
    try:
        _docker("run", "-d", "--rm", "--name", pg, "--network", net, "--network-alias", "pg",
                "-e", f"POSTGRES_PASSWORD={admin_pw}", PGVECTOR_IMAGE)  # fmt: skip
        for _ in range(60):
            if _docker("exec", pg, "pg_isready", "-h", "127.0.0.1", check=False).returncode == 0:
                break
            time.sleep(1)
        else:
            pytest.fail("pgvector container did not become ready")
        base = "pg:5432/gorgona_ai?sslmode=disable"
        yield {
            "image": image,
            "net": net,
            "pg": pg,
            "GAI_ADMIN_DATABASE_URL": f"postgresql://postgres:{admin_pw}@pg:5432/postgres",
            "GAI_OWNER_PASSWORD": owner_pw,
            "GAI_WORKER_PASSWORD": worker_pw,
            "GAI_MIGRATION_DATABASE_URL": f"postgresql://gai_owner:{owner_pw}@{base}",
            "GAI_DATABASE_URL": f"postgresql://gai_worker:{worker_pw}@{base}",
        }
    finally:
        _docker("rm", "-f", pg, check=False)
        _docker("network", "rm", net, check=False)


def _run(
    stack: dict[str, str], *command: str, env: tuple[str, ...]
) -> subprocess.CompletedProcess[str]:
    flags = [x for name in env for x in ("-e", f"{name}={stack[name]}")]
    return _docker("run", "--rm", "--network", stack["net"], *flags, stack["image"], *command,
                   check=False)  # fmt: skip


def test_image_is_non_root_without_baked_secrets(stack: dict[str, str]) -> None:
    config = _docker("image", "inspect", stack["image"], "--format", "{{json .Config}}").stdout
    for forbidden in ("DATABASE_URL", "PASSWORD", "postgresql://"):
        assert forbidden not in config
    assert json.loads(config)["User"] == "10001:10001"


def test_jobs_bootstrap_migrate_tick_and_report_health(stack: dict[str, str]) -> None:
    boot = _run(
        stack,
        "bootstrap",
        env=("GAI_ADMIN_DATABASE_URL", "GAI_OWNER_PASSWORD", "GAI_WORKER_PASSWORD"),
    )
    assert boot.returncode == 0, boot.stderr
    first = _run(stack, "migrate", env=("GAI_MIGRATION_DATABASE_URL",))
    second = _run(stack, "migrate", env=("GAI_MIGRATION_DATABASE_URL",))
    assert "0001_ai_core" in first.stdout
    assert "nothing to apply" in second.stdout

    # Seed one tenant's evidence through the worker role, as an intake job would.
    tenant = uuid4()
    catalog = {"services": [{"service_code": "LASH_LIFT", "service_name": "FAKE Lash lift"}]}
    raws = [envelope(tenant, "catalog", catalog)]
    sql = "".join(
        "insert into ai.job_runs (kind, idempotency_key, payload) values "
        f"('ingest', 'ingest:{i}', '{json.dumps(r)}'::jsonb);\n"
        for i, r in enumerate(raws)
    )
    seeded = _docker("exec", "-i", stack["pg"], "psql", "-v", "ON_ERROR_STOP=1", "-U", "postgres",
                     "-d", "gorgona_ai", stdin=sql, check=False)  # fmt: skip
    assert seeded.returncode == 0, seeded.stderr

    tick = _run(stack, "tick", env=("GAI_DATABASE_URL",))
    assert tick.returncode == 0, tick.stderr
    counts = json.loads(tick.stdout.strip().splitlines()[-1])
    assert counts.get("dead", 0) == 0
    assert counts["succeeded"] >= 3  # ingest + dataset_cut + rag_refresh
    health = _run(stack, "health", env=("GAI_DATABASE_URL",))
    assert health.returncode == 0, health.stdout
    assert json.loads(health.stdout.strip().splitlines()[-1])["healthy"] is True
    search = _run(stack, "search", str(tenant), "lash", env=("GAI_DATABASE_URL",))
    assert json.loads(search.stdout.strip().splitlines()[-1])[0][0] == "LASH_LIFT"
    # Logs are JSON lines without payload content.
    for line in tick.stdout.strip().splitlines()[:-1]:
        record = json.loads(line)
        assert "payload" not in record
