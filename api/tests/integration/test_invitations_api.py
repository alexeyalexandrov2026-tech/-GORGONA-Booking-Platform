"""Invitations and membership lifecycle over HTTP, on real PostgreSQL 18 (FAKE data)."""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import psycopg
import pytest

from gorgona_booking.api.app import create_app
from gorgona_booking.config import Settings
from gorgona_booking.db.pool import RuntimePool, tenant_transaction
from gorgona_booking.db.provisioning import add_membership, owner_tenant_transaction
from tests.integration.booking_support import BookingWorld
from tests.integration.seed import FakeUser, seed_user
from tests.support.fake_idp import FAKE_ISSUER, FakeIdp

pytestmark = pytest.mark.anyio


@dataclass(frozen=True, slots=True)
class Team:
    world: BookingWorld
    idp: FakeIdp
    owner: FakeUser
    co_owner: FakeUser
    manager: FakeUser
    artist: FakeUser
    artist_membership: UUID

    def auth(self, user: FakeUser) -> dict[str, str]:
        return self.idp.bearer(user.subject, email=user.email)

    @property
    def salon(self) -> str:
        return f"/v1/salons/{self.world.a.tenant_id}"


@pytest.fixture(scope="module")
def idp() -> FakeIdp:
    return FakeIdp()


@pytest.fixture
def team(world: BookingWorld, owner_conn: psycopg.Connection, idp: FakeIdp) -> Team:
    a = world.a.tenant_id
    users = {
        name: seed_user(owner_conn, name) for name in ("owner", "co-owner", "manager", "artist")
    }
    add_membership(owner_conn, tenant_id=a, user_id=users["owner"].user_id, role="owner")
    add_membership(owner_conn, tenant_id=a, user_id=users["co-owner"].user_id, role="owner")
    add_membership(owner_conn, tenant_id=a, user_id=users["manager"].user_id, role="manager")
    artist_membership = add_membership(
        owner_conn, tenant_id=a, user_id=users["artist"].user_id, role="artist"
    )
    return Team(
        world=world,
        idp=idp,
        owner=users["owner"],
        co_owner=users["co-owner"],
        manager=users["manager"],
        artist=users["artist"],
        artist_membership=artist_membership,
    )


@pytest.fixture
async def client(app_pool: RuntimePool, idp: FakeIdp) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(environment="test"), pool=app_pool, token_verifier=idp.verifier())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as http:
        yield http


def _code(response: httpx.Response) -> str:
    return str(response.json()["error"]["code"])


def _newcomer(
    idp: FakeIdp, label: str, *, email: str | None = None, verified: bool = True
) -> dict[str, str]:
    """A person the platform has never seen: a valid IdP token, no linked account."""
    return idp.bearer(
        f"fake-new-{label}", email=email or f"{label}@example.test", email_verified=verified
    )


async def _invite(
    client: httpx.AsyncClient,
    team: Team,
    email: str,
    role: str = "artist",
    by: FakeUser | None = None,
) -> httpx.Response:
    return await client.post(
        f"{team.salon}/invitations",
        json={"email": email, "role": role},
        headers=team.auth(by or team.owner),
    )


def _accept_body(team: Team, token: str) -> dict[str, Any]:
    return {"salon_id": str(team.world.a.tenant_id), "token": token}


async def test_11_invitation_acceptance_is_idempotent(
    client: httpx.AsyncClient, team: Team
) -> None:
    invited = await _invite(client, team, "Newcomer@Example.test")
    assert invited.status_code == 201, invited.text
    token = invited.json()["token"]
    assert invited.json()["email"] == "newcomer@example.test"
    newcomer = _newcomer(team.idp, "newcomer", email="newcomer@example.test")

    first = await client.post(
        "/v1/invitations/accept", json=_accept_body(team, token), headers=newcomer
    )
    assert first.status_code == 201, first.text
    assert (first.json()["role"], first.json()["created"]) == ("artist", True)
    again = await client.post(
        "/v1/invitations/accept", json=_accept_body(team, token), headers=newcomer
    )
    assert again.status_code == 200
    assert again.json()["membership_id"] == first.json()["membership_id"]
    assert again.json()["created"] is False

    services = await client.get(f"{team.salon}/services", headers=newcomer)
    assert services.status_code == 200
    someone_else = _newcomer(team.idp, "late", email="newcomer@example.test")
    stolen = await client.post(
        "/v1/invitations/accept", json=_accept_body(team, token), headers=someone_else
    )
    assert (stolen.status_code, _code(stolen)) == (409, "INVITATION_ALREADY_USED")


async def test_11b_concurrent_acceptance_creates_exactly_one_account(
    client: httpx.AsyncClient, team: Team, app_pool: RuntimePool
) -> None:
    token = (await _invite(client, team, "racer@example.test")).json()["token"]
    headers = _newcomer(team.idp, "racer", email="racer@example.test")
    responses = await asyncio.gather(
        *(
            client.post("/v1/invitations/accept", json=_accept_body(team, token), headers=headers)
            for _ in range(10)
        )
    )
    assert sorted(r.status_code for r in responses) == [200] * 9 + [201]
    assert len({r.json()["membership_id"] for r in responses}) == 1
    async with tenant_transaction(app_pool, team.world.a.tenant_id) as conn:
        await conn.execute(
            "select pg_catalog.set_config('gba.auth_issuer', %s, true), "
            "pg_catalog.set_config('gba.auth_subject', %s, true)",
            (FAKE_ISSUER, "fake-new-racer"),
        )
        identities = await (
            await conn.execute("select user_id from gba.user_identities")
        ).fetchall()
        members = await (
            await conn.execute(
                "select count(*) from gba.memberships where user_id = %s", (identities[0][0],)
            )
        ).fetchone()
    assert len(identities) == 1
    assert members == (1,)


@pytest.mark.parametrize(
    ("case", "status", "code"),
    [
        ("unverified_email", 403, "EMAIL_NOT_VERIFIED"),
        ("different_email", 403, "INVITATION_EMAIL_MISMATCH"),
        ("wrong_salon", 404, "INVITATION_NOT_FOUND"),
        ("bogus_token", 404, "INVITATION_NOT_FOUND"),
        ("no_token", 401, "AUTHENTICATION_REQUIRED"),
    ],
)
async def test_invitation_acceptance_rules(
    client: httpx.AsyncClient, team: Team, case: str, status: int, code: str
) -> None:
    token = (await _invite(client, team, "rules@example.test")).json()["token"]
    body = _accept_body(team, token)
    headers = _newcomer(team.idp, "rules", email="rules@example.test")
    match case:
        case "unverified_email":
            headers = _newcomer(team.idp, "rules", email="rules@example.test", verified=False)
        case "different_email":
            headers = _newcomer(team.idp, "rules", email="other@example.test")
        case "wrong_salon":
            body["salon_id"] = str(team.world.b.tenant_id)
        case "bogus_token":
            body["token"] = "x" * 43
        case "no_token":
            headers = {}
    response = await client.post("/v1/invitations/accept", json=body, headers=headers)
    assert (response.status_code, _code(response)) == (status, code)


async def test_expired_invitation_cannot_be_used_and_is_marked_expired(
    client: httpx.AsyncClient, team: Team, owner_conn: psycopg.Connection
) -> None:
    invited = (await _invite(client, team, "late@example.test")).json()
    with owner_tenant_transaction(owner_conn, team.world.a.tenant_id):
        owner_conn.execute(
            "update gba.invitations set created_at = now() - interval '9 days', "
            "expires_at = now() - interval '2 days' where id = %s",
            (invited["invitation_id"],),
        )
    response = await client.post(
        "/v1/invitations/accept",
        json=_accept_body(team, invited["token"]),
        headers=_newcomer(team.idp, "late", email="late@example.test"),
    )
    assert (response.status_code, _code(response)) == (409, "INVITATION_NOT_USABLE")
    with owner_tenant_transaction(owner_conn, team.world.a.tenant_id):
        row = owner_conn.execute(
            "select status from gba.invitations where id = %s", (invited["invitation_id"],)
        ).fetchone()
    assert row == ("expired",)


async def test_invitation_permissions_and_duplicates(client: httpx.AsyncClient, team: Team) -> None:
    by_manager = await _invite(client, team, "boss@example.test", role="owner", by=team.manager)
    assert (by_manager.status_code, _code(by_manager)) == (403, "PERMISSION_DENIED")
    by_artist = await _invite(client, team, "x@example.test", by=team.artist)
    assert (by_artist.status_code, _code(by_artist)) == (403, "PERMISSION_DENIED")
    assert (await _invite(client, team, "boss@example.test", role="manager")).status_code == 201
    duplicate = await _invite(client, team, "boss@example.test", role="artist")
    assert (duplicate.status_code, _code(duplicate)) == (409, "INVITATION_PENDING")
    member = await _invite(client, team, team.artist.email)
    assert (member.status_code, _code(member)) == (409, "ALREADY_MEMBER")
    smuggled = await client.post(
        f"{team.salon}/invitations",
        json={
            "email": "y@example.test",
            "role": "artist",
            "tenant_id": str(team.world.b.tenant_id),
        },
        headers=team.auth(team.owner),
    )
    assert smuggled.status_code == 422


async def test_invitation_token_is_shown_once_and_stored_only_as_a_hash(
    client: httpx.AsyncClient, team: Team, owner_conn: psycopg.Connection
) -> None:
    invited = (await _invite(client, team, "hash@example.test")).json()
    token = invited["token"]
    assert len(token) >= 40
    with owner_tenant_transaction(owner_conn, team.world.a.tenant_id):
        stored = owner_conn.execute(
            "select token_sha256 from gba.invitations where id = %s", (invited["invitation_id"],)
        ).fetchone()
        dumps = owner_conn.execute(
            "select coalesce(string_agg(i::text, ' '), '') from gba.invitations i"
        ).fetchone()
        audit = owner_conn.execute(
            "select coalesce(string_agg(e::text, ' '), '') from gba.audit_events e"
        ).fetchone()
    assert stored == (hashlib.sha256(token.encode()).hexdigest(),)
    assert dumps is not None
    assert token not in dumps[0]
    assert audit is not None
    assert token not in audit[0]
    listed = await client.get(f"{team.salon}/members", headers=team.auth(team.owner))
    assert token not in listed.text


async def test_member_lifecycle_takes_effect_immediately_and_is_audited(
    client: httpx.AsyncClient, team: Team, app_pool: RuntimePool
) -> None:
    artist, manager = team.auth(team.artist), team.auth(team.manager)
    member = f"{team.salon}/members/{team.artist_membership}"
    assert (await client.post(f"{member}/suspend", headers=manager)).json()["status"] == "suspended"
    assert (
        _code(await client.get(f"{team.salon}/services", headers=artist)) == "TENANT_ACCESS_DENIED"
    )
    assert (await client.post(f"{member}/reactivate", headers=manager)).json()["status"] == "active"
    assert (await client.get(f"{team.salon}/services", headers=artist)).status_code == 200
    assert (await client.post(f"{member}/revoke", headers=manager)).json()["status"] == "revoked"
    back = await client.post(f"{member}/reactivate", headers=manager)
    assert (back.status_code, _code(back)) == (409, "MEMBERSHIP_REVOKED")
    assert (
        _code(await client.get(f"{team.salon}/services", headers=artist)) == "TENANT_ACCESS_DENIED"
    )

    async with tenant_transaction(app_pool, team.world.a.tenant_id) as conn:
        events = await (
            await conn.execute(
                "select actor, details from gba.audit_events "
                "where action = 'membership.updated' and target_id = %s order by id",
                (str(team.artist_membership),),
            )
        ).fetchall()
    actor = f"user:{team.manager.user_id}"
    assert [(a, d["status"]["to"]) for a, d in events] == [
        (actor, "suspended"),
        (actor, "active"),
        (actor, "revoked"),
    ]


async def test_admin_membership_changes_need_an_owner_and_self_changes_are_refused(
    client: httpx.AsyncClient, team: Team
) -> None:
    members = (await client.get(f"{team.salon}/members", headers=team.auth(team.owner))).json()
    by_user = {m["user_id"]: m["membership_id"] for m in members}
    owner_membership = by_user[str(team.owner.user_id)]
    manager_membership = by_user[str(team.manager.user_id)]

    manager_on_owner = await client.post(
        f"{team.salon}/members/{owner_membership}/suspend", headers=team.auth(team.manager)
    )
    assert (manager_on_owner.status_code, _code(manager_on_owner)) == (403, "PERMISSION_DENIED")
    self_change = await client.post(
        f"{team.salon}/members/{owner_membership}/revoke", headers=team.auth(team.owner)
    )
    assert (self_change.status_code, _code(self_change)) == (409, "CANNOT_MODIFY_SELF")
    owner_on_manager = await client.post(
        f"{team.salon}/members/{manager_membership}/suspend", headers=team.auth(team.owner)
    )
    assert owner_on_manager.status_code == 200


async def test_owners_revoking_each_other_concurrently_leave_one_owner(
    client: httpx.AsyncClient, team: Team, app_pool: RuntimePool
) -> None:
    members = (await client.get(f"{team.salon}/members", headers=team.auth(team.owner))).json()
    by_user = {m["user_id"]: m["membership_id"] for m in members}
    first, second = await asyncio.gather(
        client.post(
            f"{team.salon}/members/{by_user[str(team.co_owner.user_id)]}/revoke",
            headers=team.auth(team.owner),
        ),
        client.post(
            f"{team.salon}/members/{by_user[str(team.owner.user_id)]}/revoke",
            headers=team.auth(team.co_owner),
        ),
    )
    assert sorted([first.status_code, second.status_code]) == [200, 403]
    async with tenant_transaction(app_pool, team.world.a.tenant_id) as conn:
        owners = await (
            await conn.execute(
                "select count(*) from gba.memberships where role = 'owner' and status = 'active'"
            )
        ).fetchone()
    assert owners == (1,)
