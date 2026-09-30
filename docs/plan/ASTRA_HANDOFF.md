# GORGONA Booking — Astra Independent Audit Handoff

Prepared 2026-09-30 for an independent adversarial audit. Everything below is backed by commands you can rerun; nothing here asks you to trust prior reports. Detailed evidence: `docs/plan/M1_REPORT.md`, `docs/plan/M2_REPORT.md`. ADRs: `docs/adr/0001`–`0010`.

## Project purpose

GORGONA Booking AI is a multi-tenant appointment platform for salons, and **KA Nails** is its first tenant. The core is deterministic:

- tenant-isolated catalog and pricing;
- holds and bookings whose double-booking protection is enforced by PostgreSQL;
- identity, salon membership and authorization;
- governed onboarding, so a salon cannot open public booking on guessed business facts.

The AI concierge, payments, notifications and customer UI are **not built**.

## Current milestone

| Milestone | Status |
| --- | --- |
| M1 — local booking foundation | **M1 DATABASE GATE: PASS** (real PostgreSQL 18.6) |
| M2 — identity, membership, authorization, onboarding | **M2 IDENTITY & TENANT GATE: PASS** |
| Branch | `m2-identity` |
| HEAD | `ff6c0c61837ce62421829b647836f82a956d18a7` (plus the commit adding this file) |
| Remote | `origin` = `github.com/alexeyalexandrov2026-tech/KA-nails` (**public**); **nothing pushed** (0 remote branches) |

## Commit history

- **M1** (branch `m1-foundation`, tip `fef30de`):
  - `6b9a0f3` Phase 0 handoff;
  - `7849853` skeleton/ADRs;
  - `5f6f23b` roles, migrations, forced RLS;
  - `6c6ddde` catalog;
  - `304f6c1` holds/exclusion;
  - `6627e6a` concurrency tests;
  - `335b358` real-PG evidence;
  - `fef30de` README.
- **M2** (branch `m2-identity`):
  - `48929f3` ADR-0007…0010;
  - `210404a` identity schema;
  - `7616296` OIDC verification and authorization;
  - `d0ce600` invitations and member lifecycle;
  - `1752921` onboarding, readiness, go-live;
  - `ff6c0c6` M2 report.

## Architecture

- **Runtime.** Python 3.14, FastAPI (0.142.1), Pydantic v2, psycopg 3 async with a pool. It is a modular monolith in `api/src/gorgona_booking/`. SQL is explicit (no ORM).
- **Database roles.** PostgreSQL 18 is the source of truth.
  - The owner/migration role owns the schema.
  - The runtime login role is a member of the NOLOGIN group `gba_runtime` only; it is non-superuser, non-`BYPASSRLS` and not an owner.
  - The app refuses to start (and readiness fails) with any other credential: `db/pool.assert_safe_runtime_role`.
- **Tenant isolation.**
  - Every tenant-owned table has `tenant_id` and composite `(tenant_id, id)` keys and FKs, so cross-salon references fail at the database.
  - RLS is **enabled and forced** on all 24 `gba` tables.
  - Tenant context is transaction-local only (`set_config('gba.tenant_id', …, true)`).
- **Catalog/booking core (M1).** Bookability requires a known booking duration (CHECK plus domain). Occupancy is a half-open `tstzrange` under a GiST exclusion constraint for HOLD/CONFIRMED (ADR-0003).
- **Identity (M2).** `gba.users`, plus `gba.user_identities (issuer, subject)` as a unique mapping. No auto-provisioning from tokens.
- **Memberships and authorization.** `gba.memberships` (role and status) is the only source of tenant authority. Permissions come from a static role map in `auth/permissions.py`.
- **Platform admin.** `gba.platform_roles` is granted owner-side only and is read-only for the runtime role. Platform admins get read-only, audited support access plus explicit tenant-status and go-live permissions.
- **Onboarding, readiness, go-live.** An operator CLI (owner credential) runs idempotent onboarding. Readiness is computed per fact. Public booking needs `booking_state = 'live'` (ADR-0010).

## Database migrations

`api/src/gorgona_booking/db/migrations/`. Each is applied once, in order, in its own transaction, and recorded with a SHA-256 checksum. **Applied migrations are immutable**: editing one is detected and refused (`db/migrate.py`, test `test_edited_migration_is_detected`). Changes go in new numbered files.

| # | Content |
| --- | --- |
| 0001 | schema `gba`, `btree_gist`, `current_tenant_id()`, tenants, tenant_hosts (host routing), locations (IANA timezone trigger), memberships (placeholder) |
| 0002 | components, services, variants (bookable ⇒ duration and published), add-ons, rules, revision trigger |
| 0003 | resources, bookings (transition trigger), booking_allocations (GiST exclusion, status cascade), booking_events, idempotency_keys |
| 0004 | users, user_identities, platform_roles, memberships bound to users (status, one live per salon+user), invitations (hash only), audit_events, audit triggers, tenant read/update policies |
| 0005 | tenants.booking_state, business_hours (no-overlap exclusion), salon_policies, salon_fact_confirmations, salon_branding_refs |

Guards in `tests/unit/test_migration_files.py` enforce:

- no `SECURITY DEFINER`, `BYPASSRLS`, or `disable`/`no force row level security`;
- every created table both enables and forces RLS.

## Authentication (ADR-0007, `auth/verifier.py`)

- **Mechanism.** OIDC-compatible bearer JWTs, verified by PyJWT 2.15.1 (`cryptography` 50.0.1) against a JWKS: static in tests, remote and cached in production, fetched off the event loop.
- **Trust configuration.** Trusted issuer and audience come from `GBA_AUTH_ISSUER` and `GBA_AUTH_AUDIENCE`, plus `GBA_AUTH_JWKS_URL`. All three or none, https only (http is allowed for localhost only in `local`/`test`).
- **Algorithms.** The allowlist is asymmetric only: `RS256, RS384, RS512, PS256, ES256, ES384`, with `RS256, ES256` by default. `none` and HMAC are never accepted.
- **Checks.** Required `kid`; required `exp`, `iat`, `iss`, `aud` and `sub`; `nbf` honoured; bounded leeway. Every failure raises one generic `INVALID_TOKEN` (401 with `WWW-Authenticate: Bearer … error="invalid_token"`). The token is never echoed.
- **Claims read.** Only `sub`, `email`, `email_verified`, `name` and `exp`. No tenant or role claim is read.
- **Principal resolution** (`auth/principal.py`). `(issuer, subject)` maps to a linked user (else `403 IDENTITY_NOT_LINKED`), and the user must be `active` (else `403 USER_DISABLED`). Platform roles are loaded from the database.
- **Not configured.** Protected routes answer `503 AUTH_NOT_CONFIGURED`, and the public hold route is unaffected.

## Authorization (ADR-0009, `tenancy/authorization.py`)

```
bearer token ─verify→ (issuer, subject) ─user_identities→ user (active)
  → path salon_id is only a *request*
  → BEGIN; set gba.tenant_id = salon_id, gba.user_id = user     (transaction-local)
  → SELECT membership WHERE tenant=salon AND user=me AND status='active' FOR SHARE
  → tenant.status = 'active'  (else 403 TENANT_SUSPENDED for members)
  → role permission map contains the route permission (else 403 PERMISSION_DENIED)
  → handler runs on the SAME connection/transaction → RLS still filters every table
  → any failure: exception before the handler, ROLLBACK, uniform 403 TENANT_ACCESS_DENIED
```

- **Platform-support path.** A caller with an active `platform_admin` row (re-checked inside the transaction) may enter any existing salon, but only for `booking.read`, `catalog.read`, `staff.read` and `readiness.read`, plus `platform.tenant_status` and `platform.go_live`. Each entry inserts a `platform.tenant_access` audit event in the same transaction.
- **No other tenant sources.** Request bodies are `extra="forbid"`, so a `tenant_id` field is rejected. Tenant headers are never read.
- **Host-based public path.** The public hold route resolves the salon by `Host` → `gba.tenant_hosts`, and requires `active` and `live`.

## Concurrency

- **Booking.** A GiST exclusion constraint on `(tenant_id =, resource_id =, during &&)` covers HOLD/CONFIRMED. The service takes `pg_advisory_xact_lock('gba:resource:<tenant>:<resource>')` before touching occupancy, so contenders queue instead of deadlocking.
  - Service races: 100-way → 1 success, 99 clean `23P01`, 0 deadlocks.
  - The raw-SQL race (no lock) has shown `40P01` deadlock victims once; the invariant still held.
- **Hold expiry** never uses `now()` in a predicate.
- **Invitation acceptance.** The invitation row is locked (`FOR UPDATE`) **before** the identity lookup. The same person re-accepting is idempotent; anyone else gets 409. Racing first sign-ins resolve through `ON CONFLICT (issuer, subject) DO NOTHING` inside a savepoint. Tested: 10 concurrent accepts → exactly 1 user, 1 identity, 1 membership.
- **Membership revoke.** Authorization holds `FOR SHARE` on the caller's membership, so a concurrent revoke waits for the in-flight request (tested).
  - Membership-changing routes first take `pg_advisory_xact_lock('gba:members:<salon>')` **before** the membership row lock. Two owners revoking each other therefore serialize (one 200, one 403, one owner left) instead of deadlocking.
  - Lock order everywhere: advisory lock → membership row → target rows.

## Audit model

- `gba.audit_events` is append-only for the runtime role (INSERT/SELECT only; no UPDATE, DELETE or TRUNCATE).
  - `tenant_id` NULL means a platform-scope event.
  - RLS: `tenant_id IS NOT DISTINCT FROM current_tenant_id()`.
- **Database triggers** (`gba.audit_row_change`) write events for inserts/updates on users, user_identities, platform_roles, memberships, invitations, tenants and salon_fact_confirmations. No application path can skip them.
  - Actor and request ID come from transaction-local `gba.actor` / `gba.request_id`.
  - Updates record only changed columns (`from` → `to`).
- **Application-written events:** `platform.tenant_access`.
- **Intentionally excluded from details:** `token_sha256`, `email_normalized`, `created_at`, `updated_at`, `granted_at`. Booking lifecycle has its own `gba.booking_events` (M1).

## Onboarding / readiness / go-live (ADR-0010)

- **Onboarding.** `gba-db onboard spec.json` runs with the owner credential. It creates the tenant (stable id from its slug), hosts, location, hours, staff, catalog, policies, fact statuses and branding references, and invites the first owner once (token to a file, never printed).
  - Re-runs are no-ops (fingerprint compare).
  - A host owned by another salon is refused.
  - An absent fact stays missing. A present fact must declare `confirmed|unconfirmed`, with no default.
- **Readiness** is computed from the database for eleven facts: owner, timezone, business_hours, staff, catalog, service_durations, bookable_services, cancellation_policy, deposit_policy, booking_rules, domain.
  - No data → `missing`, whatever the confirmation says.
  - Data without confirmation → `unconfirmed`.
  - `owner` and `bookable_services` are derived.
- **State.** `tenants.booking_state` goes `not_live` → `live` only through go-live (platform admin API or `gba-db go-live`), and only when every fact is `confirmed`.
  - The runtime role can update `booking_state`/`status` only for platform admins (a restrictive RLS policy plus column grants).
  - A live salon's facts cannot be set back to `unconfirmed`.
  - Public `POST /v1/holds` returns 404 for a salon that is not live or not active.

## KA Nails current state (persistent local DB and `gba-db readiness ka-nails`)

Tenant `ka-nails` is `active`, `not_live`, with 0 hosts. Branding: logo `assets/brand/ka-nails-logo.png` (SHA-256 `bb2fe1c0…3fbf53`). All three Hammam variants are `draft`, not bookable, with NULL durations.

| Status | Facts |
| --- | --- |
| **Confirmed** | *none* |
| **Unconfirmed** | catalog (Hammam Luxury $145, + Gel $160, + Gel French $175, from the owner brief) |
| **Missing** | owner, timezone, business_hours, staff, service_durations (HAMMAM_LUXURY, HAMMAM_LUXURY_GEL, HAMMAM_LUXURY_GEL_FRENCH), bookable_services, cancellation_policy, deposit_policy, booking_rules, domain |

`gba-db go-live ka-nails` refuses with exit code 2. No business fact was invented.

## Verification commands

Use your own disposable **PostgreSQL 18** server with a superuser DSN. Either use `infra/local/compose.yaml`, or on Windows the EDB binaries procedure in `docs/DEVELOPMENT.md` with `max_connections = 250`. Never point these commands at a shared or production server.

```bash
cd api
uv sync --locked
uv run ruff format --check .
uv run ruff check .
uv run mypy
# every integration test creates and drops its own gba_test_* database and roles
GBA_TEST_ADMIN_DSN=postgresql://<superuser>:<pw>@127.0.0.1:<port>/postgres GBA_REQUIRE_POSTGRES=1 uv run pytest -q
uv run pytest -q tests/unit/test_auth_verifier.py tests/unit/test_permissions.py tests/integration/test_authz_api.py tests/integration/test_invitations_api.py tests/integration/test_identity_schema.py
uv audit --locked
# canonical tooling against a dedicated database (see .env.example for the variables)
uv run --env-file ../.env gba-db bootstrap
uv run --env-file ../.env gba-db migrate            # second run must print "nothing to apply"
uv run --env-file ../.env gba-db check-runtime-role
uv run --env-file ../.env gba-db onboard fixtures/ka_nails_onboarding.candidate.json
uv run --env-file ../.env gba-db readiness ka-nails  # expect exit 2
```

- **M1 regression files:** `tests/unit/{test_app,test_config,test_migration_files,test_quote,test_candidate_catalog,test_booking_models,test_holds_api}.py` and `tests/integration/{test_tenant_isolation,test_roles_and_migrations,test_catalog,test_booking_rules,test_booking_concurrency}.py`.
- **M2 files:** `tests/unit/{test_auth_verifier,test_permissions,test_readiness,test_onboarding_spec}.py` and `tests/integration/{test_identity_schema,test_authz_api,test_invitations_api,test_onboarding}.py`.

## Verification results (2026-09-30, PostgreSQL 18.6, Windows 11, CPython 3.14.6)

| Check | Result |
| --- | --- |
| Full suite (`GBA_REQUIRE_POSTGRES=1`) | **208 passed, 0 failed, 0 skipped** |
| M1 regression (12 files) | **127 passed** |
| Focused auth/authz (5 files) | **60 passed** |
| M2-added tests (8 files) | **81 passed** |
| `ruff format --check` | pass (73 files) |
| `ruff check` | pass |
| `mypy --strict` (src + tests) | pass (73 files) |
| `uv sync --locked` / `uv lock --check` | consistent (38 packages) |
| `gba-db migrate` | `nothing to apply` (0001–0005 recorded) |
| `gba-db check-runtime-role` | OK: not superuser, no BYPASSRLS, not an owner, member of `gba_runtime` |
| `uv audit --locked` (experimental uv feature) | no known vulnerabilities in 37 packages |
| Database | 24/24 `gba` tables RLS enabled+forced; 0 `SECURITY DEFINER` functions; one restrictive policy (`tenants_runtime_update_platform_admin_only`) |
| Secret review | 0 real secrets in 121 tracked files; generated local credentials absent; no key or certificate files committed |
| Owner DSN as runtime | refused at startup (`UnsafeDatabaseRoleError`) |

JWT probes run during this handoff against the real verifier:
- **Rejected:** unknown `crit` extension, empty `crit`, `iat` one day in the future, non-numeric `iat`, a 4000-character `kid`, and a non-string `kid`.
- **`jku` header:** ignored. Only the configured JWKS is used, and a token signed by another key is rejected (`test_a_token_from_a_different_key_is_rejected`).

## Known intentional limitations

- **No production IdP configured.** Staff sign-in works only with the FAKE test IdP until `GBA_AUTH_*` is set.
- **KA Nails is not live.** The eleven readiness facts above need the owner.
- **No production deployment of any kind.** The app refuses `GBA_ENV=staging|production` by design: no rate limiting, payments or customer auth yet.
- **Public GitHub repository.** `origin` is public; do not push until the owner decides on visibility.
- **OCI.** An A1 database VM could not be created (`Out of host capacity` in all ADs). This is not an M2 blocker; the local PostgreSQL 18.6 path is the validated test environment.
- **Remote JWKS behaviour (hardening item, unused today).** `RemoteJwksKeySource` uses `PyJWKClient`: a token with an unknown `kid` causes a JWKS refetch. Consider throttling refetches and bounding `kid` before a production IdP is enabled.
- **Admin edits on a live salon.** An admin edit of hours or policies is recorded as `confirmed` by that admin (audited). There is no approval workflow yet.

## Astra attack checklist

Try to break each item independently; do not rely on the existing tests.

- **OIDC / JWT:**
  - algorithm confusion; `alg=none`; HS/asymmetric key confusion (HMAC keyed with the public key);
  - invalid issuer or audience; expired token; future `nbf`; invalid or future `iat`;
  - unknown `crit` headers; malformed, non-string or extremely long `kid`;
  - JWKS cache/rotation behaviour and refetch amplification in `RemoteJwksKeySource`;
  - arbitrary JWKS retrieval via `jku`/`x5u`/`x5c`; untrusted schemes and URLs in `GBA_AUTH_*`.
- **Tenant isolation:**
  - IDOR/BOLA on every `/v1/salons/{salon_id}/…` route;
  - cross-tenant booking, staff, service and location IDs;
  - forged `salon_id` in body, header or query;
  - tables or policies missing RLS;
  - `NULL` tenant context (`current_tenant_id()` returning NULL);
  - `tenant_hosts` being readable without context by design.
- **Membership/authz:**
  - revoke and role-change races; stale authorization within one request;
  - owner-to-owner and last-owner scenarios;
  - disabled users; suspended tenants; guessed or non-existent tenants (uniform 403).
- **Invitations:**
  - replay; expiry; concurrent acceptance;
  - duplicate identities for one person; mismatched or unverified email;
  - token leakage (responses, logs, audit, database).
- **Platform admin:**
  - privilege escalation to `platform_admin` from the runtime role; forging `platform_roles`;
  - mutation through the support path; unaudited support access;
  - crossing the support boundary.
- **Database:**
  - RLS bypass; owner/runtime confusion; unsafe grants (runtime DELETE exists on `locations`, `business_hours`, `variant_components`, `add_on_rules`);
  - FK and unique-violation side channels across tenants;
  - `gba.user_id`/`gba.auth_*` settings being set or left over unexpectedly;
  - `SECURITY DEFINER`; regressions from checksum or migration ordering.
- **Onboarding:**
  - rerun idempotency; partial transaction failure;
  - host ownership conflict and domain takeover;
  - stale confirmation state after data edits;
  - go-live bypass; a public hold before go-live.
- **Audit:**
  - event omission (any write path without a trigger);
  - event forgery (runtime INSERT into `audit_events` with a chosen actor or tenant);
  - cross-tenant event visibility; sensitive-data leakage in `details`.
- **Testing:**
  - weak or over-specific assertions;
  - concurrency tests that pass without real contention;
  - missing negative paths;
  - test-only behaviour that differs from production (e.g. the static JWKS source vs the remote source; seeds that mark FAKE salons live owner-side).
