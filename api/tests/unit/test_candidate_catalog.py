"""The owner-unconfirmed KA Nails candidate catalog stays non-bookable, and its
composition rules match the brief when (FAKE, test-only) durations are supplied."""

from dataclasses import replace
from pathlib import Path

import pytest

from gorgona_booking.catalog.candidate import CandidateCatalog, load_candidate_catalog
from gorgona_booking.catalog.models import AddOnSpec, VariantSpec
from gorgona_booking.catalog.quote import (
    IncompatibleSelectionError,
    ServiceNotBookableError,
    build_quote,
)

CANDIDATE = Path(__file__).resolve().parents[2] / "fixtures" / "ka_nails_catalog.candidate.json"
FAKE_TEST_ONLY_MINUTES = 60  # Not a KA Nails duration; only makes rules testable.


@pytest.fixture(scope="module")
def catalog() -> CandidateCatalog:
    return load_candidate_catalog(CANDIDATE)


def _bookable(catalog: CandidateCatalog, code: str) -> VariantSpec:
    return replace(
        catalog.variants[code],
        status="published",
        is_bookable=True,
        booking_duration_minutes=FAKE_TEST_ONLY_MINUTES,
    )


def _addon(catalog: CandidateCatalog, code: str) -> AddOnSpec:
    add_on = catalog.add_ons[code]
    return replace(
        add_on,
        status="published",
        is_bookable=True,
        duration_delta_minutes=add_on.duration_delta_minutes or 0,
    )


def test_candidate_is_marked_unconfirmed_and_nothing_is_bookable(catalog: CandidateCatalog) -> None:
    assert catalog.status == "owner_unconfirmed"
    for variant in catalog.variants.values():
        assert variant.booking_duration_minutes is None
        with pytest.raises(ServiceNotBookableError) as info:
            build_quote(variant)
        assert info.value.details["reason"] == "booking_duration_unknown"


def test_candidate_prices_match_the_brief(catalog: CandidateCatalog) -> None:
    prices = {code: v.price_cents for code, v in catalog.variants.items()}
    assert prices == {
        "HAMMAM_LUXURY": 14500,
        "HAMMAM_LUXURY_GEL": 16000,
        "HAMMAM_LUXURY_GEL_FRENCH": 17500,
    }
    assert catalog.display_ranges["HAMMAM_LUXURY"] is None
    assert catalog.display_ranges["HAMMAM_LUXURY_GEL"] == (120, 145)
    assert catalog.display_ranges["HAMMAM_LUXURY_GEL_FRENCH"] == (130, 155)
    assert catalog.add_ons["EXTRA_FOOT_MASSAGE"].duration_delta_minutes == 15
    referenced = set().union(
        *(v.provides for v in catalog.variants.values()),
        *(a.provides | a.requires | a.conflicts_with for a in catalog.add_ons.values()),
    )
    assert referenced <= catalog.components


def test_gel_plus_french_gel_prices_like_the_gel_french_variant(catalog: CandidateCatalog) -> None:
    quote = build_quote(_bookable(catalog, "HAMMAM_LUXURY_GEL"), [_addon(catalog, "FRENCH_GEL")])
    assert quote.total_cents == catalog.variants["HAMMAM_LUXURY_GEL_FRENCH"].price_cents


@pytest.mark.parametrize(
    ("variant", "add_on", "reason"),
    [
        ("HAMMAM_LUXURY", "FRENCH_GEL", "requirement_missing"),
        ("HAMMAM_LUXURY_GEL_FRENCH", "FRENCH_GEL", "duplicate_component"),
        ("HAMMAM_LUXURY", "HAMMAM_SPA_UPGRADE", "duplicate_component"),
        ("HAMMAM_LUXURY", "HEEL_CARE_ONLY", "duplicate_component"),
    ],
)
def test_brief_composition_rules(
    catalog: CandidateCatalog, variant: str, add_on: str, reason: str
) -> None:
    with pytest.raises(IncompatibleSelectionError) as info:
        build_quote(_bookable(catalog, variant), [_addon(catalog, add_on)])
    assert info.value.details["reason"] == reason


def test_extra_foot_massage_adds_exactly_fifteen_minutes(catalog: CandidateCatalog) -> None:
    base = _bookable(catalog, "HAMMAM_LUXURY")
    quote = build_quote(base, [_addon(catalog, "EXTRA_FOOT_MASSAGE")])
    assert quote.booking_duration_minutes == FAKE_TEST_ONLY_MINUTES + 15
    assert quote.total_cents == 14500 + 2000
