"""Customer surface; every route resolves the live tenant from Host."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response

from gorgona_booking.api.holds import get_tenant_id
from gorgona_booking.api.request_id import get_request_id
from gorgona_booking.customer import queries
from gorgona_booking.customer.contracts import (
    AvailabilityQuery,
    AvailabilityView,
    BootstrapView,
    CustomerBookingView,
    CustomerDetails,
    CustomerHold,
)
from gorgona_booking.customer.service import CustomerBookingService, public_booking
from gorgona_booking.db.pool import RuntimePool, tenant_transaction
from gorgona_booking.errors import DatabaseUnavailableError

router = APIRouter(prefix="/v1/customer", tags=["customer"])
type Tenant = Annotated[UUID, Depends(get_tenant_id)]
type Token = Annotated[str, Header(alias="Booking-Token", pattern=r"^[A-Za-z0-9_-]{43}$")]
type Key = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=255, pattern=r"^[A-Za-z0-9._:-]+$"),
]


def pool_for(request: Request) -> RuntimePool:
    pool: RuntimePool | None = getattr(request.app.state, "pool", None)
    if pool is None:
        raise DatabaseUnavailableError("Booking is not available right now")
    return pool


@router.get("/bootstrap")
async def bootstrap(request: Request, response: Response, tenant_id: Tenant) -> BootstrapView:
    response.headers["Cache-Control"] = "no-store"
    async with tenant_transaction(pool_for(request), tenant_id) as conn:
        return await queries.bootstrap(conn)


@router.post("/availability")
async def availability(
    body: AvailabilityQuery, request: Request, response: Response, tenant_id: Tenant
) -> AvailabilityView:
    response.headers["Cache-Control"] = "no-store"
    async with tenant_transaction(pool_for(request), tenant_id) as conn:
        return await queries.availability(conn, body)


@router.post("/holds", status_code=201)
async def hold(
    body: CustomerHold,
    request: Request,
    response: Response,
    tenant_id: Tenant,
    token: Token,
    key: Key,
) -> CustomerBookingView:
    response.headers["Cache-Control"] = "no-store"
    result = await CustomerBookingService(
        pool_for(request), ttl=request.app.state.settings.hold_ttl_seconds
    ).hold(tenant_id, body, token=token, key=key, request_id=get_request_id(request))
    if result.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return public_booking(result)


@router.post("/bookings/{booking_id}/confirm")
async def confirm(
    booking_id: UUID,
    body: CustomerDetails,
    request: Request,
    response: Response,
    tenant_id: Tenant,
    token: Token,
    key: Key,
) -> CustomerBookingView:
    response.headers["Cache-Control"] = "no-store"
    result = await CustomerBookingService(
        pool_for(request), ttl=request.app.state.settings.hold_ttl_seconds
    ).confirm(tenant_id, booking_id, body, token=token, key=key, request_id=get_request_id(request))
    if result.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return public_booking(result)
