"""Quote engine rules, using FAKE catalog data only (not KA Nails facts)."""

import json
from collections.abc import Iterable
from dataclasses import replace
from uuid import uuid4

import pytest

from gorgona_booking.catalog.models import AddOnSpec, VariantSpec
from gorgona_booking.catalog.quote import (
    AddOnNotBookableError,
    IncompatibleSelectionError,
    ServiceNotBookableError,
    build_quote,
)


def fake_variant(code: str, minutes: int | None, cents: int, provides: set[str]) -> VariantSpec:
    return VariantSpec(
        id=uuid4(),
        code=code,
        name=f"FAKE {code}",
        status="published",
        price_cents=cents,
        currency="USD",
        booking_duration_minutes=minutes,
        is_bookable=minutes is not None,
        revision=3,
        provides=frozenset(provides),
    )


def fake_add_on(
    code: str,
    minutes: int | None,
    cents: int,
    *,
    provides: Iterable[str] = (),
    requires: Iterable[str] = (),
    conflicts_with: Iterable[str] = (),
) -> AddOnSpec:
    return AddOnSpec(
        id=uuid4(),
        code=code,
        name=f"FAKE {code}",
        status="published",
        price_cents=cents,
        currency="USD",
        duration_delta_minutes=minutes,
        is_bookable=minutes is not None,
        revision=1,
        provides=frozenset(provides),
        requires=frozenset(requires),
        conflicts_with=frozenset(conflicts_with),
    )


BASE = fake_variant("FAKE_BASE", 60, 5000, {"FAKE_PEDICURE", "FAKE_HEEL_CARE", "FAKE_RITUAL"})
GEL = fake_variant("FAKE_GEL", 90, 6500, {"FAKE_PEDICURE", "FAKE_HEEL_CARE", "FAKE_GEL_COVERAGE"})
MASSAGE = fake_add_on("FAKE_MASSAGE", 15, 2000, provides={"FAKE_MASSAGE"})
FRENCH = fake_add_on(
    "FAKE_FRENCH", 10, 1500, provides={"FAKE_FRENCH_DESIGN"}, requires={"FAKE_GEL_COVERAGE"}
)
HEEL_CARE = fake_add_on("FAKE_HEEL_CARE_ONLY", 20, 4000, provides={"FAKE_HEEL_CARE"})
UPGRADE = fake_add_on(
    "FAKE_UPGRADE", 20, 4000, requires={"FAKE_PEDICURE"}, conflicts_with={"FAKE_RITUAL"}
)


def test_base_quote_uses_booking_duration_and_integer_cents() -> None:
    quote = build_quote(BASE)
    assert (quote.total_cents, quote.currency, quote.booking_duration_minutes) == (5000, "USD", 60)


def test_add_on_extends_interval_by_its_exact_delta() -> None:
    quote = build_quote(BASE, [MASSAGE])
    assert quote.total_cents == 7000
    assert quote.booking_duration_minutes == 75


def test_requires_component_from_the_selection() -> None:
    with pytest.raises(IncompatibleSelectionError) as info:
        build_quote(BASE, [FRENCH])
    assert info.value.details["reason"] == "requirement_missing"
    assert build_quote(GEL, [FRENCH]).total_cents == 8000


def test_included_component_is_never_charged_twice() -> None:
    with pytest.raises(IncompatibleSelectionError) as info:
        build_quote(BASE, [HEEL_CARE])
    assert info.value.details == {
        "add_on": "FAKE_HEEL_CARE_ONLY",
        "component": "FAKE_HEEL_CARE",
        "included_in": "FAKE_BASE",
        "reason": "duplicate_component",
    }


def test_conflicting_component_is_rejected() -> None:
    with pytest.raises(IncompatibleSelectionError) as info:
        build_quote(BASE, [UPGRADE])
    assert info.value.details["reason"] == "conflict"
    assert build_quote(GEL, [UPGRADE]).booking_duration_minutes == 110


@pytest.mark.parametrize(
    ("variant", "reason"),
    [
        (
            replace(BASE, booking_duration_minutes=None, is_bookable=False),
            "booking_duration_unknown",
        ),
        (replace(BASE, booking_duration_minutes=None), "booking_duration_unknown"),
        (replace(BASE, booking_duration_minutes=0), "booking_duration_invalid"),
        (replace(BASE, status="draft"), "not_published"),
        (replace(BASE, is_bookable=False), "not_bookable"),
    ],
)
def test_unbookable_variants_are_rejected(variant: VariantSpec, reason: str) -> None:
    with pytest.raises(ServiceNotBookableError) as info:
        build_quote(variant)
    assert info.value.details["reason"] == reason


def test_add_on_with_unknown_duration_is_not_bookable() -> None:
    unknown = replace(MASSAGE, duration_delta_minutes=None)
    with pytest.raises(AddOnNotBookableError):
        build_quote(BASE, [unknown])


@pytest.mark.parametrize(
    ("add_ons", "reason"),
    [
        ([MASSAGE, MASSAGE], "duplicate_add_on"),
        ([replace(MASSAGE, currency="EUR")], "currency_mismatch"),
        (
            [
                fake_add_on(f"FAKE_LONG_{i}", 240, 100, provides={f"FAKE_LONG_{i}"})
                for i in range(3)
            ],
            "duration_too_long",
        ),
    ],
)
def test_invalid_selections(add_ons: list[AddOnSpec], reason: str) -> None:
    with pytest.raises(IncompatibleSelectionError) as info:
        build_quote(GEL, add_ons)
    assert info.value.details["reason"] == reason


def test_snapshot_is_json_and_records_revisions() -> None:
    snapshot = build_quote(GEL, [FRENCH]).snapshot()
    decoded = json.loads(json.dumps(snapshot))
    assert decoded["total_cents"] == 8000
    assert [line["revision"] for line in decoded["lines"]] == [3, 1]
