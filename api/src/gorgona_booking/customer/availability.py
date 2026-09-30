"""Minute-precision schedules, including real instants at DST transitions."""

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

type Window = tuple[datetime, datetime]


def wall_windows(day: date, timezone: str, hours: Sequence[tuple[int, int]]) -> list[Window]:
    zone = ZoneInfo(timezone)
    midnight = datetime.combine(day, time())
    instants: set[datetime] = set()
    for opens, closes in hours:
        for minute in range(opens, closes):
            wall = midnight + timedelta(minutes=minute)
            for fold in (0, 1):
                instant = wall.replace(tzinfo=zone, fold=fold).astimezone(UTC)
                if instant.astimezone(zone).replace(tzinfo=None) == wall:
                    instants.add(instant)
    windows: list[Window] = []
    delta = timedelta(minutes=1)
    for instant in sorted(instants):
        if windows and windows[-1][1] == instant:
            windows[-1] = (windows[-1][0], instant + delta)
        else:
            windows.append((instant, instant + delta))
    return windows


def available_starts(
    location: Sequence[Window],
    artist: Sequence[Window],
    occupied: Sequence[Window],
    *,
    minutes: int,
    step: int,
    after: datetime,
) -> list[datetime]:
    if minutes <= 0 or step <= 0 or after.tzinfo is None:
        raise ValueError("positive duration/step and aware cutoff required")
    duration, increment = timedelta(minutes=minutes), timedelta(minutes=step)
    starts: set[datetime] = set()
    for left, right in location:
        for staff_left, staff_right in artist:
            start, end = max(left, staff_left), min(right, staff_right)
            while start + duration <= end:
                if start >= after and not any(
                    start < busy_end and start + duration > busy_start
                    for busy_start, busy_end in occupied
                ):
                    starts.add(start)
                start += increment
    return sorted(starts)
