"""Readiness is a pure function of a database snapshot (ADR-0010)."""

from dataclasses import replace

from gorgona_booking.onboarding.readiness import (
    CONFIRMABLE_FACTS,
    REQUIRED_FACTS,
    ReadinessSnapshot,
    evaluate,
)

EMPTY = ReadinessSnapshot(
    active_owners=0,
    locations=0,
    business_hours=0,
    active_artists=0,
    variants=0,
    variants_missing_duration=(),
    bookable_variants=0,
    cancellation_policy=False,
    deposit_policy=False,
    booking_rules=False,
    hosts=0,
    confirmations={},
)

COMPLETE = replace(
    EMPTY,
    active_owners=1,
    locations=1,
    business_hours=5,
    active_artists=2,
    variants=3,
    bookable_variants=3,
    cancellation_policy=True,
    deposit_policy=True,
    booking_rules=True,
    hosts=1,
)


def _statuses(snapshot: ReadinessSnapshot) -> dict[str, str]:
    return {item.fact: item.status for item in evaluate(snapshot).items}


def test_empty_salon_reports_every_required_fact_missing() -> None:
    result = evaluate(EMPTY)
    assert not result.ready
    assert [item.fact for item in result.items] == list(REQUIRED_FACTS)
    assert set(_statuses(EMPTY).values()) == {"missing"}


def test_data_without_confirmation_is_unconfirmed_never_confirmed() -> None:
    statuses = _statuses(COMPLETE)
    assert {statuses[f] for f in CONFIRMABLE_FACTS} == {"unconfirmed"}
    assert statuses["owner"] == statuses["bookable_services"] == "confirmed"
    assert not evaluate(COMPLETE).ready


def test_everything_present_and_confirmed_is_ready() -> None:
    confirmed = replace(COMPLETE, confirmations=dict.fromkeys(CONFIRMABLE_FACTS, "confirmed"))
    result = evaluate(confirmed)
    assert result.ready
    assert {item.status for item in result.items} == {"confirmed"}


def test_a_confirmation_cannot_stand_in_for_missing_data() -> None:
    claimed = replace(EMPTY, confirmations=dict.fromkeys(CONFIRMABLE_FACTS, "confirmed"))
    assert set(_statuses(claimed).values()) == {"missing"}


def test_missing_durations_are_named() -> None:
    snapshot = replace(
        COMPLETE,
        variants_missing_duration=("HAMMAM_LUXURY", "HAMMAM_LUXURY_GEL"),
        confirmations={"service_durations": "confirmed"},
    )
    item = next(i for i in evaluate(snapshot).items if i.fact == "service_durations")
    assert item.status == "missing"
    assert "HAMMAM_LUXURY" in item.detail
    assert "HAMMAM_LUXURY_GEL" in item.detail
