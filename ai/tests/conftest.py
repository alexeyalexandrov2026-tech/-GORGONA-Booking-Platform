"""Real PostgreSQL 18 + pgvector (disposable container) for the AI plane tests.

Enabled by GBA_REQUIRE_AI_POSTGRES=1 (then a missing Docker engine fails the run);
otherwise database tests are reported as BLOCKED (skipped). The image matches the
Azure AI database's allowlisted `vector` 0.8.2 on PostgreSQL 18.
"""

import os
import secrets
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from gorgona_ai import db

PGVECTOR_IMAGE = (
    "pgvector/pgvector:0.8.2-pg18"
    "@sha256:42e7f6b4e1eceb02ff14e3e6bc6108bbe259abbe83879dc1845d0da1ddeb555d"
)
TABLES = (
    "ai.verifications, ai.evaluations, ai.promotions, ai.model_examples, ai.models,"
    " ai.datasets, ai.embeddings, ai.documents, ai.evidence, ai.job_runs, ai.tenant_activity"
)


@dataclass(frozen=True, slots=True)
class AiDatabase:
    owner_dsn: str
    worker_dsn: str
    admin_dsn: str


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    docker = shutil.which("docker")
    if docker is None:
        pytest.fail("docker CLI not found")
    return subprocess.run(  # noqa: S603 - fixed docker CLI with test-controlled arguments
        [docker, *args], capture_output=True, text=True, timeout=300, check=check
    )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def ai_db() -> Iterator[AiDatabase]:
    if os.environ.get("GBA_REQUIRE_AI_POSTGRES") != "1":
        pytest.skip("BLOCKED: AI database tests require GBA_REQUIRE_AI_POSTGRES=1 (Docker)")
    name = f"gai-test-{secrets.token_hex(4)}"
    admin_pw, owner_pw, worker_pw = (secrets.token_urlsafe(24) for _ in range(3))
    port = _free_port()
    _docker(
        "run", "-d", "--rm", "--name", name, "-e", f"POSTGRES_PASSWORD={admin_pw}",
        "-p", f"127.0.0.1:{port}:5432", PGVECTOR_IMAGE,
    )  # fmt: skip
    try:
        admin = f"postgresql://postgres:{admin_pw}@127.0.0.1:{port}/postgres"
        deadline = time.monotonic() + 60
        while True:
            try:
                with psycopg.connect(admin, connect_timeout=2) as conn:
                    conn.execute("select 1")
                break
            except psycopg.OperationalError:
                if time.monotonic() > deadline:
                    pytest.fail("pgvector container did not become ready")
                time.sleep(1)
        db.bootstrap(admin, "gorgona_ai", owner_pw, worker_pw)
        owner = f"postgresql://{db.OWNER_ROLE}:{owner_pw}@127.0.0.1:{port}/gorgona_ai"
        applied = db.migrate(owner)
        assert applied == ["0001_ai_core"]
        yield AiDatabase(
            owner_dsn=owner,
            worker_dsn=f"postgresql://{db.WORKER_ROLE}:{worker_pw}@127.0.0.1:{port}/gorgona_ai",
            admin_dsn=admin,
        )
    finally:
        _docker("rm", "-f", name, check=False)


@pytest.fixture
def conn(ai_db: AiDatabase) -> Iterator[psycopg.Connection]:
    """A worker-role connection on a clean schema."""
    with psycopg.connect(ai_db.owner_dsn, autocommit=True) as owner:
        owner.execute(f"truncate {TABLES} cascade")
    with psycopg.connect(ai_db.worker_dsn, autocommit=True) as worker:
        yield worker


def envelope(
    tenant_id: UUID,
    kind: str,
    payload: dict[str, Any],
    *,
    env: str = "production",
    scope: str = "none",
    source: str = "concierge",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "tenant_id": str(tenant_id),
        "env": env,
        "source": source,
        "kind": kind,
        "learning_scope": scope,
        "provenance": {
            "producer": "test",
            "produced_at": datetime.now(UTC).isoformat(),
            "trace_id": uuid4().hex,
        },
        "payload": payload,
    }
