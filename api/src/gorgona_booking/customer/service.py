"""Guest orchestration around M1's authoritative transaction/occupancy primitives."""

import hashlib
from datetime import UTC
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from gorgona_booking.booking import idempotency
from gorgona_booking.booking import repository as repo
from gorgona_booking.booking.idempotency import IdempotencyScope
from gorgona_booking.booking.models import (
    BookingResult,
    HoldExpiredError,
    IdempotencyKeyReusedError,
    InvalidTransitionError,
    ReservationRequest,
    SlotConflictError,
    booking_interval,
)
from gorgona_booking.customer import queries
from gorgona_booking.customer.contracts import (
    AvailabilityQuery,
    CustomerBookingView,
    CustomerDetails,
    CustomerHold,
    QuoteView,
)
from gorgona_booking.db.pool import RuntimePool, tenant_transaction
from gorgona_booking.errors import NotFoundError


def capability_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def public_booking(result: BookingResult) -> CustomerBookingView:
    return CustomerBookingView(
        booking_id=result.booking_id,
        status=result.status,
        resource_id=result.resource_id,
        start_at=result.starts_at,
        end_at=result.ends_at,
        hold_expires_at=result.hold_expires_at,
        quote=QuoteView.model_validate(result.quote),
    )


class CustomerBookingService:
    def __init__(self, pool: RuntimePool, *, ttl: int) -> None:
        self.pool, self.ttl = pool, ttl

    async def hold(
        self, tenant_id: UUID, body: CustomerHold, *, token: str, key: str, request_id: str
    ) -> BookingResult:
        digest = capability_hash(token)
        actor = f"guest:{digest}"
        request = ReservationRequest(
            resource_id=body.resource_id,
            variant_id=body.variant_id,
            starts_at=body.start_at,
            add_on_ids=tuple(body.add_on_ids),
        )
        scope = IdempotencyScope(tenant_id, actor, "customer.hold", key)
        fingerprint = hashlib.sha256(
            (request.fingerprint("customer.hold") + str(body.location_id)).encode()
        ).hexdigest()
        outcome: BookingResult | SlotConflictError
        async with tenant_transaction(self.pool, tenant_id) as conn:
            await queries.live_name(conn)
            stored = await idempotency.claim(conn, scope, fingerprint)
            if stored is not None:
                if stored.request_hash != fingerprint:
                    raise IdempotencyKeyReusedError("Key was used for another request")
                if stored.status_code == 409:
                    raise SlotConflictError("That time is no longer available")
                return BookingResult.from_json(stored.body, replayed=True)
            await repo.lock_resource_schedule(conn, tenant_id, body.resource_id)
            zone = await queries.location_zone(conn, body.location_id)
            available = await queries.availability(
                conn,
                AvailabilityQuery(
                    **body.model_dump(exclude={"start_at"}),
                    day=body.start_at.astimezone(ZoneInfo(zone)).date(),
                ),
            )
            if not any(s.start_at == body.start_at.astimezone(UTC) for s in available.slots):
                outcome = SlotConflictError("That time is no longer available")
            else:
                quote = await queries.quote_selection(conn, body)
                starts_at, ends_at = booking_interval(body.start_at, quote.booking_duration_minutes)
                await repo.set_audit_context(
                    conn,
                    actor="system:hold-expiry",
                    reason="expired_on_contention",
                    request_id=request_id,
                )
                await repo.expire_stale_holds_overlapping(
                    conn, body.resource_id, starts_at, ends_at
                )
                await repo.set_audit_context(
                    conn, actor=actor, reason="created_hold", request_id=request_id
                )
                try:
                    async with conn.transaction():
                        booking_id = await repo.insert_booking(
                            conn,
                            tenant_id=tenant_id,
                            location_id=body.location_id,
                            resource_id=body.resource_id,
                            variant_id=body.variant_id,
                            status="HOLD",
                            starts_at=starts_at,
                            ends_at=ends_at,
                            hold_ttl_seconds=self.ttl,
                            quote=quote,
                            created_by=actor,
                        )
                        await conn.execute(
                            "insert into gba.booking_customers "
                            "(tenant_id, booking_id, capability_hash) values (%s, %s, %s)",
                            (tenant_id, booking_id, digest),
                        )
                    outcome = await repo.load_booking(conn, booking_id)
                except psycopg.errors.ExclusionViolation as exc:
                    if not repo.is_slot_conflict(exc):
                        raise
                    outcome = SlotConflictError("That time is no longer available")
            if isinstance(outcome, SlotConflictError):
                await idempotency.complete(conn, scope, 409, {"error": {"code": outcome.code}})
            else:
                await idempotency.complete(conn, scope, 201, outcome.to_json())
        if isinstance(outcome, SlotConflictError):
            raise outcome
        return outcome

    async def confirm(
        self,
        tenant_id: UUID,
        booking_id: UUID,
        body: CustomerDetails,
        *,
        token: str,
        key: str,
        request_id: str,
    ) -> BookingResult:
        digest = capability_hash(token)
        actor = f"guest:{digest}"
        scope = IdempotencyScope(tenant_id, actor, "customer.confirm", key)
        fingerprint = hashlib.sha256(
            (str(booking_id) + body.model_dump_json()).encode()
        ).hexdigest()
        outcome: BookingResult | HoldExpiredError
        async with tenant_transaction(self.pool, tenant_id) as conn:
            await queries.live_name(conn)
            await queries.policies(conn)
            guest = await (
                await conn.execute(
                    "select customer_name, email, phone from gba.booking_customers "
                    "where booking_id = %s and capability_hash = %s",
                    (booking_id, digest),
                )
            ).fetchone()
            if guest is None:
                raise NotFoundError("Booking not found")
            stored = await idempotency.claim(conn, scope, fingerprint)
            if stored is not None:
                if stored.request_hash != fingerprint:
                    raise IdempotencyKeyReusedError("Key was used for another request")
                if stored.status_code == 409:
                    raise HoldExpiredError("The hold expired; choose another time")
                return BookingResult.from_json(stored.body, replayed=True)
            for resource_id in await repo.booking_resource_ids(conn, booking_id):
                await repo.lock_resource_schedule(conn, tenant_id, resource_id)
            status, expired = await repo.lock_booking(conn, booking_id)
            await queries.live_name(conn)
            if status == "CONFIRMED":
                # A second key cannot change contact details on a confirmed booking.
                current = await (
                    await conn.execute(
                        "select customer_name, email, phone from gba.booking_customers "
                        "where booking_id = %s",
                        (booking_id,),
                    )
                ).fetchone()
                if current != (body.name, body.email, body.phone):
                    raise InvalidTransitionError("This booking is already confirmed")
                outcome = await repo.load_booking(conn, booking_id)
            elif status == "EXPIRED" or (status == "HOLD" and expired):
                if status == "HOLD":
                    await repo.set_audit_context(
                        conn, actor=actor, reason="expired_at_confirmation", request_id=request_id
                    )
                    await repo.set_status(conn, booking_id, "EXPIRED")
                outcome = HoldExpiredError("The hold expired; choose another time")
            elif status != "HOLD":
                raise InvalidTransitionError("This booking cannot be confirmed")
            else:
                await conn.execute(
                    "update gba.booking_customers set customer_name = %s, email = %s, phone = %s "
                    "where booking_id = %s",
                    (body.name, body.email, body.phone, booking_id),
                )
                await repo.set_audit_context(
                    conn, actor=actor, reason="confirmed", request_id=request_id
                )
                await repo.set_status(conn, booking_id, "CONFIRMED")
                outcome = await repo.load_booking(conn, booking_id)
            if isinstance(outcome, HoldExpiredError):
                await idempotency.complete(conn, scope, 409, {"error": {"code": outcome.code}})
            else:
                await idempotency.complete(conn, scope, 200, outcome.to_json())
        if isinstance(outcome, HoldExpiredError):
            raise outcome
        return outcome
