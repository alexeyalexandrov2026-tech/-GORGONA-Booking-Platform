"""Loads the owner-unconfirmed KA Nails candidate catalog for review and tests.

It is never seeded into a database. Every entry loads as a draft, non-bookable
spec, and booking durations stay None until the owner confirms them.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from gorgona_booking.catalog.models import AddOnSpec, VariantSpec

_NAMESPACE = uuid5(NAMESPACE_URL, "urn:gorgona-booking:candidate-catalog")


def _id(code: str) -> UUID:
    return uuid5(_NAMESPACE, code)


def _range(value: list[int] | None) -> tuple[int, int] | None:
    return (value[0], value[1]) if value else None


@dataclass(frozen=True, slots=True)
class CandidateCatalog:
    status: str
    currency: str
    components: frozenset[str]
    variants: dict[str, VariantSpec]
    add_ons: dict[str, AddOnSpec]
    display_ranges: dict[str, tuple[int, int] | None]


def load_candidate_catalog(path: Path) -> CandidateCatalog:
    data = json.loads(path.read_text(encoding="utf-8"))
    currency = data["currency"]
    variants = {
        v["code"]: VariantSpec(
            id=_id(v["code"]),
            code=v["code"],
            name=v["name"],
            status="draft",
            price_cents=v["price_cents"],
            currency=currency,
            booking_duration_minutes=v["booking_duration_minutes"],
            is_bookable=False,
            revision=1,
            provides=frozenset(v["provides"]),
        )
        for v in data["variants"]
    }
    add_ons = {
        a["code"]: AddOnSpec(
            id=_id(a["code"]),
            code=a["code"],
            name=a["name"],
            status="draft",
            price_cents=a["price_cents"],
            currency=currency,
            duration_delta_minutes=a["duration_delta_minutes"],
            is_bookable=False,
            revision=1,
            provides=frozenset(a.get("provides", ())),
            requires=frozenset(a.get("requires", ())),
            conflicts_with=frozenset(a.get("conflicts_with", ())),
        )
        for a in data["add_ons"]
    }
    display_ranges = {v["code"]: _range(v["display_duration_minutes"]) for v in data["variants"]}
    return CandidateCatalog(
        status=data["status"],
        currency=currency,
        components=frozenset(data["components"]),
        variants=variants,
        add_ons=add_ons,
        display_ranges=display_ranges,
    )
