"""Authenticated staff/admin routes. Every salon route authorizes through
`authorized_tenant`; the path's salon id is a request, never a grant (ADR-0009)."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from psycopg import errors as pg
from pydantic import BaseModel, ConfigDict, Field

from gorgona_booking.api.deps import get_principal, runtime_pool
from gorgona_booking.api.request_id import get_request_id
from gorgona_booking.auth.permissions import Permission
from gorgona_booking.auth.principal import Principal, set_user_context
from gorgona_booking.booking.repository import load_booking
from gorgona_booking.catalog.quote import ServiceNotBookableError
from gorgona_booking.errors import ConflictError, DomainError, InvalidReferenceError, NotFoundError
from gorgona_booking.tenancy.authorization import authorized_tenant

router = APIRouter(prefix="/v1", tags=["salons"])

CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


class InvalidServiceError(DomainError):
    code = "INVALID_SERVICE"


_CODE = r"^[A-Z][A-Z0-9_]{1,63}$"


class Strict(BaseModel):
    # Unknown fields (for example a smuggled tenant_id) are rejected, never consulted.
    model_config = ConfigDict(extra="forbid", frozen=True)


class MembershipView(BaseModel):
    salon_id: UUID
    salon_name: str | None
    role: str
    status: str


class MeView(BaseModel):
    user_id: UUID
    display_name: str
    platform_roles: list[str]
    memberships: list[MembershipView]


class ServiceCreate(Strict):
    service_code: str = Field(pattern=_CODE)
    service_name: str = Field(min_length=1, max_length=200)
    code: str = Field(pattern=_CODE)
    name: str = Field(min_length=1, max_length=200)
    price_cents: int = Field(ge=0, le=10_000_000)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    booking_duration_minutes: int | None = Field(default=None, ge=1, le=720)
    display_duration_min_minutes: int | None = Field(default=None, ge=1, le=720)
    display_duration_max_minutes: int | None = Field(default=None, ge=1, le=720)


class ServiceView(BaseModel):
    id: UUID
    service_id: UUID
    code: str
    name: str
    status: str
    price_cents: int
    currency: str
    booking_duration_minutes: int | None
    is_bookable: bool
    revision: int


class StaffCreate(Strict):
    display_name: str = Field(min_length=1, max_length=200)
    location_id: UUID
    kind: Literal["artist", "chair", "room"] = "artist"


class StaffView(BaseModel):
    id: UUID
    location_id: UUID
    kind: str
    display_name: str
    is_active: bool


class BookingView(BaseModel):
    booking_id: UUID
    status: str
    resource_id: UUID
    start_at: datetime
    end_at: datetime
    hold_expires_at: datetime | None
    total_cents: int
    currency: str
    quote: dict[str, Any]


_SERVICE_COLUMNS = (
    "id, service_id, code, name, status, price_cents, currency, booking_duration_minutes, "
    "is_bookable, revision"
)


def _service(row: tuple[Any, ...]) -> ServiceView:
    return ServiceView(
        id=row[0],
        service_id=row[1],
        code=row[2],
        name=row[3],
        status=row[4],
        price_cents=row[5],
        currency=row[6],
        booking_duration_minutes=row[7],
        is_bookable=row[8],
        revision=row[9],
    )


@router.get("/me")
async def me(request: Request, principal: CurrentPrincipal) -> MeView:
    async with runtime_pool(request).connection() as conn, conn.transaction():
        await set_user_context(conn, principal.user_id, request_id=get_request_id(request))
        rows = await (
            await conn.execute(
                "select m.tenant_id, t.display_name, m.role, m.status "
                "from gba.memberships m left join gba.tenants t on t.id = m.tenant_id "
                "where m.user_id = %s and m.status <> 'revoked' order by m.created_at",
                (principal.user_id,),
            )
        ).fetchall()
    return MeView(
        user_id=principal.user_id,
        display_name=principal.display_name,
        platform_roles=sorted(principal.platform_roles),
        memberships=[
            MembershipView(salon_id=r[0], salon_name=r[1], role=r[2], status=r[3]) for r in rows
        ],
    )


@router.get("/salons/{salon_id}/services")
async def list_services(
    salon_id: UUID, request: Request, principal: CurrentPrincipal
) -> list[ServiceView]:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.CATALOG_READ,
        request_id=get_request_id(request),
    ) as access:
        rows = await (
            await access.conn.execute(
                f"select {_SERVICE_COLUMNS} from gba.service_variants order by code"  # noqa: S608
            )
        ).fetchall()
    return [_service(r) for r in rows]


@router.post("/salons/{salon_id}/services", status_code=201)
async def create_service(
    salon_id: UUID, body: ServiceCreate, request: Request, principal: CurrentPrincipal
) -> ServiceView:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.CATALOG_MANAGE,
        request_id=get_request_id(request),
    ) as access:
        conn = access.conn
        await conn.execute(
            "insert into gba.services (tenant_id, code, name) values (%s, %s, %s) "
            "on conflict (tenant_id, code) do nothing",
            (salon_id, body.service_code, body.service_name),
        )
        service = await (
            await conn.execute("select id from gba.services where code = %s", (body.service_code,))
        ).fetchone()
        assert service is not None  # noqa: S101 - inserted or existing above
        try:
            row = await (
                await conn.execute(
                    "insert into gba.service_variants (tenant_id, service_id, code, name, "
                    "price_cents, currency, booking_duration_minutes, "
                    "display_duration_min_minutes, display_duration_max_minutes) "
                    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "returning id, service_id, code, name, status, price_cents, currency, "
                    "booking_duration_minutes, is_bookable, revision",
                    (
                        salon_id,
                        service[0],
                        body.code,
                        body.name,
                        body.price_cents,
                        body.currency,
                        body.booking_duration_minutes,
                        body.display_duration_min_minutes,
                        body.display_duration_max_minutes,
                    ),
                )
            ).fetchone()
        except pg.UniqueViolation:
            raise ConflictError("A service with this code already exists", code=body.code) from None
        except pg.CheckViolation as exc:
            raise InvalidServiceError(
                "The service definition is invalid", constraint=exc.diag.constraint_name
            ) from None
    assert row is not None  # noqa: S101
    return _service(row)


@router.post("/salons/{salon_id}/services/{variant_id}/publish")
async def publish_service(
    salon_id: UUID, variant_id: UUID, request: Request, principal: CurrentPrincipal
) -> ServiceView:
    """Make a service bookable. The database refuses if its booking duration is unknown."""
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.CATALOG_MANAGE,
        request_id=get_request_id(request),
    ) as access:
        try:
            row = await (
                await access.conn.execute(
                    "update gba.service_variants set status = 'published', is_bookable = true "
                    "where id = %s returning "
                    "id, service_id, code, name, status, price_cents, currency, "
                    "booking_duration_minutes, is_bookable, revision",
                    (variant_id,),
                )
            ).fetchone()
        except pg.CheckViolation as exc:
            if exc.diag.constraint_name == "service_variants_bookable_requires_duration":
                raise ServiceNotBookableError(
                    "This service has no confirmed booking duration yet",
                    variant=str(variant_id),
                    reason="booking_duration_unknown",
                ) from None
            raise
    if row is None:
        raise NotFoundError("Service not found", variant_id=str(variant_id))
    return _service(row)


@router.get("/salons/{salon_id}/staff")
async def list_staff(
    salon_id: UUID, request: Request, principal: CurrentPrincipal
) -> list[StaffView]:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.STAFF_READ,
        request_id=get_request_id(request),
    ) as access:
        rows = await (
            await access.conn.execute(
                "select id, location_id, kind, display_name, is_active from gba.resources "
                "order by display_name"
            )
        ).fetchall()
    return [
        StaffView(id=r[0], location_id=r[1], kind=r[2], display_name=r[3], is_active=r[4])
        for r in rows
    ]


@router.post("/salons/{salon_id}/staff", status_code=201)
async def create_staff(
    salon_id: UUID, body: StaffCreate, request: Request, principal: CurrentPrincipal
) -> StaffView:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.STAFF_MANAGE,
        request_id=get_request_id(request),
    ) as access:
        try:
            row = await (
                await access.conn.execute(
                    "insert into gba.resources (tenant_id, location_id, kind, display_name) "
                    "values (%s, %s, %s, %s) "
                    "returning id, location_id, kind, display_name, is_active",
                    (salon_id, body.location_id, body.kind, body.display_name),
                )
            ).fetchone()
        except pg.ForeignKeyViolation:
            # A location of another salon fails the composite (tenant_id, location_id) key.
            raise InvalidReferenceError("Unknown location", field="location_id") from None
    assert row is not None  # noqa: S101
    return StaffView(
        id=row[0], location_id=row[1], kind=row[2], display_name=row[3], is_active=row[4]
    )


@router.get("/salons/{salon_id}/bookings/{booking_id}")
async def get_booking(
    salon_id: UUID, booking_id: UUID, request: Request, principal: CurrentPrincipal
) -> BookingView:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.BOOKING_READ,
        request_id=get_request_id(request),
    ) as access:
        # Another salon's booking is invisible through RLS: indistinguishable from absent.
        booking = await load_booking(access.conn, booking_id)
    return BookingView(
        booking_id=booking.booking_id,
        status=booking.status,
        resource_id=booking.resource_id,
        start_at=booking.starts_at,
        end_at=booking.ends_at,
        hold_expires_at=booking.hold_expires_at,
        total_cents=booking.total_cents,
        currency=booking.currency,
        quote=booking.quote,
    )
