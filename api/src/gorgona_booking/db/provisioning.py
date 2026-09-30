"""Owner-side provisioning. Forced RLS applies to the owner as well, so every write
here runs inside a transaction with explicit tenant context."""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from uuid import UUID, uuid7

import psycopg


@contextmanager
def owner_tenant_transaction(
    conn: psycopg.Connection, tenant_id: UUID
) -> Iterator[psycopg.Connection]:
    with conn.transaction():
        conn.execute("select pg_catalog.set_config('gba.tenant_id', %s, true)", (str(tenant_id),))
        yield conn


def provision_tenant(
    conn: psycopg.Connection, *, slug: str, display_name: str, hosts: Iterable[str] = ()
) -> UUID:
    tenant_id = uuid7()
    with owner_tenant_transaction(conn, tenant_id):
        conn.execute(
            "insert into gba.tenants (id, slug, display_name) values (%s, %s, %s)",
            (tenant_id, slug, display_name),
        )
        for host in hosts:
            conn.execute(
                "insert into gba.tenant_hosts (host, tenant_id) values (%s, %s)",
                (host.lower(), tenant_id),
            )
    return tenant_id
