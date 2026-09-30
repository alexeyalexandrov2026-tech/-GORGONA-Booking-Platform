# ADR-0005: Nothing is bookable without a known booking duration

- Status: Accepted (2026-09-30)

## Context

The brief gives public duration *ranges* (for example Hammam Luxury + Gel, 120–145 minutes) but no deterministic booking duration, and says explicitly that the base Hammam duration must not be invented. A range cannot reserve an interval.

## Decision

- Service variants carry `display_duration_min_minutes` / `display_duration_max_minutes` for marketing, separate from `booking_duration_minutes` for scheduling. Only the latter computes an allocation.
- Database CHECK constraints: for a variant, `is_bookable` implies `booking_duration_minutes` is set (1–720) and `status = 'published'`. For an add-on, `is_bookable` implies `duration_delta_minutes` is set (0–240) and `status = 'published'`.
- The pure quote engine enforces the same rules, plus the cross-row rules the database cannot express cheaply:
  - `REQUIRES` components must be provided by the selection;
  - `CONFLICTS_WITH` components must not be;
  - no component may be provided twice, so included heel care cannot be charged again.
  Prices are integer cents with an explicit currency, and every item must use the same currency.
- The KA Nails catalog from the brief lives in `api/fixtures/ka_nails_catalog.candidate.json`, marked `owner_unconfirmed`, with every booking duration `null` and nothing bookable. It is never seeded automatically. Tests use separate `FAKE_` fixtures.

## Consequences

- Owner-confirmed durations are the only way to make a KA Nails service bookable.
- Full catalog versioning (immutable published versions) is future work. M1 records a per-row `revision` in each quote snapshot.
