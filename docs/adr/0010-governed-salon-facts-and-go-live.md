# ADR-0010: Governed salon facts, onboarding and go-live

- Status: Accepted (2026-09-30, M2)

## Decision

- **Onboarding is split by privilege.**
  - An operator runs `gba-db onboard <spec.json>` with the owner/provisioning credential. It creates the salon (a stable ID derived from its slug), host mappings, location, and optionally hours, staff, catalog, policies, fact statuses, branding references and the first owner invitation.
  - The run is idempotent: a second run with the same spec changes nothing.
  - Salon admins then maintain configuration through the authenticated API. The runtime role still cannot create tenants (an M1 invariant).
- **Every required booking fact has a governed status**: `confirmed`, `unconfirmed` or `missing`. Missing means no data. A spec field with no value is left missing. No default is invented.
  - Required facts: active owner, timezone, business hours, staff, catalog, service durations, cancellation policy, deposit policy, booking rules, domain.
- **Readiness** is computed from the database, never stored. It lists each fact with its status and detail (for example, which variants lack a booking duration). A salon is ready only when every required fact is `confirmed`.
- **Go-live.** `tenants.booking_state` defaults to `not_live`. Only the go-live operation (platform admin API or operator CLI) sets `live`, and only while readiness passes. The public hold route refuses tenants that are not `live`.
- M1 bookability rules still apply per service: no duration means not bookable, enforced by database CHECKs.

## Consequences

- KA Nails cannot go live until the owner confirms the facts listed in `docs/plan/M2_REPORT.md`.
- M1 integration tests create FAKE salons, and the M1 seed helper marks them `live` owner-side (owner decision, 2026-09-30). No M1 assertion changed.
