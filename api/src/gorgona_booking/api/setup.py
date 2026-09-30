"""Salon configuration, governed facts and readiness (ADR-0010)."""

from collections.abc import Sequence
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from psycopg import errors as pg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from gorgona_booking.api.deps import get_principal, runtime_pool
from gorgona_booking.api.request_id import get_request_id
from gorgona_booking.auth.permissions import Permission
from gorgona_booking.auth.principal import Principal
from gorgona_booking.db.pool import RuntimeConnection
from gorgona_booking.errors import DomainError, InvalidReferenceError
from gorgona_booking.onboarding.readiness import CONFIRMABLE_FACTS, readiness
from gorgona_booking.onboarding.spec import HoursEntry, clock_minutes
from gorgona_booking.tenancy.authorization import authorized_tenant

router = APIRouter(prefix="/v1", tags=["setup"])

CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


class InvalidBusinessHoursError(DomainError):
    code = "INVALID_BUSINESS_HOURS"


class UnknownFactError(DomainError):
    code = "UNKNOWN_FACT"


class SalonIsLiveError(DomainError):
    code = "SALON_IS_LIVE"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BusinessHoursUpdate(Strict):
    location_id: UUID
    hours: list[HoursEntry] = Field(min_length=1, max_length=50)


class PoliciesUpdate(Strict):
    cancellation_policy: dict[str, Any] | None = None
    deposit_policy: dict[str, Any] | None = None
    booking_rules: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _something(self) -> PoliciesUpdate:
        if not self.model_fields_set:
            raise ValueError("supply at least one policy")
        return self


class FactUpdate(Strict):
    status: Literal["confirmed", "unconfirmed"]
    source_note: str | None = Field(default=None, min_length=1, max_length=500)


class ReadinessItemView(BaseModel):
    fact: str
    status: str
    detail: str


class ReadinessView(BaseModel):
    salon_id: UUID
    ready: bool
    booking_state: str
    items: list[ReadinessItemView]


async def _booking_state(conn: RuntimeConnection) -> str:
    row = await (await conn.execute("select booking_state from gba.tenants")).fetchone()
    return str(row[0]) if row else "not_live"


async def _record_fact(
    conn: RuntimeConnection, salon_id: UUID, fact: str, status: str, note: str | None
) -> None:
    await conn.execute(
        "insert into gba.salon_fact_confirmations (tenant_id, fact_key, status, source_note, "
        "recorded_by) values (%s, %s, %s, %s, pg_catalog.current_setting('gba.actor')) "
        "on conflict (tenant_id, fact_key) do update set status = excluded.status, "
        "source_note = excluded.source_note, recorded_by = excluded.recorded_by, "
        "recorded_at = now()",
        (salon_id, fact, status, note),
    )


async def _after_edit(conn: RuntimeConnection, salon_id: UUID, facts: Sequence[str]) -> None:
    """Edited data needs a fresh confirmation, unless the salon is live: then the
    admin who edited it is on record as confirming it (audited)."""
    live = await _booking_state(conn) == "live"
    for fact in facts:
        await _record_fact(
            conn,
            salon_id,
            fact,
            "confirmed" if live else "unconfirmed",
            "edited via admin API",
        )


async def _readiness_view(conn: RuntimeConnection, salon_id: UUID) -> ReadinessView:
    result = await readiness(conn)
    return ReadinessView(
        salon_id=salon_id,
        ready=result.ready,
        booking_state=await _booking_state(conn),
        items=[
            ReadinessItemView(fact=i.fact, status=i.status, detail=i.detail) for i in result.items
        ],
    )


@router.get("/salons/{salon_id}/readiness")
async def get_readiness(
    salon_id: UUID, request: Request, principal: CurrentPrincipal
) -> ReadinessView:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.READINESS_READ,
        request_id=get_request_id(request),
    ) as access:
        return await _readiness_view(access.conn, salon_id)


@router.put("/salons/{salon_id}/business-hours")
async def put_business_hours(
    salon_id: UUID, body: BusinessHoursUpdate, request: Request, principal: CurrentPrincipal
) -> ReadinessView:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.SETTINGS_MANAGE,
        request_id=get_request_id(request),
    ) as access:
        conn = access.conn
        await conn.execute(
            "delete from gba.business_hours where location_id = %s", (body.location_id,)
        )
        try:
            for entry in body.hours:
                await conn.execute(
                    "insert into gba.business_hours (tenant_id, location_id, weekday, "
                    "opens_minute, closes_minute) values (%s, %s, %s, %s, %s)",
                    (
                        salon_id,
                        body.location_id,
                        entry.weekday,
                        clock_minutes(entry.opens),
                        clock_minutes(entry.closes),
                    ),
                )
        except pg.ExclusionViolation:
            raise InvalidBusinessHoursError("Opening hours overlap on the same day") from None
        except pg.ForeignKeyViolation:
            raise InvalidReferenceError("Unknown location", field="location_id") from None
        await _after_edit(conn, salon_id, ["business_hours"])
        return await _readiness_view(conn, salon_id)


@router.put("/salons/{salon_id}/policies")
async def put_policies(
    salon_id: UUID, body: PoliciesUpdate, request: Request, principal: CurrentPrincipal
) -> ReadinessView:
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.SETTINGS_MANAGE,
        request_id=get_request_id(request),
    ) as access:
        conn = access.conn
        await conn.execute(
            "insert into gba.salon_policies (tenant_id) values (%s) on conflict do nothing",
            (salon_id,),
        )
        edited = []
        for field in ("cancellation_policy", "deposit_policy", "booking_rules"):
            value = getattr(body, field)
            if field in body.model_fields_set and value is not None:
                await conn.execute(
                    {
                        "cancellation_policy": "update gba.salon_policies set "
                        "cancellation_policy = %s, updated_at = now()",
                        "deposit_policy": "update gba.salon_policies set "
                        "deposit_policy = %s, updated_at = now()",
                        "booking_rules": "update gba.salon_policies set "
                        "booking_rules = %s, updated_at = now()",
                    }[field],
                    (Jsonb(value),),
                )
                edited.append(field)
        await _after_edit(conn, salon_id, edited)
        return await _readiness_view(conn, salon_id)


@router.put("/salons/{salon_id}/facts/{fact_key}")
async def put_fact(
    salon_id: UUID, fact_key: str, body: FactUpdate, request: Request, principal: CurrentPrincipal
) -> ReadinessView:
    if fact_key not in CONFIRMABLE_FACTS:
        raise UnknownFactError(
            "This fact is derived and cannot be confirmed by hand", fact=fact_key
        )
    async with authorized_tenant(
        runtime_pool(request),
        principal,
        salon_id,
        Permission.SETTINGS_MANAGE,
        request_id=get_request_id(request),
    ) as access:
        conn = access.conn
        if body.status == "unconfirmed" and await _booking_state(conn) == "live":
            raise SalonIsLiveError("A live salon's booking facts cannot be unconfirmed")
        await _record_fact(conn, salon_id, fact_key, body.status, body.source_note)
        return await _readiness_view(conn, salon_id)
