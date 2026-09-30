"""`gba-db`: bootstrap roles/database, apply migrations, verify the runtime role.

Credentials come from environment variables only, so they never land in shell
history or process listings. DSNs are never printed.
"""

import argparse
import asyncio
import os
import sys

import psycopg

from gorgona_booking.db.bootstrap import BootstrapSpec, bootstrap
from gorgona_booking.db.migrate import apply_migrations
from gorgona_booking.db.pool import assert_safe_runtime_role


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"environment variable {name} is required")
    return value


def _bootstrap(_: argparse.Namespace) -> None:
    spec = BootstrapSpec(
        database=os.environ.get("GBA_DATABASE_NAME", "gorgona_booking"),
        owner_role=os.environ.get("GBA_OWNER_ROLE", "gba_owner"),
        owner_password=_require("GBA_OWNER_PASSWORD"),
        app_role=os.environ.get("GBA_APP_ROLE", "gba_app"),
        app_password=_require("GBA_APP_PASSWORD"),
    )
    bootstrap(_require("GBA_ADMIN_DATABASE_URL"), spec)
    print(
        f"bootstrapped database {spec.database} (owner {spec.owner_role}, runtime {spec.app_role})"
    )


def _migrate(_: argparse.Namespace) -> None:
    applied = apply_migrations(_require("GBA_MIGRATION_DATABASE_URL"))
    names = ", ".join(f"{m.version:04d}_{m.name}" for m in applied) or "nothing to apply"
    print(f"migrations applied: {names}")


async def _check_runtime_role_async() -> None:
    async with await psycopg.AsyncConnection.connect(_require("GBA_DATABASE_URL")) as conn:
        await assert_safe_runtime_role(conn)


def _check_runtime_role(_: argparse.Namespace) -> None:
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(_check_runtime_role_async(), loop_factory=loop_factory)
    print("runtime role OK: not superuser, no BYPASSRLS, not an owner, member of gba_runtime")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="gba-db")
    commands = parser.add_subparsers(required=True)
    commands.add_parser("bootstrap", help="create roles and database (superuser DSN)").set_defaults(
        run=_bootstrap
    )
    commands.add_parser("migrate", help="apply pending migrations (owner DSN)").set_defaults(
        run=_migrate
    )
    commands.add_parser(
        "check-runtime-role", help="verify the API credential cannot bypass RLS"
    ).set_defaults(run=_check_runtime_role)
    args = parser.parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
