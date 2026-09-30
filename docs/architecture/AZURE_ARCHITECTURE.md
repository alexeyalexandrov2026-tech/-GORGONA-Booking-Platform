# Azure architecture: GORGONA Booking Platform

Status: design (M4 checkpoint A, 2026-09-30). Nothing described here is provisioned. The decisions are recorded in ADR-0012 (hosting) and ADR-0013 (AI learning plane). The resource inventory and costs are in [`M4_PLAN.md`](../plan/M4_PLAN.md).

Primary region: **Central US** (owner decision 2026-10-01; East US 2 was dropped because PostgreSQL Flexible Server is offer-restricted for this subscription in East US 2). All regional components live there; the paired region is East US 2. Front Door is global.

## 1. System context

GORGONA is a multi-tenant booking platform. KA Nails is the first tenant; its public website lives in a separate repository and embeds or links to the hosted `/book/` wizard.

Customers, salon staff and platform operators reach GORGONA only through Front Door, using each tenant's registered hostname. PostgreSQL remains the only booking authority. The AI learning plane is a separate, persistent boundary that consumes evidence. It never decides prices, availability or confirmations.

```
 salon customers ──┐        tenant public site (KA-nails repo, own hosting)
 salon staff  ─────┤              │ iframe (allowlisted origin) / full-page link
 operators ────────┤              ▼
                   └──────► https://<tenant booking host>/book/ , /v1/...
                                   │
                          GORGONA platform (Azure)
                                   │ evidence (async, scoped, never on the booking path)
                                   ▼
                          GORGONA AI learning plane (Azure, persistent)
```

## 2. Resource topology

```
                    Internet (TLS 1.2+, HSTS)
                                │
     ┌──────────────────────────▼───────────────────────────┐
     │ Azure Front Door Premium  (global)                   │
     │  • custom domains per tenant (AFD-managed certs)     │
     │  • WAF: DRS 2.x + Bot Manager + rate-limit rules     │
     │  • rules: HSTS; no caching for HTML and /v1/*;       │
     │    cache immutable /_next/static/*                   │
     │  • origin group → 1 private-link origin; probe       │
     │    /health/ready                                     │
     └──────────────────────────┬───────────────────────────┘
          Private Link (AFD-managed private endpoint, approved at the environment)
     ┌──────────────────────────▼───────────────────────────┐ rg-gorgona-<env>
     │ VNet 10.x.0.0/16                                     │
     │  snet-aca (/23) ── Container Apps environment        │
     │     (workload profiles: Consumption; public access   │
     │      OFF; zone-redundant in prod)                    │
     │        app  gorgona-api      (FastAPI + /book/)      │
     │        job  gorgona-migrate  (manual; owner role)    │
     │        job  gorgona-bootstrap (one-time; admin)      │
     │  snet-pe (/27) ── private endpoints:                 │
     │        PostgreSQL (booking) · Key Vault              │
     │  private DNS: privatelink.postgres.database.azure.com│
     │               privatelink.vaultcore.azure.net        │
     └──────────────────────────────────────────────────────┘
       PostgreSQL 18 Flexible Server (public access OFF)
       Key Vault (RBAC, soft delete + purge protection)
       Managed identities: id-gorgona-api, id-gorgona-migrate

 rg-gorgona-shared : ACR Standard (admin OFF), Log Analytics, App Insights, budgets
 rg-gorgona-ai     : persistent AI learning plane (section 12)
```

## 3. Networking

- **Container Apps environment.** A workload-profiles environment in its own `/23` subnet (delegated to `Microsoft.App/environments`), with `publicNetworkAccess = Disabled`.
  - Front Door Premium reaches it only through its managed private endpoint, which you approve on the environment (it is an IaC-declared origin).
  - Public access to the default FQDN returns a connection error, so the origin cannot be bypassed.
- **PostgreSQL.**
  - Public access disabled, with a private endpoint in `snet-pe` and a linked private DNS zone.
  - No firewall rules at all; `0.0.0.0/0` is never used.
  - Private endpoint rather than VNet injection, so the network model can change later without recreating the server.
- **Key Vault.** Private endpoint with public network access disabled. Container Apps resolves it through private DNS.
- **ACR (Standard).** Public endpoint, but admin user disabled and anonymous pull off. Pulls use managed identity with `AcrPull`. Premium with a private endpoint is a scale-later option.
- **Egress.** The app needs outbound HTTPS only to the OIDC provider's JWKS endpoint. A NAT gateway or egress firewall is scale-later; it is added only if a fixed egress IP or egress filtering is required.

## 4. Tenant boundary behind Front Door

Front Door sends `Host: <aca-fqdn>` to the origin. It replaces any client-supplied `X-Forwarded-Host` with the host the client requested, and adds `X-Azure-FDID: <profile id>`.

The application runs in **trusted-proxy mode** (checkpoint B), configured by `GBA_TRUSTED_PROXY=azure_front_door` and `GBA_FRONT_DOOR_ID=<id>`:

1. `/health/*` is exempt, for probes.
2. Any other request whose `X-Azure-FDID` is not exactly the configured ID is rejected with a generic 404/421 and counted as a tenant-resolution failure.
3. With a valid ID, the request's effective Host becomes `X-Forwarded-Host` and the scheme becomes `X-Forwarded-Proto`, before routing. Existing Host resolution, static-file redirects and the framing policy all see the tenant host.
4. `X-Forwarded-For` is never used for authorization. Client IP for logging comes from `X-Azure-ClientIP`.

This boundary is enforced twice:
- **Network:** only Front Door can reach the origin.
- **Application:** the FDID check.

The default mode (`none`, used locally and in tests) ignores every forwarded header, exactly as today.

Required tests:
- spoofed `X-Forwarded-Host` without an FDID;
- wrong FDID;
- valid FDID for tenant A vs B, cross-tenant;
- unknown host;
- the default mode ignores forwarded headers;
- the redirect keeps the tenant host.

## 5. Request flow

Customer → `https://booking.<tenant domain>/book/`, then:
1. **Front Door.** TLS terminates at the edge. WAF evaluates the request; rate limits apply to `/v1/customer/holds` and `/confirm`.
2. **Origin.** Front Door forwards to the origin over Private Link.
3. **App.** The trusted-proxy check runs, then Host → tenant (`gba.tenant_hosts`, live gate). The static export or API route answers. Customer-web HTML carries `Content-Security-Policy: frame-ancestors 'self' <approved origins of this tenant>`.
4. **Database.** API calls run under the runtime role in transactions with tenant context (RLS), with holds, confirmation and idempotency in PostgreSQL. Nothing about booking correctness moves out of the transaction.

## 6. Identity and secret flow

```
 id-gorgona-api (user-assigned MI) ─► AcrPull on ACR
                                   ─► Key Vault Secrets User on kv-<env>
 Container App secrets = Key Vault references (resolved by MI) →
     GBA_DATABASE_URL (runtime role DSN)         → gorgona-api
     GBA_MIGRATION_DATABASE_URL (owner role DSN) → gorgona-migrate job only
     GBA_ADMIN_DATABASE_URL (server admin DSN)   → gorgona-bootstrap job only
```

- Passwords are generated once per environment by the operator, written directly to Key Vault, and never stored in Git, images, parameter files, logs or reports.
- Rotation is a runbook: write a new version, restart the revision or re-run the job.
- **CI/CD** will use GitHub OIDC workload identity federation to a deployment identity scoped to the target resource group. No Azure secret is stored in GitHub.
- **OIDC for staff APIs** (ADR-0007) stays provider-agnostic. Only public issuer, audience and JWKS URLs are configured.

## 7. Deployment flow

```
commit → CI (ruff, mypy, pytest+PG+Chromium, web checks, separation/secret checks)
       → container build (multi-stage) → image scan → push to ACR (tag = git SHA; deploy by digest)
       → staging: run gorgona-migrate job (owner role, advisory lock) → new revision (0% traffic)
       → smoke + staging acceptance → shift 100% in staging
       → production (future, owner-authorized): migrate job → new revision 0%
       → canary 10% → observe → 100% ; previous revision kept for rollback
```

- Implemented as dormant workflows: `.github/workflows/deploy-staging.yml` (manual, `staging` environment approval, OIDC, digest-pinned scanner, migrate -> revision -> Front Door smoke -> automatic traffic rollback) and `promote-production.yml` (refuses: production is not authorized). CI (`ci.yml`) builds and runs the production image on every push.
- Rollback means shifting traffic back to the previous revision; images are immutable by digest.
- Migrations are forward-only and additive. A failed migration stops the pipeline before any revision changes.

## 8. Database topology and reliability

| | Staging (ephemeral) | Production (future) | AI plane |
|---|---|---|---|
| SKU | General Purpose D2ds_v5 (for parity and load baseline) | General Purpose D2ds_v5 initially, scale up by evidence | Burstable B1ms initially |
| HA | Off; switched on only for a failover test hour if required | Zone-redundant HA | Off (scale later) |
| Storage | 64 GiB, autogrow | 128 GiB, autogrow | 32 GiB, autogrow |
| Backup retention | 7 days, local | 35 days, **geo-redundant** (must be chosen at creation) | 35 days, geo-redundant |
| Extensions | `btree_gist` | `btree_gist` | `vector` |
| Public access | Disabled | Disabled | Disabled |

- **RPO.**
  - Point-in-time restore: RPO of about 5 minutes or less from continuous WAL backup, per the Azure service description.
  - Zone-redundant HA: synchronous standby, RPO 0 for a zonal failure.
- **RTO.**
  - HA failover: typically 60–120 s (service description).
  - PITR restore: **not claimed** until measured in a restore drill in staging, then recorded.
- **Restore procedure.**
  1. PITR to a new server.
  2. Verify with `gba-db check-runtime-role` and the migration checksum table.
  3. Repoint the Key Vault DSN secrets.
  4. Restart the revision.
  5. Re-approve the private endpoint and DNS if needed.
- **Maintenance.** A custom maintenance window (low-traffic hour). Minor versions are patched by the service, and staging is exercised first when possible.
- **Connection budget.**
  - Rule: `max_replicas × GBA_DB_POOL_MAX_SIZE + migration(1) + ops reserve(≥15) ≤ max_connections`.
  - Production: 6 × 10 + 1 + 15 = 76. Staging: 3 × 10 + 1 + 15 = 46.
  - The actual `max_connections` for each SKU is read after creation, and the pool size is lowered if needed.
  - PgBouncer is added only when connection saturation is measured.
- **Bootstrap compatibility.** The Azure admin is not a superuser. `gba-db bootstrap` must be proven in staging under PostgreSQL 16+ CREATEROLE rules before production. The runtime role must pass `assert_safe_runtime_role`. Migrations 0001–0006 are unchanged.

## 9. Scaling

| | Staging | Production |
|---|---|---|
| Replicas | min 1, max 3 | min 2, max 6; zone-redundant environment |
| Scale rule | HTTP concurrency 50 per replica | HTTP concurrency 50 per replica (assumption; tuned from the load baseline) |
| Resources | 0.5 vCPU / 1 GiB | 0.5 vCPU / 1 GiB (tuned from the baseline) |
| Scale to zero | No (acceptance) | **No**: first-request latency and availability |

Background work: the hold-expiry sweeper exists in code, but correctness never depends on it. A scheduled Container Apps job (cron) is scale-later. No queue is introduced for booking.

## 10. Observability

- **Implementation (checkpoint B).** OpenTelemetry SDK with the Azure Monitor OpenTelemetry exporter (GA), configured by `APPLICATIONINSIGHTS_CONNECTION_STRING`. Exporting is disabled when that variable is unset (local and tests). Application Insights has local (key) auth disabled, so the exporter authenticates with the API's managed identity (`AZURE_CLIENT_ID`), which holds Monitoring Metrics Publisher on the component. Each environment reports as `gorgona-api-<env>`. The Container Apps managed OpenTelemetry agent is preview-only and is not used.
- **Signals.** HTTP request rate, latency (p50/p95/p99) and errors; database pool waits/timeouts and readiness failures; revision restarts.
- **Domain counters.** Mapped once from the central error-code table (`api/errors.py`): `SLOT_CONFLICT`, `HOLD_EXPIRED`, `IDEMPOTENCY_KEY_REUSED`, `TENANT_NOT_FOUND`, `PAYMENT_REQUIRED`, confirmation failures and `DATABASE_UNAVAILABLE`.
- **Structured JSON logs** carry `request_id` (`X-Request-ID`), trace ID, `tenant_id` (UUID, not a name), operation, status and duration. Never passwords, tokens, DSNs, JWTs, customer names, emails or phone numbers.
- **Alerts** (`infra/azure/modules/alerts.bicep`). Each alert has an owner (the platform operator, via the environment's action group) and an action. Thresholds are initial and are re-tuned from the staging load baseline (`api/tools/load_baseline.py`). Metric names are taken from the Azure Monitor supported-metrics reference.

| Alert | Signal | Threshold | Severity | Action |
|---|---|---|---|---|
| API unavailable | Front Door `OriginHealthPercentage` (probes `/health/ready`) | Average < 50% over 5 min | Sev 1 | Check revision health; roll back the revision |
| Error-rate spike | Front Door `Percentage5XX` | > 2% over 5 min | Sev 2 | Inspect traces; roll back if release-related |
| Database unavailable | PostgreSQL `is_db_alive` | Average < 0.6 over 5 min (down about 2 of 5 min) | Sev 1 | Check server/HA state; follow the failover/restore runbook |
| Storage near full | PostgreSQL `storage_percent` | > 80% over 15 min | Sev 3 | Confirm autogrow; review growth |
| Pool saturation | PostgreSQL `active_connections` | Minimum above the connection budget (`maxReplicas x pool + 16`) for 15 min | Sev 2 | Lower replicas or pool size; evaluate PgBouncer |
| Replica restarts (failed revision) | Container Apps `RestartCount` (cumulative per replica) | Maximum > 3 over 15 min | Sev 2 | Keep traffic on the previous revision; inspect logs |
| Sustained latency | App Insights `requests` p95 for `gorgona-api-<env>` | > 1.5 s over 10 min | Sev 3 | Check DB CPU and slow queries |
| Confirmation failures | App Insights `requests` ending `/confirm` with 5xx | > 5 in 10 min | Sev 2 | Investigate; not normal customer error |
| Tenant-resolution anomaly | `gorgona.domain_errors` with code `TENANT_NOT_FOUND` (unknown hosts and Front Door ID refusals) | Last hour > 10x the hourly average of the previous 47 h, floor 50 | Sev 3 | Check for scans/misrouting; WAF rule review |
| Budget | Consumption budget (staging, shared, AI) | 50% / 80% / 100% actual, 100% forecast | Info / Sev 3 / Sev 2 | Tear down staging; review spend |

## 11. Failure modes and disaster recovery

| Failure | Behaviour | Recovery | Evidence required before claiming |
|---|---|---|---|
| Bad revision | Readiness failing: no traffic. Healthy but buggy: errors | Shift traffic to the previous revision | Staging rollback test |
| Container app unavailable | Front Door returns 5xx; probes mark the origin down | Restart the revision; redeploy the last good digest | Staging restart test |
| PostgreSQL unavailable | Readiness 503; booking fails closed; no success-shaped responses | HA failover (prod); PITR restore | Staging PITR drill (time measured) |
| Accidental deployment | Revision gets 0% traffic by default | Deactivate the revision | Pipeline review |
| Failed migration | Transaction rolled back; lock released; pipeline stops | Fix forward with a new migration; never edit history | Migration job test in staging |
| Regional incident | Service outage (single region) | Geo-restore the database to a paired region; redeploy by IaC | Not claimed. Multi-region is scale-later |
| Lost or compromised secret | App cannot connect, or a credential is exposed | Rotate the role password in PostgreSQL and Key Vault; restart | Rotation runbook dry run in staging |
| Staging teardown mistake | Guarded script plus locks on the AI and prod resource groups | Stacks and locks prevent deletion | Script guard dry run |

## 12. AI learning plane (persistent; ADR-0013)

```
 production app ──(async evidence events, scoped, no contact data)──┐
 operator/staff corrections ─────────────────────────────────────────┤
                                                                     ▼
 ┌──────────────────────────── rg-gorgona-ai (persistent, delete-locked) ────────────────────────────┐
 │ Evidence store (StorageV2 blob: versioning, soft delete, immutability, GRS)                           │
 │      │ event                                                                                      │
 │      ▼                                                                                            │
 │ Container Apps jobs (event-driven, scale to 0): validate → normalise → dedupe → scope/consent     │
 │      │                                                                                            │
 │      ▼                                                                                            │
 │ AI PostgreSQL 18 (memory, knowledge, corrections, dataset versions, lineage, registry metadata,   │
 │                   evaluation history, pgvector embeddings; RLS per tenant)                        │
 │      │ training request                                                                           │
 │      ▼                                                                                            │
 │ Service Bus queue ──► Azure ML job on compute cluster (min 0 nodes; CPU or GPU on demand)         │
 │                            │ candidate model + metrics                                            │
 │                            ▼                                                                      │
 │ Evaluation + verification (policy thresholds, regression sets, safety checks)                     │
 │                            │ pass                                                                 │
 │                            ▼                                                                      │
 │ Registry (Azure ML model registry + AI DB metadata)  ── explicit promotion decision (recorded) ─┐ │
 └──────────────────────────────────────────────────────────────────────────────────────────────┬─┘ │
                                                                                               ▼
                                    production inference (approved version only; future; rg-gorgona-production)
                                              │ outputs are proposals; the booking domain decides
                                              ▼
                                        new evidence ─► (loop)
```

**Training and retraining lifecycle:**
1. Evidence accumulates continuously.
2. A dataset version is cut (immutable snapshot and lineage).
3. A training request is enqueued.
4. Compute scales from 0.
5. The job trains a candidate.
6. Evaluation runs against a frozen benchmark and the current production model.
7. The candidate passes or fails the policy thresholds.
8. It is registered as a candidate.
9. Promotion happens only by an explicit, recorded decision, with the previous version as the rollback target.
10. Controlled deployment follows (a revision, then traffic shift).
11. Compute scales back to 0.

The production model is never overwritten automatically.

## 13. Staging lifecycle and teardown boundaries

```
 [owner approves window] → stack up: rg-gorgona-staging (tags env=staging, lifecycle=ephemeral)
   → approve AFD private endpoint → bootstrap job → migrate job → seed FAKE tenants/hosts/embed origins
   → acceptance (M3 behaviours, framing, trusted proxy, race, expiry, restore, rollback, load baseline)
   → record evidence in M4_REPORT → stack down (deleteAll, staging RG only) → verify $0 staging spend
```

| Boundary | Stack | On delete of the stack | Protection |
|---|---|---|---|
| `rg-gorgona-staging` | `gorgona-staging` | **deletes all its resources** | Guarded script (requires tags) |
| `rg-gorgona-production` | `gorgona-production` | Detach only | Stack deny-delete + RG `CanNotDelete` lock |
| `rg-gorgona-ai` | `gorgona-ai` | Detach only | Stack deny-delete + RG lock; storage immutability; soft delete |
| `rg-gorgona-shared` | `gorgona-shared` | Detach only | Stack deny-delete + RG lock |

Staging has no role assignments outside its own resource group and does not write to the AI plane. Deleting staging cannot reach AI or production data.

## 14. Resource dependencies

```
 shared (ACR, Log Analytics, App Insights, budgets)
    ├──► staging platform (needs ACR image + Log Analytics)
    ├──► production platform (needs ACR image + Log Analytics)
    └──► AI plane (needs Log Analytics; ML workspace uses ACR, App Insights, its own Key Vault and storage)
 platform internal order: VNet → private DNS → Key Vault (+PE) → PostgreSQL (+PE) → identities + RBAC
                          → Container Apps env → jobs/app → Front Door (+WAF) → approve PE → custom domains
```

## 15. Cost model

The per-resource table is in `M4_PLAN.md`. Figures were first estimated for East US 2; Central US list prices are about 13% higher for PostgreSQL and VM compute (Retail Prices API, 2026-10-01) and equal for ACR, Container Apps and Log Analytics. Re-verify in the Azure pricing calculator after sign-in.

| Scope | Idle | Active | Scale to zero? |
|---|---|---|---|
| Shared | ~$22–27/mo | Grows with logs over 5 GB/mo | No (ACR, Log Analytics retention) |
| AI plane | ~$25–40/mo (AI PostgreSQL B1ms free for 12 months on the free account) | + compute hours (CPU ~$0.19/h, GPU ~$0.5+/h) | Jobs and ML clusters yes; storage, database and private endpoints no |
| Staging window | $0 when torn down | ~$17–19/day | Only by teardown (Front Door and PostgreSQL cannot scale to zero) |
| Production | n/a | ~$615/mo without HA, ~$760/mo with HA, + traffic | No |

**Trial credit.** $200 for 30 days, then the services are disabled unless you upgrade to pay-as-you-go.
- Consumers: shared, the AI plane and staging windows.
- One 5-day staging window (~$90) plus a month of shared and AI plane (~$50–65) fits.
- Production, GPU quota and persistent staging need pay-as-you-go.
- The production architecture is not weakened to fit the credit. Anything that cannot be validated within it becomes a production acceptance item.

## 16. Scale path (not implemented now)

- **Networking:** ACR Premium with a private endpoint and geo-replication; NAT gateway or Azure Firewall for egress control.
- **Database:** Entra token authentication for PostgreSQL roles; read replicas for reporting; PgBouncer when saturation is measured.
- **Operations:** a scheduled hold-expiry sweeper job; Service Bus for notifications, webhooks and outbox delivery.
- **Topology:** a second region with geo-restore/replica and Front Door multi-origin; dedicated tenant stamps for large tenants.
- **Deferred until evidence exists:** AKS, microservices, sharding and elastic clusters.
