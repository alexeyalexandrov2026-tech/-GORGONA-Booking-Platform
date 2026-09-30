"""Platform-admin routes. Salon roles never include platform permissions."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from gorgona_booking.api.deps import get_principal, runtime_pool
from gorgona_booking.api.request_id import get_request_id
from gorgona_booking.auth.permissions import Permission
from gorgona_booking.auth.principal import Principal
from gorgona_booking.errors import NotFoundError
from gorgona_booking.tenancy.authorization import authorized_tenant

router = APIRouter(prefix="/v1/platform", tags=["platform"])

CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


class TenantStatusView(BaseModel):
    salon_id: UUID
    status: str


async def _set_status(
    request: Request, principal: Principal, salon_id: UUID, status: Literal["active", "suspended"]
) -> TenantStatusView:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.PLATFORM_TENANT_STATUS,
        request_id=get_request_id(request),
    ) as access:
        # Also guarded in the database: only platform admins may change tenant status.
        row = await (
            await access.conn.execute(
                "update gba.tenants set status = %s where id = %s returning id, status",
                (status, salon_id),
            )
        ).fetchone()
    if row is None:
        raise NotFoundError("Salon not found")
    return TenantStatusView(salon_id=row[0], status=row[1])


@router.post("/salons/{salon_id}/suspend")
async def suspend(
    salon_id: UUID, request: Request, principal: CurrentPrincipal
) -> TenantStatusView:
    return await _set_status(request, principal, salon_id, "suspended")


@router.post("/salons/{salon_id}/reactivate")
async def reactivate(
    salon_id: UUID, request: Request, principal: CurrentPrincipal
) -> TenantStatusView:
    return await _set_status(request, principal, salon_id, "active")
