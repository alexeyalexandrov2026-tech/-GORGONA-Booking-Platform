# Booking engine and concurrency contract

Status: design only. No availability or reservation API exists.

Availability is derived from location timezone, staff schedules/skills, breaks, exceptions, service booking duration, setup/cleanup buffers, and exclusive resources. The UI shows the soonest valid slots before a calendar, but a slot display is never a guarantee. A server quote validates service composition and snapshots catalog version, price, tax/deposit policy, add-ons, duration and currency.

## Transactional invariant

Represent every active hold or appointment's exclusive staff/chair/room allocation as a `tstzrange` in one reservation table. Add `btree_gist` and a PostgreSQL `EXCLUDE USING gist` constraint on `(tenant_id WITH =, resource_id WITH =, during WITH &&)` for active states. Use half-open `[start,end)` intervals and absolute timestamps. The DB rejects overlapping active reservations, even across backend instances. Model multi-resource bookings as one transaction that inserts all allocations; a conflict aborts the whole attempt.

An expired hold must cease to block without relying on wall-clock predicates in a partial exclusion constraint. A bounded expiry worker transitions it to an inactive status, while create/confirm operations lock and re-evaluate any candidate held interval in the same transaction. Define a recovery path if the expiry worker lags. Do not make a time-variant `now()` predicate part of the constraint.

## Hold and confirmation

`create_hold` accepts an idempotency key scoped to tenant/actor/operation, validates current quote and schedules, inserts hold and resource allocations, and returns a bounded expiry. `confirm` locks the hold, verifies expiry, quote, payment requirement and actor, then changes the reservation and appointment state atomically. Retry with the same key returns the original result. A changed payload with the same key is a typed conflict.

Payment webhooks verify the signature, write to an inbox keyed by provider event ID, and advance state once. Transactional outbox rows record work such as confirmation notification in the same transaction as appointment state. Consumers deduplicate by event ID. Browser payment redirects never confirm bookings.

## State and test gate

Use explicit transition rules for HOLD, AWAITING_DEPOSIT, CONFIRMED, CHECKED_IN, IN_SERVICE, COMPLETED, CANCELLED, NO_SHOW and EXPIRED. Record actor, reason, request/trace ID and timestamp for each transition. Test DST gap/overlap and local midnight with IANA zones. Required concurrency gate: 100 simultaneous requests against one resource/slot yield exactly one success, 99 typed conflicts, one appointment, at most one payment intent and one confirmation notification; repeat across multiple API instances, timeout retries, duplicate webhooks and hold-expiry races. This gate is NOT VERIFIED.
