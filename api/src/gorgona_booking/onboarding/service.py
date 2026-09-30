"""Idempotent salon onboarding and go-live (ADR-0010).

`apply_onboarding` runs owner-side (operator CLI): the runtime role cannot create
tenants. Every row gets a stable id derived from natural keys, so re-running the
same spec changes nothing. Facts absent from the spec are left untouched and stay
missing until someone supplies them; no business default is ever written.
"""

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from gorgona_booking.db.pool import RuntimeConnection
from gorgona_booking.db.provisioning import normalize_email, owner_tenant_transaction
from gorgona_booking.errors import DomainError
from gorgona_booking.identity.invitations import INVITATION_TTL, token_digest
from gorgona_booking.onboarding.readiness import Readiness, readiness, readiness_for_owner
from gorgona_booking.onboarding.spec import OnboardingSpec, clock_minutes

_NAMESPACE = uuid5(NAMESPACE_URL, "urn:gorgona-booking:onboarding")


class OnboardingConflictError(DomainError):
    code = "ONBOARDING_CONFLICT"


class NotReadyError(DomainError):
    code = "NOT_READY"


def stable_id(tenant_id: UUID | None, kind: str, key: str) -> UUID:
    """Deterministic id for onboarded rows: the same spec always maps to the same rows."""
    return uuid5(_NAMESPACE, f"{tenant_id or ''}:{kind}:{key}")


@dataclass(frozen=True, slots=True)
class OnboardingResult:
    tenant_id: UUID
    changed: bool
    invitation_created: bool


def apply_onboarding(
    conn: psycopg.Connection,
    spec: OnboardingSpec,
    *,
    actor: str,
    on_invitation: Callable[[str], None] | None = None,
) -> OnboardingResult:
    tenant_id = stable_id(None, "tenant", spec.slug)
    before = _fingerprint(conn, tenant_id)
    invitation_token: str | None = None
    with owner_tenant_transaction(conn, tenant_id):
        conn.execute("select pg_catalog.set_config('gba.actor', %s, true)", (actor,))
        _apply(conn, tenant_id, spec)
        invitation_token = _owner_invitation(conn, tenant_id, spec)
    if invitation_token is not None and on_invitation is not None:
        on_invitation(invitation_token)  # handed over once; only its hash is stored
    return OnboardingResult(
        tenant_id=tenant_id,
        changed=_fingerprint(conn, tenant_id) != before,
        invitation_created=invitation_token is not None,
    )


def _apply(conn: psycopg.Connection, tenant_id: UUID, spec: OnboardingSpec) -> None:
    conn.execute(
        "insert into gba.tenants (id, slug, display_name) values (%s, %s, %s) "
        "on conflict (id) do update set display_name = excluded.display_name "
        "where gba.tenants.display_name is distinct from excluded.display_name",
        (tenant_id, spec.slug, spec.display_name),
    )
    if spec.domains is not None:
        for host in spec.domains.value:
            conn.execute(
                "insert into gba.tenant_hosts (host, tenant_id) values (%s, %s) "
                "on conflict (host) do nothing",
                (host, tenant_id),
            )
            owner = conn.execute(
                "select tenant_id from gba.tenant_hosts where host = %s", (host,)
            ).fetchone()
            if owner is None or owner[0] != tenant_id:
                raise OnboardingConflictError("Host already belongs to another salon", host=host)

    location_id = stable_id(tenant_id, "location", spec.location_name)
    if spec.timezone is not None:
        conn.execute(
            "insert into gba.locations (tenant_id, id, name, timezone) values (%s, %s, %s, %s) "
            "on conflict (tenant_id, id) do update set timezone = excluded.timezone "
            "where gba.locations.timezone is distinct from excluded.timezone",
            (tenant_id, location_id, spec.location_name, spec.timezone.value),
        )
    has_location = (
        conn.execute("select 1 from gba.locations where id = %s", (location_id,)).fetchone()
        is not None
    )
    if spec.business_hours is not None:
        if not has_location:
            raise OnboardingConflictError("Business hours need a location with a timezone")
        _replace_hours(
            conn,
            tenant_id,
            location_id,
            {
                (h.weekday, clock_minutes(h.opens), clock_minutes(h.closes))
                for h in spec.business_hours.value
            },
        )
    if spec.staff is not None:
        if not has_location:
            raise OnboardingConflictError("Staff need a location with a timezone")
        for person in spec.staff.value:
            conn.execute(
                "insert into gba.resources (tenant_id, id, location_id, kind, display_name) "
                "values (%s, %s, %s, 'artist', %s) on conflict (tenant_id, id) do nothing",
                (
                    tenant_id,
                    stable_id(tenant_id, "staff", person.display_name),
                    location_id,
                    person.display_name,
                ),
            )
    if spec.catalog is not None:
        _apply_catalog(conn, tenant_id, spec)
    policies = {
        "cancellation_policy": spec.cancellation_policy,
        "deposit_policy": spec.deposit_policy,
        "booking_rules": spec.booking_rules,
    }
    supplied = {k: Jsonb(v.value) for k, v in policies.items() if v is not None}
    if supplied:
        conn.execute(
            "insert into gba.salon_policies (tenant_id) values (%s) on conflict do nothing",
            (tenant_id,),
        )
        for column, value in supplied.items():
            conn.execute(
                sql.SQL(
                    "update gba.salon_policies set {col} = %s, updated_at = now() "
                    "where tenant_id = %s and {col} is distinct from %s"
                ).format(col=sql.Identifier(column)),
                (value, tenant_id, value),
            )
    statuses = {
        "timezone": spec.timezone,
        "domain": spec.domains,
        "business_hours": spec.business_hours,
        "staff": spec.staff,
        "catalog": spec.catalog,
        "service_durations": spec.service_durations,
        **policies,
    }
    for fact_key, fact in statuses.items():
        if fact is not None:
            conn.execute(
                "insert into gba.salon_fact_confirmations (tenant_id, fact_key, status, "
                "source_note, recorded_by) values (%s, %s, %s, %s, "
                "pg_catalog.current_setting('gba.actor')) "
                "on conflict (tenant_id, fact_key) do update set status = excluded.status, "
                "source_note = excluded.source_note, recorded_by = excluded.recorded_by, "
                "recorded_at = now() where (gba.salon_fact_confirmations.status, "
                "gba.salon_fact_confirmations.source_note) is distinct from "
                "(excluded.status, excluded.source_note)",
                (tenant_id, fact_key, fact.status, fact.source),
            )
    for ref in spec.branding:
        conn.execute(
            "insert into gba.salon_branding_refs (tenant_id, kind, asset_ref, sha256) "
            "values (%s, %s, %s, %s) on conflict (tenant_id, kind) do update "
            "set asset_ref = excluded.asset_ref, sha256 = excluded.sha256, updated_at = now() "
            "where (gba.salon_branding_refs.asset_ref, gba.salon_branding_refs.sha256) "
            "is distinct from (excluded.asset_ref, excluded.sha256)",
            (tenant_id, ref.kind, ref.asset_ref, ref.sha256),
        )


def _replace_hours(
    conn: psycopg.Connection,
    tenant_id: UUID,
    location_id: UUID,
    wanted: set[tuple[int, int, int]],
) -> None:
    rows = conn.execute(
        "select id, weekday, opens_minute, closes_minute from gba.business_hours "
        "where location_id = %s",
        (location_id,),
    ).fetchall()
    current = {(r[1], r[2], r[3]): r[0] for r in rows}
    for key, row_id in current.items():
        if key not in wanted:
            conn.execute("delete from gba.business_hours where id = %s", (row_id,))
    for weekday, opens, closes in sorted(wanted - current.keys()):
        conn.execute(
            "insert into gba.business_hours (tenant_id, id, location_id, weekday, opens_minute, "
            "closes_minute) values (%s, %s, %s, %s, %s, %s)",
            (
                tenant_id,
                stable_id(tenant_id, "hours", f"{location_id}:{weekday}:{opens}:{closes}"),
                location_id,
                weekday,
                opens,
                closes,
            ),
        )


def _apply_catalog(conn: psycopg.Connection, tenant_id: UUID, spec: OnboardingSpec) -> None:
    assert spec.catalog is not None  # noqa: S101 - checked by the caller
    # Publishing needs the catalog and the durations confirmed; the database still
    # refuses a bookable service without a duration.
    confirmed = spec.catalog.status == "confirmed" and (
        spec.service_durations is not None and spec.service_durations.status == "confirmed"
    )
    for v in spec.catalog.value:
        service_id = stable_id(tenant_id, "service", v.service_code)
        conn.execute(
            "insert into gba.services (tenant_id, id, code, name) values (%s, %s, %s, %s) "
            "on conflict (tenant_id, id) do update set name = excluded.name "
            "where gba.services.name is distinct from excluded.name",
            (tenant_id, service_id, v.service_code, v.service_name),
        )
        bookable = confirmed and v.booking_duration_minutes is not None
        low, high = v.display_duration_minutes or (None, None)
        conn.execute(
            "insert into gba.service_variants (tenant_id, id, service_id, code, name, status, "
            "price_cents, currency, booking_duration_minutes, display_duration_min_minutes, "
            "display_duration_max_minutes, is_bookable) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "on conflict (tenant_id, id) do update set name = excluded.name, "
            "status = excluded.status, price_cents = excluded.price_cents, "
            "currency = excluded.currency, "
            "booking_duration_minutes = excluded.booking_duration_minutes, "
            "display_duration_min_minutes = excluded.display_duration_min_minutes, "
            "display_duration_max_minutes = excluded.display_duration_max_minutes, "
            "is_bookable = excluded.is_bookable "
            "where (gba.service_variants.name, gba.service_variants.status, "
            "gba.service_variants.price_cents, gba.service_variants.currency, "
            "gba.service_variants.booking_duration_minutes, "
            "gba.service_variants.display_duration_min_minutes, "
            "gba.service_variants.display_duration_max_minutes, "
            "gba.service_variants.is_bookable) is distinct from (excluded.name, "
            "excluded.status, excluded.price_cents, excluded.currency, "
            "excluded.booking_duration_minutes, excluded.display_duration_min_minutes, "
            "excluded.display_duration_max_minutes, excluded.is_bookable)",
            (
                tenant_id,
                stable_id(tenant_id, "variant", v.code),
                service_id,
                v.code,
                v.name,
                "published" if bookable else "draft",
                v.price_cents,
                v.currency,
                v.booking_duration_minutes,
                low,
                high,
                bookable,
            ),
        )


def _owner_invitation(
    conn: psycopg.Connection, tenant_id: UUID, spec: OnboardingSpec
) -> str | None:
    """Invite the first owner once. Re-runs find the pending invitation or the owner."""
    if spec.owner_email is None:
        return None
    email = normalize_email(spec.owner_email.value)
    existing = conn.execute(
        "select 1 from gba.memberships where role = 'owner' and status = 'active' "
        "union all select 1 from gba.invitations where email_normalized = %s "
        "and role = 'owner' and status = 'pending'",
        (email,),
    ).fetchone()
    if existing is not None:
        return None
    token = secrets.token_urlsafe(32)
    conn.execute(
        "insert into gba.invitations (tenant_id, email_normalized, role, token_sha256, expires_at) "
        "values (%s, %s, 'owner', %s, now() + %s)",
        (tenant_id, email, token_digest(token), INVITATION_TTL),
    )
    return token


_FINGERPRINT_QUERIES = (
    "select slug, display_name, status, booking_state from gba.tenants",
    "select host from gba.tenant_hosts where tenant_id = gba.current_tenant_id()",
    "select id, name, timezone from gba.locations",
    "select location_id, weekday, opens_minute, closes_minute from gba.business_hours",
    "select id, location_id, kind, display_name, is_active from gba.resources",
    "select id, code, name from gba.services",
    "select id, code, name, status, price_cents, currency, booking_duration_minutes, "
    "display_duration_min_minutes, display_duration_max_minutes, is_bookable "
    "from gba.service_variants",
    "select cancellation_policy, deposit_policy, booking_rules from gba.salon_policies",
    "select fact_key, status, source_note from gba.salon_fact_confirmations",
    "select kind, asset_ref, sha256 from gba.salon_branding_refs",
    "select email_normalized, role, status from gba.invitations",
    "select user_id, role, status from gba.memberships",
)


def _fingerprint(conn: psycopg.Connection, tenant_id: UUID) -> str:
    digest = hashlib.sha256()
    with owner_tenant_transaction(conn, tenant_id):
        for query in _FINGERPRINT_QUERIES:
            rows = conn.execute(query.encode()).fetchall()
            digest.update(repr(sorted(map(repr, rows))).encode())
    return digest.hexdigest()


def _not_ready(result: Readiness) -> NotReadyError:
    return NotReadyError(
        "The salon is not ready for public booking",
        missing=result.with_status("missing"),
        unconfirmed=result.with_status("unconfirmed"),
    )


async def go_live(conn: RuntimeConnection, tenant_id: UUID) -> Readiness:
    """Runtime path (platform admin, already authorized for this tenant)."""
    result = await readiness(conn)
    if not result.ready:
        raise _not_ready(result)
    await conn.execute("update gba.tenants set booking_state = 'live' where id = %s", (tenant_id,))
    return result


def go_live_owner(conn: psycopg.Connection, tenant_id: UUID, *, actor: str) -> Readiness:
    """Operator path (owner credential)."""
    result = readiness_for_owner(conn, tenant_id)
    if not result.ready:
        raise _not_ready(result)
    with owner_tenant_transaction(conn, tenant_id):
        conn.execute("select pg_catalog.set_config('gba.actor', %s, true)", (actor,))
        conn.execute("update gba.tenants set booking_state = 'live' where id = %s", (tenant_id,))
    return result
