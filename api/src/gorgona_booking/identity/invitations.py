"""Invitations and membership lifecycle (ADR-0008).

Invitation tokens are random, shown once, and stored only as SHA-256. Acceptance
requires a verified email matching the invitation, is serialized by a row lock,
and is idempotent for the same person.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID, uuid7

from psycopg import errors as pg

from gorgona_booking.auth.permissions import ADMIN_ROLES, ROLE_PERMISSIONS, Permission
from gorgona_booking.auth.principal import linked_user_id, set_user_context
from gorgona_booking.auth.verifier import VerifiedToken
from gorgona_booking.db.pool import RuntimeConnection, RuntimePool, set_tenant_context
from gorgona_booking.db.provisioning import normalize_email
from gorgona_booking.errors import ConflictError, DomainError, NotFoundError
from gorgona_booking.tenancy.authorization import PermissionDeniedError, TenantAccess

INVITATION_TTL = timedelta(days=7)
type MemberAction = Literal["suspend", "reactivate", "revoke"]
_ACTION_STATUS: dict[str, str] = {
    "suspend": "suspended",
    "reactivate": "active",
    "revoke": "revoked",
}


class EmailNotVerifiedError(DomainError):
    code = "EMAIL_NOT_VERIFIED"


class InvitationNotFoundError(DomainError):
    code = "INVITATION_NOT_FOUND"


class InvitationAlreadyUsedError(DomainError):
    code = "INVITATION_ALREADY_USED"


class InvitationNotUsableError(DomainError):
    code = "INVITATION_NOT_USABLE"


class InvitationEmailMismatchError(DomainError):
    code = "INVITATION_EMAIL_MISMATCH"


class InvitationPendingError(DomainError):
    code = "INVITATION_PENDING"


class AlreadyMemberError(DomainError):
    code = "ALREADY_MEMBER"


class CannotModifySelfError(DomainError):
    code = "CANNOT_MODIFY_SELF"


class LastOwnerError(DomainError):
    code = "LAST_OWNER"


class MembershipRevokedError(DomainError):
    code = "MEMBERSHIP_REVOKED"


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _require_role_authority(access: TenantAccess, role: str) -> None:
    """Only an owner may grant or change owner/manager memberships."""
    if role in ADMIN_ROLES and Permission.MEMBERS_MANAGE_ADMINS not in ROLE_PERMISSIONS.get(
        access.role or "", frozenset()
    ):
        raise PermissionDeniedError("Only a salon owner can manage admin roles")


@dataclass(frozen=True, slots=True)
class CreatedInvitation:
    invitation_id: UUID
    email: str
    role: str
    expires_at: datetime
    token: str  # plaintext, returned to the caller exactly once


async def create_invitation(access: TenantAccess, email: str, role: str) -> CreatedInvitation:
    _require_role_authority(access, role)
    normalized = normalize_email(email)
    conn = access.conn
    member = await (
        await conn.execute(
            "select 1 from gba.memberships m join gba.users u on u.id = m.user_id "
            "where u.email_normalized = %s and m.status <> 'revoked'",
            (normalized,),
        )
    ).fetchone()
    if member is not None:
        raise AlreadyMemberError("This person is already a member of the salon")
    token = secrets.token_urlsafe(32)
    try:
        row = await (
            await conn.execute(
                "insert into gba.invitations (tenant_id, email_normalized, role, token_sha256, "
                "expires_at) values (%s, %s, %s, %s, now() + %s) returning id, expires_at",
                (access.tenant_id, normalized, role, token_digest(token), INVITATION_TTL),
            )
        ).fetchone()
    except pg.UniqueViolation as exc:
        if exc.diag.constraint_name == "invitations_one_pending_per_email":
            raise InvitationPendingError(
                "An invitation for this email is already pending"
            ) from None
        raise
    assert row is not None  # noqa: S101 - INSERT ... RETURNING always yields a row
    return CreatedInvitation(UUID(str(row[0])), normalized, role, row[1], token)


async def revoke_invitation(access: TenantAccess, invitation_id: UUID) -> None:
    row = await (
        await access.conn.execute(
            "select role, status from gba.invitations where id = %s for update", (invitation_id,)
        )
    ).fetchone()
    if row is None:
        raise NotFoundError("Invitation not found")
    _require_role_authority(access, str(row[0]))
    if row[1] != "pending":
        raise InvitationNotUsableError("Only a pending invitation can be revoked")
    await access.conn.execute(
        "update gba.invitations set status = 'revoked' where id = %s", (invitation_id,)
    )


@dataclass(frozen=True, slots=True)
class AcceptedInvitation:
    membership_id: UUID
    user_id: UUID
    role: str
    created: bool


async def accept_invitation(
    pool: RuntimePool,
    token: VerifiedToken,
    salon_id: UUID,
    invitation_token: str,
    *,
    request_id: str | None = None,
) -> AcceptedInvitation:
    if not token.email or not token.email_verified:
        raise EmailNotVerifiedError("A verified email address is required to accept")
    outcome: AcceptedInvitation | DomainError
    async with pool.connection() as conn, conn.transaction():
        await set_tenant_context(conn, salon_id)
        # Locked first, so the identity lookup below sees any concurrent acceptance.
        invitation = await (
            await conn.execute(
                "select i.id, i.email_normalized, i.role, i.status, i.expires_at <= now(), "
                "i.accepted_by_user_id, i.accepted_membership_id, t.status "
                "from gba.invitations i join gba.tenants t on t.id = i.tenant_id "
                "where i.token_sha256 = %s for update of i",
                (token_digest(invitation_token),),
            )
        ).fetchone()
        if invitation is None or invitation[7] != "active":
            raise InvitationNotFoundError("Invitation not found")
        inv_id, email, role, status, expired, accepted_by, accepted_membership, _ = invitation
        existing_user = await linked_user_id(conn, token)

        if status == "accepted":
            if existing_user is not None and existing_user == accepted_by:
                return AcceptedInvitation(accepted_membership, accepted_by, role, created=False)
            raise InvitationAlreadyUsedError("This invitation has already been used")
        if status != "pending" or expired:
            if status == "pending":
                await conn.execute(
                    "update gba.invitations set status = 'expired' where id = %s", (inv_id,)
                )
            outcome = InvitationNotUsableError("This invitation has expired or was withdrawn")
        elif normalize_email(token.email) != email:
            raise InvitationEmailMismatchError("This invitation was sent to a different email")
        else:
            user_id = existing_user or await _create_user(conn, token)
            await set_user_context(conn, user_id, request_id=request_id)
            try:
                async with conn.transaction():
                    membership = await (
                        await conn.execute(
                            "insert into gba.memberships (tenant_id, user_id, role) "
                            "values (%s, %s, %s) returning id",
                            (salon_id, user_id, role),
                        )
                    ).fetchone()
            except pg.UniqueViolation:
                raise AlreadyMemberError("You are already a member of this salon") from None
            assert membership is not None  # noqa: S101
            await conn.execute(
                "update gba.invitations set status = 'accepted', accepted_by_user_id = %s, "
                "accepted_membership_id = %s, accepted_at = now() where id = %s",
                (user_id, membership[0], inv_id),
            )
            outcome = AcceptedInvitation(UUID(str(membership[0])), user_id, role, created=True)
    if isinstance(outcome, DomainError):
        raise outcome  # after commit: the expiry is recorded
    return outcome


class _IdentityRaceError(Exception):
    pass


async def _create_user(conn: RuntimeConnection, token: VerifiedToken) -> UUID:
    """Create a user linked to this token. A concurrent first sign-in wins cleanly."""
    user_id = uuid7()
    display = (token.name or (token.email or "").split("@")[0] or "Member")[:200]
    try:
        async with conn.transaction():
            await conn.execute(
                "select pg_catalog.set_config('gba.user_id', %s, true), "
                "pg_catalog.set_config('gba.actor', %s, true)",
                (str(user_id), f"user:{user_id}"),
            )
            await conn.execute(
                "insert into gba.users (id, display_name, email_normalized) values (%s, %s, %s)",
                (user_id, display, normalize_email(token.email) if token.email else None),
            )
            cur = await conn.execute(
                "insert into gba.user_identities (user_id, issuer, subject) values (%s, %s, %s) "
                "on conflict (issuer, subject) do nothing",
                (user_id, token.issuer, token.subject),
            )
            if cur.rowcount != 1:
                raise _IdentityRaceError
    except _IdentityRaceError:
        existing = await linked_user_id(conn, token)
        if existing is None:
            raise ConflictError("Sign-in could not be linked; retry") from None
        return existing
    return user_id


@dataclass(frozen=True, slots=True)
class MemberView:
    membership_id: UUID
    user_id: UUID
    display_name: str | None
    role: str
    status: str


async def list_members(access: TenantAccess) -> list[MemberView]:
    rows = await (
        await access.conn.execute(
            "select m.id, m.user_id, u.display_name, m.role, m.status "
            "from gba.memberships m left join gba.users u on u.id = m.user_id "
            "order by m.created_at"
        )
    ).fetchall()
    return [MemberView(r[0], r[1], r[2], r[3], r[4]) for r in rows]


async def change_member_status(
    access: TenantAccess, membership_id: UUID, action: MemberAction
) -> MemberView:
    conn = access.conn
    target = await (
        await conn.execute(
            "select user_id, role, status from gba.memberships where id = %s for update",
            (membership_id,),
        )
    ).fetchone()
    if target is None:
        raise NotFoundError("Member not found")
    user_id, role, status = target
    if user_id == access.principal.user_id:
        raise CannotModifySelfError("You cannot change your own membership")
    _require_role_authority(access, str(role))
    if status == "revoked":
        raise MembershipRevokedError("A revoked membership cannot change")
    new_status = _ACTION_STATUS[action]
    if role == "owner" and new_status != "active":
        owners = await (
            await conn.execute(
                "select count(*) from gba.memberships "
                "where role = 'owner' and status = 'active' and id <> %s",
                (membership_id,),
            )
        ).fetchone()
        if owners is None or owners[0] == 0:
            raise LastOwnerError("A salon must keep at least one active owner")
    await conn.execute(
        "update gba.memberships set status = %s where id = %s", (new_status, membership_id)
    )
    member = await (
        await conn.execute(
            "select m.id, m.user_id, u.display_name, m.role, m.status "
            "from gba.memberships m left join gba.users u on u.id = m.user_id where m.id = %s",
            (membership_id,),
        )
    ).fetchone()
    assert member is not None  # noqa: S101
    return MemberView(member[0], member[1], member[2], member[3], member[4])
