from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from gorgona_booking.booking.models import (
    BookingResult,
    InvalidBookingTimeError,
    ReservationRequest,
    booking_interval,
)
from gorgona_booking.booking.repository import is_slot_conflict

NEW_YORK = ZoneInfo("America/New_York")  # test zone with DST, not a KA Nails fact


def test_fingerprint_is_stable_and_ignores_add_on_order_and_offset_notation() -> None:
    resource, variant, a, b = uuid4(), uuid4(), uuid4(), uuid4()
    start = datetime(2030, 5, 1, 14, 0, tzinfo=UTC)
    first = ReservationRequest(resource, variant, start, (a, b))
    same = ReservationRequest(resource, variant, start.astimezone(NEW_YORK), (b, a))
    later = ReservationRequest(resource, variant, start + timedelta(minutes=15), (a, b))
    assert first.fingerprint("booking.create_hold") == same.fingerprint("booking.create_hold")
    assert first.fingerprint("booking.create_hold") != later.fingerprint("booking.create_hold")
    assert first.fingerprint("booking.create_hold") != first.fingerprint("booking.create_confirmed")


def test_interval_is_half_open_absolute_time_across_dst_fall_back() -> None:
    # 01:00 EDT on the 2026 fall-back day; the wall clock repeats 01:00-02:00.
    start = datetime(2026, 11, 1, 1, 0, tzinfo=NEW_YORK)
    begin, end = booking_interval(start, 90)
    assert end - begin == timedelta(minutes=90)
    assert begin == datetime(2026, 11, 1, 5, 0, tzinfo=UTC)
    local_end = end.astimezone(NEW_YORK)
    assert (local_end.hour, local_end.minute, local_end.utcoffset()) == (1, 30, timedelta(hours=-5))


def test_interval_across_dst_spring_forward_keeps_real_duration() -> None:
    # 01:30 EST; 02:00-03:00 does not exist on this day.
    start = datetime(2026, 3, 8, 1, 30, tzinfo=NEW_YORK)
    begin, end = booking_interval(start, 60)
    assert end - begin == timedelta(minutes=60)
    assert end.astimezone(NEW_YORK).hour == 3  # 03:30 EDT: one real hour later


def test_interval_across_local_midnight() -> None:
    start = datetime(2030, 1, 1, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    _, end = booking_interval(start, 60)
    assert end == datetime(2030, 1, 2, 5, 30, tzinfo=UTC)


@pytest.mark.parametrize("minutes", [0, -15])
def test_interval_rejects_non_positive_duration(minutes: int) -> None:
    with pytest.raises(InvalidBookingTimeError):
        booking_interval(datetime(2030, 1, 1, tzinfo=UTC), minutes)


def test_interval_rejects_naive_datetimes() -> None:
    with pytest.raises(InvalidBookingTimeError):
        booking_interval(datetime(2030, 1, 1, 10, 0), 60)  # noqa: DTZ001


def test_booking_result_round_trips_through_json() -> None:
    result = BookingResult(
        booking_id=uuid4(),
        status="HOLD",
        resource_id=uuid4(),
        starts_at=datetime(2030, 1, 1, 15, tzinfo=UTC),
        ends_at=datetime(2030, 1, 1, 16, tzinfo=UTC),
        hold_expires_at=datetime(2029, 12, 31, 12, tzinfo=UTC),
        total_cents=5000,
        currency="USD",
        quote={"version": 1},
    )
    replayed = BookingResult.from_json(result.to_json(), replayed=True)
    assert replayed.replayed is True
    assert replayed == replace(result, replayed=True)


def _pg_error(sqlstate: str, constraint: str | None) -> SimpleNamespace:
    return SimpleNamespace(sqlstate=sqlstate, diag=SimpleNamespace(constraint_name=constraint))


def test_only_the_occupancy_exclusion_maps_to_a_slot_conflict() -> None:
    assert is_slot_conflict(_pg_error("23P01", "booking_allocations_no_overlap"))
    assert not is_slot_conflict(_pg_error("23P01", "some_other_exclusion"))
    assert not is_slot_conflict(_pg_error("23505", "booking_allocations_no_overlap"))
    assert not is_slot_conflict(_pg_error("40P01", None))
