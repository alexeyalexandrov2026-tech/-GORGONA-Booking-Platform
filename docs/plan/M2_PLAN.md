# M2 plan — identity, tenant membership, authorization, onboarding, KA Nails readiness (approved 2026-09-30)

## Context

M1 is closed: 127/127 tests pass on real PostgreSQL 18.6, and the booking engine is protected by forced RLS and a GiST exclusion constraint. The staff/admin API has no identity yet. `POST /v1/holds` is anonymous, and tenant context comes only from `Host` → `gba.tenant_hosts`. M2 adds the smallest production-grade layer that lets real staff and admins use the engine safely:

1. authenticated identity from an external OIDC provider;
2. server-side tenant membership as the only source of tenant authority;
3. permission checks at the API/service boundary, with RLS kept as defence in depth;
4. an idempotent onboarding path;
5. a readiness checklist that refuses to go live on missing KA Nails facts.

Nothing in M1 is redesigned or weakened.

### M1 constraints the design must respect (verified read-only)

- The runtime role cannot INSERT into `gba.tenants` (asserted by M1 `test_runtime_cannot_create_tenants_or_write_host_routes`). So **tenant creation and host mapping stay owner-side** (ADR-0002 provisioning model).
- Migrations may not contain `SECURITY DEFINER`, `BYPASSRLS` or `disable/no force row level security`, and every new table must `enable` + `force` RLS (M1 unit guards). So cross-tenant lookups use **extra RLS policies keyed on a transaction-local user context**, never definer functions.
- Applied migrations are checksummed, so M2 adds `0004`+ migrations and never edits `0001`–`0003`.
- `gba.memberships` (0001) is an unused placeholder (`subject`, 4 roles). No app code reads it. One M1 test inserts into it without a user and expects `ForeignKeyViolation`.
- Config is read only through the explicit `GBA_*` mapping in `config.py` (`Settings.from_env`). Errors use the envelope in `api/errors.py` (`DOMAIN_ERROR_STATUS`, `register_domain_error`). DB access goes through `db/pool.py` (`tenant_transaction`, `set_tenant_context`, `assert_safe_runtime_role`).

### Owner decisions (2026-09-30)

1. **Evolve `gba.memberships`** in `0004` (add `user_id` NOT NULL FK, `status`, …). The only M1 test file change is in `test_tenant_isolation.py::test_composite_foreign_key_blocks_cross_salon_reference`: its INSERT supplies a FAKE user (seeded owner-side). Its assertion is unchanged: a cross-salon reference must still raise `ForeignKeyViolation`.
2. **`booking_state` defaults to `not_live`.** The public `/v1/holds` requires `live`. The M1 test seed helper (`tests/integration/seed.py::seed_salon`) marks FAKE salons `live` through the owner path. No M1 assertion changes.
3. **Onboarding runs as an owner-side CLI plus the authenticated salon-admin API.** No new privileged role goes into the API process.

These are the only permitted edits to M1 test code. Any other M1 test change is out of bounds, and the report must list these two diffs verbatim.

## Architecture decisions (new ADR-0007 … 0010)

- **ADR-0007 Authentication boundary.** A `TokenVerifier` protocol (`auth/verifier.py`) returns a `VerifiedToken(issuer, subject, email, email_verified, expires_at)`.
  - One implementation: `OidcJwtVerifier` using **PyJWT[crypto]** with a JWKS key source. It enforces the issuer, audience, `exp`/`nbf`/`iat`, an explicit algorithm allowlist (RS256/ES256; never `none` or HS* with public keys) and a required `kid`.
  - The JWKS fetch is cached and run off the event loop.
  - The provider is selected only by config (`GBA_AUTH_ISSUER`, `GBA_AUTH_AUDIENCE`, `GBA_AUTH_JWKS_URL`, `GBA_AUTH_ALGORITHMS`, `GBA_AUTH_LEEWAY_SECONDS`).
  - No passwords, sessions or login UI are built here. Tokens are never stored or logged, and 401 bodies never echo token content.
  - If auth is not configured, protected routes return 503 `AUTH_NOT_CONFIGURED`. The existing public hold route is unchanged.
- **ADR-0008 Identity and authority model.** Identity (`users`, `user_identities`) is platform-level. Authority is **only** `memberships` (tenant-owned) plus `platform_roles`, stored server-side. JWT claims never carry tenant rights: the token proves *who*, the database decides *what*.
- **ADR-0009 Tenant-context derivation.** Staff routes are `/v1/salons/{salon_id}/…`. The dependency runs **inside the same transaction as the operation**:
  1. set `gba.user_id`;
  2. `select … from gba.memberships where tenant_id = $salon and user_id = me and status = 'active' for share`, plus tenant `status = 'active'`;
  3. only then `set_tenant_context(salon_id)`.

  `FOR SHARE` makes a concurrent revoke wait, so a revoked member cannot finish an in-flight request after the revoke commits, and the next request is denied.
  - Platform admins may enter any tenant through an explicit `platform.tenant_support` permission, and each entry writes an audit event.
  - Body/header tenant fields are rejected (`extra="forbid"`) and never consulted.
  - A non-member gets a uniform 403 `TENANT_ACCESS_DENIED`, whether or not the salon exists. Guessed resource IDs inside an allowed tenant resolve through RLS and return 404.
- **ADR-0010 Governed salon facts and go-live.** Each required booking fact is reported as `confirmed`, `unconfirmed` or `missing`. The public booking surface requires tenant `booking_state = 'live'`, and only the go-live service can set it, and only after readiness passes.

## Schema (migrations)

`0004_identity.sql`
- `gba.current_user_id()`: like `current_tenant_id()`, reads transaction-local `gba.user_id`.
- `gba.users` (id uuidv7, `display_name`, `email_normalized` (lower-case, CHECK), `status` active/disabled, timestamps). RLS forced. Policies:
  - self: `id = current_user_id()`;
  - co-member visibility: `exists membership in current_tenant_id()`.
- `gba.user_identities` (id, user_id FK, issuer, subject, created_at). **`unique (issuer, subject)`** prevents duplicate mapping. RLS forced; SELECT is limited to the row matching transaction-local `gba.auth_issuer`/`gba.auth_subject` (set from the verified token) or to the current user.
- `gba.platform_roles` (user_id, role `platform_admin`, granted_by, granted_at, revoked_at). A partial unique index allows one active grant per role. RLS: self-read only. Grants are made owner-side via the CLI.
- `gba.memberships` evolution (owner decision 1): add `user_id` FK → users, `status` (`active`/`suspended`/`revoked`), `updated_at`, `created_by`.
  - Role set mapped to scopes: `owner`/`manager` = salon admin; `front_desk`/`artist` = staff.
  - Partial unique index: **one non-revoked membership per (tenant, user)**.
  - Extra SELECT policy: `user_id = current_user_id()`.
- `gba.invitations` (tenant-owned): email_normalized, role, **token_sha256** (plaintext never stored), status (pending/accepted/revoked/expired), expires_at, created_by, accepted_by_user_id, accepted_at. Unique pending invite per (tenant, email).
- `gba.audit_events` (append-only; runtime has INSERT/SELECT only): tenant_id nullable (NULL = platform event), actor_user_id, action, target_type, target_id, details jsonb (no secrets), request_id, occurred_at. RLS: `tenant_id is not distinct from current_tenant_id()`.
  - **Triggers** on memberships, invitations, user_identities and platform_roles write events from `gba.actor`/`gba.request_id`, reusing the M1 `booking_events` pattern, so security-sensitive changes cannot skip the audit.
- Extra `tenants` policies: SELECT for active members and platform admins; UPDATE of `status`/`booking_state` for platform admins only (column-level grant).

`0005_salon_setup.sql`
- `tenants.booking_state` (`not_live`/`live`, default `not_live`) and `onboarding_state`.
- `gba.business_hours` (tenant, location FK, weekday 0–6, opens/closes `time`, CHECK opens < closes, no overlapping intervals per weekday via an exclusion constraint).
- `gba.salon_policies` (tenant PK): cancellation, deposit and booking-rules jsonb, each nullable (NULL = missing), with CHECK that each value is a JSON object when present.
- `gba.salon_fact_confirmations` (tenant, fact_key, status `confirmed`/`unconfirmed`, confirmed_by_user_id, confirmed_at, source_note). The fact_key set is fixed by CHECK.
- `gba.salon_branding_refs` (tenant, kind, asset_ref, sha256). References only, no binaries.

## Code

- `auth/verifier.py`, `auth/principal.py` (`Principal(user_id, platform_roles)`), `auth/permissions.py`.
  - Permissions: static, versioned role → permission map (`booking.read`, `booking.write`, `catalog.read`, `catalog.manage`, `staff.manage`, `members.manage`, `settings.manage`, `readiness.read`, `platform.tenant_support`, `platform.tenant_status`).
- `identity/service.py`:
  - `resolve_principal(verified_token)`: lookup by (issuer, subject); unknown identity → 401 `IDENTITY_NOT_LINKED`; no automatic account creation.
  - `accept_invitation(token, invitation_token, salon_id)`:
    - requires `email_verified` and an email match;
    - locks the invitation `FOR UPDATE`;
    - creates the user and identity with `ON CONFLICT` handling;
    - creates the membership;
    - **idempotent**: the same user re-accepting returns the same membership, and a different user gets 409.
- `tenancy/authorization.py`: `authorized_tenant_transaction(pool, principal, salon_id, permission)`, the ADR-0009 flow; also `platform_transaction(...)`.
- `onboarding/spec.py` (Pydantic, `extra="forbid"`): every business field is optional, with explicit `status: confirmed | unconfirmed`. **No defaults are invented.**
- `onboarding/service.py`: idempotent upserts keyed by natural keys (slug, host, codes, weekday).
  - Tenant + hosts are created owner-side; salon configuration is written inside tenant context.
- `onboarding/readiness.py`: pure evaluator over a DB snapshot, returning `[{fact, status, detail}]` and `ready: bool`.
  - Required facts: owner membership active; location timezone; business hours; ≥1 active artist; ≥1 published bookable variant, with no published variant lacking a duration; cancellation policy; deposit config; booking rules; domain/host; each confirmed.
- `onboarding/golive.py`: `go_live` flips `booking_state` only if readiness passes; otherwise 409 `NOT_READY` with the list.
- API (`api/…`):
  - `GET /v1/me`;
  - salon staff/admin routes: services, staff, bookings by id, members, invitations, suspend/revoke member, readiness, facts confirm, settings (hours/policies);
  - `POST /v1/invitations/accept`;
  - platform routes: tenant support access, suspend/reactivate, readiness;
  - `/v1/holds` gains the `booking_state = 'live'` gate in `tenancy/resolver.py`.
- CLI (`db/cli.py`): `gba-db onboard <spec.json>` (owner credential, idempotent), `gba-db readiness <slug>`, `gba-db grant-platform-admin`.
  - KA Nails spec file: `fixtures/ka_nails_onboarding.candidate.json`, with every unknown fact left missing or unconfirmed.
- Config: new `GBA_AUTH_*` fields in `config.py`. `assert_environment_allowed` stays unchanged: still no rate limiting or payments, so staging/production stay refused.

## Test plan (RED first, per slice; RED output captured for the report)

New tests, all against real PostgreSQL 18.6 where the DB is involved:
- **Unit:** token verification (valid; expired; nbf; wrong iss/aud; `alg=none`; HS256 with public key; unknown/missing kid; tampered signature); permission map; onboarding spec rejects invented defaults; readiness evaluator (missing / unconfirmed / confirmed).
- **Integration:** tests with locally generated RSA keys and a static JWKS source (no network, no debug endpoints), numbered to the requirement list:
  1. no/invalid token → 401;
  2. staff reads own-salon services/bookings;
  3. non-member → 403;
  4. guessed booking id from another salon → 404;
  5. body/header `tenant_id` ignored or rejected; effective tenant unchanged;
  6. salon admin creates a service and staff;
  7. salon admin on another salon → 403;
  8. platform admin support access + suspend, with audit;
  9. revoked membership → 403 on the next request, and a concurrent revoke waits;
  10. suspended tenant → member 403, public host 404;
  11. invitation accept, re-accept idempotent, a different user → 409, concurrent accepts → 1 membership;
  12. duplicate (issuer, subject) → unique violation handled;
  13. audit events for membership/role/invite/platform actions;
  14. runtime role still non-superuser/non-BYPASSRLS, and startup refusal with the owner DSN unchanged;
  15. new tables have forced RLS, plus a cross-tenant RLS probe on invitations/audit/hours;
  16. full M1 suite.
- Go-live: blocked with the missing list; allowed once readiness is complete; public hold refused before go-live.

## Slices / commits

1. ADRs 0007–0010 + M2 plan doc.
2. `0004_identity.sql` + identity/membership/audit tests (RED → GREEN).
3. Auth verifier + principal + authorization dependency + protected routes (RED → GREEN).
4. Invitations + member lifecycle (RED → GREEN).
5. `0005_salon_setup.sql` + onboarding service/CLI + readiness + go-live gate (RED → GREEN).
6. KA Nails candidate onboarding spec + readiness report + `docs/plan/M2_REPORT.md`.

## Verification (final M2 gate)

From `api/`, against the running local PG 18.6 (`--env-file %USERPROFILE%\.gba\secrets\local-pg18.env`, `GBA_REQUIRE_POSTGRES=1`):
- `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy`;
- focused `pytest tests/unit/test_auth* tests/integration/test_authz*`;
- full `uv run pytest -q` (expect 127 M1 + new, 0 skipped);
- `gba-db bootstrap` / `migrate` (applies 0004–0005, then a no-op) / `check-runtime-role`;
- `gba-db onboard` run twice (the second run changes nothing), then `gba-db readiness ka-nails` listing the missing facts;
- live API: token → `/v1/me` and a salon route; the owner DSN is still refused;
- `git diff --check`; secret scan of tracked files. No push.
