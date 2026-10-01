"""Authenticated staff/admin routes. Every salon route authorizes through
`authorized_tenant`; the path's salon id is a request, never a grant (ADR-0009)."""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID, uuid7
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from psycopg import errors as pg
from pydantic import BaseModel, ConfigDict, Field

from gorgona_booking.api.deps import get_principal, runtime_pool
from gorgona_booking.api.request_id import get_request_id
from gorgona_booking.auth.permissions import Permission
from gorgona_booking.auth.principal import Principal, set_user_context
from gorgona_booking.booking import repository as repo
from gorgona_booking.booking.models import booking_interval
from gorgona_booking.booking.repository import load_booking
from gorgona_booking.catalog.quote import ServiceNotBookableError, build_quote
from gorgona_booking.catalog.repository import load_add_ons, load_variant
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


class ServiceVariantUpdate(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    price_cents: int | None = Field(default=None, ge=0, le=10_000_000)
    booking_duration_minutes: int | None = Field(default=None, ge=1, le=720)
    is_bookable: bool | None = None


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


class StaffUpdate(Strict):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None


class StaffView(BaseModel):
    id: UUID
    location_id: UUID
    kind: str
    display_name: str
    is_active: bool


class ResourceHoursView(BaseModel):
    id: UUID
    weekday: int
    opens_minute: int
    closes_minute: int


class ResourceHoursEntry(Strict):
    weekday: int = Field(ge=1, le=7)
    opens_minute: int = Field(ge=0, le=1439)
    closes_minute: int = Field(ge=1, le=1440)


class StaffScheduleView(BaseModel):
    resource_id: UUID
    display_name: str
    is_active: bool
    location_id: UUID
    hours: list[ResourceHoursView]
    service_ids: list[UUID]


class StaffScheduleUpdate(Strict):
    hours: list[ResourceHoursEntry] = Field(min_length=0, max_length=50)


class StaffServicesUpdate(Strict):
    service_ids: list[UUID] = Field(min_length=0, max_length=100)


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


class BookingSummaryView(BaseModel):
    booking_id: UUID
    status: str
    location_id: UUID
    resource_id: UUID
    resource_name: str
    variant_id: UUID
    service_name: str
    variant_name: str
    starts_at: datetime
    ends_at: datetime
    total_cents: int
    currency: str
    customer_name: str | None
    customer_email: str | None
    customer_phone: str | None
    created_at: datetime
    created_by: str


class OverviewStats(BaseModel):
    today_bookings_count: int
    confirmed_bookings_count: int
    cancelled_bookings_count: int
    today_revenue_cents: int
    active_staff_count: int
    total_services_count: int


class ActivityItemView(BaseModel):
    id: UUID
    actor: str
    action: str
    target_type: str
    target_id: str
    details: dict[str, Any]
    occurred_at: datetime


class SalonOverviewView(BaseModel):
    salon_id: UUID
    salon_name: str
    status: str
    booking_state: str
    stats: OverviewStats
    today_bookings: list[BookingSummaryView]
    recent_activity: list[ActivityItemView]


class StaffBookingCreate(Strict):
    location_id: UUID
    resource_id: UUID
    variant_id: UUID
    starts_at: datetime
    customer_name: str = Field(min_length=1, max_length=100)
    customer_email: str = Field(min_length=3, max_length=254)
    customer_phone: str = Field(min_length=7, max_length=32)
    add_on_ids: list[UUID] = Field(default_factory=list)


class BookingRescheduleRequest(Strict):
    new_starts_at: datetime
    new_resource_id: UUID | None = None


class BookingCancelRequest(Strict):
    reason: str = Field(default="cancelled_by_staff", min_length=1, max_length=200)


class ClientView(BaseModel):
    customer_name: str
    email: str
    phone: str
    total_bookings: int
    confirmed_bookings: int
    last_booking_at: datetime | None


class LocationItemView(BaseModel):
    id: UUID
    name: str
    timezone: str


class BusinessHoursItemView(BaseModel):
    id: UUID
    location_id: UUID
    weekday: int
    opens_minute: int
    closes_minute: int


class SalonFactItemView(BaseModel):
    fact_key: str
    status: str
    source_note: str | None
    recorded_by: str
    recorded_at: datetime


class SalonEmbedOriginItemView(BaseModel):
    id: UUID
    origin: str
    status: str
    updated_at: datetime


class SalonSettingsView(BaseModel):
    salon_id: UUID
    slug: str
    display_name: str
    status: str
    booking_state: str
    locations: list[LocationItemView]
    business_hours: list[BusinessHoursItemView]
    policies: dict[str, Any]
    fact_confirmations: list[SalonFactItemView]
    embed_origins: list[SalonEmbedOriginItemView]


_SERVICE_COLUMNS = (
    "id, service_id, code, name, status, price_cents, currency, booking_duration_minutes, "
    "is_bookable, revision"
)

_BOOKING_SUMMARY_SELECT = (
    "select b.id, b.status, b.location_id, a.resource_id, coalesce(r.display_name, 'Unknown'), "
    "b.variant_id, coalesce(s.name, 'Unknown'), coalesce(v.name, 'Unknown'), "
    "b.starts_at, b.ends_at, b.total_cents, b.currency, "
    "c.customer_name, c.email, c.phone, b.created_at, b.created_by "
    "from gba.bookings b "
    "join gba.booking_allocations a on a.tenant_id = b.tenant_id and a.booking_id = b.id "
    "left join gba.resources r on r.tenant_id = b.tenant_id and r.id = a.resource_id "
    "left join gba.service_variants v on v.tenant_id = b.tenant_id and v.id = b.variant_id "
    "left join gba.services s on s.tenant_id = b.tenant_id and s.id = v.service_id "
    "left join gba.booking_customers c on c.tenant_id = b.tenant_id and c.booking_id = b.id"
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


def _booking_summary(r: tuple[Any, ...]) -> BookingSummaryView:
    return BookingSummaryView(
        booking_id=r[0],
        status=r[1],
        location_id=r[2],
        resource_id=r[3],
        resource_name=r[4],
        variant_id=r[5],
        service_name=r[6],
        variant_name=r[7],
        starts_at=r[8],
        ends_at=r[9],
        total_cents=r[10],
        currency=r[11],
        customer_name=r[12],
        customer_email=r[13],
        customer_phone=r[14],
        created_at=r[15],
        created_by=r[16],
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


# --- Overview ---


@router.get("/salons/{salon_id}/overview")
async def salon_overview(
    salon_id: UUID, request: Request, principal: CurrentPrincipal
) -> SalonOverviewView:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.BOOKING_READ,
        request_id=get_request_id(request),
    ) as access:
        conn = access.conn
        tenant_row = await (
            await conn.execute(
                "select display_name, status, booking_state from gba.tenants where id = %s",
                (salon_id,),
            )
        ).fetchone()
        if tenant_row is None:
            raise NotFoundError("Salon not found", salon_id=str(salon_id))

        loc_row = await (
            await conn.execute(
                "select timezone from gba.locations where tenant_id = %s limit 1", (salon_id,)
            )
        ).fetchone()
        tz = ZoneInfo(str(loc_row[0])) if loc_row else UTC
        now_local = datetime.now(tz)
        today_start_local = datetime(
            now_local.year, now_local.month, now_local.day, 0, 0, 0, tzinfo=tz
        )
        today_end_local = today_start_local + timedelta(days=1)
        today_start_utc = today_start_local.astimezone(UTC)
        today_end_utc = today_end_local.astimezone(UTC)

        today_rows = await (
            await conn.execute(
                f"{_BOOKING_SUMMARY_SELECT} "
                "where b.tenant_id = %s and b.starts_at >= %s and b.starts_at < %s "
                "order by b.starts_at",
                (salon_id, today_start_utc, today_end_utc),
            )
        ).fetchall()
        today_bookings = [_booking_summary(r) for r in today_rows]

        confirmed_count = sum(1 for b in today_bookings if b.status == "CONFIRMED")
        cancelled_count = sum(1 for b in today_bookings if b.status == "CANCELLED")
        today_revenue = sum(b.total_cents for b in today_bookings if b.status == "CONFIRMED")

        staff_count_row = await (
            await conn.execute(
                "select count(*) from gba.resources where tenant_id = %s and is_active = true",
                (salon_id,),
            )
        ).fetchone()
        active_staff = staff_count_row[0] if staff_count_row else 0

        services_count_row = await (
            await conn.execute(
                "select count(*) from gba.service_variants where tenant_id = %s",
                (salon_id,),
            )
        ).fetchone()
        total_services = services_count_row[0] if services_count_row else 0

        activity_rows = await (
            await conn.execute(
                "select id, actor, action, target_type, target_id, details, occurred_at "
                "from gba.audit_events where tenant_id = %s "
                "order by occurred_at desc limit 15",
                (salon_id,),
            )
        ).fetchall()
        recent_activity = [
            ActivityItemView(
                id=r[0],
                actor=r[1],
                action=r[2],
                target_type=r[3],
                target_id=r[4],
                details=r[5],
                occurred_at=r[6],
            )
            for r in activity_rows
        ]

    return SalonOverviewView(
        salon_id=salon_id,
        salon_name=str(tenant_row[0]),
        status=str(tenant_row[1]),
        booking_state=str(tenant_row[2]),
        stats=OverviewStats(
            today_bookings_count=len(today_bookings),
            confirmed_bookings_count=confirmed_count,
            cancelled_bookings_count=cancelled_count,
            today_revenue_cents=today_revenue,
            active_staff_count=active_staff,
            total_services_count=total_services,
        ),
        today_bookings=today_bookings,
        recent_activity=recent_activity,
    )


# --- Bookings Management ---


@router.get("/salons/{salon_id}/bookings")
async def list_bookings(
    salon_id: UUID,
    request: Request,
    principal: CurrentPrincipal,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    resource_id: UUID | None = None,
    status: str | None = None,
) -> list[BookingSummaryView]:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.BOOKING_READ,
        request_id=get_request_id(request),
    ) as access:
        conditions = ["b.tenant_id = %s"]
        params: list[Any] = [salon_id]
        if start_date is not None:
            conditions.append("b.starts_at >= %s")
            params.append(start_date)
        if end_date is not None:
            conditions.append("b.starts_at <= %s")
            params.append(end_date)
        if resource_id is not None:
            conditions.append("a.resource_id = %s")
            params.append(resource_id)
        if status is not None:
            conditions.append("b.status = %s")
            params.append(status)
        where = " and ".join(conditions)
        sql = f"{_BOOKING_SUMMARY_SELECT} where {where} order by b.starts_at desc limit 200"
        rows = await (await access.conn.execute(sql, tuple(params))).fetchall()
    return [_booking_summary(r) for r in rows]


@router.post("/salons/{salon_id}/bookings", status_code=201)
async def create_staff_booking(
    salon_id: UUID,
    body: StaffBookingCreate,
    request: Request,
    principal: CurrentPrincipal,
) -> BookingSummaryView:
    if body.starts_at.tzinfo is None or body.starts_at.utcoffset() is None:
        raise DomainError("start time must include a UTC offset")
    if body.starts_at.second or body.starts_at.microsecond:
        raise DomainError("start time must be on a whole minute")

    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.BOOKING_WRITE,
        request_id=get_request_id(request),
    ) as access:
        conn = access.conn
        variant = await load_variant(conn, body.variant_id)
        add_ons = await load_add_ons(conn, body.add_on_ids)
        quote = build_quote(variant, add_ons)
        starts_at, ends_at = booking_interval(body.starts_at, quote.booking_duration_minutes)
        await repo.active_resource_location(conn, body.resource_id)
        await repo.lock_resource_schedule(conn, salon_id, body.resource_id)

        await repo.set_audit_context(
            conn,
            actor="system:hold-expiry",
            reason="expired_on_contention",
            request_id=get_request_id(request),
        )
        await repo.expire_stale_holds_overlapping(conn, body.resource_id, starts_at, ends_at)

        await repo.set_audit_context(
            conn,
            actor=principal.actor,
            reason="created_confirmed",
            request_id=get_request_id(request),
        )
        try:
            async with conn.transaction():
                booking_id = await repo.insert_booking(
                    conn,
                    tenant_id=salon_id,
                    location_id=body.location_id,
                    resource_id=body.resource_id,
                    variant_id=variant.id,
                    status="CONFIRMED",
                    starts_at=starts_at,
                    ends_at=ends_at,
                    hold_ttl_seconds=600,
                    quote=quote,
                    created_by=principal.actor,
                )
                cap_hash = hashlib.sha256(f"staff:{uuid7()}".encode()).hexdigest()
                await conn.execute(
                    "insert into gba.booking_customers "
                    "(tenant_id, booking_id, capability_hash, customer_name, email, phone) "
                    "values (%s, %s, %s, %s, %s, %s)",
                    (
                        salon_id,
                        booking_id,
                        cap_hash,
                        body.customer_name,
                        body.customer_email,
                        body.customer_phone,
                    ),
                )
        except pg.ExclusionViolation as exc:
            if repo.is_slot_conflict(exc):
                raise ConflictError("That time is no longer available for this resource") from None
            raise

        row = await (
            await conn.execute(
                f"{_BOOKING_SUMMARY_SELECT} where b.tenant_id = %s and b.id = %s",
                (salon_id, booking_id),
            )
        ).fetchone()
        assert row is not None  # noqa: S101
        return _booking_summary(row)


@router.post("/salons/{salon_id}/bookings/{booking_id}/reschedule")
async def reschedule_booking(
    salon_id: UUID,
    booking_id: UUID,
    body: BookingRescheduleRequest,
    request: Request,
    principal: CurrentPrincipal,
) -> BookingSummaryView:
    if body.new_starts_at.tzinfo is None or body.new_starts_at.utcoffset() is None:
        raise DomainError("new start time must include a UTC offset")
    if body.new_starts_at.second or body.new_starts_at.microsecond:
        raise DomainError("new start time must be on a whole minute")

    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.BOOKING_WRITE,
        request_id=get_request_id(request),
    ) as access:
        conn = access.conn
        status, _ = await repo.lock_booking(conn, booking_id)
        if status not in ("HOLD", "CONFIRMED"):
            raise ConflictError(f"A {status.lower()} booking cannot be rescheduled")

        old_row = await (
            await conn.execute(
                "select b.location_id, b.variant_id, b.quote, a.resource_id, "
                "c.customer_name, c.email, c.phone, c.capability_hash "
                "from gba.bookings b "
                "join gba.booking_allocations a on a.tenant_id = b.tenant_id "
                "and a.booking_id = b.id "
                "left join gba.booking_customers c on c.tenant_id = b.tenant_id "
                "and c.booking_id = b.id "
                "where b.tenant_id = %s and b.id = %s",
                (salon_id, booking_id),
            )
        ).fetchone()
        if old_row is None:
            raise NotFoundError("Booking not found", booking_id=str(booking_id))

        location_id: UUID = old_row[0]
        variant_id: UUID = old_row[1]
        quote_data: dict[str, Any] = old_row[2]
        old_resource_id: UUID = old_row[3]
        target_resource_id = body.new_resource_id or old_resource_id
        customer_name: str | None = old_row[4]
        customer_email: str | None = old_row[5]
        customer_phone: str | None = old_row[6]
        cap_hash: str = old_row[7] or hashlib.sha256(f"staff:{uuid7()}".encode()).hexdigest()

        variant = await load_variant(conn, variant_id)
        duration = variant.booking_duration_minutes or int(
            quote_data.get("booking_duration_minutes", 60)
        )
        starts_at, ends_at = booking_interval(body.new_starts_at, duration)

        await repo.active_resource_location(conn, target_resource_id)
        await repo.lock_resource_schedule(conn, salon_id, old_resource_id)
        if target_resource_id != old_resource_id:
            await repo.lock_resource_schedule(conn, salon_id, target_resource_id)

        await repo.set_audit_context(
            conn,
            actor="system:hold-expiry",
            reason="expired_on_contention",
            request_id=get_request_id(request),
        )
        await repo.expire_stale_holds_overlapping(conn, target_resource_id, starts_at, ends_at)

        # Cancel old booking to release its allocation in gba.booking_allocations
        await repo.set_audit_context(
            conn,
            actor=principal.actor,
            reason="rescheduled_to_new_booking",
            request_id=get_request_id(request),
        )
        await repo.set_status(conn, booking_id, "CANCELLED")

        # Insert new booking
        await repo.set_audit_context(
            conn,
            actor=principal.actor,
            reason="rescheduled_from_old_booking",
            request_id=get_request_id(request),
        )
        try:
            async with conn.transaction():
                new_booking_id = await repo.insert_booking(
                    conn,
                    tenant_id=salon_id,
                    location_id=location_id,
                    resource_id=target_resource_id,
                    variant_id=variant_id,
                    status="CONFIRMED",
                    starts_at=starts_at,
                    ends_at=ends_at,
                    hold_ttl_seconds=600,
                    quote=build_quote(variant),
                    created_by=principal.actor,
                )
                if customer_name is not None:
                    await conn.execute(
                        "insert into gba.booking_customers "
                        "(tenant_id, booking_id, capability_hash, customer_name, email, phone) "
                        "values (%s, %s, %s, %s, %s, %s)",
                        (
                            salon_id,
                            new_booking_id,
                            cap_hash,
                            customer_name,
                            customer_email,
                            customer_phone,
                        ),
                    )
        except pg.ExclusionViolation as exc:
            if repo.is_slot_conflict(exc):
                raise ConflictError("That time is no longer available for this resource") from None
            raise

        row = await (
            await conn.execute(
                f"{_BOOKING_SUMMARY_SELECT} where b.tenant_id = %s and b.id = %s",
                (salon_id, new_booking_id),
            )
        ).fetchone()
        assert row is not None  # noqa: S101
        return _booking_summary(row)


@router.post("/salons/{salon_id}/bookings/{booking_id}/cancel")
async def cancel_booking(
    salon_id: UUID,
    booking_id: UUID,
    body: BookingCancelRequest,
    request: Request,
    principal: CurrentPrincipal,
) -> BookingSummaryView:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.BOOKING_WRITE,
        request_id=get_request_id(request),
    ) as access:
        conn = access.conn
        status, _ = await repo.lock_booking(conn, booking_id)
        if status == "CANCELLED":
            pass
        elif status not in ("HOLD", "CONFIRMED"):
            raise ConflictError(f"A {status.lower()} booking cannot be cancelled")
        else:
            await repo.set_audit_context(
                conn, actor=principal.actor, reason=body.reason, request_id=get_request_id(request)
            )
            await repo.set_status(conn, booking_id, "CANCELLED")

        row = await (
            await conn.execute(
                f"{_BOOKING_SUMMARY_SELECT} where b.tenant_id = %s and b.id = %s",
                (salon_id, booking_id),
            )
        ).fetchone()
        if row is None:
            raise NotFoundError("Booking not found", booking_id=str(booking_id))
        return _booking_summary(row)


# --- Clients Management ---


@router.get("/salons/{salon_id}/clients")
async def list_clients(
    salon_id: UUID,
    request: Request,
    principal: CurrentPrincipal,
    q: str | None = None,
) -> list[ClientView]:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.BOOKING_READ,
        request_id=get_request_id(request),
    ) as access:
        conn = access.conn
        like_pattern = f"%{q.strip()}%" if q and q.strip() else None
        rows = await (
            await conn.execute(
                "select c.customer_name, c.email, c.phone, "
                "count(distinct b.id) as total_bookings, "
                "count(distinct b.id) filter (where b.status = 'CONFIRMED') as confirmed_bookings, "
                "max(b.starts_at) as last_booking_at "
                "from gba.booking_customers c "
                "join gba.bookings b on b.tenant_id = c.tenant_id and b.id = c.booking_id "
                "where c.tenant_id = %s "
                "and (%s::text is null or c.customer_name ilike %s "
                "or c.email ilike %s or c.phone ilike %s) "
                "group by c.customer_name, c.email, c.phone "
                "order by max(b.starts_at) desc nulls last limit 100",
                (salon_id, like_pattern, like_pattern, like_pattern, like_pattern),
            )
        ).fetchall()
    return [
        ClientView(
            customer_name=r[0],
            email=r[1],
            phone=r[2],
            total_bookings=r[3],
            confirmed_bookings=r[4],
            last_booking_at=r[5],
        )
        for r in rows
    ]


@router.get("/salons/{salon_id}/clients/history")
async def client_history(
    salon_id: UUID,
    request: Request,
    principal: CurrentPrincipal,
    email: str | None = None,
    phone: str | None = None,
) -> list[BookingSummaryView]:
    if not email and not phone:
        raise DomainError("Either email or phone is required")
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.BOOKING_READ,
        request_id=get_request_id(request),
    ) as access:
        rows = await (
            await access.conn.execute(
                f"{_BOOKING_SUMMARY_SELECT} "
                "where b.tenant_id = %s and (c.email = %s or c.phone = %s) "
                "order by b.starts_at desc limit 50",
                (salon_id, email, phone),
            )
        ).fetchall()
    return [_booking_summary(r) for r in rows]


# --- Services Catalog ---


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


@router.patch("/salons/{salon_id}/services/{variant_id}")
async def update_service_variant(
    salon_id: UUID,
    variant_id: UUID,
    body: ServiceVariantUpdate,
    request: Request,
    principal: CurrentPrincipal,
) -> ServiceView:
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
                    "update gba.service_variants "
                    "set name = coalesce(%s, name), "
                    "price_cents = coalesce(%s, price_cents), "
                    "booking_duration_minutes = coalesce(%s, booking_duration_minutes), "
                    "is_bookable = coalesce(%s, is_bookable) "
                    "where id = %s returning "
                    "id, service_id, code, name, status, price_cents, currency, "
                    "booking_duration_minutes, is_bookable, revision",
                    (
                        body.name,
                        body.price_cents,
                        body.booking_duration_minutes,
                        body.is_bookable,
                        variant_id,
                    ),
                )
            ).fetchone()
        except pg.CheckViolation as exc:
            raise InvalidServiceError(
                "The updated service definition violates constraints",
                constraint=exc.diag.constraint_name,
            ) from None
    if row is None:
        raise NotFoundError("Service not found", variant_id=str(variant_id))
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


@router.post("/salons/{salon_id}/services/{variant_id}/unpublish")
async def unpublish_service(
    salon_id: UUID, variant_id: UUID, request: Request, principal: CurrentPrincipal
) -> ServiceView:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.CATALOG_MANAGE,
        request_id=get_request_id(request),
    ) as access:
        row = await (
            await access.conn.execute(
                "update gba.service_variants set status = 'draft', is_bookable = false "
                "where id = %s returning "
                "id, service_id, code, name, status, price_cents, currency, "
                "booking_duration_minutes, is_bookable, revision",
                (variant_id,),
            )
        ).fetchone()
    if row is None:
        raise NotFoundError("Service not found", variant_id=str(variant_id))
    return _service(row)


# --- Staff Management ---


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
            raise InvalidReferenceError("Unknown location", field="location_id") from None
    assert row is not None  # noqa: S101
    return StaffView(
        id=row[0], location_id=row[1], kind=row[2], display_name=row[3], is_active=row[4]
    )


@router.patch("/salons/{salon_id}/staff/{resource_id}")
async def update_staff(
    salon_id: UUID,
    resource_id: UUID,
    body: StaffUpdate,
    request: Request,
    principal: CurrentPrincipal,
) -> StaffView:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.STAFF_MANAGE,
        request_id=get_request_id(request),
    ) as access:
        row = await (
            await access.conn.execute(
                "update gba.resources set "
                "display_name = coalesce(%s, display_name), "
                "is_active = coalesce(%s, is_active) "
                "where id = %s returning id, location_id, kind, display_name, is_active",
                (body.display_name, body.is_active, resource_id),
            )
        ).fetchone()
    if row is None:
        raise NotFoundError("Staff not found", resource_id=str(resource_id))
    return StaffView(
        id=row[0], location_id=row[1], kind=row[2], display_name=row[3], is_active=row[4]
    )


@router.get("/salons/{salon_id}/staff/{resource_id}/schedule")
async def get_staff_schedule(
    salon_id: UUID, resource_id: UUID, request: Request, principal: CurrentPrincipal
) -> StaffScheduleView:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.STAFF_READ,
        request_id=get_request_id(request),
    ) as access:
        conn = access.conn
        staff = await (
            await conn.execute(
                "select id, display_name, is_active, location_id from gba.resources where id = %s",
                (resource_id,),
            )
        ).fetchone()
        if staff is None:
            raise NotFoundError("Staff not found", resource_id=str(resource_id))

        hours = await (
            await conn.execute(
                "select id, weekday, opens_minute, closes_minute from gba.resource_hours "
                "where resource_id = %s order by weekday, opens_minute",
                (resource_id,),
            )
        ).fetchall()

        services = await (
            await conn.execute(
                "select service_id from gba.resource_services where resource_id = %s",
                (resource_id,),
            )
        ).fetchall()

    return StaffScheduleView(
        resource_id=staff[0],
        display_name=staff[1],
        is_active=staff[2],
        location_id=staff[3],
        hours=[
            ResourceHoursView(id=h[0], weekday=h[1], opens_minute=h[2], closes_minute=h[3])
            for h in hours
        ],
        service_ids=[s[0] for s in services],
    )


@router.put("/salons/{salon_id}/staff/{resource_id}/schedule")
async def update_staff_schedule(
    salon_id: UUID,
    resource_id: UUID,
    body: StaffScheduleUpdate,
    request: Request,
    principal: CurrentPrincipal,
) -> StaffScheduleView:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.STAFF_MANAGE,
        request_id=get_request_id(request),
    ) as access:
        conn = access.conn
        staff = await (
            await conn.execute(
                "select id, display_name, is_active, location_id from gba.resources where id = %s",
                (resource_id,),
            )
        ).fetchone()
        if staff is None:
            raise NotFoundError("Staff not found", resource_id=str(resource_id))

        await conn.execute("delete from gba.resource_hours where resource_id = %s", (resource_id,))
        for entry in body.hours:
            await conn.execute(
                "insert into gba.resource_hours "
                "(tenant_id, resource_id, weekday, opens_minute, closes_minute) "
                "values (%s, %s, %s, %s, %s)",
                (salon_id, resource_id, entry.weekday, entry.opens_minute, entry.closes_minute),
            )

        hours = await (
            await conn.execute(
                "select id, weekday, opens_minute, closes_minute from gba.resource_hours "
                "where resource_id = %s order by weekday, opens_minute",
                (resource_id,),
            )
        ).fetchall()

        services = await (
            await conn.execute(
                "select service_id from gba.resource_services where resource_id = %s",
                (resource_id,),
            )
        ).fetchall()

    return StaffScheduleView(
        resource_id=staff[0],
        display_name=staff[1],
        is_active=staff[2],
        location_id=staff[3],
        hours=[
            ResourceHoursView(id=h[0], weekday=h[1], opens_minute=h[2], closes_minute=h[3])
            for h in hours
        ],
        service_ids=[s[0] for s in services],
    )


@router.put("/salons/{salon_id}/staff/{resource_id}/services")
async def update_staff_services(
    salon_id: UUID,
    resource_id: UUID,
    body: StaffServicesUpdate,
    request: Request,
    principal: CurrentPrincipal,
) -> StaffScheduleView:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.STAFF_MANAGE,
        request_id=get_request_id(request),
    ) as access:
        conn = access.conn
        staff = await (
            await conn.execute(
                "select id, display_name, is_active, location_id from gba.resources where id = %s",
                (resource_id,),
            )
        ).fetchone()
        if staff is None:
            raise NotFoundError("Staff not found", resource_id=str(resource_id))

        await conn.execute(
            "delete from gba.resource_services where resource_id = %s", (resource_id,)
        )
        for sid in body.service_ids:
            await conn.execute(
                "insert into gba.resource_services (tenant_id, resource_id, service_id) "
                "values (%s, %s, %s) on conflict do nothing",
                (salon_id, resource_id, sid),
            )

        hours = await (
            await conn.execute(
                "select id, weekday, opens_minute, closes_minute from gba.resource_hours "
                "where resource_id = %s order by weekday, opens_minute",
                (resource_id,),
            )
        ).fetchall()

        services = await (
            await conn.execute(
                "select service_id from gba.resource_services where resource_id = %s",
                (resource_id,),
            )
        ).fetchall()

    return StaffScheduleView(
        resource_id=staff[0],
        display_name=staff[1],
        is_active=staff[2],
        location_id=staff[3],
        hours=[
            ResourceHoursView(id=h[0], weekday=h[1], opens_minute=h[2], closes_minute=h[3])
            for h in hours
        ],
        service_ids=[s[0] for s in services],
    )


# --- Activity & Settings ---


@router.get("/salons/{salon_id}/activity")
async def salon_activity(
    salon_id: UUID, request: Request, principal: CurrentPrincipal
) -> list[ActivityItemView]:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.BOOKING_READ,
        request_id=get_request_id(request),
    ) as access:
        rows = await (
            await access.conn.execute(
                "select id, actor, action, target_type, target_id, details, occurred_at "
                "from gba.audit_events where tenant_id = %s "
                "order by occurred_at desc limit 50",
                (salon_id,),
            )
        ).fetchall()
    return [
        ActivityItemView(
            id=r[0],
            actor=r[1],
            action=r[2],
            target_type=r[3],
            target_id=r[4],
            details=r[5],
            occurred_at=r[6],
        )
        for r in rows
    ]


@router.get("/salons/{salon_id}/settings")
async def salon_settings(
    salon_id: UUID, request: Request, principal: CurrentPrincipal
) -> SalonSettingsView:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.CATALOG_READ,
        request_id=get_request_id(request),
    ) as access:
        conn = access.conn
        tenant = await (
            await conn.execute(
                "select id, slug, display_name, status, booking_state "
                "from gba.tenants where id = %s",
                (salon_id,),
            )
        ).fetchone()
        if tenant is None:
            raise NotFoundError("Salon not found", salon_id=str(salon_id))

        locations = await (
            await conn.execute(
                "select id, name, timezone from gba.locations where tenant_id = %s order by name",
                (salon_id,),
            )
        ).fetchall()

        hours = await (
            await conn.execute(
                "select id, location_id, weekday, opens_minute, closes_minute "
                "from gba.business_hours where tenant_id = %s order by weekday, opens_minute",
                (salon_id,),
            )
        ).fetchall()

        policies_row = await (
            await conn.execute(
                "select cancellation_policy, deposit_policy, booking_rules "
                "from gba.salon_policies where tenant_id = %s",
                (salon_id,),
            )
        ).fetchone()
        policies_dict = {
            "cancellation_policy": policies_row[0] if policies_row else None,
            "deposit_policy": policies_row[1] if policies_row else None,
            "booking_rules": policies_row[2] if policies_row else None,
        }

        facts = await (
            await conn.execute(
                "select fact_key, status, source_note, recorded_by, recorded_at "
                "from gba.salon_fact_confirmations where tenant_id = %s order by fact_key",
                (salon_id,),
            )
        ).fetchall()

        origins = await (
            await conn.execute(
                "select id, origin, status, updated_at "
                "from gba.tenant_embed_origins where tenant_id = %s order by origin",
                (salon_id,),
            )
        ).fetchall()

    return SalonSettingsView(
        salon_id=tenant[0],
        slug=tenant[1],
        display_name=tenant[2],
        status=tenant[3],
        booking_state=tenant[4],
        locations=[LocationItemView(id=loc[0], name=loc[1], timezone=loc[2]) for loc in locations],
        business_hours=[
            BusinessHoursItemView(
                id=h[0], location_id=h[1], weekday=h[2], opens_minute=h[3], closes_minute=h[4]
            )
            for h in hours
        ],
        policies=policies_dict,
        fact_confirmations=[
            SalonFactItemView(
                fact_key=f[0],
                status=f[1],
                source_note=f[2],
                recorded_by=f[3],
                recorded_at=f[4],
            )
            for f in facts
        ],
        embed_origins=[
            SalonEmbedOriginItemView(id=o[0], origin=o[1], status=o[2], updated_at=o[3])
            for o in origins
        ],
    )


# --- Booking Read ---


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
