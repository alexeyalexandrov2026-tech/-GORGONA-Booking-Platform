# ADR-0003: Booking occupancy as half-open ranges under a GiST exclusion constraint

- Status: Accepted (2026-09-30)
- Scope: holds, bookings, allocations

## Context

Two customers, a customer and the AI concierge, or two API instances can try to take the same artist at the same time. An application-level "check, then insert" is racy unless everything runs serializable with retries, and even then every code path has to do it right.

## Decision

- `gba.bookings` holds the lifecycle (`HOLD`, `CONFIRMED`, `CANCELLED`, `EXPIRED`) and the quote snapshot.
- `gba.booking_allocations` holds occupancy: one row per reserved resource. `during tstzrange` is always half-open `[start, end)`, bounded and non-empty, enforced by a CHECK constraint.
- `EXCLUDE USING gist (tenant_id WITH =, resource_id WITH =, during WITH &&) WHERE (booking_status IN ('HOLD','CONFIRMED'))`, with `btree_gist` providing the scalar `=` operator classes. PostgreSQL rejects the second of two overlapping capacity-reserving allocations with SQLSTATE `23P01`, across connections and API instances.
- Each allocation carries `booking_status`, kept equal to its booking's status by the composite foreign key `(tenant_id, booking_id, booking_status) → bookings (tenant_id, id, status) ON UPDATE CASCADE`. Changing a booking's status moves its allocations into or out of the exclusion set atomically, with no second code path to forget.
- A trigger enforces the allowed transitions: `HOLD → CONFIRMED | CANCELLED | EXPIRED` and `CONFIRMED → CANCELLED`. Terminal states are immutable. Every transition writes a `booking_events` row.
- **Hold expiry never uses `now()` in a constraint or index predicate.** A hold stays in the blocking set until a transaction moves it to `EXPIRED`:
  - `create_hold` and `create_booking` first expire stale holds that overlap the requested range on the same resource, in their own transaction;
  - `confirm` re-checks `hold_expires_at` under a row lock, and expires the hold instead of confirming it;
  - a bounded per-tenant sweeper (`expire_due_holds`) cleans up the rest.
  So a lagging sweeper never causes a false conflict or a late confirmation.
- **Contention management.** Two transactions inserting overlapping ranges at the same instant can each wait on the other's uncommitted index entry, and PostgreSQL breaks that cycle with `40P01 deadlock_detected` rather than `23P01`. So the service takes `pg_advisory_xact_lock` on a per-tenant, per-resource key before changing occupancy (new reservations and `HOLD → CONFIRMED`, in resource-id order). This only orders the contenders. The exclusion constraint still decides. The raw-SQL race test runs without the lock, to prove that the constraint alone never admits two reservations.
- The repository maps `23P01` on `booking_allocations_no_overlap` to the domain error `SlotConflict`, which the API returns as HTTP 409 `SLOT_CONFLICT`.

## Consequences

- Correctness does not depend on the API layer, the AI layer or the number of instances.
- Multi-resource bookings (artist plus chair) insert several allocations in one transaction; any conflict aborts the whole attempt.
- Rescheduling, buffers and schedules come later and must keep this invariant.
