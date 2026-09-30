"""POST /v1/holds: reserve a slot for a limited time.

M1 development surface only: no authentication or rate limiting, so it must not
be exposed publicly (the app refuses to start in staging/production).
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from gorgona_booking.api.request_id import get_request_id
from gorgona_booking.booking.models import BookingResult, ReservationRequest
from gorgona_booking.booking.service import BookingService
from gorgona_booking.errors import DatabaseUnavailableError
from gorgona_booking.tenancy.resolver import resolve_tenant_by_host

router = APIRouter(prefix="/v1", tags=["booking"])

# Until authentication exists every caller shares one idempotency namespace.
ANONYMOUS_ACTOR = "anonymous"


class HoldCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: UUID
    variant_id: UUID
    add_on_ids: list[UUID] = Field(default_factory=list, max_length=10)
    start_at: AwareDatetime


class HoldView(BaseModel):
    booking_id: UUID
    status: str
    resource_id: UUID
    start_at: datetime
    end_at: datetime
    hold_expires_at: datetime | None
    total_cents: int
    currency: str
    quote: dict[str, Any]

    @classmethod
    def from_result(cls, result: BookingResult) -> HoldView:
        return cls(
            booking_id=result.booking_id,
            status=result.status,
            resource_id=result.resource_id,
            start_at=result.starts_at,
            end_at=result.ends_at,
            hold_expires_at=result.hold_expires_at,
            total_cents=result.total_cents,
            currency=result.currency,
            quote=result.quote,
        )


def get_booking_service(request: Request) -> BookingService:
    service: BookingService | None = getattr(request.app.state, "booking_service", None)
    if service is None:
        raise DatabaseUnavailableError("Booking is not available right now")
    return service


async def get_tenant_id(request: Request) -> UUID:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise DatabaseUnavailableError("Booking is not available right now")
    return await resolve_tenant_by_host(pool, request.headers.get("host", ""))


@router.post("/holds", status_code=201)
async def create_hold(
    body: HoldCreate,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key", min_length=8, max_length=255, pattern=r"^[A-Za-z0-9._:-]+$"
        ),
    ],
    service: Annotated[BookingService, Depends(get_booking_service)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> HoldView:
    result = await service.create_hold(
        tenant_id,
        ReservationRequest(
            resource_id=body.resource_id,
            variant_id=body.variant_id,
            starts_at=body.start_at,
            add_on_ids=tuple(body.add_on_ids),
        ),
        actor=ANONYMOUS_ACTOR,
        idempotency_key=idempotency_key,
        request_id=get_request_id(request),
    )
    if result.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return HoldView.from_result(result)
