from datetime import UTC, date, datetime, timedelta

from gorgona_booking.customer.availability import available_starts, wall_windows


def test_work_hours_intersect_and_duration_must_fit() -> None:
    day = date(2031, 6, 2)
    windows = wall_windows(day, "UTC", [(540, 720), (780, 1020)])
    staff = wall_windows(day, "UTC", [(600, 840)])
    starts = available_starts(
        windows, staff, [], minutes=60, step=30, after=datetime(2031, 6, 1, tzinfo=UTC)
    )
    assert [x.hour * 60 + x.minute for x in starts] == [600, 630, 660, 780]


def test_occupancy_half_open_and_lead_time() -> None:
    windows = wall_windows(date(2031, 6, 2), "UTC", [(540, 720)])
    occupied = [(datetime(2031, 6, 2, 10, tzinfo=UTC), datetime(2031, 6, 2, 11, tzinfo=UTC))]
    starts = available_starts(
        windows,
        windows,
        occupied,
        minutes=60,
        step=30,
        after=datetime(2031, 6, 2, 9, 1, tzinfo=UTC),
    )
    assert starts == [datetime(2031, 6, 2, 11, tzinfo=UTC)]


def test_missing_artist_schedule_has_no_availability() -> None:
    windows = wall_windows(date(2031, 6, 2), "UTC", [(540, 1020)])
    assert (
        available_starts(
            windows, [], [], minutes=60, step=15, after=datetime(2031, 6, 1, tzinfo=UTC)
        )
        == []
    )


def test_dst_gap_is_not_invented() -> None:
    windows = wall_windows(date(2031, 3, 9), "America/New_York", [(60, 240)])
    starts = available_starts(
        windows, windows, [], minutes=30, step=30, after=datetime(2031, 3, 8, tzinfo=UTC)
    )
    assert [x.isoformat() for x in starts] == [
        f"2031-03-09T{h}:00+00:00" for h in ("06:00", "06:30", "07:00", "07:30")
    ]


def test_dst_overlap_has_two_distinct_real_slots() -> None:
    windows = wall_windows(date(2031, 11, 2), "America/New_York", [(60, 120)])
    starts = available_starts(
        windows, windows, [], minutes=30, step=30, after=datetime(2031, 11, 1, tzinfo=UTC)
    )
    assert len(starts) == 4
    assert starts[-1] - starts[0] == timedelta(minutes=90)


def test_short_wall_window_cannot_bridge_closed_time_in_dst_overlap() -> None:
    windows = wall_windows(date(2031, 11, 2), "America/New_York", [(90, 105)])
    assert len(windows) == 2
    assert (
        available_starts(
            windows, windows, [], minutes=60, step=5, after=datetime(2031, 11, 1, tzinfo=UTC)
        )
        == []
    )
