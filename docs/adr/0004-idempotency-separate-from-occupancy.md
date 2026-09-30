# ADR-0004: Request idempotency is separate from double-booking protection

- Status: Accepted (2026-09-30)

## Context

Mobile networks and AI tool calls retry, and a retry must not create a second hold. But clients choose their own idempotency keys, so keys cannot protect a slot: two different clients send two different keys.

## Decision

- `gba.idempotency_keys` has primary key `(tenant_id, actor_key, operation, idempotency_key)`. It stores a SHA-256 hash of the canonical request and the stored response.
- The key is claimed with `INSERT … ON CONFLICT DO NOTHING` in **the same transaction** as the booking. A concurrent request with the same key waits on the unique index until the first commits, then reads the stored result. If the first transaction rolls back, the key is free again, so there is no stuck "in progress" state.
- Same key and same request: return the stored result (HTTP 201 replay, or the stored 409 `SLOT_CONFLICT`). Same key with a different request: `IDEMPOTENCY_KEY_REUSED` (422).
- The booking insert runs in a savepoint, so a slot conflict is recorded against the key and replays consistently. Validation errors are not recorded, and the key stays unused.
- Contention between different keys for one slot is resolved only by the exclusion constraint (ADR-0003).

## Consequences

- M1 has no authentication, so `actor_key` is `anonymous`. It becomes the authenticated principal or guest capability once identity exists.
- Keys carry `expires_at` (24 hours); the cleanup job is future work.
