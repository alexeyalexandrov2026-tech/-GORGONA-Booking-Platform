"""Booking commands, results and domain errors."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from gorgona_booking.errors import DomainError

type BookingStatus = Literal["HOLD", "CONFIRMED", "CANCELLED", "EXPIRED"]


class SlotConflictError(DomainError):
    code = "SLOT_CONFLICT"


class IdempotencyKeyReusedError(DomainError):
    code = "IDEMPOTENCY_KEY_REUSED"


class InvalidIdempotencyKeyError(DomainError):
    code = "INVALID_IDEMPOTENCY_KEY"


class InvalidTransitionError(DomainError):
    code = "INVALID_STATE_TRANSITION"


class HoldExpiredError(DomainError):
    code = "HOLD_EXPIRED"


class ResourceUnavailableError(DomainError):
    code = "RESOURCE_UNAVAILABLE"


class InvalidBookingTimeError(DomainError):
    code = "INVALID_BOOKING_TIME"


@dataclass(frozen=True, slots=True)
class ReservationRequest:
    resource_id: UUID
    variant_id: UUID
    starts_at: datetime
    add_on_ids: tuple[UUID, ...] = ()

    def fingerprint(self, operation: str) -> str:
        """Stable hash of the logical request, for idempotency comparison."""
        payload = {
            "operation": operation,
            "resource_id": str(self.resource_id),
            "variant_id": str(self.variant_id),
            "add_on_ids": sorted(str(i) for i in self.add_on_ids),
            "starts_at": self.starts_at.astimezone(UTC).isoformat(),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def booking_interval(starts_at: datetime, minutes: int) -> tuple[datetime, datetime]:
    """Half-open [start, end) in absolute time.

    Arithmetic is done in UTC so a booking that crosses a DST change lasts
    exactly `minutes` of real time, whatever the wall clock does.
    """
    if starts_at.tzinfo is None or starts_at.utcoffset() is None:
        raise InvalidBookingTimeError("start time must include a UTC offset")
    if minutes <= 0:
        raise InvalidBookingTimeError("booking duration must be positive")
    start = starts_at.astimezone(UTC)
    return start, start + timedelta(minutes=minutes)


@dataclass(frozen=True, slots=True)
class BookingResult:
    booking_id: UUID
    status: BookingStatus
    resource_id: UUID
    starts_at: datetime
    ends_at: datetime
    hold_expires_at: datetime | None
    total_cents: int
    currency: str
    quote: dict[str, Any]
    replayed: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "booking_id": str(self.booking_id),
            "status": self.status,
            "resource_id": str(self.resource_id),
            "starts_at": self.starts_at.isoformat(),
            "ends_at": self.ends_at.isoformat(),
            "hold_expires_at": self.hold_expires_at.isoformat() if self.hold_expires_at else None,
            "total_cents": self.total_cents,
            "currency": self.currency,
            "quote": self.quote,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any], *, replayed: bool) -> BookingResult:
        expires = data["hold_expires_at"]
        return cls(
            booking_id=UUID(data["booking_id"]),
            status=data["status"],
            resource_id=UUID(data["resource_id"]),
            starts_at=datetime.fromisoformat(data["starts_at"]),
            ends_at=datetime.fromisoformat(data["ends_at"]),
            hold_expires_at=datetime.fromisoformat(expires) if expires else None,
            total_cents=data["total_cents"],
            currency=data["currency"],
            quote=data["quote"],
            replayed=replayed,
        )
