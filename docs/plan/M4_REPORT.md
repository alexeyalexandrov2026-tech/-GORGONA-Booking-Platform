# M4 report: Azure cloud foundation, checkpoint A

Date: 2026-09-30 (America/New_York). Scope: checkpoint A of [`M4_PLAN.md`](M4_PLAN.md). That covers design, IaC (authored and validated locally), tooling, read-only discovery and the M3 baseline rerun. **No Azure resource was created, changed, previewed or deployed.**

## Gates

| Gate | Status | Why |
|---|---|---|
| **M3 APPLICATION BASELINE** | **PASS** | The full local baseline was rerun at the M4 start (see Validation) |
| **M4 AZURE FOUNDATION GATE** | **FAIL** | Checkpoint B (trusted-proxy host mode, framing allowlist, start guard, container, observability, KA decoupling) and checkpoint C (Azure evidence) are not done |
| **AZURE STAGING ACCEPTANCE** | **NOT DEPLOYED** | No staging window has been approved |
| **PRODUCTION IFRAME RELEASE GATE** | **BLOCKED** | The M3 clickjacking finding is still open. The fix is designed (ADR-0012, AZURE_ARCHITECTURE §4–5) but not implemented. No KA production origin is approved |
| **PRODUCTION DEPLOYMENT** | **NOT AUTHORIZED** | |
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
| Commits in checkpoint A | `2736772` (docs), `fc283f4` (infra), plus the commit adding this report | none (unchanged) |
| Remote | `main` = `784312d` "Initial commit"; **UNRELATED HISTORIES**; re-queried read-only | empty; re-queried read-only |
| Push | **Not performed** | **Not performed** |

Files added:
- **Docs:** ADR-0012, ADR-0013, `AZURE_ARCHITECTURE.md`, `M4_PLAN.md`, `M4_REPORT.md`.
- **IaC:** `infra/azure/`, 32 files:
  - `bicepconfig.json` and `README.md`;
  - 3 entry points;
  - 17 modules;
  - 7 parameter files;
  - 2 scripts.
- No application code changed.

## Tooling (installed with owner approval)

| Tool | Version | Evidence |
|---|---|---|
| Azure CLI | 2.90.0 | winget; installer hash verified |
| Bicep CLI | 0.47.16 | `az bicep install` |
| Docker Desktop | client 29.8.1 | winget; installer hash verified. **The engine is not running.** First launch needs the owner to accept Docker's subscription agreement, and possibly a WSL2 setup or reboot. |

## Azure discovery

**BLOCKED: AZURE AUTHENTICATION REQUIRED.** `az account show` answers "Please run 'az login'". The owner signs in; credentials are never handled by the agent.

Known from the owner's portal screenshot:
- A free-trial subscription with **$200 credit**; the "upgrade to pay-as-you-go" option is offered.
- Directory `alexandrov20211992gmail.onmicrosoft.com`.
- The credit expiry date, quotas, existing resources and provider registrations are **not yet read**.

Pending read-only commands after sign-in (IDs will be masked in the report):
- `az account show`
- `az group list`, `az resource list`
- `az provider show` for Microsoft.App, Cdn, DBforPostgreSQL, KeyVault, Network, MachineLearningServices, ServiceBus
- `az postgres flexible-server list-skus -l eastus2` (PostgreSQL 18 offer)
- `az vm list-usage -l eastus2` plus PostgreSQL/Container Apps quota
- `az consumption budget list`

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

## Validation: M3 application baseline (rerun, current trees)

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

## Azure resources

- **Created:** none.
- **Not created:** every line of the Resource Creation Plan (`M4_PLAN.md`, items 0–22), including budgets.

Estimates (to re-verify after sign-in):
- Persistent shared + AI plane: ~$45–65/month idle.
- Staging: ~$17–19/day while up.
- Production: ~$615/month without HA, ~$760/month with HA.

## Security posture (design)

- **Unchanged:** Host-resolved tenancy (the trusted-proxy extension is designed, not coded), RLS and runtime-role guard, idempotency, concurrency.
- **Planned protections:**
  - no public database or Key Vault;
  - no origin bypass (Private Link + FDID);
  - WAF in Prevention mode with managed and rate-limit rules;
  - managed identities with least-privilege secret grants;
  - no Azure secrets in GitHub (OIDC federation, dormant workflows in checkpoint B).
- **Open:** the clickjacking finding (M3) stays open until checkpoint B implements and tests the governed `frame-ancestors` allowlist, followed by staging evidence.

## Remaining blockers and next steps

1. **Owner sign-in** (`az login`) for read-only discovery. Stop if PostgreSQL 18 isn't offered in East US 2 or the account is ambiguous.
2. **Docker Desktop first launch** and agreement acceptance by the owner (needed for the checkpoint B container build).
3. **Owner confirmation of the checkpoint A architecture**, which starts checkpoint B.
4. **Before any staging window:**
   - choose a staging OIDC provider (the start guard requires one; for example an Entra ID test app registration, which is itself an approval item);
   - produce an image digest;
   - approve the specific resource creations.
5. **Trial credit expiry** (date unknown). Persistent resources beyond it need a pay-as-you-go upgrade (owner decision) or deletion.
