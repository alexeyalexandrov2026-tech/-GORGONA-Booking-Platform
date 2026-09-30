# Development

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) (installs CPython 3.14 on demand).
- A PostgreSQL 18 server for integration tests. The simplest route is Docker via `infra/local/compose.yaml`; any PostgreSQL 18 reachable with a superuser DSN works. The application itself never needs Docker.
- Node.js 24 for the M3 customer export and Chromium browser checks.

## Customer booking (M3)

```bash
cd web
npm ci --ignore-scripts
npm run typecheck
npm run lint
npm run format:check
npm run build
npx playwright install chromium
```

The build exports `web/out/`. Set `GBA_CUSTOMER_WEB_DIR` to its absolute path when starting the existing API with its **runtime-role** DSN, then visit `/book/` on the salon's configured host. API routes are registered before static files. Unknown, suspended and not-live hosts receive the unchanged safe 404 from the bootstrap API; the web shows an unavailable state. No tenant ID URL parameter or header is required or trusted.

The real browser gate creates disposable FAKE salons and a throwaway PostgreSQL 18 database. It does not modify KA Nails or the persistent development database:

```bash
cd api
GBA_REQUIRE_POSTGRES=1 GBA_REQUIRE_BROWSER=1 uv run --env-file ../.env pytest -q
```

PowerShell: set `$env:GBA_REQUIRE_POSTGRES='1'` and `$env:GBA_REQUIRE_BROWSER='1'`, then run the same `uv` command. Use your actual private env-file path; never put credentials in the repository. Browser tests launch the existing FastAPI app on a random loopback port, execute `web/tests/booking.spec.ts`, verify confirmed records via SQL, stop the server and drop the test database. Without `GBA_REQUIRE_BROWSER=1` the browser test is explicitly BLOCKED/skipped; this is not an M3 acceptance run.

For a live test fixture, location timezone/hours, artist hours, artist/service eligibility and versioned policies are explicit FAKE values in `tests/integration/customer_support.py`. Consult ADR-0011 for the customer policy contract. Do not transplant those values into KA Nails. Required deposits are unsupported and fail closed. `npm run dev` alone is not an integrated tenant environment; the acceptance path uses the static export and API on the same Host.

Tenant assets are owned by the independent site repository, not bundled as a platform default. KA-nails verifies the supplied logo bytes with SHA-256 in its acceptance suite. A published logo reference can use `/assets/ka-nails-logo.png`; an optional palette reference can use a validated `#RRGGBB` accent. Branding references are supplied explicitly and never selected by a hard-coded tenant ID.

## Deployment readiness (M4)

**Embedding allowlist.** The customer web may be framed only by origins the owner approves per tenant (migration 0007, audited, FORCE RLS). Every HTML response carries `Content-Security-Policy: frame-ancestors 'self' <approved>`; API responses carry `frame-ancestors 'none'`. With no approved origin, `X-Frame-Options: SAMEORIGIN` is added too. Plain `http://` origins are accepted only for loopback, and only in `local`/`test`/`ci`.

```bash
cd api
uv run --env-file ../.env gba-db embed-origin list <tenant-slug>
uv run --env-file ../.env gba-db embed-origin add <tenant-slug> https://www.example.test
uv run --env-file ../.env gba-db embed-origin revoke <tenant-slug> https://www.example.test
```

**Behind Azure Front Door.** Set `GBA_TRUSTED_PROXY=azure_front_door` and `GBA_FRONT_DOOR_ID=<profile frontDoorId>`. Every request except `/health/live` and `/health/ready` must then carry exactly one matching `X-Azure-FDID`. The tenant host is taken from `X-Forwarded-Host`; anything else is a 404 `TENANT_NOT_FOUND`. The default (`none`) ignores forwarded headers. `GBA_ENV=staging` starts only with a configured OIDC provider and this Front Door mode; `production` always refuses to start.

**Observability.** Logs are JSON lines with an allowlist of fields, never customer data. Telemetry is exported to Application Insights only when `APPLICATIONINSIGHTS_CONNECTION_STRING` is set; in Azure the exporter authenticates with the managed identity in `AZURE_CLIENT_ID`.

Opt-in gates (each fails, never skips, once enabled):

| Gate | Enable | Proves |
|---|---|---|
| Container | `GBA_REQUIRE_CONTAINER=1`, `GBA_CONTAINER_IMAGE=<tag>` (build: `docker build -t gorgona-api:local .` at the repo root) | The production image runs bootstrap/migrate jobs, is non-root with no baked secrets, serves health, `/book/` and a real booking, and exits cleanly within the grace period |
| Tenant site | `GBA_REQUIRE_TENANT_SITE=1`, `GBA_TENANT_SITE_DIR=<KA-nails checkout>` (build `web/` first) | An independent site embeds the real wizard through public configuration only; confirmed rows and tenant isolation are verified |

Load baseline (public customer API only; creates bookings only for a tenant named `FAKE ...`; non-loopback targets need `--allow-remote`):

```bash
cd api
uv run python tools/load_baseline.py --base-url http://127.0.0.1:8000 \
  --host <fake-salon-host> --day <YYYY-MM-DD> --out load-report.json
```

It reports throughput, p50/p95/p99 and status/error codes per operation, and a contention check where exactly one concurrent hold may win. It exits 1 on any 5xx, a transport error, or a contention violation.

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

## Local database and API

```bash
cd api
uv run --env-file ../.env gba-db bootstrap            # superuser: roles + database
uv run --env-file ../.env gba-db migrate              # owner: apply migrations
uv run --env-file ../.env gba-db check-runtime-role   # runtime role cannot bypass RLS
uv run --env-file ../.env python -m gorgona_booking   # API on 127.0.0.1:8000
```

Tenants, host mappings and catalog data are provisioned by the owner role (see `gorgona_booking.db.provisioning`). Nothing seeds KA Nails data: `api/fixtures/ka_nails_catalog.candidate.json` is owner-unconfirmed and never loaded into a database.

`POST /v1/holds` resolves the salon from the `Host` header through `gba.tenant_hosts`. It has no authentication or rate limiting and must not be exposed publicly.

## Authentication (M2)

Staff and admin routes need an external OIDC identity provider (ADR-0007). Set all three or none:

```
GBA_AUTH_ISSUER=https://<your-idp>/
GBA_AUTH_AUDIENCE=<api audience>
GBA_AUTH_JWKS_URL=https://<your-idp>/.well-known/jwks.json
# optional: GBA_AUTH_ALGORITHMS=RS256,ES256   GBA_AUTH_LEEWAY_SECONDS=30
```

Without them, protected routes answer `503 AUTH_NOT_CONFIGURED`. Tests use a FAKE IdP (`tests/support/fake_idp.py`) with local keys and no network.

## Salon onboarding (M2, owner credential)

```bash
cd api
uv run --env-file ../.env gba-db onboard fixtures/ka_nails_onboarding.candidate.json
uv run --env-file ../.env gba-db readiness ka-nails        # exit 2 while facts are missing/unconfirmed
uv run --env-file ../.env gba-db go-live ka-nails         # refuses until readiness passes
uv run --env-file ../.env gba-db link-user --issuer https://<idp>/ --subject <sub> --display-name "<name>"
uv run --env-file ../.env gba-db grant-platform-admin --issuer https://<idp>/ --subject <sub>
```

A spec that invites the first owner needs `--invitation-token-file PATH`. The token is written there once and never printed. Only its SHA-256 is stored.

## Credentials

| Credential | Used by | Never used by |
| --- | --- | --- |
| Superuser (`GBA_ADMIN_DATABASE_URL`, `GBA_TEST_ADMIN_DSN`) | bootstrap, test fixture | migrations, the API |
| Owner (`GBA_MIGRATION_DATABASE_URL`) | migrations, provisioning | the API |
| Runtime (`GBA_DATABASE_URL`) | the API | DDL. It is refused at startup if it is a superuser, `BYPASSRLS`, or a member of the table owner |

## Windows

psycopg's async driver needs a selector event loop. The test suite and `uv run python -m gorgona_booking` set one up. Production runs on Linux.

Without Docker, the official EDB PostgreSQL 18 Windows binaries ZIP works as a disposable local server (`initdb --pwfile`, `listen_addresses = '127.0.0.1'`, `max_connections = 250`). Start it **detached**, e.g. PowerShell `Start-Process pg_ctl.exe -ArgumentList 'start','-D',<dir>,'-l',<log>,'-w' -WindowStyle Hidden`. If the shell or console that ran `pg_ctl start` is closed, new backends can fail with `0xC0000142` / `could not reserve shared memory region … error code 487`, and every connection then drops with "server closed the connection unexpectedly".

Repository ownership, hosted embed and tenant asset routing are documented in `architecture/REPOSITORY_SEPARATION.md`.
