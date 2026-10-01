"""Customer reads, always inside a Host-resolved tenant transaction."""

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from gorgona_booking.catalog.models import Quote
from gorgona_booking.catalog.quote import build_quote
from gorgona_booking.catalog.repository import load_add_ons, load_variant
from gorgona_booking.customer.availability import available_starts, wall_windows
from gorgona_booking.customer.contracts import (
    AddOnView,
    ArtistView,
    AvailabilityQuery,
    AvailabilityView,
    BookingRules,
    BootstrapView,
    BrandingView,
    CancellationPolicy,
    DepositPolicy,
    LocationView,
    QuoteView,
    Selection,
    Slot,
    VariantView,
)
from gorgona_booking.db.pool import RuntimeConnection
from gorgona_booking.errors import DomainError, NotFoundError
from gorgona_booking.tenancy.resolver import TenantNotFoundError


class BookingConfigurationError(DomainError):
    code = "BOOKING_CONFIGURATION_MISSING"


class PaymentRequiredError(DomainError):
    code = "PAYMENT_REQUIRED"


async def live_name(conn: RuntimeConnection) -> str:
    row = await (
        await conn.execute(
            "select display_name from gba.tenants where status = 'active' "
            "and booking_state = 'live'"
        )
    ).fetchone()
    if row is None:
        raise TenantNotFoundError("Unknown site")
    return str(row[0])


async def policies(conn: RuntimeConnection) -> tuple[BookingRules, CancellationPolicy]:
    row = await (
        await conn.execute(
            "select booking_rules, deposit_policy, cancellation_policy from gba.salon_policies "
            "for share"
        )
    ).fetchone()
    try:
        if row is None:
            raise BookingConfigurationError("Booking settings have not been supplied")
        rules = BookingRules.model_validate(row[0], strict=True)
        deposit = DepositPolicy.model_validate(row[1], strict=True)
        cancellation = CancellationPolicy.model_validate(row[2], strict=True)
    except ValidationError as exc:
        raise BookingConfigurationError("Booking settings have not been supplied") from exc
    if deposit.required:
        raise PaymentRequiredError("Online booking requiring a deposit is not available yet")
    return rules, cancellation


async def quote_selection(conn: RuntimeConnection, selection: Selection) -> Quote:
    # A hold must validate and persist exactly the same catalog revisions. Lock in
    # stable order before loading composition, so concurrent price edits wait.
    await conn.execute(
        "select id from gba.service_variants where id = %s for share", (selection.variant_id,)
    )
    if selection.add_on_ids:
        await conn.execute(
            "select id from gba.add_ons where id = any(%s) order by id for share",
            (selection.add_on_ids,),
        )
    return build_quote(
        await load_variant(conn, selection.variant_id),
        await load_add_ons(conn, selection.add_on_ids),
    )


async def artists(
    conn: RuntimeConnection, location_id: UUID | None = None, variant_id: UUID | None = None
) -> list[ArtistView]:
    rows = await (
        await conn.execute(
            "select r.id, r.display_name, r.location_id, "
            "array_agg(rs.service_id order by rs.service_id) "
            "from gba.resources r join gba.resource_services rs "
            "on rs.tenant_id = r.tenant_id and rs.resource_id = r.id "
            "where r.is_active and r.kind = 'artist' "
            "and (%s::uuid is null or r.location_id = %s) "
            "and (%s::uuid is null or rs.service_id = "
            "(select service_id from gba.service_variants where id = %s)) "
            "group by r.tenant_id, r.id order by r.display_name, r.id",
            (location_id, location_id, variant_id, variant_id),
        )
    ).fetchall()
    return [ArtistView(id=r[0], name=r[1], location_id=r[2], service_ids=r[3]) for r in rows]


async def bootstrap(conn: RuntimeConnection) -> BootstrapView:
    name = await live_name(conn)
    rules, cancellation = await policies(conn)
    now = datetime.now(UTC)
    locations = await (
        await conn.execute("select id, name, timezone from gba.locations order by id")
    ).fetchall()
    variants = await (
        await conn.execute(
            "select v.id, v.service_id, s.name from gba.service_variants v join gba.services s "
            "on s.tenant_id = v.tenant_id and s.id = v.service_id "
            "where v.status = 'published' and v.is_bookable order by s.name, v.name, v.id"
        )
    ).fetchall()
    variant_views: list[VariantView] = []
    for vid, sid, service_name in variants:
        variant = await load_variant(conn, vid)
        build_quote(variant)
        variant_views.append(
            VariantView(
                id=vid,
                service_id=sid,
                service_name=service_name,
                name=variant.name,
                price_cents=variant.price_cents,
                currency=variant.currency,
                duration_minutes=variant.booking_duration_minutes or 0,
                provides=sorted(variant.provides),
            )
        )
    ids = await (
        await conn.execute(
            "select id from gba.add_ons where status = 'published' and is_bookable "
            "order by name, id"
        )
    ).fetchall()
    add_ons = await load_add_ons(conn, [r[0] for r in ids])
    refs: dict[str, str] = dict(
        await (await conn.execute("select kind, asset_ref from gba.salon_branding_refs")).fetchall()
    )
    logo, accent = refs.get("logo", ""), refs.get("palette", "")
    safe_logo = logo if re.fullmatch(r"/assets/[A-Za-z0-9_/-]+\.(png|webp|svg|jpg)", logo) else None
    return BootstrapView(
        name=name,
        rules=rules,
        cancellation=cancellation,
        branding=BrandingView(
            logo_url=safe_logo, accent=accent if re.fullmatch(r"#[0-9a-fA-F]{6}", accent) else None
        ),
        locations=[
            LocationView(
                id=r[0],
                name=r[1],
                timezone=r[2],
                today=now.astimezone(ZoneInfo(r[2])).date(),
                last_day=now.astimezone(ZoneInfo(r[2])).date()
                + timedelta(days=rules.max_days_ahead),
            )
            for r in locations
        ],
        variants=variant_views,
        artists=await artists(conn),
        add_ons=[
            AddOnView(
                id=a.id,
                name=a.name,
                price_cents=a.price_cents,
                currency=a.currency,
                duration_minutes=a.duration_delta_minutes or 0,
                provides=sorted(a.provides),
                requires=sorted(a.requires),
                conflicts_with=sorted(a.conflicts_with),
            )
            for a in add_ons
        ],
    )


async def location_zone(conn: RuntimeConnection, location_id: UUID) -> str:
    row = await (
        await conn.execute("select timezone from gba.locations where id = %s", (location_id,))
    ).fetchone()
    if row is None:
        raise NotFoundError("Location not found")
    return str(row[0])


async def availability(
    conn: RuntimeConnection,
    query: AvailabilityQuery,
    *,
    preserved_quote: Quote | None = None,
    exclude_booking_id: UUID | None = None,
    require_live: bool = True,
) -> AvailabilityView:
    # Internal management callers have already authorized an active membership.
    # They may inspect setup before go-live, but cannot override scheduling rules.
    if require_live:
        await live_name(conn)
    rules, _ = await policies(conn)
    quote = preserved_quote or await quote_selection(conn, query)
    zone = await location_zone(conn, query.location_id)
    now = datetime.now(UTC)
    today = now.astimezone(ZoneInfo(zone)).date()
    if not today <= query.day <= today + timedelta(days=rules.max_days_ahead):
        raise DomainError("Date is outside the booking window")
    eligible = await artists(conn, query.location_id, query.variant_id)
    if query.resource_id is not None:
        eligible = [a for a in eligible if a.id == query.resource_id]
        if not eligible:
            raise NotFoundError("Artist not found for this service and location")
    location_hours = await (
        await conn.execute(
            "select opens_minute, closes_minute from gba.business_hours "
            "where location_id = %s and weekday = %s",
            (query.location_id, query.day.isoweekday()),
        )
    ).fetchall()
    windows = wall_windows(query.day, zone, [(r[0], r[1]) for r in location_hours])
    slots: list[Slot] = []
    if windows:
        for artist in eligible:
            hours = await (
                await conn.execute(
                    "select opens_minute, closes_minute from gba.resource_hours "
                    "where resource_id = %s and weekday = %s",
                    (artist.id, query.day.isoweekday()),
                )
            ).fetchall()
            busy = await (
                await conn.execute(
                    "select lower(a.during), upper(a.during) from gba.booking_allocations a "
                    "join gba.bookings b on b.tenant_id = a.tenant_id and b.id = a.booking_id "
                    "where a.resource_id = %s and (b.status = 'CONFIRMED' "
                    "or (b.status = 'HOLD' and b.hold_expires_at > clock_timestamp())) "
                    "and (%s::uuid is null or b.id <> %s) "
                    "and a.during && tstzrange(%s, %s, '[)') "
                    "union all select starts_at, ends_at from gba.resource_blocks "
                    "where resource_id = %s and starts_at < %s and ends_at > %s",
                    (
                        artist.id,
                        exclude_booking_id,
                        exclude_booking_id,
                        windows[0][0],
                        windows[-1][1],
                        artist.id,
                        windows[-1][1],
                        windows[0][0],
                    ),
                )
            ).fetchall()
            starts = available_starts(
                windows,
                wall_windows(query.day, zone, [(r[0], r[1]) for r in hours]),
                [(r[0], r[1]) for r in busy],
                minutes=quote.booking_duration_minutes,
                step=rules.slot_interval_minutes,
                after=now + timedelta(minutes=rules.advance_notice_minutes),
            )
            slots.extend(
                Slot(
                    resource_id=artist.id,
                    start_at=start,
                    end_at=start + timedelta(minutes=quote.booking_duration_minutes),
                )
                for start in starts
            )
    return AvailabilityView(
        timezone=zone,
        quote=QuoteView.from_quote(quote),
        slots=sorted(slots, key=lambda s: (s.start_at, str(s.resource_id))),
    )
