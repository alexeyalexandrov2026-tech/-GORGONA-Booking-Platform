# M1 report — local booking foundation

Date: 2026-09-30. Branch `m1-foundation`, on top of Phase 0 commit `6b9a0f3`. Plan: `docs/plan/M1_PLAN.md`.

**Headline: the code, SQL and tests are written, but no PostgreSQL test has been executed yet.** This machine has no Docker, no WSL distribution and no PostgreSQL. Following the owner's instruction, the 61 database tests are reported **BLOCKED** and were not replaced by SQLite or mocks. Until they run, the booking invariants are designed and statically checked, not proven.

## Commits

| # | Commit | Scope |
| --- | --- | --- |
| 1 | `7849853` | Skeleton, tooling, config, health, error envelope, Compose, CI, ADR-0001–0006, plan |
| 2 | `5f6f23b` | `gba-db` bootstrap/migrate/check, `0001_tenancy.sql`, pool and tenant context, runtime-role guard, isolation tests |
| 3 | `6c6ddde` | `0002_catalog.sql`, quote engine, catalog repository, candidate KA Nails fixture, tests |
| 4 | `304f6c1` | `0003_booking.sql`, booking service, idempotency, `POST /v1/holds`, 23P01 mapping, unit tests |
| 5 | this commit | Booking integration and concurrency tests, this report, docs |

## Status

| Gate | Status | Evidence |
| --- | --- | --- |
| Format (`ruff format --check`) | PASS | 47 files unchanged |
| Lint (`ruff check`) | PASS | "All checks passed!" |
| Types (`mypy --strict`, src + tests) | PASS | "no issues found in 47 source files" |
| Unit tests (no database) | PASS | 65 passed |
| Race helper self-check | PASS | 1 passed (no database needed) |
| Whitespace (`git diff --cached --check`) | PASS | clean before every commit |
| SQL syntax (`pglast` 8.4, libpg_query parser) | PASS | 0001: 34, 0002: 34, 0003: 32 statements parsed, including PL/pgSQL bodies. **Syntax only**: no semantics, privileges or constraint behaviour |
| PostgreSQL 18 integration tests | **BLOCKED** | 61 tests written, **0 executed**; skipped with "BLOCKED: no PostgreSQL 18 server configured" |
| Salon isolation (SELECT/INSERT/UPDATE/DELETE, FK injection, pool leak) | BLOCKED | written in `test_tenant_isolation.py` |
| Runtime role cannot bypass RLS / owner and superuser refused | BLOCKED | written in `test_roles_and_migrations.py` |
| Bookability CHECKs in the database | BLOCKED | written in `test_catalog.py` |
| 100-way race: 1 success, 99 clean conflicts | BLOCKED | written in `test_booking_concurrency.py` |
| Overlap matrix, expiry, HOLD vs CONFIRMED, idempotency, HTTP flow | BLOCKED | written in `test_booking_rules.py` |
| CI workflow | NOT TESTED | `.github/workflows/ci.yml` written; the repository has not been pushed |
| Deployment / infrastructure | NOT TESTED (out of scope) | nothing deployed; no external system modified |

## Commands run (from `api/`)

```text
uv sync
uv run ruff format --check .      # also `ruff format .` while iterating
uv run ruff check .
uv run mypy
uv run pytest -q                  # 66 passed, 61 skipped (BLOCKED)
uv run pytest -q --collect-only -m postgres       # 62 collected
uv run pytest -q --collect-only -m "not postgres" # 65 collected
uvx --python 3.11 --with pglast python <scratch>/pgparse.py src/gorgona_booking/db/migrations/*.sql
git diff --cached --check          # before each commit
```

Environment: CPython 3.14.6, uv 0.12.3, Windows 11. Resolved: FastAPI 0.142.1, Starlette 1.7.0, Pydantic 2.13.5, psycopg 3.3.6, psycopg-pool 3.3.3, pytest 9.1.1, anyio 4.15.1, mypy 2.3.1, ruff 0.16.9.

## PostgreSQL tests: written, not executed

| File | Tests | What it proves once run |
| --- | --- | --- |
| `test_tenant_isolation.py` | 13 | Four-verb isolation with two fake salons and the real runtime role; no-context sees nothing; composite FK blocks cross-salon references; context does not leak through a pooled connection (same backend PID) or survive a failed transaction; runtime cannot create tenants or rewrite host routes; IANA zone validation; every `tenant_id` table has RLS enabled **and forced** |
| `test_roles_and_migrations.py` | 13 | Runtime passes the guard; owner and superuser are refused; runtime cannot disable/unforce RLS, drop policies, `SET ROLE` owner, read migration history or create tables; readiness uses the real role; re-migration is a no-op; edited migrations are detected; migrating as superuser is refused |
| `test_catalog.py` | 12 | Six variant CHECKs by constraint name (bookable ⇒ duration, bookable ⇒ published, duration range, display range, price, currency); unknown-duration variant cannot be made bookable; add-on duration CHECK; revision bump; repository → quote round trip; catalog invisible across salons; FK blocks cross-salon components |
| `test_booking_rules.py` | 16 | Adjacent `[10:00,11:00)`+`[11:00,12:00)` allowed; partial and containment overlap (both directions) rejected; other resource and other salon allowed; cancelled and expired no longer block; expiry is transactional and audited; sweeper; confirm re-checks expiry; HOLD vs CONFIRMED same rule; lifecycle trigger and grants stop direct SQL; unknown duration cannot be held; add-on extends the interval; salons cannot touch each other's bookings; idempotent retry, key reuse and replayed conflict; full HTTP flow |
| `test_booking_concurrency.py` | 7 (+1 helper check that ran) | **Raw SQL**, 100 independent connections, barrier before the allocation insert, no app locking: exactly 1 commit, every other attempt `23P01` (or `40P01`, see below), 1 blocking allocation, 0 overlapping pairs, no orphan bookings. **Service**, 100-connection pool, 100 distinct keys: 1 success, 99 `SlotConflictError`, no other exceptions, 100 idempotency rows (1×201, 99×409). Two service instances with separate pools. 50 HOLD vs 50 CONFIRMED. Race over an expired hold (expired exactly once). Confirm racing 20 new holds. 20 racers with one idempotency key → 1 booking |

### What the first real run must confirm

These are the assumptions most likely to surface on first execution. Each is covered by the tests above:

- `btree_gist` opclass resolution for `uuid` inside the exclusion constraint (extension installed in `public`).
- The composite FK `ON UPDATE CASCADE` carrying status into allocations while RLS is forced on both tables. PostgreSQL runs RI actions as the owner without forced RLS, but this has not been observed here.
- psycopg sending a parameter-less multi-statement migration file in one `execute()`.
- `INSERT … ON CONFLICT DO UPDATE … WHERE` returning rowcount 0 for a live idempotency key.
- **Raw-SQL deadlocks.** Without application-level ordering, concurrent overlapping inserts can deadlock (`40P01`). The raw test accepts `40P01` as a non-committing outcome and asserts the invariant (exactly one reservation). The service test requires 99 clean `23P01` conflicts, which the per-resource advisory lock is designed to guarantee (ADR-0003).

## Design decisions made during M1 (beyond the plan)

- Migrations live in the package (`gorgona_booking/db/migrations/`) so they ship with any install. ADR-0001 and the plan were updated.
- Per-resource `pg_advisory_xact_lock` before occupancy changes, to avoid deadlocks under contention. It orders the contenders; the exclusion constraint still decides (ADR-0003).
- Booking audit events are written by a trigger from transaction-local `gba.actor` / `gba.reason` / `gba.request_id`, so no transition can skip the audit.
- Host routing (`gba.tenant_hosts`) is readable without tenant context by design. It holds hostnames and IDs only, and the runtime role cannot write to it.
- The app refuses to start with `GBA_ENV=staging|production`, because M1 has no authentication or rate limiting.

## Remaining blockers and open items

1. **PostgreSQL 18 on this machine (or CI).** Install Docker Desktop, or a native PostgreSQL 18, then run the commands in `docs/DEVELOPMENT.md`. Alternatively, push so CI runs them. Until then every database gate stays BLOCKED.
2. **GitHub push.** `origin` points to `alexeyalexandrov2026-tech/KA-nails`, which is **public** and empty; the handoff specified private. Nothing has been pushed. It needs owner confirmation of visibility, then an authenticated push.
3. **OCI.** The `GORGONA` CLI session is expired. No infrastructure work was attempted, per scope.
4. **Owner business facts.** Booking durations (including base Hammam), hours, staff, location/timezone, deposit/cancellation/tax policy and domain. None were invented. The candidate catalog is `owner_unconfirmed` and nothing in it is bookable.
5. **Not in M1:** authentication and guest capabilities, rate limiting, schedules/working hours and availability search, payments/deposits, notifications/outbox, rescheduling, multi-resource bookings, a cross-tenant expiry scheduler, idempotency-key cleanup, catalog versioning.
