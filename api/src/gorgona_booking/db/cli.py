"""`gba-db`: bootstrap roles/database, apply migrations, verify the runtime role,
and operator-side salon onboarding (owner credential; ADR-0010).

Credentials come from environment variables only, so they never land in shell
history or process listings. DSNs are never printed.
"""

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path
from uuid import UUID

import psycopg

from gorgona_booking.db.bootstrap import BootstrapSpec, bootstrap
from gorgona_booking.db.migrate import apply_migrations
from gorgona_booking.db.pool import assert_safe_runtime_role
from gorgona_booking.db.provisioning import grant_platform_admin, provision_user


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


def _owner_conn() -> psycopg.Connection:
    return psycopg.connect(_require("GBA_MIGRATION_DATABASE_URL"), autocommit=True)


def _operator() -> str:
    return f"operator:{getpass.getuser()}"


def _write_secret(path: str, value: str) -> None:
    """Create the file exclusively with owner-only permissions; never print the value."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(value + chr(10))


def _print_readiness(tenant_id: UUID) -> bool:
    from gorgona_booking.onboarding.readiness import readiness_for_owner

    with _owner_conn() as conn:
        result = readiness_for_owner(conn, tenant_id)
    for item in result.items:
        print(f"  {item.status:<11} {item.fact:<20} {item.detail}")
    print(f"ready for public booking: {'yes' if result.ready else 'no'}")
    return result.ready


def _onboard(args: argparse.Namespace) -> None:
    from gorgona_booking.onboarding.service import apply_onboarding
    from gorgona_booking.onboarding.spec import load_spec

    spec = load_spec(Path(args.spec))
    if spec.owner_email is not None and not args.invitation_token_file:
        raise SystemExit("the spec invites an owner: pass --invitation-token-file PATH")
    with _owner_conn() as conn:
        result = apply_onboarding(
            conn,
            spec,
            actor=_operator(),
            on_invitation=lambda token: _write_secret(args.invitation_token_file, token),
        )
    print(f"salon {spec.slug} ({result.tenant_id}): {'changed' if result.changed else 'unchanged'}")
    if result.invitation_created:
        print(f"owner invitation created; token written to {args.invitation_token_file}")
    _print_readiness(result.tenant_id)


def _readiness(args: argparse.Namespace) -> None:
    from gorgona_booking.onboarding.service import stable_id

    if not _print_readiness(stable_id(None, "tenant", args.slug)):
        raise SystemExit(2)


def _go_live(args: argparse.Namespace) -> None:
    from gorgona_booking.onboarding.service import (
        NotReadyError,
        go_live_owner,
        stable_id,
    )

    tenant_id = stable_id(None, "tenant", args.slug)
    try:
        with _owner_conn() as conn:
            go_live_owner(conn, tenant_id, actor=_operator())
    except NotReadyError as exc:
        print(f"not ready: {exc.details}")
        raise SystemExit(2) from None
    print(f"salon {args.slug} is live")


def _link_user(args: argparse.Namespace) -> None:
    with _owner_conn() as conn:
        user_id = provision_user(
            conn,
            display_name=args.display_name,
            email=args.email,
            issuer=args.issuer,
            subject=args.subject,
            actor=_operator(),
        )
    print(f"user {user_id} linked to {args.issuer}")


def _grant_platform_admin(args: argparse.Namespace) -> None:
    with _owner_conn() as conn:
        with conn.transaction():
            conn.execute(
                "select pg_catalog.set_config('gba.auth_issuer', %s, true), "
                "pg_catalog.set_config('gba.auth_subject', %s, true)",
                (args.issuer, args.subject),
            )
            row = conn.execute(
                "select user_id from gba.user_identities where issuer = %s and subject = %s",
                (args.issuer, args.subject),
            ).fetchone()
        if row is None:
            raise SystemExit("no user is linked to that identity (use link-user first)")
        grant_platform_admin(conn, user_id=row[0], granted_by=_operator())
    print(f"platform_admin granted to user {row[0]}")


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
    onboard = commands.add_parser("onboard", help="apply a salon onboarding spec (owner DSN)")
    onboard.add_argument("spec")
    onboard.add_argument("--invitation-token-file")
    onboard.set_defaults(run=_onboard)
    readiness = commands.add_parser("readiness", help="list missing/unconfirmed salon facts")
    readiness.add_argument("slug")
    readiness.set_defaults(run=_readiness)
    live = commands.add_parser("go-live", help="open public booking if the salon is ready")
    live.add_argument("slug")
    live.set_defaults(run=_go_live)
    link = commands.add_parser("link-user", help="create a user for an external identity")
    for flag in ("--issuer", "--subject", "--display-name"):
        link.add_argument(flag, required=True)
    link.add_argument("--email")
    link.set_defaults(run=_link_user)
    grant = commands.add_parser("grant-platform-admin", help="grant platform_admin (owner DSN)")
    grant.add_argument("--issuer", required=True)
    grant.add_argument("--subject", required=True)
    grant.set_defaults(run=_grant_platform_admin)
    args = parser.parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
