"""Readiness: which required booking facts are confirmed, unconfirmed or missing.

Computed from the database on demand, never stored (ADR-0010). A confirmation
cannot stand in for missing data, and data without a confirmation is unconfirmed.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

import psycopg

from gorgona_booking.db.pool import RuntimeConnection
from gorgona_booking.db.provisioning import owner_tenant_transaction

type FactState = Literal["confirmed", "unconfirmed", "missing"]

REQUIRED_FACTS: tuple[str, ...] = (
    "owner",
    "timezone",
    "business_hours",
    "staff",
    "catalog",
    "service_durations",
    "bookable_services",
    "cancellation_policy",
    "deposit_policy",
    "booking_rules",
    "domain",
)
# Facts a salon admin or operator confirms explicitly. The others are derived:
# an active owner membership and a published bookable service are facts in themselves.
CONFIRMABLE_FACTS = frozenset(REQUIRED_FACTS) - {"owner", "bookable_services"}


@dataclass(frozen=True, slots=True)
class ReadinessSnapshot:
    active_owners: int
    locations: int
    business_hours: int
    active_artists: int
    variants: int
    variants_missing_duration: tuple[str, ...]
    bookable_variants: int
    cancellation_policy: bool
    deposit_policy: bool
    booking_rules: bool
    hosts: int
    confirmations: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ReadinessItem:
    fact: str
    status: FactState
    detail: str


@dataclass(frozen=True, slots=True)
class Readiness:
    ready: bool
    items: tuple[ReadinessItem, ...]

    def with_status(self, status: FactState) -> list[str]:
        return [i.fact for i in self.items if i.status == status]


def evaluate(s: ReadinessSnapshot) -> Readiness:
    missing_durations = ", ".join(s.variants_missing_duration)
    present: dict[str, tuple[bool, str]] = {
        "owner": (s.active_owners > 0, "an active owner membership"),
        "timezone": (s.locations > 0, "a location with an IANA timezone"),
        "business_hours": (s.business_hours > 0, "business hours"),
        "staff": (s.active_artists > 0, "at least one active artist"),
        "catalog": (s.variants > 0, "at least one service"),
        "service_durations": (
            s.variants > 0 and not s.variants_missing_duration,
            f"booking duration missing for: {missing_durations}"
            if missing_durations
            else "a booking duration for every service",
        ),
        "bookable_services": (s.bookable_variants > 0, "at least one published bookable service"),
        "cancellation_policy": (s.cancellation_policy, "a cancellation policy"),
        "deposit_policy": (s.deposit_policy, "a deposit policy"),
        "booking_rules": (s.booking_rules, "booking rules"),
        "domain": (s.hosts > 0, "a host name"),
    }
    items: list[ReadinessItem] = []
    for fact in REQUIRED_FACTS:
        has_data, description = present[fact]
        if not has_data:
            status: FactState = "missing"
        elif fact not in CONFIRMABLE_FACTS or s.confirmations.get(fact) == "confirmed":
            status = "confirmed"
        else:
            status = "unconfirmed"
        items.append(ReadinessItem(fact, status, description))
    return Readiness(ready=all(i.status == "confirmed" for i in items), items=tuple(items))


# Runs inside a tenant transaction (runtime or owner); RLS scopes every table.
_SNAPSHOT = """
select
    (select count(*) from gba.memberships where role = 'owner' and status = 'active'),
    (select count(*) from gba.locations),
    (select count(*) from gba.business_hours),
    (select count(*) from gba.resources where kind = 'artist' and is_active),
    (select count(*) from gba.service_variants where status <> 'archived'),
    (select coalesce(array_agg(code order by code), '{}'::text[]) from gba.service_variants
      where status <> 'archived' and booking_duration_minutes is null),
    (select count(*) from gba.service_variants where status = 'published' and is_bookable),
    coalesce((select cancellation_policy is not null from gba.salon_policies), false),
    coalesce((select deposit_policy is not null from gba.salon_policies), false),
    coalesce((select booking_rules is not null from gba.salon_policies), false),
    (select count(*) from gba.tenant_hosts where tenant_id = gba.current_tenant_id()),
    (select coalesce(jsonb_object_agg(fact_key, status), '{}'::jsonb)
       from gba.salon_fact_confirmations)
"""


def _snapshot(row: tuple[Any, ...] | None) -> ReadinessSnapshot:
    assert row is not None  # noqa: S101 - a SELECT without FROM always yields one row
    return ReadinessSnapshot(
        active_owners=row[0],
        locations=row[1],
        business_hours=row[2],
        active_artists=row[3],
        variants=row[4],
        variants_missing_duration=tuple(row[5]),
        bookable_variants=row[6],
        cancellation_policy=row[7],
        deposit_policy=row[8],
        booking_rules=row[9],
        hosts=row[10],
        confirmations=dict(row[11]),
    )


async def readiness(conn: RuntimeConnection) -> Readiness:
    return evaluate(_snapshot(await (await conn.execute(_SNAPSHOT)).fetchone()))


def readiness_for_owner(conn: psycopg.Connection, tenant_id: UUID) -> Readiness:
    with owner_tenant_transaction(conn, tenant_id):
        return evaluate(_snapshot(conn.execute(_SNAPSHOT).fetchone()))
