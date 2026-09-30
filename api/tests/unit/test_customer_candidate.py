"""KA Nails web asset candidate; no business fact may become a default."""

from pathlib import Path

from gorgona_booking.onboarding.spec import load_spec

ROOT = Path(__file__).resolve().parents[3]


def test_ka_nails_candidate_references_its_independently_owned_logo() -> None:
    spec = load_spec(ROOT / "api/fixtures/ka_nails_onboarding.candidate.json")
    logo = next(ref for ref in spec.branding if ref.kind == "logo")
    assert logo.asset_ref == "/assets/ka-nails-logo.png"
    assert logo.sha256 == "bb2fe1c05eb7183b8b8f55eee861b06d80256cf5349b2958e1432fa3b83fbf53"
    # Byte integrity is verified by the independent KA Nails site acceptance suite.
    # The reusable platform export must never bundle one tenant's default logo.
    assert not (ROOT / "web/public/assets/ka-nails-logo.png").exists()


def test_ka_nails_customer_candidate_keeps_unknown_facts_absent() -> None:
    spec = load_spec(ROOT / "api/fixtures/ka_nails_onboarding.candidate.json")
    for fact in (
        spec.timezone,
        spec.business_hours,
        spec.staff,
        spec.cancellation_policy,
        spec.deposit_policy,
        spec.booking_rules,
        spec.domains,
        spec.service_durations,
    ):
        assert fact is None
    assert spec.catalog is not None
    assert spec.catalog.status == "unconfirmed"
    assert all(v.booking_duration_minutes is None for v in spec.catalog.value)
