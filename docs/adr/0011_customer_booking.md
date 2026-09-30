# ADR-0011 — public customer booking

Status: implemented locally for M3; no production activation. Date: 2026-09-30.

## Decision

Keep the modular FastAPI/PostgreSQL architecture and M1/M2 invariants. Add a versioned `/v1/customer` surface and a Next.js static export at `/book/`, served from the same origin. Tenant identity comes exclusively from the existing approved Host mapping and live gate. No tenant ID enters the customer contract. Customer web assets contain no catalog, hours, staff or tenant-specific booking logic.

The server computes minute-precision availability from explicit location and artist weekly hours, artist/service eligibility, dated resource blocks and active occupancy. Each location retains its supplied IANA timezone. DST gaps have no invented instants; folds are distinct instants. Step, lead time and horizon are explicit version-1 policy values. Missing settings or schedules fail closed. Half-open intervals and elapsed UTC booking duration remain authoritative.

The browser submits a server-issued artist/instant pair, including for Any Available. Availability is informational. Hold creation revalidates it under M1's resource advisory lock and uses the existing booking repository, expiration and exclusion constraint in one transaction. Catalog rows are held with shared locks while selection is validated and persisted. The quote in the hold is the immutable price/duration shown for review.

Guest confirmation uses a browser-generated 256-bit capability in `Booking-Token`, never a URL. Only SHA-256 is stored. The capability record and hold are inserted atomically. Confirmation validates tenant/live state, policies, contact details, capability, expiry and transition, then stores contacts and confirms atomically. Confirmation returns no contact data. Hash-scoped idempotency preserves repeated requests and records no raw token or contact payload in its stored response. A new key cannot alter contacts on a confirmed booking.

All four 0006 tables enable and force RLS, with composite tenant FKs and bounded runtime grants. No historical migration or security-definer function is changed. Tenant reads retain M2's ordinary SELECT policies; row-locking the tenant would also invoke its platform-admin-only UPDATE restriction, so customer code does not broaden those policies.

## Policy contract

Existing `salon_policies` JSON columns remain backward compatible. M3 consumes explicit objects:

```json
{
  "booking_rules": {"version": 1, "slot_interval_minutes": 30, "advance_notice_minutes": 60, "max_days_ahead": 60},
  "deposit_policy": {"version": 1, "required": false},
  "cancellation_policy": {"version": 1, "summary": "FAKE example only; owner supplies actual terms"}
}
```

These numbers are illustrative test values, **not KA Nails facts or defaults**. Required deposits return `PAYMENT_REQUIRED`; M3 has no payment verification. Older arbitrary JSON accepted by M2 is not silently interpreted as a customer booking policy. M2 readiness/go-live is unchanged.

## Frontend boundary

Strict TypeScript and runtime Zod validation guard HTTP responses. React renders text without injected HTML. Tenant name, logo and accent come from bootstrap. Only bounded same-origin `/assets/` paths and six-digit hex accents are exposed. The accent is decorative; control/text contrast uses fixed accessible defaults. The official KA Nails logo is copied unchanged into the export and appears only when a tenant's logo reference selects it. No other tenant inherits KA Nails identity.

The frontend owns interaction state only. It retains capability/idempotency keys in memory for safe retries, clears stale selections after conflicts/expiry, cancels superseded availability reads, and uses a bounded 15-second HTTP timeout. It does not store customer details in browser storage. Reload loses the unfinished flow; any abandoned hold expires normally.

## Consequences and limits

- One local origin/process; no Next server, deployment or provider dependency is needed.
- Migration 0006 contains explicit schedules/eligibility/blocks/guest contacts. Administration of artist schedules is outside this customer milestone; owner-side setup must supply them before opening booking.
- All business settings remain absent for KA Nails, and its candidate stays not_live/unbookable.
- M1's production-start guard remains. Rate limiting, payment, notification and deployment gates are future work; local M3 acceptance does not establish production readiness.
- Next's current React/import/a11y ESLint plugins require ESLint 9. The attempted ESLint 10 upgrade produced invalid peers and a runtime lint failure; 9.39.5 is pinned to the compatible official config. npm marks it deprecated. Upgrade this development tool when the upstream plugin chain supports 10; no peer overrides are shipped.
- The separate camera OCI instance and public/unresolved GitHub remote remain untouched.
