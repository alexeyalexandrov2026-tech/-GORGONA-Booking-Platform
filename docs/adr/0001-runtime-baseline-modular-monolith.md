# ADR-0001: Runtime baseline and modular monolith

- Status: Accepted (2026-09-30, owner baseline decision)
- Scope: whole backend

## Context

GORGONA Booking AI needs a transactional booking core first; the AI concierge, notifications and payments come later. The team is small and there is no measured need for independently deployed services.

## Decision

- One deployable Python backend (a modular monolith) with modules for tenancy, catalog, booking and, later, payments, notifications and AI tools. Modules talk through typed in-process interfaces, not the network.
- Python **3.14**, **FastAPI**, **Pydantic v2** for API contracts, frozen dataclasses for domain values.
- **PostgreSQL 18** for local development, tests and CI. Drop to 17 only if a verified OCI deployment constraint requires it.
- **Psycopg 3** with async connections and `psycopg_pool.AsyncConnectionPool`.
- **Explicit SQL migrations** (`api/migrations/NNNN_name.sql`), applied in order, each in its own transaction, recorded with a SHA-256 checksum so edited history is detected. No ORM unless a demonstrated benefit appears.
- `mypy --strict`, `ruff` lint and format, `pytest` with the `anyio` plugin.

## Consequences

- SQL is reviewed as SQL. Constraints, RLS policies and grants are visible in one place.
- Query-to-domain mapping is written by hand in repositories. That is more code, but the database stays the source of truth.
- On Windows, psycopg async needs a selector event loop; tests and `python -m gorgona_booking` configure it. Production targets Linux.
