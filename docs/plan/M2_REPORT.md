# M2 report — identity, tenant membership, authorization, onboarding

Date: 2026-09-30. Branch `m2-identity`, from M1 tip `fef30de`. Plan: `docs/plan/M2_PLAN.md`. ADRs 0007–0010.

## Verdict

**M2 IDENTITY & TENANT GATE: PASS**

- Every M2 behaviour was written test-first and ran against a real PostgreSQL 18.6 server; RED output was captured before each implementation.
- The full suite passes: 208 passed, 0 failed, 0 skipped, with `GBA_REQUIRE_POSTGRES=1`.
- The M1 regression suite passes unchanged in its assertions: 127/127.

OCI deployment remains blocked by A1 capacity (see `M1_REPORT.md`). Per the owner, it is not an M2 blocker.

## Commits

| # | Commit | Slice |
| --- | --- | --- |
| 1 | `48929f3` | ADR-0007…0010, approved M2 plan |
| 2 | `210404a` | `0004_identity.sql`: users, identities, platform roles, identity-bound memberships, invitations, audit |
| 3 | `7616296` | OIDC token verification, principals, tenant authorization, salon and platform routes |
| 4 | `d0ce600` | Invitations and member lifecycle |
| 5 | `1752921` | `0005_salon_setup.sql`, onboarding, readiness, go-live gate, CLI |
| 6 | this commit | M2 report and docs |

## Test results (final run, PostgreSQL 18.6, `GBA_REQUIRE_POSTGRES=1`)

| Suite | Executed | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: | ---: |
| Full suite | 208 | 208 | 0 | 0 |
| M1 regression (the 12 M1 test files) | 127 | 127 | 0 | 0 |
| Focused auth/authorization (verifier, permissions, authz API, invitations, identity schema) | 60 | 60 | 0 | 0 |
| New in M2 | 81 | 81 | 0 | 0 |
| Database-marked (real PostgreSQL) | 109 | 109 | 0 | 0 |
| Unit (no database) | 99 | 99 | 0 | 0 |

`ruff format --check` passes (73 files), `ruff check` passes, `mypy --strict` passes (73 files), and `git diff --check` is clean.

### RED → GREEN evidence per slice

| Slice | RED (before implementation) | GREEN |
| --- | --- | --- |
| 2 identity schema | 13 failed (`UndefinedTable`); 1 regression guard passed | 36 targeted passed; full 140 |
| 3 auth + authorization | collection error `ModuleNotFoundError: gorgona_booking.auth` | 34 passed; full 174 |
| 4 invitations + lifecycle | 13 failed (routes absent) | 13 passed; full 187 |
| 5 onboarding + readiness | collection errors `ModuleNotFoundError: gorgona_booking.onboarding` | 31 passed after one test-defect fix; full 208 after two assertion fixes (see Defects) |

## Required behaviours → tests

| # | Behaviour | Test(s) |
| --- | --- | --- |
| 1 | Unauthenticated / invalid token → 401 (`WWW-Authenticate`), unlinked identity → 403 | `test_authz_api::test_01_*`, `test_auth_verifier` (12 invalid-token cases: expired, nbf, wrong iss/aud, missing sub, `alg=none`, HS256 key confusion, missing/unknown kid, tampered, garbage, oversized, foreign key) |
| 2 | Staff reads own salon | `test_02_staff_reads_own_salon` |
| 3 | Cross-salon access denied (uniform 403, including for non-existent salons) | `test_03_non_member_is_denied_uniformly` |
| 4 | Guessed cross-salon booking id → 404 | `test_04_guessed_cross_salon_booking_id_is_not_found` |
| 5 | Body `tenant_id` rejected; `X-Tenant-Id`/`X-Salon-Id` headers ignored; writes land only in the authorized salon | `test_05_client_supplied_tenant_cannot_change_effective_tenant`, `test_invitation_permissions_and_duplicates` |
| 6 | Salon admin manages own services and staff; publishing without a duration → 422 | `test_06_salon_admin_manages_own_services_and_staff` |
| 7 | Salon admin cannot manage another salon; cross-salon location → 422 | `test_07_salon_admin_cannot_manage_another_salon` |
| 8 | Platform admin: audited read-only support access, suspend/reactivate, go-live; salon owners cannot | `test_08_*`, `test_go_live_requires_readiness_and_gates_public_booking` |
| 9 | Revoked or suspended membership denied on the next request; a concurrent revoke waits for the in-flight request | `test_09_*`, `test_09b_concurrent_revoke_waits_for_the_in_flight_request`, `test_member_lifecycle_*` |
| 10 | Suspended salon: members 403 `TENANT_SUSPENDED`, public host 404; not-live salon: public host 404 | `test_10_*`, `test_go_live_*` |
| 11 | Invitation acceptance idempotent; 10 concurrent accepts → one user, one identity, one membership; rules (verified email, email match, expiry, wrong salon) | `test_11_*`, `test_11b_*`, `test_invitation_acceptance_rules[5]`, `test_expired_invitation_*` |
| 12 | Duplicate `(issuer, subject)` mapping rejected by the database | `test_duplicate_identity_mapping_is_rejected`, `test_runtime_cannot_link_an_identity_to_someone_else` |
| 13 | Security-sensitive changes audited (trigger-written, no secrets or emails) | `test_security_sensitive_changes_are_audited_without_secrets`, `test_08_*`, `test_member_lifecycle_*`, `test_salon_admin_settings_*` |
| 14 | Runtime role non-superuser/non-BYPASSRLS; owner and superuser DSNs still refused at startup | `test_runtime_role_remains_unprivileged`, `test_14_privileged_database_credentials_are_still_refused`, plus the live check below |
| 15 | RLS isolation intact; new tables forced | M1 `test_every_tenant_owned_table_has_forced_rls`, `test_identity_tables_force_row_level_security`, `test_invitations_are_tenant_isolated_*`, M1 guard `test_every_created_table_enables_and_forces_rls` |
| 16 | M1 suite green | 127/127 |

## Schema added

- **`0004_identity.sql`:**
  - context functions `gba.current_user_id()`, `current_auth_issuer()`, `current_auth_subject()`;
  - `gba.users`; `gba.user_identities` (`unique (issuer, subject)`); `gba.platform_roles` (one active grant per role; runtime read-only);
  - `gba.memberships` evolved: `subject` → `user_id` FK; `status` active/suspended/revoked, with revoked terminal (trigger); one non-revoked membership per salon+user; no runtime DELETE;
  - `gba.invitations`: SHA-256 token only, one pending per email, terminal states immutable;
  - `gba.audit_events`: append-only for the runtime role; tenant-scoped or platform-scoped (NULL tenant);
  - `gba.audit_row_change()` triggers on users, identities, platform roles, memberships, invitations and tenants;
  - extra `tenants` policies: members and platform admins may read; runtime updates are restricted to platform admins by a restrictive policy.
- **`0005_salon_setup.sql`:**
  - `tenants.booking_state` (default `not_live`);
  - `gba.business_hours`: ISO weekday, minute ranges, no overlap per weekday (GiST exclusion);
  - `gba.salon_policies`: NULL means missing;
  - `gba.salon_fact_confirmations`: fixed fact keys, audited;
  - `gba.salon_branding_refs`.
- On the persistent database: all **24/24** `gba` tables have RLS enabled **and forced**. There are **0** `SECURITY DEFINER` functions, and the only extension is `btree_gist` 1.8 (plus built-in `plpgsql`).

## Identity model

A person is a `users` row. An external IdP account is a `user_identities (issuer, subject)` row. Users are created only by accepting an invitation (or by the operator with `gba-db link-user`); they are never created on first sight of a token. Emails are normalized. Disabling a user denies every request.

## Authorization model

- Authentication (ADR-0007) is the provider-agnostic `TokenVerifier`: PyJWT 2.15.1 validating against JWKS, with an asymmetric algorithm allowlist and one generic `INVALID_TOKEN`. It is configured by `GBA_AUTH_*` (all or none, https only).
- Authorization (ADR-0008) uses a static, versioned role → permission map: artist < front_desk < manager < owner. Only owners manage owner and manager roles. The platform admin gets read-only support permissions plus explicit `platform.tenant_status` and `platform.go_live`.
- Enforcement is at the API/service boundary (`tenancy/authorization.authorized_tenant`), with PostgreSQL RLS still underneath.

## Tenant-context derivation (ADR-0009)

1. The path salon id is only a request.
2. In the same transaction as the work:
   - the tenant and user context are set;
   - the caller's **active** membership is read `FOR SHARE`, and the tenant must be active;
   - the role must hold the route's permission.
3. Otherwise, an active platform admin may enter with read-only support permissions, and each entry writes a `platform.tenant_access` audit event.
4. Anything else gets a uniform 403. The transaction rolls back, so the candidate context never outlives the check.
5. Membership-changing routes first take a per-salon advisory lock (`exclusive="members"`), so mutual revocations serialize instead of deadlocking.

## Onboarding flow (ADR-0010)

1. The operator runs `gba-db onboard spec.json` with the owner credential. It is idempotent:
   - stable ids come from natural keys, and no-op updates are skipped;
   - a second run reports `unchanged`;
   - it refuses a host already owned by another salon;
   - the first owner is invited once; the token goes to an exclusive owner-only file and is never printed.
2. Salon admins configure hours and policies and confirm facts through the API. On a salon that is not live, edits reset a fact to `unconfirmed`. A live salon cannot be downgraded.
3. Readiness is computed from the database per required fact: `confirmed`, `unconfirmed` or `missing`.
4. Go-live happens only when every fact is confirmed, via `POST /v1/platform/salons/{id}/go-live` or `gba-db go-live`. The public `POST /v1/holds` requires `booking_state = 'live'`.

## Security checks

- **Tokens:** never stored, logged or echoed. Invitation tokens are stored as SHA-256 only, which is verified against table dumps and the audit log.
- **Secrets:** a scan of tracked files found no generated secrets. Test output was passed through the password redactor.
- **Runtime role:** non-superuser, non-BYPASSRLS, not an owner. Its tenant grants are exactly `SELECT, UPDATE(status), UPDATE(booking_state)`, and a restrictive policy limits those updates to platform admins. It still cannot INSERT tenants (M1 test) or write platform roles (M2 test).
- **Live socket:**
  - `/health/ready` → 200 on the runtime role;
  - protected routes → 503 `AUTH_NOT_CONFIGURED` without an IdP;
  - the owner DSN is refused at startup (`UnsafeDatabaseRoleError: … not a member of gba_runtime; owns … gba objects`).
- **Canonical path:** `gba-db bootstrap` → `migrate` (applied `0004_identity, 0005_salon_setup`) → `migrate` (nothing to apply) → `check-runtime-role` OK.

## KA Nails readiness (from `gba-db readiness ka-nails` on the persistent database)

`fixtures/ka_nails_onboarding.candidate.json` contains only:
- the brand name;
- the logo reference with its SHA-256;
- the three Hammam prices from the brief, with public display ranges where the brief gives them and every booking duration `null`.

Result: **not ready**, and `gba-db go-live ka-nails` refuses (exit 2).

| Fact | Status | What the owner must supply |
| --- | --- | --- |
| owner | missing | owner email for the first invitation |
| timezone | missing | location and IANA timezone |
| business_hours | missing | opening hours |
| staff | missing | artists |
| catalog | unconfirmed | confirm the three Hammam variants and prices; add-ons remain in the M1 candidate catalog |
| service_durations | missing | booking duration for HAMMAM_LUXURY, HAMMAM_LUXURY_GEL, HAMMAM_LUXURY_GEL_FRENCH (base Hammam included) |
| bookable_services | missing | follows from confirmed durations |
| cancellation_policy | missing | cancellation terms |
| deposit_policy | missing | deposit terms |
| booking_rules | missing | booking rules |
| domain | missing | production host name |

The KA Nails tenant exists on the local development database as `active`, `not_live`, with all three variants `draft` and not bookable.

## Defects found and fixed

No application defect was found by the real PostgreSQL runs. Three **test** defects were found and fixed; none weakened an assertion:

1. **`test_salon_admin_settings_*`.** It asserted that the "first" `salon_fact.created` event was the owner's, but onboarding had already recorded the operator's `timezone` fact. The assertion now matches the business-hours fact row exactly: `[created by owner, updated by owner]`.
2. **`test_security_sensitive_changes_*`.** It asserted that salon B had no audit events at all. After the approved seed change, salon B has its own go-live event. The assertion now checks the actual intent: none of salon A's events (by tenant id or target id) are visible from salon B.
3. **`test_tenant_status_change_*`.** It asserted that the only `tenant.updated` event was the platform admin's. The seed's go-live event made the list longer. The assertion now selects status changes (`details ? 'status'`) and still requires exactly the platform admin's event.

## M1 test code changes (owner decisions 1 and 2, verbatim)

`api/tests/integration/test_tenant_isolation.py` — the assertion (`ForeignKeyViolation`) is unchanged:

```diff
-from tests.integration.seed import Salon
+from tests.integration.seed import Salon, seed_user
@@
 async def test_composite_foreign_key_blocks_cross_salon_reference(
-    app_pool: RuntimePool, salons: tuple[Salon, Salon]
+    app_pool: RuntimePool, salons: tuple[Salon, Salon], owner_conn: psycopg.Connection
 ) -> None:
     a, b = salons
+    user = seed_user(owner_conn, "fk")  # M2: memberships reference a user (owner decision 1)
     with pytest.raises(errors.ForeignKeyViolation):
         async with tenant_transaction(app_pool, a.tenant_id) as conn:
             await conn.execute(
-                "insert into gba.memberships (tenant_id, subject, role, location_id) "
-                "values (%s, 'fake-subject', 'artist', %s)",
-                (a.tenant_id, b.location_id),
+                "insert into gba.memberships (tenant_id, user_id, role, location_id) "
+                "values (%s, %s, 'artist', %s)",
+                (a.tenant_id, user.user_id, b.location_id),
             )
```

`api/tests/integration/seed.py::seed_salon` (test infrastructure):

```diff
             (tenant_id, f"FAKE location {label.upper()}", FAKE_TIMEZONE),
         ).fetchone()
+        # M2 (owner decision 2): public booking requires an explicit go-live. FAKE test
+        # salons are published owner-side so M1 booking tests exercise the same paths.
+        conn.execute("update gba.tenants set booking_state = 'live' where id = %s", (tenant_id,))
```

`seed.py` also gained M2 helpers: `seed_user`, `seed_resource`/`force_hold_expired` (unchanged since M1), and `FAKE_ISSUER` imported from `tests/support/fake_idp.py`. No other M1 test file changed.

## Files changed in M2 (vs `fef30de`)

- **Added:**
  - `api/fixtures/ka_nails_onboarding.candidate.json`;
  - `api/src/gorgona_booking/{api/deps.py, api/members.py, api/platform.py, api/salons.py, api/setup.py, auth/__init__.py, auth/permissions.py, auth/principal.py, auth/verifier.py, db/migrations/0004_identity.sql, db/migrations/0005_salon_setup.sql, identity/__init__.py, identity/invitations.py, onboarding/__init__.py, onboarding/readiness.py, onboarding/service.py, onboarding/spec.py, tenancy/authorization.py}`;
  - `api/tests/integration/{test_authz_api.py, test_identity_schema.py, test_invitations_api.py, test_onboarding.py}`;
  - `api/tests/support/{__init__.py, fake_idp.py}`;
  - `api/tests/unit/{test_auth_verifier.py, test_onboarding_spec.py, test_permissions.py, test_readiness.py}`;
  - `docs/adr/0007…0010`, `docs/plan/M2_PLAN.md`, `docs/plan/M2_REPORT.md`.
- **Modified:**
  - `api/pyproject.toml` and `api/uv.lock` (added `pyjwt[crypto]>=2.10`, resolved PyJWT 2.15.1 and cryptography 50.0.1);
  - `api/src/gorgona_booking/{api/app.py, api/errors.py, config.py, db/cli.py, db/provisioning.py, errors.py, tenancy/resolver.py}`;
  - `api/tests/integration/{seed.py, test_tenant_isolation.py}`;
  - `docs/DEVELOPMENT.md`, `README.md`.

## Remaining blockers

1. **KA Nails business facts:** the eleven readiness items above, all owner input. Nothing was invented.
2. **Identity provider not chosen.** The code is provider-agnostic, but a real issuer, audience and JWKS URL are needed before staff can sign in anywhere but tests.
3. **GitHub:** the `KA-nails` remote is public and nothing has been pushed; visibility needs owner approval. CI has never run.
4. **OCI:** A1 `Out of host capacity` (see M1). Not an M2 blocker.
5. **Still out of scope:** customer accounts and guest booking capabilities, rate limiting (production start remains refused by design), payments and deposit collection, notifications, schedules and availability search, staff↔service skills, and edits to a live salon's facts beyond confirmation.
