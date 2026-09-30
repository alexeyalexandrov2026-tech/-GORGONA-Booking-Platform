"""Members and invitations. Membership changes are serialized per salon."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from gorgona_booking.api.deps import get_principal, get_verified_token, runtime_pool
from gorgona_booking.api.request_id import get_request_id
from gorgona_booking.auth.permissions import Permission
from gorgona_booking.auth.principal import Principal
from gorgona_booking.auth.verifier import VerifiedToken
from gorgona_booking.identity import invitations
from gorgona_booking.tenancy.authorization import authorized_tenant

router = APIRouter(prefix="/v1", tags=["members"])

CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
Role = Literal["owner", "manager", "front_desk", "artist"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InvitationCreate(Strict):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: Role


class InvitationCreated(BaseModel):
    invitation_id: UUID
    email: str
    role: str
    expires_at: datetime
    token: str  # shown once; only its SHA-256 is stored


class InvitationAccept(Strict):
    salon_id: UUID
    token: str = Field(min_length=20, max_length=200)


class AcceptedView(BaseModel):
    membership_id: UUID
    user_id: UUID
    salon_id: UUID
    role: str
    created: bool


class MemberView(BaseModel):
    membership_id: UUID
    user_id: UUID
    display_name: str | None
    role: str
    status: str


def _member(view: invitations.MemberView) -> MemberView:
    return MemberView(
        membership_id=view.membership_id,
        user_id=view.user_id,
        display_name=view.display_name,
        role=view.role,
        status=view.status,
    )


@router.post("/salons/{salon_id}/invitations", status_code=201)
async def create_invitation(
    salon_id: UUID, body: InvitationCreate, request: Request, principal: CurrentPrincipal
) -> InvitationCreated:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.MEMBERS_MANAGE,
        request_id=get_request_id(request),
        exclusive="members",
    ) as access:
        created = await invitations.create_invitation(access, body.email, body.role)
    return InvitationCreated(
        invitation_id=created.invitation_id,
        email=created.email,
        role=created.role,
        expires_at=created.expires_at,
        token=created.token,
    )


@router.post("/salons/{salon_id}/invitations/{invitation_id}/revoke", status_code=204)
async def revoke_invitation(
    salon_id: UUID, invitation_id: UUID, request: Request, principal: CurrentPrincipal
) -> Response:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.MEMBERS_MANAGE,
        request_id=get_request_id(request),
        exclusive="members",
    ) as access:
        await invitations.revoke_invitation(access, invitation_id)
    return Response(status_code=204)


@router.post("/invitations/accept")
async def accept_invitation(
    body: InvitationAccept,
    request: Request,
    response: Response,
    token: Annotated[VerifiedToken, Depends(get_verified_token)],
) -> AcceptedView:
    """Authenticated but not yet a member: the invitation itself is the grant."""
    accepted = await invitations.accept_invitation(
        runtime_pool(request), token, body.salon_id, body.token, request_id=get_request_id(request)
    )
    response.status_code = 201 if accepted.created else 200
    return AcceptedView(
        membership_id=accepted.membership_id,
        user_id=accepted.user_id,
        salon_id=body.salon_id,
        role=accepted.role,
        created=accepted.created,
    )


@router.get("/salons/{salon_id}/members")
async def list_members(
    salon_id: UUID, request: Request, principal: CurrentPrincipal
) -> list[MemberView]:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.MEMBERS_MANAGE,
        request_id=get_request_id(request),
    ) as access:
        members = await invitations.list_members(access)
    return [_member(m) for m in members]


@router.post("/salons/{salon_id}/members/{membership_id}/{action}")
async def change_member_status(
    salon_id: UUID,
    membership_id: UUID,
    action: Literal["suspend", "reactivate", "revoke"],
    request: Request,
    principal: CurrentPrincipal,
) -> MemberView:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.MEMBERS_MANAGE,
        request_id=get_request_id(request),
        exclusive="members",
    ) as access:
        member = await invitations.change_member_status(access, membership_id, action)
    return _member(member)
