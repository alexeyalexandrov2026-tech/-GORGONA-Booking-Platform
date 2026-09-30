"""M2 identity, membership and audit invariants enforced by PostgreSQL (FAKE data).

Every runtime assertion uses the real non-owner runtime role.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import psycopg
import pytest
from psycopg import errors

from gorgona_booking.db.pool import RuntimeConnection, RuntimePool, assert_safe_runtime_role
from gorgona_booking.db.provisioning import add_membership, grant_platform_admin, provision_user
from tests.integration.seed import FakeUser, Salon, seed_user
from tests.support.fake_idp import FAKE_ISSUER

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def runtime_tx(
    pool: RuntimePool,
    *,
    tenant: UUID | None = None,
    user: UUID | None = None,
    identity: FakeUser | None = None,
    actor: str = "test:actor",
) -> AsyncIterator[RuntimeConnection]:
    """Runtime transaction with the transaction-local context the app would set."""
    settings = {"actor": actor}
    if tenant:
        settings["tenant_id"] = str(tenant)
    if user:
        settings["user_id"] = str(user)
    if identity:
        settings["auth_issuer"], settings["auth_subject"] = FAKE_ISSUER, identity.subject
    async with pool.connection() as conn, conn.transaction():
        for key, value in settings.items():
            await conn.execute("select pg_catalog.set_config(%s, %s, true)", (f"gba.{key}", value))
        yield conn


async def _rows(conn: RuntimeConnection, query: str, *params: object) -> list[tuple[object, ...]]:
    return await (await conn.execute(query.encode(), params or None)).fetchall()


def test_duplicate_identity_mapping_is_rejected(owner_conn: psycopg.Connection) -> None:
    first = seed_user(owner_conn, "dup")
    with pytest.raises(errors.UniqueViolation) as info:
        provision_user(
            owner_conn,
            display_name="FAKE impostor",
            email="impostor@example.test",
            issuer=FAKE_ISSUER,
            subject=first.subject,
        )
    assert info.value.diag.constraint_name == "user_identities_issuer_subject_key"


async def test_runtime_sees_only_the_identity_matching_the_verified_token(
    app_pool: RuntimePool, owner_conn: psycopg.Connection
) -> None:
    alice, bob = seed_user(owner_conn, "alice"), seed_user(owner_conn, "bob")
    async with runtime_tx(app_pool, identity=alice) as conn:
        rows = await _rows(conn, "select user_id from gba.user_identities")
    assert rows == [(alice.user_id,)]
    async with runtime_tx(app_pool) as conn:
        assert await _rows(conn, "select user_id from gba.user_identities") == []
        assert await _rows(conn, "select id from gba.users") == []
    assert bob.user_id != alice.user_id


async def test_runtime_cannot_link_an_identity_to_someone_else(
    app_pool: RuntimePool, owner_conn: psycopg.Connection
) -> None:
    alice, bob = seed_user(owner_conn, "alice"), seed_user(owner_conn, "bob")
    with pytest.raises(errors.InsufficientPrivilege):
        async with runtime_tx(app_pool, user=alice.user_id, identity=alice) as conn:
            await conn.execute(
                "insert into gba.user_identities (user_id, issuer, subject) values (%s, %s, %s)",
                (bob.user_id, FAKE_ISSUER, "fake-sub-hijack"),
            )


async def test_user_sees_own_memberships_across_salons_and_nobody_elses(
    app_pool: RuntimePool, owner_conn: psycopg.Connection, salons: tuple[Salon, Salon]
) -> None:
    a, b = salons
    alice, bob = seed_user(owner_conn, "alice"), seed_user(owner_conn, "bob")
    add_membership(owner_conn, tenant_id=a.tenant_id, user_id=alice.user_id, role="artist")
    add_membership(owner_conn, tenant_id=b.tenant_id, user_id=alice.user_id, role="owner")
    add_membership(owner_conn, tenant_id=a.tenant_id, user_id=bob.user_id, role="owner")
    async with runtime_tx(app_pool, user=alice.user_id) as conn:
        memberships = await _rows(conn, "select tenant_id, role from gba.memberships")
        tenants = await _rows(conn, "select id from gba.tenants")
    assert sorted(memberships, key=str) == sorted(
        [(a.tenant_id, "artist"), (b.tenant_id, "owner")], key=str
    )
    assert {t for (t,) in tenants} == {a.tenant_id, b.tenant_id}


def test_one_live_membership_per_salon_and_user(
    owner_conn: psycopg.Connection, salons: tuple[Salon, Salon]
) -> None:
    a, _ = salons
    alice = seed_user(owner_conn, "alice")
    add_membership(owner_conn, tenant_id=a.tenant_id, user_id=alice.user_id, role="artist")
    with pytest.raises(errors.UniqueViolation) as info:
        add_membership(owner_conn, tenant_id=a.tenant_id, user_id=alice.user_id, role="owner")
    assert info.value.diag.constraint_name == "memberships_one_live_per_user"


async def test_revoked_membership_is_terminal_and_rows_cannot_be_deleted(
    app_pool: RuntimePool, owner_conn: psycopg.Connection, salons: tuple[Salon, Salon]
) -> None:
    a, _ = salons
    alice = seed_user(owner_conn, "alice")
    membership = add_membership(
        owner_conn, tenant_id=a.tenant_id, user_id=alice.user_id, role="artist"
    )
    async with runtime_tx(app_pool, tenant=a.tenant_id) as conn:
        await conn.execute(
            "update gba.memberships set status = 'revoked' where id = %s", (membership,)
        )
    with pytest.raises(errors.CheckViolation) as info:
        async with runtime_tx(app_pool, tenant=a.tenant_id) as conn:
            await conn.execute(
                "update gba.memberships set status = 'active' where id = %s", (membership,)
            )
    assert info.value.diag.constraint_name == "memberships_revoked_terminal"
    with pytest.raises(errors.InsufficientPrivilege):
        async with runtime_tx(app_pool, tenant=a.tenant_id) as conn:
            await conn.execute("delete from gba.memberships where id = %s", (membership,))
    # After revocation a new membership for the same person is allowed.
    add_membership(owner_conn, tenant_id=a.tenant_id, user_id=alice.user_id, role="artist")


async def test_runtime_cannot_grant_platform_admin(
    app_pool: RuntimePool, owner_conn: psycopg.Connection
) -> None:
    alice = seed_user(owner_conn, "alice")
    with pytest.raises(errors.InsufficientPrivilege):
        async with runtime_tx(app_pool, user=alice.user_id) as conn:
            await conn.execute(
                "insert into gba.platform_roles (user_id, role, granted_by) "
                "values (%s, 'platform_admin', 'self')",
                (alice.user_id,),
            )


async def test_security_sensitive_changes_are_audited_without_secrets(
    app_pool: RuntimePool, owner_conn: psycopg.Connection, salons: tuple[Salon, Salon]
) -> None:
    a, b = salons
    alice = seed_user(owner_conn, "alice")
    membership = add_membership(
        owner_conn,
        tenant_id=a.tenant_id,
        user_id=alice.user_id,
        role="artist",
        actor="operator:test",
    )
    async with runtime_tx(app_pool, tenant=a.tenant_id, actor="user:fake-admin") as conn:
        await conn.execute(
            "update gba.memberships set status = 'suspended' where id = %s", (membership,)
        )
        await conn.execute(
            "insert into gba.invitations (tenant_id, email_normalized, role, token_sha256, "
            "expires_at) values (%s, 'invitee@example.test', 'artist', %s, "
            "now() + interval '7 days')",
            (a.tenant_id, "ab" * 32),
        )
    async with runtime_tx(app_pool, tenant=a.tenant_id) as conn:
        events = await _rows(
            conn,
            "select action, actor, target_id, details from gba.audit_events order by id",
        )
    actions = [(action, actor) for action, actor, _, _ in events]
    assert ("membership.created", "operator:test") in actions
    assert ("membership.updated", "user:fake-admin") in actions
    assert ("invitation.created", "user:fake-admin") in actions
    updated = next(d for action, _, _, d in events if action == "membership.updated")
    assert updated == {"status": {"from": "active", "to": "suspended"}}
    serialized = str(events)
    assert "ab" * 32 not in serialized
    assert "invitee@example.test" not in serialized
    # Identity creation is a platform-scope event (no tenant), invisible inside a salon.
    async with runtime_tx(app_pool) as conn:
        platform = await _rows(
            conn, "select action from gba.audit_events where target_id = %s", str(alice.user_id)
        )
    assert ("user.created",) in platform
    async with runtime_tx(app_pool, tenant=b.tenant_id) as conn:
        # Salon A's events are invisible from salon B.
        assert (
            await _rows(conn, "select 1 from gba.audit_events where tenant_id = %s", a.tenant_id)
            == []
        )
        assert (
            await _rows(
                conn, "select 1 from gba.audit_events where target_id = %s", str(membership)
            )
            == []
        )


async def test_audit_log_is_append_only_for_the_runtime_role(
    app_pool: RuntimePool, salons: tuple[Salon, Salon]
) -> None:
    a, _ = salons
    for statement in ("update gba.audit_events set actor = 'x'", "delete from gba.audit_events"):
        with pytest.raises(errors.InsufficientPrivilege):
            async with runtime_tx(app_pool, tenant=a.tenant_id) as conn:
                await conn.execute(statement.encode())


async def test_invitations_are_tenant_isolated_and_store_only_a_hash(
    app_pool: RuntimePool, owner_conn: psycopg.Connection, salons: tuple[Salon, Salon]
) -> None:
    a, b = salons
    async with runtime_tx(app_pool, tenant=a.tenant_id) as conn:
        await conn.execute(
            "insert into gba.invitations (tenant_id, email_normalized, role, token_sha256, "
            "expires_at) values (%s, 'someone@example.test', 'artist', %s, "
            "now() + interval '7 days')",
            (a.tenant_id, "cd" * 32),
        )
    async with runtime_tx(app_pool, tenant=b.tenant_id) as conn:
        assert await _rows(conn, "select 1 from gba.invitations") == []
    columns = owner_conn.execute(
        "select column_name from information_schema.columns "
        "where table_schema = 'gba' and table_name = 'invitations'"
    ).fetchall()
    assert ("token_sha256",) in columns
    assert not any("token" in c and c != "token_sha256" for (c,) in columns)


async def test_tenant_status_change_requires_platform_admin(
    app_pool: RuntimePool, owner_conn: psycopg.Connection, salons: tuple[Salon, Salon]
) -> None:
    a, _ = salons
    owner, operator = seed_user(owner_conn, "owner"), seed_user(owner_conn, "platform")
    add_membership(owner_conn, tenant_id=a.tenant_id, user_id=owner.user_id, role="owner")
    grant_platform_admin(owner_conn, user_id=operator.user_id, granted_by="test")
    async with runtime_tx(app_pool, tenant=a.tenant_id, user=owner.user_id) as conn:
        cur = await conn.execute(
            "update gba.tenants set status = 'suspended' where id = %s", (a.tenant_id,)
        )
        assert cur.rowcount == 0  # a salon owner cannot change platform-controlled status
    async with runtime_tx(
        app_pool, tenant=a.tenant_id, user=operator.user_id, actor="platform:test"
    ) as conn:
        cur = await conn.execute(
            "update gba.tenants set status = 'suspended' where id = %s", (a.tenant_id,)
        )
        assert cur.rowcount == 1
        events = await _rows(
            conn,
            "select actor, details from gba.audit_events "
            "where action = 'tenant.updated' and details ? 'status'",
        )
    assert events == [("platform:test", {"status": {"from": "active", "to": "suspended"}})]
    with pytest.raises(errors.InsufficientPrivilege):
        async with runtime_tx(app_pool, tenant=a.tenant_id, user=operator.user_id) as conn:
            await conn.execute("update gba.tenants set slug = 'renamed'")


async def test_runtime_role_remains_unprivileged(app_pool: RuntimePool) -> None:
    async with app_pool.connection() as conn:
        await assert_safe_runtime_role(conn)
        row = await (
            await conn.execute(
                "select rolsuper, rolbypassrls from pg_catalog.pg_roles "
                "where rolname = current_user"
            )
        ).fetchone()
    assert row == (False, False)


def test_identity_tables_force_row_level_security(owner_conn: psycopg.Connection) -> None:
    rows = owner_conn.execute(
        "select c.relname, c.relrowsecurity, c.relforcerowsecurity from pg_catalog.pg_class c "
        "join pg_catalog.pg_namespace n on n.oid = c.relnamespace where n.nspname = 'gba' "
        "and c.relname in ('users', 'user_identities', 'platform_roles', 'invitations', "
        "'audit_events') order by 1"
    ).fetchall()
    assert rows == [
        ("audit_events", True, True),
        ("invitations", True, True),
        ("platform_roles", True, True),
        ("user_identities", True, True),
        ("users", True, True),
    ]
