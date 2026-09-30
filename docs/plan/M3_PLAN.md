# M3 — customer booking experience

Starting branch `m2-identity`, verified HEAD `dd272e72d947f4e8a9f7ae60fa93a2e8c6bd363b`, clean tree. M1/M2 are accepted baseline. No push or deployment.

## Inspection

There is no customer frontend. FastAPI already exposes public `POST /v1/holds` through Host resolution and the active/live gate, plus protected staff/catalog/onboarding routes. M1 `BookingService` already creates holds, confirms them, expires them under lock, and enforces occupancy through shared repository functions, advisory locks, GiST exclusion and idempotency. It does not compute availability or validate work schedules. M2 supplies location business hours, policies, branding references and readiness, but no artist work hours, service eligibility or guest booking capability/contact record.

Keep migrations 0001–0005 and baseline assertions unchanged. Add 0006 for explicit resource/service eligibility, resource weekly hours and dated blocks, and booking-scoped guest capability/contact records. All new tables enable/force RLS, use tenant composite FKs and safe runtime grants. No security-definer function. Missing artist schedules mean no availability; no default work hours or service assignment is invented.

## Slices

1. **Availability domain and schema.** RED pure DST/interval tests and real-PG schema/schedule/isolation tests. Derive intervals from confirmed location timezone, location hours, explicit artist hours/eligibility, dated blocks, active holds and confirmed bookings. Whole-minute slots use tenant-configured step, lead time and horizon. Prices/durations use the existing quote engine.
2. **Public bootstrap/quote/availability.** Typed customer endpoints all resolve tenant from Host using the existing gate. Return published, bookable catalog, eligible artists, safe branding and versioned customer booking/deposit settings. Missing/malformed settings fail closed; deposits requiring payment remain unavailable in M3. No M2 readiness rewrite.
3. **Guest hold/review/confirmation.** Browser-generated 256-bit capability is sent in a header and only its hash is persisted. Use the existing M1 repository occupancy/lifecycle primitives inside a single customer transaction, including schedule revalidation under the resource lock. Persist capability with hold atomically. Confirm validates details/capability, rechecks expiry/live state and records contacts plus transition atomically. Retry and conflicts use existing idempotency/error conventions. Never expose contact data in public responses.
4. **Customer web.** Next.js App Router + strict TypeScript static export, served with FastAPI at the same origin. No second backend or tenant ID input. Reusable tenant branding; service/add-ons → artist/any → date/server slots → customer details → review → confirm. Accessible loading/error/not-live/expired/race/retry states. No payment/AI/admin scope.
5. **Verification/report.** Real PostgreSQL API and cross-tenant/race/expiry tests; real browser booking E2E on a labelled live FAKE tenant, mobile and keyboard checks. Full baseline + M3 suite, Python/static web checks, reproducible build, migration history and secret review. Update DEVELOPMENT/README and M3_REPORT, local commits, clean tree.

## Business facts and boundaries

KA Nails candidate data remains draft/not_bookable with unknown durations and `not_live`. Owner facts are not needed to develop and exercise the platform with explicit FAKE fixtures. Production timezone/hours/staff/durations/policies/domain remain missing and are reported as such. Customer booking configuration is explicit, versioned data, not React defaults. Required deposits block this milestone's confirm flow rather than pretending payment occurred. The camera OCI instance is outside scope.

## Verification policy

For each meaningful slice: tests → captured RED → smallest implementation → focused tests → static checks → full relevant regression before local commit. Record exact observed evidence. Stop for a genuine business decision, destructive action, or security-boundary redesign. No GitHub push until visibility/ownership approval.
