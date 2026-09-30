"""Onboarding spec: governed salon facts, never invented defaults (ADR-0010).

Every business fact is optional. An absent fact stays missing. A present fact must
say whether it is `confirmed` or `unconfirmed` — there is no default status.
"""

import json
import re
import zoneinfo
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

type FactStatus = Literal["confirmed", "unconfirmed"]

_CODE = r"^[A-Z][A-Z0-9_]{1,63}$"
_SLUG = r"^[a-z0-9]+(-[a-z0-9]+)*$"
_HOST = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]{1,5})?$")
_CLOCK = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$|^24:00$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Fact[T](Strict):
    value: T
    status: FactStatus
    source: str | None = Field(default=None, min_length=1, max_length=500)


class StatusOnly(Strict):
    """A fact whose data lives elsewhere (e.g. durations inside the catalog)."""

    status: FactStatus
    source: str | None = Field(default=None, min_length=1, max_length=500)


def clock_minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


class HoursEntry(Strict):
    weekday: int = Field(ge=1, le=7)  # ISO 8601: 1 = Monday
    opens: str
    closes: str

    @field_validator("opens", "closes")
    @classmethod
    def _clock(cls, value: str) -> str:
        if not _CLOCK.fullmatch(value):
            raise ValueError("times must be HH:MM (24h)")
        return value

    @model_validator(mode="after")
    def _order(self) -> HoursEntry:
        if clock_minutes(self.opens) >= clock_minutes(self.closes) or self.opens == "24:00":
            raise ValueError("opening time must be before closing time")
        return self


class StaffEntry(Strict):
    display_name: str = Field(min_length=1, max_length=200)


class VariantEntry(Strict):
    service_code: str = Field(pattern=_CODE)
    service_name: str = Field(min_length=1, max_length=200)
    code: str = Field(pattern=_CODE)
    name: str = Field(min_length=1, max_length=200)
    price_cents: int = Field(ge=0, le=10_000_000)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    # None = unknown. Never filled from a public range or a guess.
    booking_duration_minutes: int | None = Field(default=None, ge=1, le=720)
    display_duration_minutes: tuple[int, int] | None = None


class BrandingRef(Strict):
    kind: Literal["logo", "favicon", "palette", "typography"]
    asset_ref: str = Field(min_length=1, max_length=500)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class OnboardingSpec(Strict):
    spec_version: Literal[1]
    slug: str = Field(pattern=_SLUG, max_length=63)
    display_name: str = Field(min_length=1, max_length=200)
    location_name: str = Field(min_length=1, max_length=200)
    timezone: Fact[str] | None = None
    domains: Fact[list[str]] | None = None
    business_hours: Fact[list[HoursEntry]] | None = None
    staff: Fact[list[StaffEntry]] | None = None
    catalog: Fact[list[VariantEntry]] | None = None
    service_durations: StatusOnly | None = None
    cancellation_policy: Fact[dict[str, Any]] | None = None
    deposit_policy: Fact[dict[str, Any]] | None = None
    booking_rules: Fact[dict[str, Any]] | None = None
    owner_email: Fact[str] | None = None
    branding: list[BrandingRef] = Field(default_factory=list)

    @field_validator("timezone")
    @classmethod
    def _iana(cls, fact: Fact[str] | None) -> Fact[str] | None:
        if fact is None:
            return None
        zone = fact.value
        if not re.fullmatch(r"UTC|[A-Za-z_]+(/[A-Za-z0-9_+-]+)+", zone):
            raise ValueError("timezone must be an IANA name such as Europe/London")
        try:
            zoneinfo.ZoneInfo(zone)
        except zoneinfo.ZoneInfoNotFoundError, ValueError:
            raise ValueError("unknown IANA timezone") from None
        return fact

    @field_validator("domains")
    @classmethod
    def _hosts(cls, fact: Fact[list[str]] | None) -> Fact[list[str]] | None:
        if fact is None:
            return None
        if not fact.value or any(not _HOST.fullmatch(h) or len(h) > 260 for h in fact.value):
            raise ValueError("domains must be lower-case host names")
        return fact

    @field_validator("owner_email")
    @classmethod
    def _email(cls, fact: Fact[str] | None) -> Fact[str] | None:
        if fact is not None and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", fact.value):
            raise ValueError("owner_email must be an email address")
        return fact


def load_spec(path: Path) -> OnboardingSpec:
    return OnboardingSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))
