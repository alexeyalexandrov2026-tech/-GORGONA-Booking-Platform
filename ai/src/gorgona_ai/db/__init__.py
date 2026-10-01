"""AI database: bootstrap (roles + database), migrations, tenant-scoped transactions."""

import hashlib
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
from uuid import UUID

import psycopg
from psycopg import sql

OWNER_ROLE = "gai_owner"
WORKER_ROLE = "gai_worker"
_FILENAME = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")
_LOCK_KEY = 0x6761695F6D6967  # "gai_mig"


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def load_migrations() -> list[Migration]:
    found: list[Migration] = []
    for entry in (resources.files("gorgona_ai.db") / "migrations").iterdir():
        if not entry.name.endswith(".sql"):
            continue
        match = _FILENAME.fullmatch(entry.name)
        if match is None:
            raise MigrationError(f"invalid migration filename: {entry.name}")
        found.append(Migration(int(match["version"]), match["name"], entry.read_text("utf-8")))
    found.sort(key=lambda m: m.version)
    if [m.version for m in found] != list(range(1, len(found) + 1)):
        raise MigrationError("migration versions must be contiguous, starting at 0001")
    return found


def bootstrap(admin_dsn: str, database: str, owner_password: str, worker_password: str) -> None:
    """Create the owner/worker roles and the database (idempotent). Needs CREATEROLE/CREATEDB.

    The worker role cannot bypass row-level security and owns nothing.
    """
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        for role, password in ((OWNER_ROLE, owner_password), (WORKER_ROLE, worker_password)):
            exists = conn.execute("select 1 from pg_roles where rolname = %s", (role,)).fetchone()
            verb = "alter" if exists else "create"
            conn.execute(
                sql.SQL("{} role {} login nosuperuser nobypassrls noinherit password {}").format(
                    sql.SQL(verb), sql.Identifier(role), sql.Literal(password)
                )
            )
        if (
            conn.execute("select 1 from pg_database where datname = %s", (database,)).fetchone()
            is None
        ):
            # On Azure the admin is not a superuser: it must be a member of the owner to
            # create a database owned by it.
            conn.execute(sql.SQL("grant {} to current_user").format(sql.Identifier(OWNER_ROLE)))
            conn.execute(
                sql.SQL("create database {} owner {}").format(
                    sql.Identifier(database), sql.Identifier(OWNER_ROLE)
                )
            )
        conn.execute(
            sql.SQL("grant connect on database {} to {}").format(
                sql.Identifier(database), sql.Identifier(WORKER_ROLE)
            )
        )
    # Extensions are created by the admin (on Azure only azure_pg_admin members may create
    # allowlisted extensions); the migration's `create extension if not exists` is then a no-op.
    db_dsn = psycopg.conninfo.make_conninfo(admin_dsn, dbname=database)
    with psycopg.connect(db_dsn, autocommit=True) as conn:
        conn.execute("create extension if not exists vector")


def migrate(owner_dsn: str) -> list[str]:
    """Apply pending migrations as the owner role; refuses superusers. Returns names applied."""
    applied: list[str] = []
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        row = conn.execute("select rolsuper from pg_roles where rolname = current_user").fetchone()
        if row is None or row[0]:
            raise MigrationError("refusing to migrate as a superuser; use the owner role")
        conn.execute("select pg_advisory_lock(%s)", (_LOCK_KEY,))
        try:
            conn.execute(
                "create table if not exists public.gai_schema_migrations ("
                " version integer primary key, name text not null, checksum text not null,"
                " applied_at timestamptz not null default now())"
            )
            done: dict[int, str] = dict(
                conn.execute(
                    "select version, checksum from public.gai_schema_migrations"
                ).fetchall()
            )
            for migration in load_migrations():
                if migration.version in done:
                    if done[migration.version] != migration.checksum:
                        raise MigrationError(f"checksum mismatch for {migration.version:04d}")
                    continue
                with conn.transaction():
                    conn.execute(migration.sql.encode("utf-8"))
                    conn.execute(
                        "insert into public.gai_schema_migrations (version, name, checksum) "
                        "values (%s, %s, %s)",
                        (migration.version, migration.name, migration.checksum),
                    )
                applied.append(f"{migration.version:04d}_{migration.name}")
        finally:
            conn.execute("select pg_advisory_unlock(%s)", (_LOCK_KEY,))
    return applied


@contextmanager
def tenant_transaction(conn: psycopg.Connection, tenant_id: UUID) -> Iterator[psycopg.Connection]:
    """A transaction whose row-level security context is one tenant."""
    with conn.transaction():
        conn.execute("select set_config('gai.tenant_id', %s, true)", (str(tenant_id),))
        yield conn


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in values) + "]"
