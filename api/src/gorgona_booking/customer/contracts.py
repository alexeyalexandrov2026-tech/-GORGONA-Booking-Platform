"""Versioned public contracts. No tenant ID or caller-supplied price/duration."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from gorgona_booking.catalog.models import Quote


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BookingRules(Contract):
    version: Literal[1]
    slot_interval_minutes: int = Field(ge=5, le=120)
    advance_notice_minutes: int = Field(ge=0, le=43200)
    max_days_ahead: int = Field(ge=1, le=365)


class DepositPolicy(Contract):
    version: Literal[1]
    required: bool


class CancellationPolicy(Contract):
    version: Literal[1]
    summary: str = Field(min_length=1, max_length=2000)


class Selection(Contract):
    location_id: UUID
    variant_id: UUID
    add_on_ids: list[UUID] = Field(default_factory=list, max_length=10)
    resource_id: UUID | None = None


class AvailabilityQuery(Selection):
    day: date


class CustomerHold(Selection):
    # A slot already resolved by the server, including Any Available's assigned artist.
    resource_id: UUID
    start_at: AwareDatetime


class CustomerDetails(Contract):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    phone: str = Field(min_length=7, max_length=32, pattern=r"^\+?[0-9 ()\-]+$")
    accept_policy: Literal[True]

    @field_validator("name", "email", "phone", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("name")
    @classmethod
    def no_control_characters(cls, value: str) -> str:
        if any(ord(c) < 32 for c in value):
            raise ValueError("control characters are not allowed")
        return value

    @field_validator("phone")
    @classmethod
    def phone_digits(cls, value: str) -> str:
        if not 7 <= sum(c.isdigit() for c in value) <= 15:
            raise ValueError("phone must contain 7 to 15 digits")
        return value


class QuoteLineView(Contract):
    kind: Literal["variant", "add_on"]
    id: UUID
    code: str
    name: str
    price_cents: int
    duration_minutes: int
    revision: int


class QuoteView(Contract):
    version: Literal[1] = 1
    currency: str
    total_cents: int
    booking_duration_minutes: int
    lines: list[QuoteLineView]

    @classmethod
    def from_quote(cls, quote: Quote) -> QuoteView:
        return cls.model_validate(quote.snapshot())


class Slot(Contract):
    resource_id: UUID
    start_at: datetime
    end_at: datetime


class AvailabilityView(Contract):
    version: Literal[1] = 1
    timezone: str
    quote: QuoteView
    slots: list[Slot]


class LocationView(Contract):
    id: UUID
    name: str
    timezone: str
    today: date
    last_day: date


class ArtistView(Contract):
    id: UUID
    name: str
    location_id: UUID
    service_ids: list[UUID]


class VariantView(Contract):
    id: UUID
    service_id: UUID
    service_name: str
    name: str
    price_cents: int
    currency: str
    duration_minutes: int
    provides: list[str]


class AddOnView(Contract):
    id: UUID
    name: str
    price_cents: int
    currency: str
    duration_minutes: int
    provides: list[str]
    requires: list[str]
    conflicts_with: list[str]


class BrandingView(Contract):
    logo_url: str | None = None
    accent: str | None = None


class BootstrapView(Contract):
    version: Literal[1] = 1
    name: str
    branding: BrandingView
    locations: list[LocationView]
    variants: list[VariantView]
    add_ons: list[AddOnView]
    artists: list[ArtistView]
    rules: BookingRules
    cancellation: CancellationPolicy


class CustomerBookingView(Contract):
    version: Literal[1] = 1
    booking_id: UUID
    status: Literal["HOLD", "CONFIRMED", "CANCELLED", "EXPIRED"]
    resource_id: UUID
    start_at: datetime
    end_at: datetime
    hold_expires_at: datetime | None
    quote: QuoteView
