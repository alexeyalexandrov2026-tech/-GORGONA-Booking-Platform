# M4: Azure cloud foundation

Starting branch `m2-identity` at `b5e59cee0c82ab14e8903ebbab266c9d03844a91`, with a clean tree. M1–M3 are the accepted baseline (M3 gate PASS, local).

Architecture: [`AZURE_ARCHITECTURE.md`](../architecture/AZURE_ARCHITECTURE.md). Decisions: ADR-0012 (Azure hosting) and ADR-0013 (AI learning plane).

Owner decisions (2026-09-30):
- Region **Central US** (owner decision 2026-10-01; originally East US 2, where PostgreSQL Flexible Server is offer-restricted for this subscription in East US 2).
- **Time-boxed production-parity staging**: ephemeral and IaC-controlled.
- A **persistent AI learning plane**, independent of staging.
- Start guard on the bridge topology for staging and production. Production remains gated behind the approved production-bridge acceptance process. No final production cutover occurs until the required bridge/security/E2E gates pass and the production deployment is explicitly authorized.
- Tooling (Azure CLI, Bicep, Docker) may be installed.
- **No Azure resource is created, changed or purchased without separate explicit approval of that specific action.**

## Checkpoints

| Checkpoint | Scope | Authorization |
|---|---|---|
| **A: design** | Tooling, read-only Azure discovery, ADRs, architecture, this plan and the resource plan, Bicep IaC (built/linted, not deployed), M3 baseline rerun, `M4_REPORT` | Approved |
| **B: local application** | Trusted Front Door host mode, framing allowlist (migration 0007), conditional start guard, container build/run, OpenTelemetry + JSON logs, KA HTTP-only integration, dormant CI/CD, load-baseline script, alert definitions | Starts after the owner confirms checkpoint A |
| **C: Azure** | Budgets → shared → AI plane → staging window → acceptance → teardown | **Each resource-creation action separately approved** |

## Implement now vs scale later

**Implement now (M4):**
- **Bicep foundation.** Shared, AI plane and platform (staging/production) stacks, with guards and teardown.
- **Edge.** Front Door Premium + WAF + Private Link; private PostgreSQL 18 and Key Vault.
- **Identity.** Managed identities, deploy-by-digest, and a migration job.
- **Application.** The trusted-proxy tenant boundary and the governed framing policy.
- **Operations.**
  - OpenTelemetry and structured logs.
  - Decision-oriented alerts and budgets.
  - A staging acceptance suite, a load baseline, and rollback and restore drills.

**Scale later (documented, not built):**
- Multi-region or active-active; dedicated tenant stamps.
- Worker fleet and event-bus expansion (notifications, webhooks, outbox).
- AKS; database sharding or elastic clusters; read replicas.
- PgBouncer (only on measured saturation).
- ACR Premium with a private endpoint; NAT gateway or firewall egress.
- Entra token authentication for PostgreSQL.
- Implementing the AI plane (ingestion, memory, training code) and GPU quota.

## Resource creation plan

Region: Central US (Front Door is global). Costs below were estimated for East US 2; Central US PostgreSQL and VM compute are about 13% higher.

Classes:
1. Persistent 24/7
2. Persistent, scale-to-zero / consumption
3. On-demand training/evaluation compute
4. Ephemeral production-parity staging
5. Production-only

Costs are pay-as-you-go estimates, to re-verify in the pricing calculator after sign-in. **Nothing below exists.** Each line needs its own approval before creation.

| # | Resource | RG / stack | Class | Tier / SKU | Idle cost | Active cost | Est. monthly | Free allowance | Why required |
|---|---|---|---|---|---|---|---|---|---|
| 0 | Subscription budget + daily cost-anomaly alert (`main-budgets.bicep`, stack `gorgona-budgets`); per-RG staging budget in the platform stack | subscription | 1 | Cost Management budget + scheduled action | $0 | $0 | $0 | n/a | Guard the credit and stop surprises. **Create first; enforced by `stack-up.ps1`.** |
| 1 | Resource groups (shared, AI, staging, prod) + locks | subscription | 1 / 4 / 5 | n/a | $0 | $0 | $0 | n/a | Lifecycle boundaries; staging has no lock |
| 2 | Log Analytics workspace | shared | 1 | Pay-per-GB, 30-day retention | ~$0 | ~$2.3–2.8/GB over the free 5 GB | $0–10 | 5 GB/mo ingestion (Azure Monitor) | Central logs and metrics for alerts |
| 3 | Application Insights (workspace-based) | shared | 1 | Workspace-based | ~$0 | Included in Log Analytics ingestion | $0–5 | Shares the 5 GB | Traces and request telemetry |
| 4 | Container Registry | shared | 1 | Standard | ~$20/mo | + storage over 100 GB | ~$20 | none | Private image store, deploy by digest |
| 5 | Evidence storage (blob) | AI | 1 | StorageV2 GRS (no HNS), versioning, soft delete, container immutability | ~$0.05/GB | + transactions | <$5 | 12 months: 5 GB LRS hot blob (partial) | Immutable learning evidence, survives staging |
| 6 | AI PostgreSQL 18 Flexible Server | AI | 1 | Burstable B1ms, 32 GiB, geo-redundant backup 35 d | ~$16/mo | same | ~$16 (≈$0 for 12 months) | 750 h B1ms + 32 GB storage + 32 GB backup/mo, for 12 months | Memory, lineage, registry metadata, `vector` |
| 7 | AI private endpoints (PostgreSQL, storage, Key Vault) + VNet/DNS | AI | 1 | Private endpoint | ~$7.3/mo each | + $0.01/GB | ~$22 | none | Learning data must not be public |
| 8 | AI Key Vault | AI | 1 | Standard, RBAC, purge protection | ~$0 | $0.03 per 10k operations | <$1 | none | AI-plane secrets |
| 9 | Service Bus namespace + queue | AI | 2 | Basic | ~$0 | $0.05 per million operations | <$1 | none | Training/validation request queue with dead-letter |
| 10 | AI Container Apps environment + jobs | AI | 2 | Workload profiles, Consumption | $0 | Per vCPU-s / GiB-s | ~$0–5 | 180k vCPU-s + 360k GiB-s per month | Event-driven validation/orchestration, scale to 0 |
| 11 | Azure ML workspace | AI | 1 | Basic (uses #4, #3, #5-type storage, #8) | $0 | n/a | $0 (dependencies billed) | n/a | Model registry, experiments, evaluations |
| 12 | Azure ML CPU cluster | AI | 3 | e.g. Standard_D4s_v5, min 0 / max 2 | $0 | ~$0.19/h per node | Per run | none | Training and evaluation on demand |
| 13 | Azure ML GPU cluster | AI | 3 | e.g. NC4as_T4_v3, min 0 / max 1 | $0 | ~$0.5+/h | Per run | **Quota request + pay-as-you-go needed** | Only when a real GPU workload exists |
| 14 | Staging VNet, subnets, private DNS | staging | 4 | n/a | $0 | $0 | $0 | n/a | Private topology parity |
| 15 | Staging Key Vault + private endpoint | staging | 4 | Standard | n/a | ~$0.25/day | Window only | none | DSN secrets via managed identity |
| 16 | Staging PostgreSQL 18 + private endpoint | staging | 4 | GP D2ds_v5, 64 GiB, no HA, 7-day backup | n/a | ~$4.5/day | Window only | none | Real PostgreSQL parity, race and load baseline |
| 17 | Staging Container Apps env (+Private Link) + app + migrate/bootstrap jobs + identities | staging | 4 | Workload profiles, Consumption, min 1 / max 3 | n/a | ~$1.5–2.5/day | Window only | Shared Container Apps free grant | Real revisions, probes, rollback test |
| 18 | Staging Front Door Premium + WAF policy | staging | 4 | Premium_AzureFrontDoor | n/a | ~$11/day + requests | Window only | none | Private Link, WAF and trusted-host parity |
| 19 | Production VNet/DNS/Key Vault/private endpoints | prod | 5 | as staging | ~$15/mo | | ~$15 | none | Production topology |
| 20 | Production PostgreSQL 18 | prod | 5 | GP D2ds_v5, **zone-redundant HA**, 128 GiB, geo-redundant backup 35 d | ~$290/mo | | ~$290 | none | Booking authority with HA and restore |
| 21 | Production Container Apps env + app + jobs | prod | 5 | Zone-redundant, min 2 / max 6 | ~$75–110/mo | Scales with load | ~$75–110 | Container Apps grant | Serving |
| 22 | Production Front Door Premium + WAF | prod | 5 | Premium_AzureFrontDoor | $330/mo | + $0.015 per 10k requests + $0.083/GB egress | ~$340–360 | none | Secure global ingress |

**Totals** (estimates):
- Persistent shared + AI plane: **~$45–65/month idle**.
- Staging: **~$17–19/day while up, $0 when torn down**.
- Production: **~$615/month without HA, ~$760/month with HA**, plus traffic.

**Trial credit plan.** Create budgets (#0) first, then shared + AI plane. Then one staging window of ≤5 days (~$90), after which staging is torn down immediately. The credit expires after 30 days whatever the balance. Persistent resources then need a pay-as-you-go upgrade (an owner decision) or deletion.

## Checkpoint B detail

**Status: implemented locally, 2026-09-30.** Evidence, commits and deviations are in [`M4_REPORT.md`](M4_REPORT.md). Staging acceptance scripts are replaced by the dormant `deploy-staging` workflow plus the load-baseline tool; the full acceptance list is in the report's next steps.

1. **Trusted Front Door host mode.** Settings `GBA_TRUSTED_PROXY` and `GBA_FRONT_DOOR_ID`, plus an ASGI middleware. Tests as in AZURE_ARCHITECTURE §4.
2. **Framing allowlist.**
   - Migration `0007_tenant_embed_origins.sql`: FORCE RLS, tenant FK, HTTPS-origin CHECK, audit.
   - `gba-db embed-origin add|remove|list`.
   - A CSP middleware on customer-web responses: `frame-ancestors 'self' <approved>` for live tenants, otherwise `'self'`.
   - Browser tests: approved test origin allowed, unapproved blocked, top-level navigation allowed, full-page fallback working.
   - Invert the M3 security-gap scenario.
3. **Start guard.** `staging` is allowed only with OIDC + trusted-proxy ID + framing enabled; `production` is still refused. Tests first.
4. **Container.**
   - Multi-stage Dockerfile: Node 24 → `web/out`, then Python 3.14 slim + `uv sync --locked --no-dev`; digest-pinned bases; non-root; no secrets; `GBA_HOST=0.0.0.0`; graceful shutdown.
   - `.dockerignore`.
   - Local run: health/readiness, migration job, booking flow.
5. **Observability.** OpenTelemetry SDK + Azure Monitor exporter (GA; enabled only when `APPLICATIONINSIGHTS_CONNECTION_STRING` is set), JSON logs, and domain counters from the error-code table, with a no-PII log test.
6. **KA.** Replace the pytest-internal adapter with an HTTP/browser suite driven by `KA_BOOKING_TEST_URL`. The GORGONA local acceptance harness seeds FAKE data and verifies rows.
7. **Pipelines and runbooks.**
   - Dormant GitHub workflows (`workflow_dispatch`; OIDC federation; environment approvals).
   - A load-baseline script (throughput, p50/p95/p99, errors, contention).
   - Staging acceptance scripts; alerts in Bicep.

Every item follows the established method: relevant test → RED → smallest change → focused, static and full regression → reviewed local commit.

## Stop conditions (unchanged from the M4 brief)

Stop and report instead of continuing if any of these occur:
- PostgreSQL 18 is unavailable in East US 2.
- The Azure account is ambiguous.
- There is an unexpected paid-resource requirement.
- Migration incompatibility, or an RLS, isolation or concurrency regression.
- Secret exposure.
- The Private Link topology is unsupported, or the framing policy conflicts with the tenant model.
- A production domain or business fact is needed.
