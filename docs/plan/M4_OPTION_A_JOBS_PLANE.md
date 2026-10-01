# M4 Option A: AI jobs plane in West US 3 (Phase 1: design and validation)

**Status 2026-10-01: designed and validated read-only. Nothing created.** This is the AI learning plane's jobs compute only. It is not the production bridge and does not replace it; the bridge (staging, then production on the same topology) is described in [`BRIDGE_ACCEPTANCE.md`](BRIDGE_ACCEPTANCE.md). Every creation step below needs its own owner approval.

## Why

Central US refused to create a new Container Apps environment, twice:

- The first attempt failed with `ManagedEnvironmentCapacityHeavyUsageError` / `AKSCapacityHeavyUsage`.
- The single owner-approved retry failed with the same error.

Its state now:

- The failed `cae-gorgona-ai` stays in `rg-gorgona-ai`, in `Updating` state. It still counts 1 of the 15 environments allowed per region.
- It is **not deleted**; deleting it is a separate approval after the West US 3 environment is proven.
- The `gorgona-ai` stack is `failed` only because of that environment. Its 24 data-plane resources succeeded.

## What changes (IaC)

| File | Change |
|---|---|
| `infra/azure/main-ai-jobs.bicep` | **New** entry point and stack (`gorgona-ai-jobs`). It deploys only the Container Apps jobs plane, into `jobsLocation` (`westus3`), in three gated steps. It references existing Central US identities and the vault as `existing` resources and does not modify them. |
| `infra/azure/params/ai-jobs.bicepparam` | **New.** Sets `jobsLocation = 'westus3'` and the address plan `10.31.0.0/16`. Step gates come from `GBA_AI_JOBS_CONNECTIVITY`, `GBA_AI_NETCHECK_IMAGE`, `GBA_AI_JOBS_DEPLOY` and `GBA_AI_IMAGE`. No secrets. |
| `infra/azure/modules/aca-vnet.bicep` | **New.** A VNet with a single subnet delegated to Container Apps (`/23`). |
| `infra/azure/modules/vnet-peering.bicep` | **New.** One peering direction: no forwarded traffic, no gateway transit, no remote gateways. |
| `infra/azure/modules/private-dns-links.bicep` | **New.** Links *existing* private DNS zones to another VNet, with no auto-registration. |
| `infra/azure/modules/ai-netcheck-job.bicep` | **New.** A manual probe job (`gba-ai netcheck`) running as the worker identity. |
| `infra/azure/modules/ai-jobs-environment.bicep` | Adds an optional `name` parameter (default unchanged) and updates comments. |
| `infra/azure/params/ai.{create,update}.bicepparam` | Sets `deployJobsEnvironment = false`. A future `gorgona-ai` update therefore never retries the Central US environment. That update would **detach** (detachAll) the failed `cae-gorgona-ai`, not delete it. No `gorgona-ai` update is part of this plan. |
| `infra/azure/scripts/stack-up.ps1`, `operator-env.ps1` | Add the stack/stage `ai-jobs`: detachAll and denyDelete like `ai`; one parameter file; no secrets. |
| `ai/src/gorgona_ai/netcheck.py`, `cli.py`, `ai/tests/test_netcheck.py` | **New** `gba-ai netcheck` command: DNS + TCP to each dependency. A name expected to be private that resolves to a public address fails the run (no silent public fallback). Secrets are checked for presence only and never printed. |

The 5 jobs reuse `modules/ai-jobs.bicep` unchanged.

Evidence:

- **Bicep:** builds and lints clean.
- **AI suite:** 29 passed, including the real-image container test.
- **Image:** `gorgona-ai@sha256:4ff261a409387ec6c9da06f205756ef6b0e90094a011dd405012aac083a81a0b` (commit `20b63e0`) is pushed to the Central US registry. Its `netcheck` command was smoke-tested locally and fails closed.

## Steps (each a separate approval)

| Step | Gate | Creates (all West US 3 unless noted) |
|---|---|---|
| **1. Prove the environment** | defaults | `rg-gorgona-ai-jobs`, `vnet-gorgona-ai-jobs` (10.31.0.0/16, `snet-aca` 10.31.0.0/23), `cae-gorgona-ai-jobs` (internal, public network access disabled, Consumption only, not zone-redundant), and its diagnostic setting to the shared Log Analytics workspace (Central US). **4 resources.** |
| **2. Private connectivity + probe** | `GBA_AI_JOBS_CONNECTIVITY=true`, `GBA_AI_NETCHECK_IMAGE=<digest>` | Peering `peer-ai-jobs-to-ai` on the West US 3 VNet; **in Central US `rg-gorgona-ai`:** peering `peer-ai-to-ai-jobs` on `vnet-gorgona-ai` and DNS links `link-gorgona-ai-jobs` on the 3 existing zones; probe job `caj-gorgona-ai-netcheck` (manual). **+6 resources.** Then one manual run of the probe. |
| **3. The 5 AI jobs** | `GBA_AI_JOBS_DEPLOY=true`, `GBA_AI_IMAGE=<digest>` | `caj-gorgona-ai-{bootstrap,migrate,tick,health,intake}`. **Blocked on the Service Bus decision below.** |

Steps 2 and 3 never run before the environment reports `Succeeded`.

## What-if results

These are read-only previews.

### Step 1: 4 to create, nothing else touched

- `resourceGroups/rg-gorgona-ai-jobs` (westus3)
- `Microsoft.Network/virtualNetworks/vnet-gorgona-ai-jobs`
- `Microsoft.App/managedEnvironments/cae-gorgona-ai-jobs`:
  - internal
  - `publicNetworkAccess: Disabled`
  - Consumption profile
  - zoneRedundant false
- `.../cae-gorgona-ai-jobs/providers/Microsoft.Insights/diagnosticSettings/to-log-analytics`

### Step 2: 10 to create, 25 Central US resources reported **Ignore** (unchanged)

The 10 creates are the 4 above plus the following 6:

- `vnet-gorgona-ai-jobs/virtualNetworkPeerings/peer-ai-jobs-to-ai`
- `rg-gorgona-ai`: `vnet-gorgona-ai/virtualNetworkPeerings/peer-ai-to-ai-jobs`. This is a child resource; the VNet itself is "Ignore".
- `rg-gorgona-ai`: `privateDnsZones/{privatelink.postgres.database.azure.com, privatelink.vaultcore.azure.net, privatelink.blob.core.windows.net}/virtualNetworkLinks/link-gorgona-ai-jobs`
- `Microsoft.App/jobs/caj-gorgona-ai-netcheck`:
  - worker identity
  - Key Vault reference `ai-database-url`
  - image by digest
  - targets: PostgreSQL:5432 and Key Vault:443 private, blob:443 private, Service Bus:5671 public, registry:443 public

**Central US resources that remain unchanged.** what-if reports all 25 as Ignore:

- `cae-gorgona-ai` (failed, kept)
- `psql-gorgona-ai-<sfx>`
- `kv-gba-ai-<sfx>`, `kv-gba-ml-<sfx>`
- `mlw-gorgona-ai-<sfx>`
- `id-gorgona-ai-{ops,worker,scaler}`
- 3 private endpoints and their 3 NICs
- 3 private DNS zones and their existing `link-gorgona-ai` links
- `vnet-gorgona-ai`
- `sb-gorgona-ai-<sfx>`
- `stevid<sfx>gba`, `stml<sfx>gba`
- the resource group

Outside `rg-gorgona-ai`, `gorgona-shared` (registry, Log Analytics, App Insights) and `gorgona-budgets` are not in scope. No role assignment is created or changed: the identities already hold Key Vault Secrets User (per secret), Service Bus Data Receiver / Data Owner (scaler, one queue), and AcrPull.

## Topology

```
 West US 3  rg-gorgona-ai-jobs                      Central US  rg-gorgona-ai (unchanged)
 ┌──────────────────────────────────┐   global    ┌────────────────────────────────────────┐
 │ vnet-gorgona-ai-jobs 10.31/16    │   peering   │ vnet-gorgona-ai 10.30/16               │
 │  snet-aca 10.31.0/23 (delegated) │◄──────────►│  snet-pe 10.30.2.0/27                  │
 │   cae-gorgona-ai-jobs (internal) │ no transit  │   pe-psql ─► psql-gorgona-ai (B1ms)    │
 │    caj-…-netcheck / 5 AI jobs    │ no gateways │   pe-kv   ─► kv-gba-ai                 │
 └──────────────┬───────────────────┘             │   pe-blob ─► stevid…gba (evidence)     │
                │ Azure-provided DNS (168.63.129.16)└────────────────────────────────────────┘
                ▼
   private DNS zones (global, in rg-gorgona-ai), each linked to BOTH VNets:
     privatelink.postgres.database.azure.com · privatelink.vaultcore.azure.net · privatelink.blob.core.windows.net
   (no DNS resolver, no forwarder: linked zones are honoured by Azure DNS)

 Public endpoints by design (no private endpoint exists at these tiers):
   sb-gorgona-ai (Service Bus Basic, local auth disabled, Entra only)  ── see decision below
   gorgonaacr…  (Container Registry Standard, admin off, anonymous off, AcrPull via managed identity)
```

## Connectivity per dependency of the 5 jobs

| Dependency | Used by | Path from West US 3 | Resolution | Public fallback? | Proven by |
|---|---|---|---|---|---|
| AI PostgreSQL | bootstrap, migrate, tick, health, intake | Global peering to the private endpoint | Linked `privatelink.postgres` zone, private IP | **No**: public access is disabled. A missing link resolves publicly, which netcheck fails and the server refuses. | netcheck (DNS private + TCP 5432); then bootstrap/migrate |
| AI Key Vault | All jobs (secret references) | The platform resolves the reference with the job's managed identity over the environment network, then peering to the private endpoint. Microsoft docs imply this: their firewall guidance lists the `AzureKeyVault` tag. **This is the main assumption step 2 proves.** | Linked `privatelink.vaultcore` zone | **No**: public access is disabled; trusted-services bypass exists only for ARM `getSecret`. | The probe job starts only if its Key Vault reference resolves; netcheck reports `secrets_present` plus DNS/TCP 443. |
| Evidence storage | **None of the 5 jobs today** (no blob SDK in `ai/src`) | Peering to the private endpoint (linked for completeness) | Linked `privatelink.blob` zone | **No**: public access is disabled and the ACL bypass is None. | netcheck DNS/TCP |
| Service Bus | intake job and its KEDA scaler | **Public endpoint** (Basic tier has no private endpoint) | Public | n/a: **this is public access**, Entra-only | netcheck TCP 5671 (expect public) |
| Container Registry | All jobs (image pull) | **Public endpoint** (Standard tier has no private endpoint) | Public | n/a: unchanged registry networking, Entra-only pull | The job starting at all |
| Azure ML | **None of the 5 jobs** (no ML SDK use; ML training is separate) | none | none | none | none |
| Log Analytics | environment diagnostics and console logs | Azure Monitor ingestion (platform-managed, cross-region) | none | none | Logs appear |

### Owner decision needed before step 3: Service Bus

Your constraint says no public Service Bus access. The existing Central US namespace is **Basic**. Microsoft Learn says private endpoints and service endpoints exist only in Premium, and IP firewall rules start at Standard. Today the namespace is reachable only on its public endpoint, with Entra RBAC and local keys disabled. That is true from Central US as well, so it is not a new exposure from West US 3. Options:

1. **Accept** the public endpoint with Entra-only auth, as originally designed. $0 extra.
2. **Premium** (1 messaging unit, $0.9275/h ≈ **$677/month** list) with a private endpoint in `vnet-gorgona-ai`, a `privatelink.servicebus.windows.net` zone linked to both VNets, and public access disabled.
   - This changes or replaces a working Central US resource, so it needs a separate approval.
   - Whether Basic can be upgraded in place was not verified.
3. **Standard** ($10/month base) with an IP firewall that allows only a static egress IP for the jobs environment. That needs a NAT gateway in West US 3 (price not retrieved). The endpoint stays public but restricted.
4. **Remove Service Bus** from the jobs plane: evidence would be enqueued only through PostgreSQL. This changes the ADR-0013 event path.

### Container Registry pull from West US 3

- The registry is **Standard** in Central US: public endpoint, admin disabled, anonymous pull disabled, AcrPull by managed identity. Private endpoints and geo-replication need **Premium**.
- West US 3 jobs pull over the public endpoint exactly as Central US jobs would. **Nothing about registry networking is weakened.**
- Private connectivity for the registry is **not possible at the current tier.**

| Option | Monthly fixed | Effect |
|---|---|---|
| Keep as is (recommended until measured) | $0 | Cross-region pull: $0.02/GB × 63.4 MB per cold pull (see variable model) |
| Second registry, Basic, West US 3 | ≈ $5.07 ($0.1666/day) | Same-region pulls. Requires pushing images to both registries and new AcrPull grants. Still a public endpoint. |
| Premium + geo-replica in West US 3 | ≈ +$81 (Premium $1.6666/day vs Standard $0.6666/day, plus replica $1.6666/day) | Same-region data endpoint; makes a private endpoint possible (extra hourly and per-GB Private Link fees) |
| Premium, private endpoint, no replica | ≈ +$30 + private endpoint fees | Private pull across peering ($0.07/GB peering, see below) |

## Cost

### New fixed monthly cost (steps 1–3): ≈ $0.00

| Item | Fixed | Source |
|---|---|---|
| Container Apps environment (internal, Consumption only; no Dedicated profile, no inbound private endpoint, no planned maintenance) | $0 | Azure pricing page: no fixed charge for a Consumption-only environment. The "Environment Management" meter ($0.10/h) applies only to the Dedicated plan, inbound private endpoint, and planned maintenance, none of which is used. |
| VNet, subnet, peering (both directions) | $0 | Peering is billed per GB only |
| 3 DNS zone links | $0 | Zones already exist and are billed in Central US. Queries cost $0.40 per million (variable). |
| Probe job and 5 jobs while idle | $0 | Consumption jobs bill only while running |

### Variable cross-region model

Unknown means not measured yet; it is measurable after step 2 and step 3.

| Path | Billed meters (list) | Volume today |
|---|---|---|
| Jobs ↔ AI PostgreSQL (peering) | Global VNet peering: **$0.035/GB egress + $0.035/GB ingress, charged at both ends**, so ≈ **$0.07 per GB moved in either direction**. Plus Private Link data processing (price not retrieved). | **Unknown.** Depends on evidence, dataset and embedding volume; tick and intake read and write the AI database. |
| Platform → Key Vault (peering) | Same peering rates | Kilobytes per execution start. Roughly 4,320 scheduled executions/month gives an estimated < 0.1 GB (unmeasured). |
| Jobs → evidence blob | Same peering rates | **0** today (not used) |
| Intake and KEDA ↔ Service Bus (public, Central US) | Inter-region data transfer **$0.02/GB** (North America) | **Unknown.** KEDA polls every 30 s (~86k polls/month) plus message payloads. |
| Image pull from the Central US registry (public) | Inter-region data transfer **$0.02/GB** | 63.4 MB compressed per cold pull. **Worst case**, every scheduled execution pulls cold: 4,320 × 63.4 MB ≈ 274 GB → ≈ **$5.48/month**, plus intake and probe runs. **Best case** (node image cache hits) ≈ $0. Cache behaviour for Consumption jobs is undocumented, so the real figure is **unknown** within that range. |
| Logs → Log Analytics (Central US) | Ingestion $2.76/GB after 5 GB/month free (shared). Cross-region ingestion bandwidth not determined. | **Unknown** |
| Compute (Consumption, West US 3) | $0.000024/vCPU-s and $0.000003/GiB-s, after a free grant of 180k vCPU-s and 360k GiB-s per month per subscription, shared with staging | **Unknown durations.** Tick 2,880 runs/month and health 1,440 runs/month at 0.5 vCPU / 1 GiB. Cost = max(0, 0.5·T − 180,000)·0.000024 + max(0, T − 360,000)·0.000003, where T is total execution seconds. |

## Corrections to the earlier region report

- **AI PostgreSQL is Burstable B1ms, which does not support HA.** It stays unchanged. "Zone-redundant HA" in the region table describes what a region offers for a **future production** server. That server must be General Purpose or Memory Optimized and is designed separately.
- **North Central US and West Central US.** Front Door Private Link is region-agnostic and could use a nearby Private Link region, so that is **not** the reason they are excluded. They are excluded on other grounds:
  - North Central US has no availability zones and `Standard_D4ds_v5` is location-restricted for this subscription.
  - West Central US has no availability zones and PostgreSQL zone-redundant HA is disabled for this subscription.
- **Canada Central** passes PostgreSQL 18, zone-redundant HA, Front Door Private Link and availability zones. However, `Standard_D4ds_v5` (the ML CPU node) is **location-restricted for this subscription** (`NotAvailableForSubscription`, location and all zones). It therefore fails the ML-node requirement as currently specified.
  - If that SKU were changed, it would be technically viable.
  - It is undesirable only if US data residency is a requirement, which has not been stated.
- **For the jobs plane alone**, any region that can create an internal Container Apps environment and peer globally is technically viable. That includes South Central US, West US 2 and East US 2. West US 3 is chosen because it also passes every whole-platform check (for any later consolidation) and has the lowest unit prices of the candidates.
- **Container Apps capacity** cannot be verified read-only in any region. Step 1 is the test.

## Rollback

| After | Command (owner-approved) | Effect on Central US |
|---|---|---|
| Step 1 | `az stack sub delete --name gorgona-ai-jobs --action-on-unmanage deleteAll` | None. It deletes only the West US 3 resource group, VNet, environment and diagnostics. |
| Step 2 | Same command | It deletes everything in West US 3 and the probe job. The 4 Central US child resources (peering `peer-ai-to-ai-jobs` and the 3 `link-gorgona-ai-jobs` links) **cannot be deleted yet**: the `gorgona-ai` stack's denyDelete deny assignments apply to child scopes. Two ways to handle them: **(a)** leave them; they are inert, because the peering shows Disconnected once the remote VNet is gone and the links point at a deleted VNet; cost $0. **(b)** With a separate approval, add `excludedActions` for `virtualNetworkPeerings/delete` and `virtualNetworkLinks/delete` to the `gorgona-ai` deny settings, then delete them. |
| Step 3 | Set `GBA_AI_JOBS_DEPLOY=false` and update the stack; detachAll leaves the jobs, so delete them explicitly. Or delete the whole stack as above. | None. Durable state lives in AI PostgreSQL (`ai.job_runs`). |

No rollback step deletes or recreates a working Central US resource.

## Next owner decisions

1. Approve **step 1** (environment only, 4 resources, $0 fixed).
2. After it reaches `Succeeded`: approve **step 2** (peering, links, probe job, one probe run).
3. Decide on **Service Bus** (options 1–4) before step 3.
4. After steps 1 and 2 are proven: approve deletion of the failed Central US `cae-gorgona-ai`.
5. Note: a separate process committed in this repository on 2026-09-30 (`251e011`, `a2c4ddd`, `3edf1f0`, and `20b63e0`, which contains this IaC). Those commits were not made by this session and were not altered.
