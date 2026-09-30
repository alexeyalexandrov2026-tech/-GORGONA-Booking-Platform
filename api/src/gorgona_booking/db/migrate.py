"""Explicit SQL migrations.

Files named NNNN_name.sql in the package's `migrations` directory are applied in
order, each in its own transaction, by the owner role. Applied migrations are
recorded with a SHA-256 checksum so that editing history is detected rather than
silently diverging.
"""

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

import psycopg

_FILENAME = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")
# pg_advisory_lock key so concurrent deploys cannot interleave migrations.
_LOCK_KEY = 0x6762615F6D6967


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


def load_migrations(directory: Traversable | Path | None = None) -> list[Migration]:
    source = directory or resources.files("gorgona_booking.db") / "migrations"
    migrations: list[Migration] = []
    for entry in source.iterdir():
        if not entry.name.endswith(".sql"):
            continue
        match = _FILENAME.fullmatch(entry.name)
        if match is None:
            raise MigrationError(f"invalid migration filename: {entry.name}")
        migrations.append(
            Migration(int(match["version"]), match["name"], entry.read_text(encoding="utf-8"))
        )
    migrations.sort(key=lambda m: m.version)
    if [m.version for m in migrations] != list(range(1, len(migrations) + 1)):
        raise MigrationError("migration versions must be contiguous, starting at 0001")
    return migrations


def apply_migrations(
    conninfo: str, migrations: Sequence[Migration] | None = None
) -> list[Migration]:
    """Apply pending migrations as the connected (owner) role. Returns what was applied."""
    pending = load_migrations() if migrations is None else list(migrations)
    applied_now: list[Migration] = []
    with psycopg.connect(conninfo, autocommit=True) as conn:
        row = conn.execute(
            "select rolsuper from pg_catalog.pg_roles where rolname = current_user"
        ).fetchone()
        if row is None or row[0]:
            # Tables must be owned by the dedicated owner role, not a superuser.
            raise MigrationError("refusing to migrate as a superuser; use the owner role")

        conn.execute("select pg_catalog.pg_advisory_lock(%s)", (_LOCK_KEY,))
        try:
            conn.execute(
                """
                create table if not exists public.gba_schema_migrations (
                    version    integer primary key,
                    name       text not null,
                    checksum   text not null,
                    applied_at timestamptz not null default now()
                )
                """
            )
            recorded: dict[int, str] = dict(
                conn.execute(
                    "select version, checksum from public.gba_schema_migrations"
                ).fetchall()
            )
            known = {m.version for m in pending}
            if unknown := sorted(set(recorded) - known):
                raise MigrationError(f"database has migrations this code does not know: {unknown}")

            for migration in pending:
                if migration.version in recorded:
                    if recorded[migration.version] != migration.checksum:
                        raise MigrationError(
                            f"migration {migration.version:04d}_{migration.name} was edited "
                            "after it was applied"
                        )
                    continue
                with conn.transaction():
                    # No parameters: sent as-is, so a file may hold many statements.
                    conn.execute(migration.sql.encode("utf-8"))
                    conn.execute(
                        "insert into public.gba_schema_migrations (version, name, checksum) "
                        "values (%s, %s, %s)",
                        (migration.version, migration.name, migration.checksum),
                    )
                applied_now.append(migration)
        finally:
            conn.execute("select pg_catalog.pg_advisory_unlock(%s)", (_LOCK_KEY,))
    return applied_now
