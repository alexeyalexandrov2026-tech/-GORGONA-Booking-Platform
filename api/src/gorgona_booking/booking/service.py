"""Booking use cases. PostgreSQL's exclusion constraint is the final arbiter of
occupancy; this layer validates, quotes, and turns 23P01 into SlotConflictError."""

from datetime import UTC, datetime
from uuid import UUID

import psycopg

from gorgona_booking.booking import idempotency
from gorgona_booking.booking import repository as repo
from gorgona_booking.booking.idempotency import IdempotencyScope
from gorgona_booking.booking.models import (
    BookingResult,
    BookingStatus,
    HoldExpiredError,
    IdempotencyKeyReusedError,
    InvalidBookingTimeError,
    InvalidTransitionError,
    ReservationRequest,
    SlotConflictError,
    booking_interval,
)
from gorgona_booking.catalog.quote import build_quote
from gorgona_booking.catalog.repository import load_add_ons, load_variant
from gorgona_booking.db.pool import RuntimePool, tenant_transaction

_OPERATIONS: dict[BookingStatus, str] = {
    "HOLD": "booking.create_hold",
    "CONFIRMED": "booking.create_confirmed",
}


def _slot_conflict() -> SlotConflictError:
    return SlotConflictError("That time is no longer available for this resource")


class BookingService:
    def __init__(self, pool: RuntimePool, *, hold_ttl_seconds: int = 600) -> None:
        self._pool = pool
        self._hold_ttl_seconds = hold_ttl_seconds

    async def create_hold(
        self,
        tenant_id: UUID,
        request: ReservationRequest,
        *,
        actor: str,
        idempotency_key: str | None,
        request_id: str | None = None,
    ) -> BookingResult:
        """Reserve capacity for a limited time. Holds never start in the past."""
        if request.starts_at.tzinfo is not None and request.starts_at <= datetime.now(UTC):
            raise InvalidBookingTimeError("start time must be in the future")
        return await self._reserve(
            tenant_id, request, "HOLD", actor=actor, key=idempotency_key, request_id=request_id
        )

    async def create_confirmed_booking(
        self,
        tenant_id: UUID,
        request: ReservationRequest,
        *,
        actor: str,
        idempotency_key: str | None,
        request_id: str | None = None,
    ) -> BookingResult:
        """Staff-created booking. Same occupancy rule as a hold; no payment step in M1."""
        return await self._reserve(
            tenant_id, request, "CONFIRMED", actor=actor, key=idempotency_key, request_id=request_id
        )

    async def _reserve(
        self,
        tenant_id: UUID,
        request: ReservationRequest,
        status: BookingStatus,
        *,
        actor: str,
        key: str | None,
        request_id: str | None,
    ) -> BookingResult:
        if request.starts_at.tzinfo is None or request.starts_at.utcoffset() is None:
            raise InvalidBookingTimeError("start time must include a UTC offset")
        if request.starts_at.second or request.starts_at.microsecond:
            raise InvalidBookingTimeError("start time must be on a whole minute")
        operation = _OPERATIONS[status]
        request_hash = request.fingerprint(operation)
        scope = IdempotencyScope(tenant_id, actor, operation, key) if key is not None else None

        outcome: BookingResult | SlotConflictError
        async with tenant_transaction(self._pool, tenant_id) as conn:
            if scope is not None:
                stored = await idempotency.claim(conn, scope, request_hash)
                if stored is not None:
                    if stored.request_hash != request_hash:
                        raise IdempotencyKeyReusedError(
                            "Idempotency key was already used for a different request"
                        )
                    if stored.status_code == 409:
                        raise _slot_conflict()
                    return BookingResult.from_json(stored.body, replayed=True)

            variant = await load_variant(conn, request.variant_id)
            add_ons = await load_add_ons(conn, request.add_on_ids)
            quote = build_quote(variant, add_ons)
            starts_at, ends_at = booking_interval(request.starts_at, quote.booking_duration_minutes)
            location_id = await repo.active_resource_location(conn, request.resource_id)
            await repo.lock_resource_schedule(conn, tenant_id, request.resource_id)

            await repo.set_audit_context(
                conn,
                actor="system:hold-expiry",
                reason="expired_on_contention",
                request_id=request_id,
            )
            await repo.expire_stale_holds_overlapping(conn, request.resource_id, starts_at, ends_at)

            await repo.set_audit_context(
                conn, actor=actor, reason=f"created_{status.lower()}", request_id=request_id
            )
            try:
                async with conn.transaction():  # savepoint: a conflict keeps the key claim
                    booking_id = await repo.insert_booking(
                        conn,
                        tenant_id=tenant_id,
                        location_id=location_id,
                        resource_id=request.resource_id,
                        variant_id=variant.id,
                        status=status,
                        starts_at=starts_at,
                        ends_at=ends_at,
                        hold_ttl_seconds=self._hold_ttl_seconds,
                        quote=quote,
                        created_by=actor,
                    )
                outcome = await repo.load_booking(conn, booking_id)
            except psycopg.errors.ExclusionViolation as exc:
                if not repo.is_slot_conflict(exc):
                    raise
                outcome = _slot_conflict()

            if scope is not None:
                if isinstance(outcome, SlotConflictError):
                    await idempotency.complete(conn, scope, 409, {"error": {"code": outcome.code}})
                else:
                    await idempotency.complete(conn, scope, 201, outcome.to_json())

        if isinstance(outcome, SlotConflictError):
            raise outcome
        return outcome

    async def confirm(
        self, tenant_id: UUID, booking_id: UUID, *, actor: str, request_id: str | None = None
    ) -> BookingResult:
        """HOLD -> CONFIRMED. An expired hold is expired instead, and the caller told so."""
        outcome: BookingResult | HoldExpiredError
        async with tenant_transaction(self._pool, tenant_id) as conn:
            # Entering the blocking set re-checks the exclusion constraint, so take
            # the same per-resource locks as a new reservation, in the same order.
            for resource_id in await repo.booking_resource_ids(conn, booking_id):
                await repo.lock_resource_schedule(conn, tenant_id, resource_id)
            status, hold_expired = await repo.lock_booking(conn, booking_id)
            if status == "CONFIRMED":
                return await repo.load_booking(conn, booking_id)
            if status != "HOLD":
                raise InvalidTransitionError(
                    f"A {status.lower()} booking cannot be confirmed", status=status
                )
            if hold_expired:
                await repo.set_audit_context(
                    conn, actor=actor, reason="expired_at_confirmation", request_id=request_id
                )
                await repo.set_status(conn, booking_id, "EXPIRED")
                outcome = HoldExpiredError("The hold expired before it was confirmed")
            else:
                await repo.set_audit_context(
                    conn, actor=actor, reason="confirmed", request_id=request_id
                )
                await repo.set_status(conn, booking_id, "CONFIRMED")
                outcome = await repo.load_booking(conn, booking_id)
        if isinstance(outcome, HoldExpiredError):
            raise outcome
        return outcome

    async def cancel(
        self,
        tenant_id: UUID,
        booking_id: UUID,
        *,
        actor: str,
        reason: str = "cancelled",
        request_id: str | None = None,
    ) -> BookingResult:
        async with tenant_transaction(self._pool, tenant_id) as conn:
            status, _ = await repo.lock_booking(conn, booking_id)
            if status == "CANCELLED":
                return await repo.load_booking(conn, booking_id)
            if status not in ("HOLD", "CONFIRMED"):
                raise InvalidTransitionError(
                    f"A {status.lower()} booking cannot be cancelled", status=status
                )
            await repo.set_audit_context(conn, actor=actor, reason=reason, request_id=request_id)
            await repo.set_status(conn, booking_id, "CANCELLED")
            return await repo.load_booking(conn, booking_id)

    async def expire_due_holds(self, tenant_id: UUID, *, limit: int = 500) -> int:
        """Sweeper for one tenant. Correctness never depends on it running on time."""
        async with tenant_transaction(self._pool, tenant_id) as conn:
            await repo.set_audit_context(
                conn, actor="system:hold-expiry", reason="expired_by_sweeper", request_id=None
            )
            return await repo.expire_due_holds(conn, limit)
