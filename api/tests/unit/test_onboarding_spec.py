"""The onboarding spec never invents business facts (ADR-0010)."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from gorgona_booking.onboarding.spec import OnboardingSpec, load_spec

KA_NAILS = Path(__file__).resolve().parents[2] / "fixtures" / "ka_nails_onboarding.candidate.json"

MINIMAL: dict[str, Any] = {
    "spec_version": 1,
    "slug": "fake-salon",
    "display_name": "FAKE salon",
    "location_name": "FAKE location",
}


def test_minimal_spec_leaves_every_business_fact_missing() -> None:
    spec = OnboardingSpec.model_validate(MINIMAL)
    for field in (
        "timezone",
        "domains",
        "business_hours",
        "staff",
        "catalog",
        "service_durations",
        "cancellation_policy",
        "deposit_policy",
        "booking_rules",
        "owner_email",
    ):
        assert getattr(spec, field) is None, field


@pytest.mark.parametrize(
    "overrides",
    [
        {"timezone": {"value": "America/New_York"}},  # a fact without a status
        {"timezone": {"value": "Mars/Olympus_Mons", "status": "confirmed"}},
        {"timezone": {"value": "EST", "status": "confirmed"}},
        {"domains": {"value": ["Bad Host!"], "status": "unconfirmed"}},
        {
            "business_hours": {
                "value": [{"weekday": 8, "opens": "09:00", "closes": "17:00"}],
                "status": "unconfirmed",
            }
        },
        {
            "business_hours": {
                "value": [{"weekday": 1, "opens": "17:00", "closes": "09:00"}],
                "status": "unconfirmed",
            }
        },
        {"tenant_id": "00000000-0000-0000-0000-000000000000"},
        {"spec_version": 2},
    ],
)
def test_invalid_or_undeclared_facts_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        OnboardingSpec.model_validate(MINIMAL | overrides)


def test_ka_nails_candidate_spec_only_contains_known_facts() -> None:
    spec = load_spec(KA_NAILS)
    assert spec.slug == "ka-nails"
    assert spec.display_name == "KA Nails"
    for unknown in (
        "timezone",
        "domains",
        "business_hours",
        "staff",
        "service_durations",
        "cancellation_policy",
        "deposit_policy",
        "booking_rules",
        "owner_email",
    ):
        assert getattr(spec, unknown) is None, (
            f"{unknown} must stay missing until the owner confirms"
        )
    assert spec.catalog is not None
    assert spec.catalog.status == "unconfirmed"
    assert all(v.booking_duration_minutes is None for v in spec.catalog.value)
    assert {v.code: v.price_cents for v in spec.catalog.value} == {
        "HAMMAM_LUXURY": 14500,
        "HAMMAM_LUXURY_GEL": 16000,
        "HAMMAM_LUXURY_GEL_FRENCH": 17500,
    }
