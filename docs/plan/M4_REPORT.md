# M4 report: Azure cloud foundation, checkpoints A and B

Date: 2026-09-30 (America/New_York). Scope: checkpoints A and B of [`M4_PLAN.md`](M4_PLAN.md).

- **Checkpoint A:** design, IaC authored and validated locally, tooling, read-only discovery and the M3 baseline rerun.
- **Checkpoint B:** local application, container, observability, IaC and pipeline changes, plus KA decoupling.

**No Azure resource was created, changed, previewed or deployed. Nothing was pushed.**

## Gates

| Gate | Status | Why |
|---|---|---|
| **M3 APPLICATION BASELINE** | **PASS** | Rerun at the M4 start, and every M3 behaviour is still covered by the current full suite (see Validation) |
| **M4 AZURE FOUNDATION GATE** | **FAIL (pending Azure evidence)** | Checkpoint B is complete locally. The gate needs checkpoint C: read-only discovery (blocked on sign-in), approved resource creation and a staging window |
| **AZURE STAGING ACCEPTANCE** | **NOT DEPLOYED** | No staging window has been approved |
| **PRODUCTION IFRAME RELEASE GATE** | **BLOCKED** | The M3 clickjacking finding is **fixed in code**: a governed per-tenant `frame-ancestors` allowlist, with the unapproved origin blocked and the approved origin allowed in real Chromium. The gate still needs staging evidence through Front Door, and an owner-approved KA production origin (none is approved) |
| **PRODUCTION DEPLOYMENT** | **NOT AUTHORIZED** | Refused in code at process start and by the `promote-production` workflow |
| **KA NAILS GO-LIVE** | **NOT AUTHORIZED** | KA remains `not_live`; business facts are unconfirmed |

## Owner decisions recorded

- **Region:** East US 2.
- **Staging:** time-boxed production-parity, ephemeral and IaC-controlled, with budgets.
- **AI plane:** a persistent 24/7 AI learning plane, independent of staging (ADR-0013).
- **Start guard:** staging is allowed only when conditions are met; production stays refused in code.
- **Tooling:** Azure CLI, Bicep and Docker may be installed.
- **Actions:** every Azure action needs separate explicit approval.

## Repository state

| | GORGONA | KA Nails |
|---|---|---|
| Path | `C:\Users\alexa\Documents\Codex\2026-09-30\also-create-new-project-in-a-2\outputs\gorgona-booking-ai` | `C:\Users\alexa\Documents\Codex\2026-09-30\referenced-chatgpt-conversation-this-is-an\work\ka-nails-public` |
| Branch / start HEAD | `m2-identity` @ `b5e59ce` (clean) | `main` @ `f131b0b` (clean) |
| Checkpoint A commits | `2736772` docs, `fc283f4` infra, `9b13e9f` report | none |
| Checkpoint B commits | `99c6e7d`, `9d81f51`, `49fba22`, `325cf00`, `37a930b`, `c9605ad`, `e661cdf`, `f16323e`, `861de38`, plus the commit adding this report | `4df7455` |
| Remote | `main` = `784312d` "Initial commit"; **UNRELATED HISTORIES**; not synchronized | empty |
| Push | **Not performed** | **Not performed** |

- Checkpoint A added the docs (ADR-0012, ADR-0013, `AZURE_ARCHITECTURE.md`, `M4_PLAN.md`, this report) and `infra/azure/` (32 files).
- Checkpoint B changed application code, one additive migration (`0007`), the container, IaC modules, workflows and docs, as listed below.
- Migrations 0001–0006 are unchanged; the 0006 SHA-256 is still `cd315f8f…6eb9576`.

## Tooling (installed with owner approval)

| Tool | Version | Evidence |
|---|---|---|
| Azure CLI | 2.90.0 | winget; installer hash verified |
| Bicep CLI | 0.47.16 | `az bicep install` |
| Docker Desktop | client 29.8.1 | winget; installer hash verified. The owner completed the first launch; the engine ran every container gate in checkpoint B |

## Azure discovery (read-only, 2026-09-30, after the owner's `az login`)

Only read calls were made. IDs are masked.

| Item | Result |
|---|---|
| Subscriptions | One ("Azure subscription 1"), `Enabled`, default. Not ambiguous |
| Offer | `FreeTrial_2014-09-01`, **spending limit On**: services are disabled rather than billed when credit is exhausted or expires |
| Billing account | Microsoft Customer Agreement, individual, active |
| Credit | Azure sign-up credit **$200.00, $200.00 remaining**, started 2026-09-30 14:05 UTC, **expires 2026-10-30 14:05 UTC** |
| Existing resources | One resource group `rg-alexandrov20211992-0316` (westus3): an Azure AI Services account (S0) and one AI Services project. **Not part of GORGONA; not touched.** It is pay-per-use and can draw on the same credit |
| Budgets | None |
| PostgreSQL 18 in East US 2 | **CORRECTION: not available to this subscription.** The first read happened before `Microsoft.DBforPostgreSQL` was registered and returned the generic regional offer (versions 11-18). After registration, the capability API reports `OfferRestricted: Enabled`, an empty version list, and "Subscriptions are restricted from provisioning in this region". The first AI deployment failed accordingly (`ParameterOutOfRange: The value of the 'Version' should be in: []`) |
| Resource providers | Registered: ManagedIdentity, Consumption, Resources. **Not registered:** App, Cdn, DBforPostgreSQL, KeyVault, Network, ContainerRegistry, OperationalInsights, Insights, Storage, ServiceBus, MachineLearningServices, Compute |
| Quotas | Not readable until the providers are registered (vCPU and PostgreSQL usage returned empty) |

### Deployment log (owner-approved actions, 2026-09-30 / 2026-10-01 UTC)

| Step | Result |
|---|---|
| Budgets stack `gorgona-budgets` | **Created.** $100/month from 2026-10-01; actual 25/50/80/100% plus forecast 100%; daily cost-anomaly alert. The first attempt hit a transient `DeploymentStackTenantRegistrationFailed`; the retry succeeded |
| Resource providers | **Registered:** App, Cdn, Network, DBforPostgreSQL, KeyVault, ContainerRegistry, OperationalInsights, Insights, Storage, ServiceBus, MachineLearningServices, plus Quota (needed for the quota request) |
| Shared stack (East US 2) | **Created:** Log Analytics, Application Insights, ACR, delete lock. The image `gorgona-api@sha256:dc5e7986…d7a5` (commit `5e8e37f`, container gate 3/3 on that exact image) was pushed by digest |
| AI stack (East US 2) | **Partial.** 25 resources created: VNet, DNS zones, evidence and ML storage, both Key Vaults, Service Bus, ML workspace, CPU cluster (after fixing the VM size to `Standard_D4ds_v5`) and private endpoints. **PostgreSQL failed** (offer restriction). The jobs environment was not created (quota) |
| Staging stack (East US 2) | **Blocked at Front Door, as designed to fail fast:** `BadRequest: Free Trial and Student account is forbidden for Azure Frontdoor resources.` Only free resources exist: 2 managed identities and an unattached WAF policy |
| Quota increase, Container Apps environments 1 → 2 (East US 2) | **Refused before evaluation:** `MFARequired: Quota requests needs Multi-Factor Authentication.` It is pending the owner's MFA sign-in |

### Central US validation (read-only, 2026-10-01; owner decision to move the platform)

| Check | Result |
|---|---|
| PostgreSQL 18 Flexible Server | Offered, `OfferRestricted: Disabled`, versions 11-18 |
| SKUs | `Standard_B1ms` and `Standard_D2ds_v5` in zones 1-3; HA modes SameZone and ZoneRedundant |
| PostgreSQL quota | 16 cores (B-series and DDSv4 family counters at 0/16) |
| Azure ML `Standard_D4ds_v5` | Supported (AmlCompute). **Quota 4 vCPUs** (standardDDSv5Family), so the CPU cluster max nodes is set to 1 |
| Container Apps | Available; workload profiles available. **ManagedEnvironmentCount limit 1** (same as East US 2) |
| Front Door Private Link | Central US is a supported Private Link region; Azure Container Apps is a supported origin type |
| Availability zones | 3; the paired region is East US 2 |
| Services | App environments/jobs, PostgreSQL, Key Vault, Service Bus, Storage, ML workspaces, private endpoints, ACR, Log Analytics and App Insights are all offered |
| Names | The new regional suffix is free for ACR, both storage accounts and all three vaults |
| Price delta vs East US 2 | PostgreSQL D2ds_v5 $0.201/h vs $0.178/h; B1ms $0.0192/h vs $0.0170/h; ML D4ds_v5 $0.255/h vs $0.226/h; ACR, Container Apps and Log Analytics equal |

Still blocked in any region on this subscription: **Front Door (Free Trial)** and a **second Container Apps environment (quota; request needs MFA)**.

### Finding: Azure Front Door is not available on this Free Trial (STOP condition)

- Microsoft's Front Door subscription-offers page says Standard/Premium profiles are bandwidth-throttled on free and trial subscriptions. On pay-as-you-go, throttling lasts until the first payment establishes good standing.
- A Microsoft Q&A answer from Microsoft staff (2024) reports that creation on a free trial fails with "Free Trial & Student Account is forbidden for Azure front door resources".

The production-parity staging topology (Front Door Premium -> Private Link -> Container Apps) therefore **cannot be created on this subscription as it stands**. This is the M4 stop condition "unexpected paid-resource requirement / topology unsupported". Work stopped here pending an owner decision:

- **(a) Upgrade to pay-as-you-go.** Owner action in the portal; removes the spending limit, so real billing becomes possible; remaining credit still applies until 2026-10-30. Front Door may be throttled until the first payment. Full-parity staging becomes possible.
- **(b) Reduced-scope staging on the trial.** Everything except Front Door. This proves bootstrap as the non-superuser admin, migrations 0001-0007, RLS, runtime role, container jobs, Key Vault references, managed identities, telemetry ingestion and alerts. The Front Door, WAF, Private Link and framing-through-Front-Door evidence is deferred. The production architecture is not changed, and the iframe release gate stays BLOCKED.
- **(c) Wait.** No staging until the owner chooses (a); only the persistent shared/AI plane, if approved.

### Finding: zone-redundant HA unavailable

The production design uses zone-redundant PostgreSQL HA. This subscription reports it as disabled in East US 2. Production is not authorized, so nothing breaks now. Before production, re-check after any subscription upgrade, or choose same-zone HA or another region by owner decision.

## Platform facts verified in current Microsoft documentation

| Fact | Result |
|---|---|
| PostgreSQL 18 on Flexible Server | **GA, 18.6**. Limits: some extensions unsupported; no `io_uring` |
| `btree_gist` (migration 0001) on PostgreSQL 18 | Supported, 1.8. Must be allowlisted in `azure.extensions` (declared in IaC) |
| `vector` (AI plane) on PostgreSQL 18 | Supported, 0.8.2 |
| Front Door → Container Apps via Private Link | Supported. Needs Front Door **Premium**, a **workload-profiles** environment with public network access disabled, and approval of the private endpoint connection |
| Front Door Private Link in East US 2 | Supported (a region with availability zones) |
| Front Door origin headers | Front Door **overwrites** `X-Forwarded-Host` and adds `X-Azure-FDID`. The Microsoft pattern sends the origin FQDN as `Host` |
| Front Door Premium price | $330/month base, WAF and Private Link included; $0.015 per 10k requests; $0.083/GB egress (first tier) |
| Free account | $200 credit for 30 days; services are disabled when it is exhausted or expires; 12-month free quantities apply |
| Container Apps managed OpenTelemetry agent | Preview-only in the Bicep types. **Not used**: the app will export with the GA Azure Monitor exporter |

The Bicep type check accepts `version: '18'` on `Microsoft.DBforPostgreSQL/flexibleServers@2025-08-01`, and `publicNetworkAccess` on `Microsoft.App/managedEnvironments@2026-01-01` (GA).

**Runtime compatibility is NOT TESTED:**
- bootstrap as the non-superuser Azure admin;
- migrations 0001–0006;
- RLS and runtime-role checks.

It needs a staging window.

## IaC validation (local, no Azure calls)

| Check | Result |
|---|---|
| `az bicep lint` on `main-shared.bicep`, `main-ai.bicep`, `main-platform.bicep` | **0 findings each**. Security rules are errors: secrets in outputs, secure defaults, hard-coded locations/URLs, unused parameters. All API versions are current GA. |
| `az bicep build-params` on all 7 `.bicepparam` files (dummy non-secret environment values) | **7/7 compile, 0 issues** |
| `stack-up.ps1` / `staging-down.ps1` parse check | 0 parse errors |
| Dry runs | Plan and commands printed; **no Azure call** |
| Teardown guard, offline cases | Allows a staging-only set. **Refuses**: an AI-plane resource, look-alike `rg-gorgona-staging2`, the shared ACR itself, and wrong tags |
| Secret scan of all new files | Clean. No keys, tokens, JWTs, connection strings, subscription IDs or emails; DSNs come only from secure parameters |
| `az deployment sub what-if` | **NOT RUN**. Needs sign-in and per-call owner approval |

Design details worth recording:
- **Key Vault access.** Public access is disabled. ARM template deployment may read secrets (trusted-services bypass plus RBAC) so that stack updates can re-supply passwords via `getSecret()` rather than regenerate them. Without that, an update could delete secrets under a `deleteAll` staging stack.
- **Per-secret grants.** The API identity can read only `database-url`. The jobs identity can read only the migration/admin/role-password secrets.
- **Front Door is split** into profile (with WAF) and routing modules, so the app receives the profile ID for the trusted-proxy check before its origin is declared. This avoids a dependency cycle.
- **HSTS** is `max-age=31536000` without `includeSubDomains`: tenant apex and sibling domains are not ours to pin.
- **Evidence storage** is StorageV2 blob without hierarchical namespace, because blob versioning is unavailable with it. The container immutability policy is left **unlocked**; locking is irreversible and an owner decision.
- **Azure ML** gets its own system storage and Key Vault, holding metadata only. A managed-VNet workspace is required before sensitive training data flows (scale later).

## Checkpoint B: what changed

| Slice | Commit | Change | Evidence |
|---|---|---|---|
| Trusted Front Door host boundary | `99c6e7d` | `GBA_TRUSTED_PROXY=azure_front_door` + `GBA_FRONT_DOOR_ID`. The tenant host comes from `X-Forwarded-Host` only when exactly one `X-Azure-FDID` matches (constant-time compare). Only `/health/live` and `/health/ready` are exempt; everything else is a 404 `TENANT_NOT_FOUND` | 22 tests: spoofed forwarded host, missing/wrong/duplicate FDID, malformed host, tenant A vs B, default mode ignores forwarded headers, redirect keeps the tenant host |
| Framing allowlist (**closes the M3 finding in code**) | `9d81f51` | Migration `0007_tenant_embed_origins` (FORCE RLS, audited, origin CHECK). `gba-db embed-origin add\|revoke\|list`. HTML responses carry `frame-ancestors 'self' <approved>`, adding `X-Frame-Options: SAMEORIGIN` when none are approved; API responses carry `'none'`; failures fall back to `'self'` | 18 tests, including RLS isolation and audit. The M3 gap scenario is inverted: real Chromium **blocks** an unapproved loopback embedder and **allows** the approved one; top-level navigation still works |
| Start guard | `49fba22` | `staging` starts only with an OIDC provider **and** Front Door mode with a profile ID; `production` is always refused | Unit tests name the missing condition |
| Production container | `325cf00` | Multi-stage Dockerfile: digest-pinned Node 24, Python 3.14 and uv bases; `uv sync --locked --no-dev`; non-root uid 10001; one image for the API and the `gba-db` jobs; 25 s graceful shutdown | Real-image gate on disposable `postgres:18`: bootstrap job; migrate job twice (second is a no-op); no baked secrets; health/ready; `/book/` CSP; a real booking to CONFIRMED; JSON logs without IDs or email; clean exit in < 25 s. Image 295 MB |
| Observability | `37a930b`, `e661cdf` | JSON logs with allowlisted fields; one access line per request by route template (probes skipped); `gorgona.domain_errors` counter by code and route; OpenTelemetry to Application Insights when configured, **Entra-authenticated** through the API managed identity (local auth is disabled on the component); per-environment service name | Unit tests: no PII or DSN in logs, exception messages dropped, route templates instead of raw paths, counters recorded, credential selection |
| KA decoupling | `c9605ad`, KA `4df7455` | KA no longer imports platform pytest internals or touches PostgreSQL. Its suite reads only `KA_BOOKING_TEST_URL`, `KA_BOOKING_TEST_DAY` and `KA_SITE_PORT`. The platform harness seeds FAKE tenants, approves the loopback origin, runs the site's own `npm` commands and verifies rows | Harness: the KA site's own **10** Chromium scenarios pass; 2 CONFIRMED rows; tenant B untouched; the site is rebuilt without a booking URL afterwards |
| Alerts as IaC | `e661cdf` | `alerts.bicep`: action group, 6 metric alerts, 3 log alerts (AZURE_ARCHITECTURE section 10). `appinsights-publisher.bicep` role grant | Metric names checked against the Azure Monitor supported-metrics reference; GA API versions; lint 0 |
| Load baseline | `f16323e` | `api/tools/load_baseline.py`: read, contention and hold+confirm phases; p50/p95/p99, throughput, error codes; FAKE-tenant and remote-target guards | Real run against the API and PostgreSQL: 6 concurrent holds on one slot gave **exactly 1 winner and 5 clean 409 `SLOT_CONFLICT`**; 0 failures; confirmed rows match the report |
| Pipelines | `861de38` | `ci.yml` builds the image and runs the container gate. `deploy-staging.yml` is dormant (manual; environment approval; OIDC; digest-pinned scanner; open-window flag; migrate, revision, Front Door smoke, automatic rollback). `promote-production.yml` refuses | YAML parses (3/3); `bash -n` on all 19 `run:` blocks is clean. **actionlint not run** (not installed; a download would need approval). **Never executed on GitHub** (no push) |

Design corrections made during checkpoint B:
- **Telemetry authentication.** Checkpoint A set `DisableLocalAuth` on Application Insights but configured only a connection string. That combination would have had telemetry rejected in Azure. It is fixed with a managed-identity credential and a role grant.
- **Alert thresholds.** Thresholds were aligned to what the platform can evaluate:
  - metric windows use allowed values, so "10 min" became 15 min;
  - the connection alert uses the design connection budget, not an unverified SKU `max_connections`;
  - the tenant anomaly compares against the previous 47 h (a log-alert query range limit) instead of 7 days.

## Validation: M3 application baseline (rerun at the M4 start)

| Working directory | Command | Result |
|---|---|---|
| platform `api/` | `uv sync --locked` | PASS, 38 packages |
| platform `api/` | `uv run ruff format --check src tests` / `ruff check src tests` / `mypy src tests` | PASS / PASS / PASS (86 files) |
| platform `api/` | `GBA_REQUIRE_POSTGRES=1 GBA_REQUIRE_BROWSER=1 uv run --env-file <private> pytest -q -s -rs` | **PASS: 232 passed**, 0 skipped; nested **14** Chromium passed |
| platform `web/` | `npm ci --ignore-scripts`, `typecheck`, `lint`, `format:check`, `build` | PASS; 0 vulnerabilities reported; no tenant PNG in `out/` |
| KA root | `npm ci --ignore-scripts`, `typecheck`, `lint`, `format:check`, `build` (unconfigured) | PASS; 0 vulnerabilities reported |
| KA root | `npm run test:e2e -- --grep "website\|unsafe\|unconfigured"` | PASS, 6; logo SHA-256 `bb2fe1c0…3fbf53` unchanged |
| platform `api/` | KA real integration (`tools/test_gorgona_integration.py`) | **PASS: 1 passed**; nested **10** Chromium passed. Rebuilt unconfigured afterwards; no loopback origin in `out/` |

Nested browser scenarios are never added to pytest totals.

## Validation: checkpoint B final (current trees, 2026-09-30)

| Working directory | Command | Result |
|---|---|---|
| platform `api/` | `uv run ruff format --check .` / `ruff check .` / `mypy` | PASS / PASS / PASS (102 files) |
| repo root | `docker build -t gorgona-api:local .` | PASS, 295 MB |
| platform `api/` | `GBA_REQUIRE_POSTGRES=1 GBA_REQUIRE_BROWSER=1 GBA_REQUIRE_CONTAINER=1 GBA_CONTAINER_IMAGE=gorgona-api:local GBA_REQUIRE_TENANT_SITE=1 GBA_TENANT_SITE_DIR=<KA> uv run --env-file <private> pytest -q -s -rs` | **PASS: 291 passed, 0 skipped.** Nested: **16** platform Chromium scenarios; **10** KA-site Chromium scenarios. No leftover smoke containers or networks |
| platform `web/` | `typecheck`, `lint`, `format:check`, `build` | PASS |
| KA root | `typecheck`, `lint`, `format:check`, `build` (unconfigured) | PASS |
| KA root | `npm run test:e2e` (unconfigured) | 6 passed; 6 integration-only scenarios skipped by design (they need `KA_BOOKING_TEST_URL`, supplied by the platform harness) |
| `infra/azure/` | `bicep lint` on the 3 entry points; `bicep build-params` on all 7 parameter files (dummy non-secret values) | 0 findings; 7/7 compile (Bicep 0.47.16) |
| both repos | Secret scan of every commit | Clean. The only GUID added is the public built-in role ID for Monitoring Metrics Publisher |

Pytest totals: 232 at M4 start → 254 → 272 → 276 → 279 → 286 → 287 → 291.

## Azure resources

- **Created:** none.
- **Not created:** every line of the Resource Creation Plan (`M4_PLAN.md`, items 0–22), including budgets.

Estimates (to re-verify after sign-in):
- Persistent shared + AI plane: ~$45–65/month idle.
- Staging: ~$17–19/day while up.
- Production: ~$615/month without HA, ~$760/month with HA.

## Security posture (design)

- **Unchanged:** RLS and runtime-role guard, idempotency, concurrency. Host-resolved tenancy now has the tested Front Door trusted-proxy mode.
- **Planned protections:**
  - no public database or Key Vault;
  - no origin bypass (Private Link + FDID);
  - WAF in Prevention mode with managed and rate-limit rules;
  - managed identities with least-privilege secret grants;
  - no Azure secrets in GitHub (OIDC federation; the workflows exist and are dormant).
- **Clickjacking (M3 finding):** fixed in code and proven in local Chromium. Release still needs staging evidence through Front Door, and an approved KA production origin.

## Remaining blockers and next steps

1. **Owner decision (2026-09-30): option (a).** The owner upgrades to pay-as-you-go, keeps the remaining credit, and approves every resource individually. Budget and cost alerts come before any paid resource.
   - Prepared: `main-budgets.bicep` (subscription budget: actual 25/50/80/100%, forecast 100%; daily cost-anomaly alert); `register-providers.ps1`, a dry run by default.
   - `stack-up.ps1` now refuses every other stack until `gorgona-budgets` exists. Verified against the live subscription: shared, ai and staging are refused, using only a read-only check.
   - Next approvals in order: (1) budgets stack, with the owner stating the amount and recipient; (2) provider registration; (3) shared; (4) AI plane; (5) staging window.
2. **Before any staging window:**
   - choose a staging OIDC provider (the start guard requires one; for example an Entra ID test app registration, which is itself an approval item);
   - create a GitHub OIDC federated credential and the `staging` environment with reviewers and variables (only if CI deploys are wanted; `stack-up.ps1` works without it);
   - approve registering the required resource providers (a free subscription-level change);
   - approve the specific resource creations (budgets first).
3. **Staging acceptance**, once approved, covers:
   - bootstrap as the non-superuser admin, then migrations 0001–0007, RLS and runtime-role checks;
   - the Front Door trusted-proxy path and framing through Front Door;
   - the load baseline with `--allow-remote`;
   - alert queries against real telemetry;
   - a rollback drill and a PITR restore drill, then teardown.
4. **Trial credit expiry: 2026-10-30 14:05 UTC.** Persistent resources beyond it need a pay-as-you-go upgrade (owner decision) or deletion.

Cost estimates (re-verify after sign-in): persistent shared + AI plane ~$45–65/month idle; staging ~$17–19/day while up, $0 when torn down; production ~$615/month without HA, ~$760/month with HA.
