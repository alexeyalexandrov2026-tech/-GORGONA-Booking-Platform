"""Deterministic, side-effect-free quote engine.

Mirrors the database bookability CHECKs and adds the cross-row composition rules:
REQUIRES components must be provided by the rest of the selection, CONFLICTS_WITH
components must not be, and no component may be provided twice (so an included
component, such as heel care, is never charged again).
"""

from collections.abc import Sequence

from gorgona_booking.catalog.models import AddOnSpec, Quote, QuoteLine, VariantSpec
from gorgona_booking.errors import DomainError

MAX_BOOKING_MINUTES = 720


class ServiceNotBookableError(DomainError):
    code = "SERVICE_NOT_BOOKABLE"


class AddOnNotBookableError(DomainError):
    code = "ADD_ON_NOT_BOOKABLE"


class IncompatibleSelectionError(DomainError):
    code = "INCOMPATIBLE_SELECTION"


def _variant_problem(variant: VariantSpec) -> str | None:
    if variant.booking_duration_minutes is None:
        return "booking_duration_unknown"
    if not 1 <= variant.booking_duration_minutes <= MAX_BOOKING_MINUTES:
        return "booking_duration_invalid"
    if variant.status != "published":
        return "not_published"
    if not variant.is_bookable:
        return "not_bookable"
    return None


def _add_on_problem(add_on: AddOnSpec) -> str | None:
    if add_on.duration_delta_minutes is None:
        return "duration_unknown"
    if not 0 <= add_on.duration_delta_minutes <= 240:
        return "duration_invalid"
    if add_on.status != "published":
        return "not_published"
    if not add_on.is_bookable:
        return "not_bookable"
    return None


def build_quote(variant: VariantSpec, add_ons: Sequence[AddOnSpec] = ()) -> Quote:
    if reason := _variant_problem(variant):
        raise ServiceNotBookableError(
            f"{variant.name} cannot be booked yet", variant=variant.code, reason=reason
        )

    seen: set[str] = set()
    for add_on in add_ons:
        if add_on.code in seen:
            raise IncompatibleSelectionError(
                "An add-on was selected twice", add_on=add_on.code, reason="duplicate_add_on"
            )
        seen.add(add_on.code)
        if reason := _add_on_problem(add_on):
            raise AddOnNotBookableError(
                f"{add_on.name} cannot be booked yet", add_on=add_on.code, reason=reason
            )
        if add_on.currency != variant.currency:
            raise IncompatibleSelectionError(
                "Add-on currency differs from the service",
                add_on=add_on.code,
                reason="currency_mismatch",
            )

    provider_of: dict[str, str] = dict.fromkeys(variant.provides, variant.code)
    for add_on in add_ons:
        for component in sorted(add_on.provides):
            if component in provider_of:
                raise IncompatibleSelectionError(
                    f"{add_on.name} duplicates something already included",
                    add_on=add_on.code,
                    component=component,
                    included_in=provider_of[component],
                    reason="duplicate_component",
                )
            provider_of[component] = add_on.code

    for add_on in add_ons:
        provided_by_others = {c for c, owner in provider_of.items() if owner != add_on.code}
        if missing := sorted(add_on.requires - provided_by_others):
            raise IncompatibleSelectionError(
                f"{add_on.name} is not available with this service",
                add_on=add_on.code,
                component=missing[0],
                reason="requirement_missing",
            )
        if clash := sorted(add_on.conflicts_with & provided_by_others):
            raise IncompatibleSelectionError(
                f"{add_on.name} cannot be combined with this selection",
                add_on=add_on.code,
                component=clash[0],
                reason="conflict",
            )

    lines = [
        QuoteLine(
            kind="variant",
            id=variant.id,
            code=variant.code,
            name=variant.name,
            price_cents=variant.price_cents,
            duration_minutes=variant.booking_duration_minutes or 0,
            revision=variant.revision,
        ),
        *(
            QuoteLine(
                kind="add_on",
                id=a.id,
                code=a.code,
                name=a.name,
                price_cents=a.price_cents,
                duration_minutes=a.duration_delta_minutes or 0,
                revision=a.revision,
            )
            for a in add_ons
        ),
    ]
    duration = sum(line.duration_minutes for line in lines)
    if duration > MAX_BOOKING_MINUTES:
        raise IncompatibleSelectionError(
            "The selection is longer than one booking allows",
            minutes=duration,
            reason="duration_too_long",
        )
    return Quote(
        currency=variant.currency,
        total_cents=sum(line.price_cents for line in lines),
        booking_duration_minutes=duration,
        lines=tuple(lines),
    )
