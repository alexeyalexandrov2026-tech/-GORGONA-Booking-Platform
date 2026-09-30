"""Cluster bootstrap, run once per environment with a superuser DSN.

Creates the NOLOGIN runtime group, the owner (migration) login role, the runtime
login role and the database. It is idempotent and refuses to touch a role that
is a superuser. Passwords are sent pre-hashed (SCRAM), never in clear text.
"""

import re
from dataclasses import dataclass

import psycopg
from psycopg import sql

RUNTIME_GROUP = "gba_runtime"
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_SAFE_ATTRIBUTES = sql.SQL("NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")


class BootstrapError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BootstrapSpec:
    database: str
    owner_role: str
    owner_password: str
    app_role: str
    app_password: str

    def validate(self) -> None:
        for name in (self.database, self.owner_role, self.app_role):
            if not _IDENTIFIER.fullmatch(name):
                raise BootstrapError(f"invalid identifier: {name!r}")
        if len({self.owner_role, self.app_role, RUNTIME_GROUP}) != 3:
            raise BootstrapError("owner role, runtime role and runtime group must all differ")
        if min(len(self.owner_password), len(self.app_password)) < 12:
            raise BootstrapError("role passwords must be at least 12 characters")


def bootstrap(admin_conninfo: str, spec: BootstrapSpec) -> None:
    spec.validate()
    with psycopg.connect(admin_conninfo, autocommit=True) as conn:
        _ensure_role(conn, RUNTIME_GROUP, password=None)
        _ensure_role(conn, spec.owner_role, password=spec.owner_password)
        _ensure_role(conn, spec.app_role, password=spec.app_password)
        conn.execute(
            sql.SQL("grant {} to {}").format(
                sql.Identifier(RUNTIME_GROUP), sql.Identifier(spec.app_role)
            )
        )
        for member, role in ((spec.app_role, spec.owner_role), (spec.owner_role, RUNTIME_GROUP)):
            if _is_member(conn, member, role):
                raise BootstrapError(f"{member} must not be a member of {role}")

        owner = conn.execute(
            "select pg_catalog.pg_get_userbyid(datdba) from pg_catalog.pg_database "
            "where datname = %s",
            (spec.database,),
        ).fetchone()
        if owner is None:
            conn.execute(
                sql.SQL("create database {} owner {} encoding 'UTF8' template template0").format(
                    sql.Identifier(spec.database), sql.Identifier(spec.owner_role)
                )
            )
        elif owner[0] != spec.owner_role:
            raise BootstrapError(f"database {spec.database} is owned by {owner[0]}, not the owner")
        conn.execute(
            sql.SQL("revoke all on database {} from public").format(sql.Identifier(spec.database))
        )
        conn.execute(
            sql.SQL("grant connect on database {} to {}").format(
                sql.Identifier(spec.database), sql.Identifier(RUNTIME_GROUP)
            )
        )


def _ensure_role(conn: psycopg.Connection, name: str, password: str | None) -> None:
    row = conn.execute(
        "select rolsuper from pg_catalog.pg_roles where rolname = %s", (name,)
    ).fetchone()
    if row is not None and row[0]:
        raise BootstrapError(f"role {name} is a superuser; refusing to reuse or alter it")

    statement = sql.SQL("{} role {} {} {}").format(
        sql.SQL("alter" if row is not None else "create"),
        sql.Identifier(name),
        sql.SQL("LOGIN" if password is not None else "NOLOGIN"),
        _SAFE_ATTRIBUTES,
    )
    if password is not None:
        hashed = conn.pgconn.encrypt_password(
            password.encode("utf-8"), name.encode("utf-8"), b"scram-sha-256"
        )
        statement = sql.SQL("{} password {}").format(statement, sql.Literal(hashed.decode()))
    conn.execute(statement)


def _is_member(conn: psycopg.Connection, member: str, role: str) -> bool:
    row = conn.execute("select pg_catalog.pg_has_role(%s, %s, 'MEMBER')", (member, role)).fetchone()
    return bool(row and row[0])
