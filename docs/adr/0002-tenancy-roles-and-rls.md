# ADR-0002: Shared-schema tenancy, separated roles and forced RLS

- Status: Accepted (2026-09-30)
- Scope: every tenant-owned table

## Context

KA Nails is the first tenant (salon) of a multi-tenant platform. A leak between salons is the most damaging class of bug, and application checks alone are one missed `WHERE` away from it.

## Decision

- Shared schema `gba`. Every tenant-owned table has `tenant_id uuid not null` and primary key `(tenant_id, id)`. References between tenant-owned tables are **composite foreign keys** `(tenant_id, x_id)`, so a row can never point at another salon's row even if an ID leaks.
- Database roles:
  - an **owner/migration** login role owns the schema and tables and runs migrations;
  - `gba_runtime` is a NOLOGIN group that holds the runtime grants (DML only; no DDL, no ownership);
  - a **runtime** login role is a member of `gba_runtime` and nothing else, and is `NOSUPERUSER NOBYPASSRLS`.
- `ENABLE` **and** `FORCE ROW LEVEL SECURITY` on every table with a `tenant_id` column. A test asserts this for all such tables, including future ones.
- Policies compare `tenant_id` with `gba.current_tenant_id()`, which reads the transaction-local setting `gba.tenant_id`. Without context it returns NULL and every tenant-owned query sees zero rows.
- The application sets context only with `set_config('gba.tenant_id', $id, true)` inside an explicit transaction. The setting is discarded at commit or rollback, so it cannot leak to the next borrower of a pooled connection.
- At startup and on readiness, the app checks its own role. It refuses to run as a superuser, a `BYPASSRLS` role, a member of a table owner, or a role outside `gba_runtime`.
- HTTP tenant resolution uses `gba.tenant_hosts` (host → tenant), which is readable without tenant context by design. It only maps hostnames to IDs, and the runtime role cannot write to it.

## Consequences

- Provisioning (tenants, host mappings, seed data) is done by the owner role. Forced RLS applies to the owner too, so provisioning sets tenant context explicitly.
- Jobs that span tenants need an explicit tenant list and run per tenant. M1 has no cross-tenant scheduler.
- Integration tests prove SELECT, INSERT, UPDATE and DELETE isolation with two fake salons and the real runtime role.
