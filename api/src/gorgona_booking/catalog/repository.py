"""Reads catalog rows (under the caller's tenant transaction) into domain specs."""

from collections.abc import Sequence
from uuid import UUID

from gorgona_booking.catalog.models import AddOnSpec, VariantSpec
from gorgona_booking.db.pool import RuntimeConnection
from gorgona_booking.errors import NotFoundError

_VARIANT = """
select v.id, v.code, v.name, v.status, v.price_cents, v.currency,
       v.booking_duration_minutes, v.is_bookable, v.revision,
       coalesce(array_agg(vc.component_code) filter (where vc.component_code is not null),
                '{}'::text[])
from gba.service_variants v
left join gba.variant_components vc
       on vc.tenant_id = v.tenant_id and vc.variant_id = v.id
where v.id = %s
group by v.tenant_id, v.id
"""

_ADD_ONS = """
select a.id, a.code, a.name, a.status, a.price_cents, a.currency,
       a.duration_delta_minutes, a.is_bookable, a.revision,
       coalesce(array_agg(r.component_code) filter (where r.relation = 'PROVIDES'), '{}'::text[]),
       coalesce(array_agg(r.component_code) filter (where r.relation = 'REQUIRES'), '{}'::text[]),
       coalesce(array_agg(r.component_code) filter (where r.relation = 'CONFLICTS_WITH'),
                '{}'::text[])
from gba.add_ons a
left join gba.add_on_rules r
       on r.tenant_id = a.tenant_id and r.add_on_id = a.id
where a.id = any(%s)
group by a.tenant_id, a.id
"""


async def load_variant(conn: RuntimeConnection, variant_id: UUID) -> VariantSpec:
    row = await (await conn.execute(_VARIANT, (variant_id,))).fetchone()
    if row is None:
        raise NotFoundError("Service not found", variant_id=str(variant_id))
    return VariantSpec(
        id=row[0],
        code=row[1],
        name=row[2],
        status=row[3],
        price_cents=row[4],
        currency=row[5],
        booking_duration_minutes=row[6],
        is_bookable=row[7],
        revision=row[8],
        provides=frozenset(row[9]),
    )


async def load_add_ons(conn: RuntimeConnection, add_on_ids: Sequence[UUID]) -> list[AddOnSpec]:
    if not add_on_ids:
        return []
    rows = await (await conn.execute(_ADD_ONS, (list(add_on_ids),))).fetchall()
    by_id = {
        row[0]: AddOnSpec(
            id=row[0],
            code=row[1],
            name=row[2],
            status=row[3],
            price_cents=row[4],
            currency=row[5],
            duration_delta_minutes=row[6],
            is_bookable=row[7],
            revision=row[8],
            provides=frozenset(row[9]),
            requires=frozenset(row[10]),
            conflicts_with=frozenset(row[11]),
        )
        for row in rows
    }
    missing = [str(i) for i in add_on_ids if i not in by_id]
    if missing:
        raise NotFoundError("Add-on not found", add_on_id=missing[0])
    # Preserve the caller's order; duplicates are reported by the quote engine.
    return [by_id[i] for i in add_on_ids]
