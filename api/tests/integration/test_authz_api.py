"""Authentication and tenant authorization over HTTP, against real PostgreSQL 18.

Numbered tests map to the M2 required behaviours. FAKE users, salons and IdP only.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid7

import httpx
import psycopg
import pytest

from gorgona_booking.api.app import create_app
from gorgona_booking.auth.permissions import Permission
from gorgona_booking.auth.principal import resolve_principal
from gorgona_booking.booking.service import BookingService
from gorgona_booking.config import Settings
from gorgona_booking.db.pool import RuntimePool, UnsafeDatabaseRoleError, tenant_transaction
from gorgona_booking.db.provisioning import (
    add_membership,
    grant_platform_admin,
    set_membership_status,
    set_user_status,
)
from gorgona_booking.tenancy.authorization import TenantAccessDeniedError, authorized_tenant
from tests.integration.booking_support import BookingWorld, at
from tests.integration.conftest import ProvisionedDatabase
from tests.integration.seed import FakeUser, seed_user
from tests.support.fake_idp import FakeIdp

pytestmark = pytest.mark.anyio


@dataclass(frozen=True, slots=True)
class AuthzWorld:
    world: BookingWorld
    idp: FakeIdp
    staff_a: FakeUser
    staff_a_membership: UUID
    admin_a: FakeUser
    admin_b: FakeUser
    platform: FakeUser
    outsider: FakeUser

    def auth(self, user: FakeUser) -> dict[str, str]:
        return self.idp.bearer(user.subject, email=user.email)


@pytest.fixture(scope="module")
def idp() -> FakeIdp:
    return FakeIdp()


@pytest.fixture
def authz(world: BookingWorld, owner_conn: psycopg.Connection, idp: FakeIdp) -> AuthzWorld:
    a, b = world.a.tenant_id, world.b.tenant_id
    staff_a, admin_a = seed_user(owner_conn, "staff-a"), seed_user(owner_conn, "admin-a")
    admin_b, platform = seed_user(owner_conn, "admin-b"), seed_user(owner_conn, "platform")
    staff_membership = add_membership(
        owner_conn, tenant_id=a, user_id=staff_a.user_id, role="artist"
    )
    add_membership(owner_conn, tenant_id=a, user_id=admin_a.user_id, role="owner")
    add_membership(owner_conn, tenant_id=b, user_id=admin_b.user_id, role="owner")
    grant_platform_admin(owner_conn, user_id=platform.user_id, granted_by="test")
    return AuthzWorld(
        world=world,
        idp=idp,
        staff_a=staff_a,
        staff_a_membership=staff_membership,
        admin_a=admin_a,
        admin_b=admin_b,
        platform=platform,
        outsider=seed_user(owner_conn, "outsider"),
    )


@pytest.fixture
async def client(app_pool: RuntimePool, idp: FakeIdp) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(environment="test"), pool=app_pool, token_verifier=idp.verifier())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as http:
        yield http


def _code(response: httpx.Response) -> str:
    return str(response.json()["error"]["code"])


async def _service_ids(client: httpx.AsyncClient, salon: UUID, headers: dict[str, str]) -> set[str]:
    response = await client.get(f"/v1/salons/{salon}/services", headers=headers)
    assert response.status_code == 200, response.text
    return {s["id"] for s in response.json()}


async def test_01_unauthenticated_requests_are_rejected(
    client: httpx.AsyncClient, authz: AuthzWorld
) -> None:
    url = f"/v1/salons/{authz.world.a.tenant_id}/services"
    missing = await client.get(url)
    assert (missing.status_code, _code(missing)) == (401, "AUTHENTICATION_REQUIRED")
    assert missing.headers["www-authenticate"].startswith("Bearer")
    basic = await client.get(url, headers={"Authorization": "Basic Zm9vOmJhcg=="})
    assert basic.status_code == 401
    garbage = await client.get(url, headers={"Authorization": "Bearer not-a-jwt"})
    assert (garbage.status_code, _code(garbage)) == (401, "INVALID_TOKEN")
    assert 'error="invalid_token"' in garbage.headers["www-authenticate"]
    expired = await client.get(
        url, headers=authz.idp.bearer(authz.staff_a.subject, expires_in=-120)
    )
    assert (expired.status_code, _code(expired)) == (401, "INVALID_TOKEN")
    unlinked = await client.get(url, headers=authz.idp.bearer("fake-sub-never-linked"))
    assert (unlinked.status_code, _code(unlinked)) == (403, "IDENTITY_NOT_LINKED")
    assert (await client.get("/v1/me")).status_code == 401


async def test_02_staff_reads_own_salon(
    client: httpx.AsyncClient, authz: AuthzWorld, app_pool: RuntimePool
) -> None:
    w = authz.world
    held = await BookingService(app_pool).create_hold(
        w.a.tenant_id, w.request(at(10)), actor="test", idempotency_key=None
    )
    headers = authz.auth(authz.staff_a)
    assert str(w.catalog_a.base_variant_id) in await _service_ids(client, w.a.tenant_id, headers)
    booking = await client.get(
        f"/v1/salons/{w.a.tenant_id}/bookings/{held.booking_id}", headers=headers
    )
    assert booking.status_code == 200
    assert booking.json()["booking_id"] == str(held.booking_id)
    staff = await client.get(f"/v1/salons/{w.a.tenant_id}/staff", headers=headers)
    assert str(w.artist_a1) in {s["id"] for s in staff.json()}


async def test_03_non_member_is_denied_uniformly(
    client: httpx.AsyncClient, authz: AuthzWorld
) -> None:
    w = authz.world
    attempts = [
        (w.b.tenant_id, authz.staff_a),
        (w.a.tenant_id, authz.outsider),
        (uuid7(), authz.staff_a),  # a salon that does not exist looks the same
    ]
    bodies = []
    for salon, user in attempts:
        response = await client.get(f"/v1/salons/{salon}/services", headers=authz.auth(user))
        assert (response.status_code, _code(response)) == (403, "TENANT_ACCESS_DENIED")
        bodies.append(response.json()["error"]["message"])
    assert len(set(bodies)) == 1


async def test_04_guessed_cross_salon_booking_id_is_not_found(
    client: httpx.AsyncClient, authz: AuthzWorld, app_pool: RuntimePool
) -> None:
    w = authz.world
    other = await BookingService(app_pool).create_hold(
        w.b.tenant_id,
        w.request(at(10), resource=w.artist_b1, variant=w.catalog_b.base_variant_id),
        actor="test",
        idempotency_key=None,
    )
    response = await client.get(
        f"/v1/salons/{w.a.tenant_id}/bookings/{other.booking_id}",
        headers=authz.auth(authz.staff_a),
    )
    assert (response.status_code, _code(response)) == (404, "NOT_FOUND")


async def test_05_client_supplied_tenant_cannot_change_effective_tenant(
    client: httpx.AsyncClient, authz: AuthzWorld, app_pool: RuntimePool
) -> None:
    w = authz.world
    admin = authz.auth(authz.admin_a)
    body = {
        "service_code": "FAKE_NEW",
        "service_name": "FAKE new service",
        "code": "FAKE_NEW_VARIANT",
        "name": "FAKE new variant",
        "price_cents": 1000,
        "currency": "USD",
    }
    smuggled = await client.post(
        f"/v1/salons/{w.a.tenant_id}/services",
        json=body | {"tenant_id": str(w.b.tenant_id)},
        headers=admin,
    )
    assert (smuggled.status_code, _code(smuggled)) == (422, "INVALID_REQUEST")
    spoofed_headers = admin | {"X-Tenant-Id": str(w.b.tenant_id), "X-Salon-Id": str(w.b.tenant_id)}
    ids = await _service_ids(client, w.a.tenant_id, spoofed_headers)
    assert str(w.catalog_a.base_variant_id) in ids
    assert str(w.catalog_b.base_variant_id) not in ids
    created = await client.post(
        f"/v1/salons/{w.a.tenant_id}/services", json=body, headers=spoofed_headers
    )
    assert created.status_code == 201, created.text
    new_id = UUID(created.json()["id"])
    for salon, visible in ((w.a.tenant_id, True), (w.b.tenant_id, False)):
        async with tenant_transaction(app_pool, salon) as conn:
            row = await (
                await conn.execute("select 1 from gba.service_variants where id = %s", (new_id,))
            ).fetchone()
        assert (row is not None) is visible


async def test_06_salon_admin_manages_own_services_and_staff(
    client: httpx.AsyncClient, authz: AuthzWorld
) -> None:
    w = authz.world
    admin = authz.auth(authz.admin_a)
    base = f"/v1/salons/{w.a.tenant_id}"
    draft = await client.post(
        f"{base}/services",
        json={
            "service_code": "FAKE_SVC",
            "service_name": "FAKE service",
            "code": "FAKE_NO_DURATION",
            "name": "FAKE no duration yet",
            "price_cents": 2500,
            "currency": "USD",
        },
        headers=admin,
    )
    assert draft.status_code == 201, draft.text
    assert (draft.json()["status"], draft.json()["is_bookable"]) == ("draft", False)
    refused = await client.post(f"{base}/services/{draft.json()['id']}/publish", headers=admin)
    assert (refused.status_code, _code(refused)) == (422, "SERVICE_NOT_BOOKABLE")
    assert refused.json()["error"]["details"]["reason"] == "booking_duration_unknown"

    timed = await client.post(
        f"{base}/services",
        json={
            "service_code": "FAKE_SVC",
            "service_name": "FAKE service",
            "code": "FAKE_TIMED",
            "name": "FAKE timed",
            "price_cents": 3000,
            "currency": "USD",
            "booking_duration_minutes": 45,
        },
        headers=admin,
    )
    published = await client.post(f"{base}/services/{timed.json()['id']}/publish", headers=admin)
    assert published.status_code == 200
    assert (published.json()["status"], published.json()["is_bookable"]) == ("published", True)

    staff = await client.post(
        f"{base}/staff",
        json={"display_name": "FAKE new artist", "location_id": str(w.a.location_id)},
        headers=admin,
    )
    assert staff.status_code == 201, staff.text

    as_artist = await client.post(
        f"{base}/staff",
        json={"display_name": "FAKE sneaky", "location_id": str(w.a.location_id)},
        headers=authz.auth(authz.staff_a),
    )
    assert (as_artist.status_code, _code(as_artist)) == (403, "PERMISSION_DENIED")


async def test_07_salon_admin_cannot_manage_another_salon(
    client: httpx.AsyncClient, authz: AuthzWorld
) -> None:
    w = authz.world
    admin_a = authz.auth(authz.admin_a)
    before = await _service_ids(client, w.b.tenant_id, authz.auth(authz.admin_b))
    for path, body in (
        (
            "services",
            {
                "service_code": "FAKE_X",
                "service_name": "FAKE x",
                "code": "FAKE_X",
                "name": "FAKE x",
                "price_cents": 1,
                "currency": "USD",
            },
        ),
        ("staff", {"display_name": "FAKE x", "location_id": str(w.b.location_id)}),
    ):
        response = await client.post(
            f"/v1/salons/{w.b.tenant_id}/{path}", json=body, headers=admin_a
        )
        assert (response.status_code, _code(response)) == (403, "TENANT_ACCESS_DENIED")
    assert await _service_ids(client, w.b.tenant_id, authz.auth(authz.admin_b)) == before
    # Own salon, but pointing at the other salon's location: rejected by the composite FK.
    crossed = await client.post(
        f"/v1/salons/{w.a.tenant_id}/staff",
        json={"display_name": "FAKE crossed", "location_id": str(w.b.location_id)},
        headers=admin_a,
    )
    assert (crossed.status_code, _code(crossed)) == (422, "INVALID_REFERENCE")


async def test_08_platform_admin_has_explicit_platform_permissions(
    client: httpx.AsyncClient, authz: AuthzWorld, app_pool: RuntimePool
) -> None:
    w = authz.world
    platform = authz.auth(authz.platform)
    assert str(w.catalog_a.base_variant_id) in await _service_ids(client, w.a.tenant_id, platform)
    write = await client.post(
        f"/v1/salons/{w.a.tenant_id}/staff",
        json={"display_name": "FAKE support", "location_id": str(w.a.location_id)},
        headers=platform,
    )
    assert (write.status_code, _code(write)) == (403, "PERMISSION_DENIED")
    by_owner = await client.post(
        f"/v1/platform/salons/{w.a.tenant_id}/suspend", headers=authz.auth(authz.admin_a)
    )
    assert (by_owner.status_code, _code(by_owner)) == (403, "PERMISSION_DENIED")
    suspended = await client.post(f"/v1/platform/salons/{w.a.tenant_id}/suspend", headers=platform)
    assert (suspended.status_code, suspended.json()["status"]) == (200, "suspended")
    reactivated = await client.post(
        f"/v1/platform/salons/{w.a.tenant_id}/reactivate", headers=platform
    )
    assert (reactivated.status_code, reactivated.json()["status"]) == (200, "active")
    async with tenant_transaction(app_pool, w.a.tenant_id) as conn:
        events = await (
            await conn.execute(
                "select action, actor from gba.audit_events "
                "where action in ('platform.tenant_access', 'tenant.updated') order by id"
            )
        ).fetchall()
    actor = f"user:{authz.platform.user_id}"
    assert ("platform.tenant_access", actor) in events
    assert events.count(("tenant.updated", actor)) == 2


async def test_09_revoked_or_suspended_membership_is_denied_on_the_next_request(
    client: httpx.AsyncClient, authz: AuthzWorld, owner_conn: psycopg.Connection
) -> None:
    w = authz.world
    headers = authz.auth(authz.staff_a)
    url = f"/v1/salons/{w.a.tenant_id}/services"
    assert (await client.get(url, headers=headers)).status_code == 200
    set_membership_status(
        owner_conn,
        tenant_id=w.a.tenant_id,
        membership_id=authz.staff_a_membership,
        status="suspended",
    )
    assert _code(await client.get(url, headers=headers)) == "TENANT_ACCESS_DENIED"
    set_membership_status(
        owner_conn,
        tenant_id=w.a.tenant_id,
        membership_id=authz.staff_a_membership,
        status="revoked",
    )
    denied = await client.get(url, headers=headers)
    assert (denied.status_code, _code(denied)) == (403, "TENANT_ACCESS_DENIED")


async def test_09b_concurrent_revoke_waits_for_the_in_flight_request(
    authz: AuthzWorld, app_pool: RuntimePool
) -> None:
    w = authz.world
    verified = await authz.idp.verifier().verify(authz.idp.token(authz.staff_a.subject))
    principal = await resolve_principal(app_pool, verified)

    async def revoke() -> None:
        async with tenant_transaction(app_pool, w.a.tenant_id) as conn:
            await conn.execute(
                "update gba.memberships set status = 'revoked' where id = %s",
                (authz.staff_a_membership,),
            )

    async with authorized_tenant(
        app_pool, principal, w.a.tenant_id, Permission.CATALOG_READ
    ) as access:
        assert access.role == "artist"
        task = asyncio.create_task(revoke())
        await asyncio.sleep(0.5)
        assert not task.done()  # blocked by the in-flight request's FOR SHARE lock
    await asyncio.wait_for(task, timeout=10)
    with pytest.raises(TenantAccessDeniedError):
        async with authorized_tenant(app_pool, principal, w.a.tenant_id, Permission.CATALOG_READ):
            pass


async def test_10_suspended_salon_denies_members_and_public_booking(
    client: httpx.AsyncClient, authz: AuthzWorld
) -> None:
    w = authz.world
    platform, staff = authz.auth(authz.platform), authz.auth(authz.staff_a)
    await client.post(f"/v1/platform/salons/{w.a.tenant_id}/suspend", headers=platform)
    member = await client.get(f"/v1/salons/{w.a.tenant_id}/services", headers=staff)
    assert (member.status_code, _code(member)) == (403, "TENANT_SUSPENDED")
    public = await client.post(
        "/v1/holds",
        json={
            "resource_id": str(w.artist_a1),
            "variant_id": str(w.catalog_a.base_variant_id),
            "start_at": at(10).isoformat(),
        },
        headers={"Idempotency-Key": "suspended-0001", "Host": w.a.host},
    )
    assert (public.status_code, _code(public)) == (404, "TENANT_NOT_FOUND")
    assert (
        await client.get(f"/v1/salons/{w.a.tenant_id}/services", headers=platform)
    ).status_code == 200
    await client.post(f"/v1/platform/salons/{w.a.tenant_id}/reactivate", headers=platform)
    assert (
        await client.get(f"/v1/salons/{w.a.tenant_id}/services", headers=staff)
    ).status_code == 200


async def test_me_lists_only_the_callers_memberships(
    client: httpx.AsyncClient, authz: AuthzWorld
) -> None:
    me = await client.get("/v1/me", headers=authz.auth(authz.admin_a))
    assert me.status_code == 200
    body = me.json()
    assert body["user_id"] == str(authz.admin_a.user_id)
    assert [(m["salon_id"], m["role"], m["status"]) for m in body["memberships"]] == [
        (str(authz.world.a.tenant_id), "owner", "active")
    ]
    assert body["platform_roles"] == []
    platform = (await client.get("/v1/me", headers=authz.auth(authz.platform))).json()
    assert (platform["platform_roles"], platform["memberships"]) == (["platform_admin"], [])


async def test_disabled_user_is_denied(
    client: httpx.AsyncClient, authz: AuthzWorld, owner_conn: psycopg.Connection
) -> None:
    set_user_status(owner_conn, user_id=authz.staff_a.user_id, status="disabled")
    response = await client.get(
        f"/v1/salons/{authz.world.a.tenant_id}/services", headers=authz.auth(authz.staff_a)
    )
    assert (response.status_code, _code(response)) == (403, "USER_DISABLED")


async def test_protected_routes_need_configured_authentication(
    app_pool: RuntimePool, authz: AuthzWorld
) -> None:
    app = create_app(Settings(environment="test"), pool=app_pool)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as http:
        response = await http.get(
            f"/v1/salons/{authz.world.a.tenant_id}/services", headers=authz.auth(authz.admin_a)
        )
        assert (response.status_code, _code(response)) == (503, "AUTH_NOT_CONFIGURED")
        assert (await http.get("/health/live")).status_code == 200


async def test_14_privileged_database_credentials_are_still_refused(
    test_database: ProvisionedDatabase,
) -> None:
    for dsn in (
        test_database.owner_dsn,
        psycopg.conninfo.make_conninfo(test_database.admin_dsn, dbname=test_database.name),
    ):
        app = create_app(Settings(environment="test", database_url=dsn))
        with pytest.raises(UnsafeDatabaseRoleError):
            async with app.router.lifespan_context(app):
                pass
