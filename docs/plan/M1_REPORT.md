# M1 report — local booking foundation

Last updated: 2026-09-30. Branch `m1-foundation`, on top of Phase 0 commit `6b9a0f3`. Plan: `docs/plan/M1_PLAN.md`.

## Verdicts

| Gate | Verdict |
| --- | --- |
| **M1 database correctness gate** (real PostgreSQL 18) | **PASS**: all 62 database-marked tests executed against a real PostgreSQL 18.6 server and passed, together with the 65 unit tests (127/127, 0 skipped), on two consecutive full runs |
| **OCI deployment** of the database VM | **BLOCKED**: `Out of host capacity` for `VM.Standard.A1.Flex` in all three us-ashburn-1 ADs, at 2 OCPU/8 GB and 1 OCPU/6 GB. No VM exists |

The database gate ran on a **local, disposable PostgreSQL 18.6 cluster on the Windows development machine**, not on OCI. No OCI deployment is claimed.

## Commits

| # | Commit | Scope |
| --- | --- | --- |
| 1 | `7849853` | Skeleton, tooling, config, health, error envelope, Compose, CI, ADR-0001–0006, plan |
| 2 | `5f6f23b` | `gba-db` bootstrap/migrate/check, `0001_tenancy.sql`, pool and tenant context, runtime-role guard, isolation tests |
| 3 | `6c6ddde` | `0002_catalog.sql`, quote engine, catalog repository, candidate KA Nails fixture, tests |
| 4 | `304f6c1` | `0003_booking.sql`, booking service, idempotency, `POST /v1/holds`, 23P01 mapping, unit tests |
| 5 | `6627e6a` | Booking integration and concurrency tests, first report |
| 6 | this commit | Evidence from the real PostgreSQL 18 run (this report) and a Windows note in `DEVELOPMENT.md`. **No application or test code changed** |

## Test results

| Suite | Tests | Executed | Passed | Failed | Skipped | Evidence |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Unit (`tests/unit`, no database) | 65 | 65 | 65 | 0 | 0 | runs 2 and 3 |
| `test_tenant_isolation.py` | 13 | 13 | 13 | 0 | 0 | real PG 18.6, runtime role `gba_test_app` |
| `test_roles_and_migrations.py` | 13 | 13 | 13 | 0 | 0 | real PG 18.6 |
| `test_catalog.py` | 12 | 12 | 12 | 0 | 0 | real PG 18.6 |
| `test_booking_rules.py` | 16 | 16 | 16 | 0 | 0 | real PG 18.6, including the HTTP flow |
| `test_booking_concurrency.py` | 8 | 8 | 8 | 0 | 0 | real PG 18.6, 100 independent connections |
| **Total** | **127** | **127** | **127** | **0** | **0** | `GBA_REQUIRE_POSTGRES=1`, so a missing database would have failed, not skipped |

### Runs

| Run | Result | Notes |
| --- | --- | --- |
| Pre-DB baseline | 66 passed, 61 skipped | matches the earlier report |
| 1 | 66 passed, **61 errors** | Infrastructure, not code: see "Incidents" below |
| 2 | **127 passed** in 133.6 s | the raw race took 102.5 s because of 99 deadlock victims (see below) |
| 3 (final) | **127 passed** in 33.1 s | server log: 0 deadlocks, 525 `booking_allocations_no_overlap` 23P01, 0 FATAL/PANIC |

Static checks on the final tree: `ruff format --check` PASS (47 files), `ruff check` PASS, `mypy --strict` PASS (47 files), `git diff --check` clean.

## What the real run proved

- **Salon isolation (runtime role, two fake salons):**
  - SELECT only sees the own salon; INSERT into another salon → 42501; UPDATE and DELETE across salons affect 0 rows; moving a row to another salon → 42501.
  - No tenant context → 0 rows visible and writes rejected.
  - Composite FK blocks cross-salon references (23503).
  - Context does not leak through a pooled connection (same backend PID) or survive a failed transaction.
- **Roles:**
  - The runtime role passes the guard; owner and superuser are refused.
  - Runtime cannot disable or unforce RLS, drop policies, `SET ROLE` owner, read migration history or create tables (all 42501).
  - Superuser migrations are refused, and edited migrations are detected.
- **Migrations:** `0001`–`0003` apply cleanly as the owner role; re-applying is a no-op; `btree_gist` 1.8 is created; all 15 `gba` tables have RLS **enabled and forced**.
- **Catalog:** six variant CHECKs fire by constraint name. A variant with unknown duration cannot be made bookable, the add-on duration CHECK fires, and revisions bump. Catalog rows are invisible across salons. The owner-unconfirmed KA Nails candidate stays non-bookable (unit tests; it is never seeded).
- **Overlap matrix:**
  - adjacent `[10:00,11:00)` + `[11:00,12:00)` allowed;
  - partial overlap rejected (both sides);
  - containment and reverse containment rejected;
  - same slot, other artist allowed; same slot, other salon allowed.
- **Lifecycle:**
  - cancelled holds and expired holds stop blocking; expiry is transactional and audited (`HOLD → EXPIRED` event by `system:hold-expiry`);
  - the sweeper works, and confirm re-checks expiry under lock;
  - HOLD and CONFIRMED block each other;
  - terminal and interval immutability is enforced for direct SQL, and the runtime role cannot update or delete allocations or delete bookings.
- **Idempotency:**
  - same key and same request → same booking (`replayed=True`), 1 allocation;
  - same key and different request → `IDEMPOTENCY_KEY_REUSED`;
  - a replayed conflict stays a conflict;
  - 20 racers sharing one key → 1 booking.
- **HTTP (`POST /v1/holds`):**
  - 201 on create; 201 plus `Idempotent-Replayed: true` on replay; 409 `SLOT_CONFLICT` with no raw database detail; 404 `TENANT_NOT_FOUND` for an unknown host.
  - Also checked on a real socket: `python -m gorgona_booking` with the runtime DSN served `/health/ready` → 200 `{"database":"ok"}`.
  - With the **owner** DSN it refused to start (`UnsafeDatabaseRoleError: … not a member of gba_runtime; owns … gba objects`).

## 100-way races (exact counts from the PostgreSQL server log)

Counts come from the server's own log between marker lines, not from the tests. Every race also asserted exactly one blocking allocation and zero overlapping pairs afterwards.

### Raw SQL: 100 independent connections, barrier before the allocation insert, no application locking

| Run | Attempts | Committed | Clean 23P01 | Deadlock 40P01 | Other errors | Surviving reservations |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| full suite, run 2 | 100 | 1 | 0 | **99** | 0 | **1** |
| isolated × 6 | 100 each | 1 | 99 | 0 | 0 | **1** |
| full suite, run 3 | 100 | 1 | 99 | 0 | 0 | **1** |

Run 2's 99 `deadlock detected` errors all fall between 03:04:51.095 and 03:06:26.982, before any `no_overlap` error. They were resolved one per `deadlock_timeout` (1 s), which explains the 102 s duration. Example: `Process 5380 waits for ShareLock on transaction 789; blocked by process 16556. Process 16556 waits for ShareLock on transaction 793; blocked by process 5380.`

This is the behaviour ADR-0003 predicted for concurrent overlapping inserts under an exclusion constraint. **Even then, the constraint admitted exactly one reservation.**

### Service layer: per-resource advisory lock, then the exclusion constraint

| Test | Attempts | Winners | Clean conflicts | Deadlocks | Unexpected errors | Surviving | Peak lock waiters |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 keys, one 100-connection pool | 100 | 1 | 99 | 0 | 0 | 1 | 61 |
| two service instances (2 × 50 pools) | 100 | 1 | 99 | 0 | 0 | 1 | 73 |
| 50 HOLD vs 50 CONFIRMED | 100 | 1 | 99 | 0 | 0 | 1 | 62 |
| over an expired hold (expired exactly once) | 100 | 1 | 99 | 0 | 0 | 1 | 64 |
| confirm racing 20 new holds | 21 | 1 (the confirm) | 20 | 0 | 0 | 1 | — |
| 20 racers, one idempotency key | 20 | 1 booking, 19 replays | 0 | 0 | 0 | 1 | — |

Idempotency state after the 100-key race: 100 records, 1×201 and 99×409, and one booking row.

### Resources during the races

- Up to 103 client backends.
- PostgreSQL working set peaked at about 2.4 GB; free RAM on the 15.7 GB host never fell below about 2.1 GB.
- No OOM, crash, FATAL or PANIC. Zero leftover `gba_test_*` databases.

## Environment used for the gate

| Item | Value |
| --- | --- |
| PostgreSQL | **18.6** (`PostgreSQL 18.6 on x86_64-windows, compiled by msvc-19.44.35228, 64-bit`); `psql` 18.6 |
| Source | Official EDB binaries `postgresql-18.6-1-windows-x64-binaries.zip` (343,808,005 bytes, SHA-256 `fbe23da234ee31547bf8a36d29dfd81e82b849df2d2b78d2eecb43d360252f8c`), extracted to `%USERPROFILE%\.gba\tools`. No system install, no Windows service |
| Host | Windows 11 Home, 13th Gen Intel Core i5-13420H (8 cores / 12 threads), 15.7 GB RAM, x86_64 |
| Cluster | Disposable, `%USERPROFILE%\.gba\pg18-m1-data`, `initdb --pwfile` (file deleted after init), UTF8, locale C |
| Network | `listen_addresses = 127.0.0.1`, port 55432; `pg_hba`: a single `host all all 127.0.0.1/32 scram-sha-256` rule; nothing else |
| Key settings | `max_connections = 250` (repository requirement), `shared_buffers = 128MB` (default), `password_encryption = scram-sha-256`, `deadlock_timeout = 1s`, `log_lock_waits = on` |
| Superuser | `gba_superuser` (initdb bootstrap superuser), used only via `GBA_TEST_ADMIN_DSN` for bootstrap and fixtures |
| Secrets | ACL-restricted (owner-only) files under `%USERPROFILE%\.gba\secrets`; never on a command line; test output passed through a redactor |
| Extensions used by the project | `btree_gist` 1.8 (plus built-in `plpgsql`). `uuidv7()` is built into PostgreSQL 18 |
| Canonical provisioning | `gba-db bootstrap` → `gba-db migrate` (0001–0003; a second run applied nothing) → `gba-db check-runtime-role` OK, into database `gorgona_booking` owned by `gba_owner`; runtime role `gba_app` |

## Incidents and defects

**No application defect was demonstrated by the real PostgreSQL test run.** No application or test code was changed.

**Infrastructure incident (run 1, 61 errors).**
- **Symptom:** every database test failed in the session fixture with `server closed the connection unexpectedly`.
- **Evidence from the server log:**
  - `autovacuum worker (PID 25084) was terminated by exception 0xC0000142` (STATUS_DLL_INIT_FAILED);
  - then repeated `could not reserve shared memory region … error code 487` for every new child.
- **Cause:** the server had been started from a tool-managed shell whose console was torn down when that shell was stopped, so the postmaster could no longer spawn backends.
- **Fix:** stop the server cleanly (`pg_ctl stop -m fast`) and restart it detached, in its own hidden window.
- **Result:** runs 2 and 3 passed. Documented in `docs/DEVELOPMENT.md`.

**Observed design behaviour (not a defect).** The raw-SQL race can end in `40P01` deadlocks instead of `23P01`, as in run 2. The raw test accepts `40P01` as a non-committing outcome by design, and the invariant (exactly one reservation) held. The service layer's advisory lock produced 0 deadlocks in every run. This confirms the mitigation in ADR-0003.

## OCI attempt (read-only preflight, then create)

- **Tenancy:** home region us-ashburn-1, root compartment only, ADs `rGvv:US-ASHBURN-AD-1/2/3`.
- **`gorgona-node`:** RUNNING, `VM.Standard.E2.1.Micro` (1 OCPU / 1 GB), AD-3, created 2026-09-28. **Untouched**: no changes to its instance, subnets, route tables, security lists or internet gateway.
- **Limits verified live before creating anything:**
  - A1: 2 OCPU / 12 GB regional, 0 used;
  - free block storage: 200 GB regional, 50 used (the `gorgona-node` boot volume);
  - every paid compute limit (E3/E4/E5/Standard2/3) is 0;
  - VCNs: 1 of 2 used; internet gateways: 1 allowed, already used by `gorgona-vcn`.
- **Created (free, isolated, left in place for a retry):**
  - subnet `gba-db-subnet` 10.0.2.0/24 in `gorgona-vcn`;
  - its own route table `gba-db-rt` (default route to the existing gateway, which is unmodified);
  - its own security list `gba-db-ssh-only`: TCP 22 from `73.139.26.188/32` only, **no 5432 rule**.
- **Launch attempts:** `gorgona-booking-db`, `VM.Standard.A1.Flex`, Ubuntu 24.04 aarch64 (`Canonical-Ubuntu-24.04-aarch64-2026.09.18-0`), 50 GB boot, key `~/.ssh/gba_booking_db`:
  - 2 OCPU / 8 GB in AD-1, AD-2, AD-3 → `500 InternalError "Out of host capacity."` (with CLI retries);
  - 1 OCPU / 6 GB in AD-1, AD-2, AD-3 (`--no-retry`, one attempt each) → the same error. opc-request-ids: AD-1 `32C91AFD…/4782406E…`, AD-2 `C4CC7106…/C3C655C6…`, AD-3 `01B96298…/08475BDC…`.
- **Result:** no instance was created and A1 usage is still 0. No paid resource was requested.

## Remaining blockers

1. **OCI A1 capacity.** Retry later: one attempt per AD, same shape, no indefinite loop. The subnet, route table and security list are ready, and the server setup script (PGDG PostgreSQL 18, `127.0.0.1` only, `max_connections = 250`) is prepared. OCI session tokens expire after about an hour and need `oci session authenticate --profile-name GORGONA`.
2. **GitHub.** `origin` → `alexeyalexandrov2026-tech/KA-nails` is **public** and empty. Nothing has been pushed; the owner must confirm visibility first. CI (`.github/workflows/ci.yml`) has therefore never run.
3. **Owner business facts** (durations including base Hammam, hours, staff, timezone, deposit/cancellation/tax, domain). None were invented; the candidate catalog stays `owner_unconfirmed` and non-bookable.
4. **Not in M1:** authentication and guest capabilities, rate limiting, schedules and availability search, payments, notifications/outbox, rescheduling, multi-resource bookings, a cross-tenant expiry scheduler, idempotency-key cleanup, catalog versioning.
