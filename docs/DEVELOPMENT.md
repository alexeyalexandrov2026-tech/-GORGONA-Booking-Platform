# Development

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) (installs CPython 3.14 on demand).
- A PostgreSQL 18 server for integration tests. The simplest route is Docker via `infra/local/compose.yaml`; any PostgreSQL 18 reachable with a superuser DSN works. The application itself never needs Docker.

## Checks (no database needed)

```bash
cd api
uv sync
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```

Without `GBA_TEST_ADMIN_DSN`, tests marked `postgres` are **skipped with a BLOCKED reason**. They are never replaced by SQLite or mocks. CI sets `GBA_REQUIRE_POSTGRES=1`, which turns a missing database into a failure.

## Local PostgreSQL 18

From the repository root:

```bash
cp .env.example .env    # then replace every password
docker compose --env-file .env -f infra/local/compose.yaml up -d
```

Integration tests create a throwaway database per run, bootstrap the test roles, apply migrations as the owner role and run every assertion as the non-owner runtime role:

```bash
cd api
uv run --env-file ../.env pytest
```

Set `GBA_TEST_KEEP_DB=1` to keep the test database for inspection.

## Credentials

| Credential | Used by | Never used by |
| --- | --- | --- |
| Superuser (`GBA_ADMIN_DATABASE_URL`, `GBA_TEST_ADMIN_DSN`) | bootstrap, test fixture | migrations, the API |
| Owner (`GBA_MIGRATION_DATABASE_URL`) | migrations, provisioning | the API |
| Runtime (`GBA_DATABASE_URL`) | the API | DDL. It is refused at startup if it is a superuser, `BYPASSRLS`, or a member of the table owner |

## Windows

psycopg's async driver needs a selector event loop. The test suite and `uv run python -m gorgona_booking` set one up. Production runs on Linux.
