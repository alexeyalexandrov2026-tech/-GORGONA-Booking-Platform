# M1 plan — local booking foundation

Prepared 2026-09-30 from `CLOUD_CODE_HANDOFF.md`, `docs/architecture/*` and `source/PRODUCT_BRIEF.txt`, then revised the same day with the owner's baseline decisions (below). This is a plan. What is actually built and tested is recorded in `docs/plan/M1_REPORT.md`.

## Owner baseline decisions (2026-09-30)

These are binding for M1 and recorded as ADRs in `docs/adr/`.

1. **Runtime baseline.** Python 3.14. PostgreSQL 18 for local development, tests and CI, unless a verified OCI deployment constraint later forces 17. FastAPI. Psycopg 3 with async connections and `psycopg_pool`. Explicit, versioned SQL migrations. Strongly typed domain and API contracts. No ORM unless a demonstrated benefit appears.
2. **Roles and tenant isolation.** The migration/owner credential and the runtime credential are separate roles. Runtime code never connects as the table owner, a superuser, or a `BYPASSRLS` role, and the app refuses to start if it does. Every tenant-owned table carries `tenant_id` (the salon). RLS is enabled **and forced** on every tenant-owned table. Tenant context is transaction-scoped (`set_config(..., true)`) and cannot leak through pooled connections. Integration tests use two fake salons and the real non-owner runtime role, and cover SELECT, INSERT, UPDATE and DELETE.
3. **Booking concurrency.** PostgreSQL is the final authority against double booking. Occupancy is a half-open `tstzrange` `[start, end)`. A GiST exclusion constraint (with `btree_gist`) enforces tenant equality, resource equality and range overlap (`&&`), but only for capacity-reserving states (`HOLD`, `CONFIRMED`). Hold expiry never uses `now()` in a constraint or index predicate. Expired holds transition out of the blocking set transactionally. SQLSTATE `23P01` on that constraint maps to a clean `SLOT_CONFLICT` domain error and an HTTP 409.
4. **Concurrency proof.** A real PostgreSQL instance with 100 independent connections synchronized to race for the same salon/resource/range must give exactly 1 committed reservation, 99 clean conflicts, no duplicate occupancy, no uncaught database exceptions and a consistent database afterwards. Also covered: adjacent ranges allowed; partial and containment overlap rejected; different resource allowed; another salon allowed; cancelled/expired no longer block; HOLD vs CONFIRMED under the same rule.
5. **Idempotency** is separate from double-booking protection. A retry with the same key recovers the same logical result. Different keys racing for one slot are still resolved by the exclusion constraint.
6. **Bookability.** A service variant or add-on cannot be bookable until its booking duration is known and valid. This is enforced in the domain and by database CHECK constraints. No real KA Nails duration is invented; tests use fake fixture data that is clearly marked.
7. **Local PostgreSQL.** A reproducible Docker Compose definition (PostgreSQL 18) and a CI workflow are provided. The application does not depend on Docker. If Docker cannot run, all non-database work is completed and the PostgreSQL tests are reported **BLOCKED**, never replaced by SQLite or mocks.
8. **Architecture.** OCI remains the production target. Supabase remains deferred. Cloudflare remains optional. The supplied diagram is reference material; later OCI-first decisions win where it conflicts.
9. **Commits** are small, reviewable slices, with tests, lint/type checks and `git diff --check` after each.

## Verified starting state (2026-09-30)

| Check | Status | Evidence |
| --- | --- | --- |
| Local Git | PASS | Phase 0 commit `6b9a0f3`; M1 work on branch `m1-foundation` |
| Logo integrity | PASS | `assets/brand/ka-nails-logo.png` SHA-256 `bb2fe1c0…3fbf53` matches the handoff |
| Architecture diagram | RESOLVED | Supplied by the owner; `assets/architecture/ka-nails-architecture-diagram.webp` (SHA-256 `fc32152c…908878`) |
| GitHub | REMOTE SET, NOT PUSHED | `origin` = `https://github.com/alexeyalexandrov2026-tech/KA-nails.git`. The repository exists but is **public** and empty, while the handoff specified private. Nothing is pushed until the owner confirms visibility |
| OCI access | BLOCKED | `oci session validate --profile GORGONA` → session expired |
| Local PostgreSQL | BLOCKED | No Docker, no WSL distribution, no PostgreSQL install on this machine |
| Toolchain | PASS | CPython 3.14.6 (via `uv`), `uv` 0.12.3, git 2.55.0 |

## Repository layout

```
api/                      Python 3.14 project (uv)
  src/gorgona_booking/    application package
    db/migrations/        explicit, ordered, checksummed SQL migrations (shipped in the package)
    api/                  FastAPI app, routes, error envelope
    catalog/              quote engine (pure) + repository
    booking/              hold/booking service, idempotency, repository
    db/                   bootstrap, migration runner, pool, tenant transactions, role guard
  fixtures/               owner-unconfirmed candidate catalog (never seeded)
  tests/unit/             no database
  tests/integration/      real PostgreSQL 18 only (marker: postgres)
infra/local/compose.yaml  PostgreSQL 18 for local development
.github/workflows/ci.yml  lint, types, unit and PostgreSQL integration tests
docs/adr/                 architecture decision records
```

## Slices and commits

| Commit | Content | Tests |
| --- | --- | --- |
| 1 | Skeleton, tooling (ruff, mypy strict, pytest/anyio), config, FastAPI app with real `/health/live` and `/health/ready`, error envelope, Compose, CI, ADRs, this plan | Unit |
| 2 | Bootstrap CLI (roles, database), migration runner, `0001_tenancy.sql` (tenants, hosts, locations, memberships, RLS forced, grants), pool + transaction-scoped tenant context, runtime-role guard | Unit + integration: four-verb isolation, FK injection, pooled-connection leak, role guard, all-tables-forced check |
| 3 | `0002_catalog.sql` (components, services, variants, add-ons, rules; bookable ⇒ duration CHECK), pure quote engine, catalog repository, candidate KA Nails fixture (nothing bookable) | Unit: composition/pricing/bookability. Integration: DB CHECKs, catalog RLS |
| 4 | `0003_booking.sql` (resources, bookings, allocations with GiST exclusion and status cascade, transition trigger, events, idempotency keys), booking service, `POST /v1/holds`, 23P01 mapping | Unit: error mapping, API envelope |
| 5 | Integration and concurrency tests (100-way race via raw SQL and via the service, overlap matrix, expiry, HOLD vs CONFIRMED, idempotency), `M1_REPORT.md` | Integration |

## Explicitly out of M1

Deployment of any kind; DNS; public UI; authentication and customer/staff sessions; payments and deposits; notifications and outbox consumers; AI; analytics; staff schedules, working hours and availability search; multi-resource bookings (the allocation table is ready for it); rescheduling; a cross-tenant hold-expiry scheduler. `POST /v1/holds` is a local development surface only, with no auth or rate limiting, and must not be exposed publicly.

## M1 exit criteria

All unit and PostgreSQL 18 integration tests green on the same commit, locally or in CI. Isolation and concurrency gates recorded as PASS with command output. No KA Nails fact published as bookable. `M1_REPORT.md` lists exact changes, commands, results and blockers.

## Owner decisions still open

1. Repository visibility: make `KA-nails` private (recommended, as the handoff specified) before the first push, or confirm public is intended.
2. A PostgreSQL route for this machine: Docker Desktop (recommended), a native PostgreSQL 18 install, or CI only once pushed.
3. Business facts, which M1 does not need: location and IANA timezone, hours, staff/skills, base Hammam booking duration, deposit/cancellation/tax rules, domain, photo rights, Square's role, OCI budget and compartment.
