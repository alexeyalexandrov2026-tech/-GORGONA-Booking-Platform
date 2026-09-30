"""Catalog value types. Money is integer cents with an explicit ISO currency."""

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

type CatalogStatus = Literal["draft", "published", "archived"]


@dataclass(frozen=True, slots=True)
class VariantSpec:
    id: UUID
    code: str
    name: str
    status: CatalogStatus
    price_cents: int
    currency: str
    # Deterministic scheduling duration. None means unknown: never bookable.
    booking_duration_minutes: int | None
    is_bookable: bool
    revision: int
    provides: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class AddOnSpec:
    id: UUID
    code: str
    name: str
    status: CatalogStatus
    price_cents: int
    currency: str
    duration_delta_minutes: int | None
    is_bookable: bool
    revision: int
    provides: frozenset[str] = field(default_factory=frozenset)
    requires: frozenset[str] = field(default_factory=frozenset)
    conflicts_with: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class QuoteLine:
    kind: Literal["variant", "add_on"]
    id: UUID
    code: str
    name: str
    price_cents: int
    duration_minutes: int
    revision: int


@dataclass(frozen=True, slots=True)
class Quote:
    currency: str
    total_cents: int
    booking_duration_minutes: int
    lines: tuple[QuoteLine, ...]

    def snapshot(self) -> dict[str, object]:
        """Immutable record stored with a booking (JSON-serialisable)."""
        return {
            "version": 1,
            "currency": self.currency,
            "total_cents": self.total_cents,
            "booking_duration_minutes": self.booking_duration_minutes,
            "lines": [
                {
                    "kind": line.kind,
                    "id": str(line.id),
                    "code": line.code,
                    "name": line.name,
                    "price_cents": line.price_cents,
                    "duration_minutes": line.duration_minutes,
                    "revision": line.revision,
                }
                for line in self.lines
            ],
        }
