# ADR-0009: Deriving tenant context from identity and membership

- Status: Accepted (2026-09-30, M2)

## Decision

Staff routes name the salon in the path (`/v1/salons/{salon_id}/…`). The path is a *request*, not a grant. For every protected request, in **the same transaction as the operation**:

1. The bearer token is verified (ADR-0007) and mapped to a user through `user_identities`. The user must be `active`.
2. The candidate salon becomes the transaction's tenant context, together with `gba.user_id` for the caller.
3. The caller's membership is read with `FOR SHARE`. It must be `active`, and the tenant must be `active`.
   - The row lock makes a concurrent suspend or revoke wait until the request finishes. The next request sees the new status and is denied.
4. The permission required by the route must be in the role's permission set.
5. Otherwise, a caller with an active `platform_admin` grant may enter with the **support permission set** (read-only). Each entry writes a `platform.tenant_access` audit event.
6. Anything else is a uniform `403 TENANT_ACCESS_DENIED`, whether or not the salon exists. On failure the transaction rolls back, so the candidate context never outlives the check.

Inside an authorized salon, a guessed ID of another salon's row is invisible through RLS and returns 404. Request bodies are `extra="forbid"`, so a `tenant_id` field is rejected, never consulted.

The public booking route keeps its M1 host-based resolution. It additionally requires the tenant to be `active` and `booking_state = 'live'` (ADR-0010).

## Consequences

- RLS stays the last line of defence. The runtime role remains non-superuser, non-`BYPASSRLS`, not an owner, and is still checked at startup and on readiness.
- Platform-wide listing is limited to what the platform-admin RLS policies expose: tenant rows only.
