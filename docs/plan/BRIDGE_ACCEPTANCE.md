# Production bridge acceptance

Production remains gated behind the approved production-bridge acceptance process. No final production cutover occurs until the required bridge/security/E2E gates pass and the production deployment is explicitly authorized.

The bridge is the production-parity staging environment. It runs exactly the production topology:

- optional Cloudflare edge for frontend delivery only;
- Azure Front Door Premium with WAF;
- Private Link to private Container Apps;
- the GORGONA API;
- private PostgreSQL 18.

After its gates pass for an image digest, production is created from the **same** `main-platform.bicep` and receives the **same** digest. Production is never redesigned after staging. There is one authoritative booking/domain system, and PostgreSQL remains its system of record. See [AZURE_ARCHITECTURE §1a](../architecture/AZURE_ARCHITECTURE.md).

## How a gate is proven

`api/tools/bridge_acceptance.py` produces JSON evidence per image digest from three sources:

| Source | What it does | Changes Azure? |
|---|---|---|
| `http` | Runs through Front Door (positive checks) and around it (negative checks) from an outside client. It books only for `FAKE ` tenants. | No; it creates FAKE bookings in staging |
| `azure` | Read-only `az`: app secrets, registry auth, Key Vault/PostgreSQL/environment network settings, Front Door private endpoint approval, WAF mode, alerts, and this run's request IDs in Log Analytics | No |
| `record` | Records the outcome of operator drills (revision rollback, PITR restore). The tool never performs them. | The drills themselves are owner-approved actions |

`verify` passes only if every required check of every gate is present and ok for **one** digest. A missing input makes a gate **blocked**, never passed. `promote-production` runs `verify` before anything else.

## Gate matrix

| # | Gate | Control in code / IaC | Local proof today | Deployed proof (runner checks) |
|---|---|---|---|---|
| 1 | Real Azure E2E | the whole platform | full suite (PostgreSQL 18 + Chromium); `test_bridge_acceptance_live` | `http`: bootstrap of a FAKE tenant, availability, hold, confirm through Front Door |
| 2 | Authentication / OIDC | `auth/verifier.py`, OIDC settings required by the start guard | `test_authz_api`, `test_invitations_api` (fake IdP) | `http`: `/v1/me` returns 401 anonymous, 401 with an invalid token, 200 with a real staff token |
| 3 | Trusted Front Door boundary | `api/trusted_proxy.py` (exact `X-Azure-FDID`), start guard | `test_trusted_proxy`, `test_trusted_proxy_tenancy` | `http`: spoofed `X-Forwarded-Host`/`X-Azure-FDID` don't change the tenant. `azure`: app bound to this profile's Front Door ID |
| 4 | WAF / rate limiting | `frontdoor-profile.bicep`: Prevention, DRS 2.1, Bot Manager, 30 writes/min and 600 API calls/min per IP | Bicep build | `http`: attack signature gets an edge 403; opt-in write burst gets an edge 403. `azure`: policy is Prevention and Enabled |
| 5 | Private Link / private origin | `containerapps.bicep` (environment `publicNetworkAccess: Disabled`), `frontdoor-routing.bicep` (private-link origin) | Bicep build | `http`: the container app FQDN never reaches the app from outside. `azure`: environment public access disabled, Front Door private endpoint Approved |
| 6 | Private PostgreSQL | `postgres.bicep` (public access disabled, private endpoint) | none | `http`: a login attempt from outside never reaches PostgreSQL authentication. `azure`: public access disabled |
| 7 | Tenant isolation | Forced row-level security, host resolution, per-booking capability | `test_tenant_isolation`, `test_customer_api`; `test_bridge_acceptance_live` (two hosts) | `http`: two FAKE tenants on the two staging Front Door endpoints resolve separately; tenant A's booking is refused on tenant B's host |
| 8 | CSP / frame-ancestors | `api/framing.py`, migration 0007 (governed allowlist) | `test_embed_origins`, Chromium allow/deny tests | `http`: HTML carries `frame-ancestors 'self' <approved>`; the API carries `'none'`; an unapproved origin is absent |
| 9 | Authorized KA Nails origin | `gba-db embed-origin add` (audited) | Chromium test with a FAKE approved origin | `http`: the owner-approved KA site origin is listed in `frame-ancestors` |
| 10 | Direct-origin bypass refused | Private Link only, plus the Front Door ID check | `test_trusted_proxy` (forged headers return 404) | `http`: a direct request with forged Front Door headers never reaches the app |
| 11 | Secrets and managed identity | Key Vault references, user-assigned identities, AcrPull, no admin user | `test_container_image` (no baked secrets) | `azure`: no plaintext secrets, registry pull by identity, Key Vault public access disabled |
| 12 | Monitoring | OTLP to App Insights, JSON logs with request IDs, `alerts.bicep` | `test_request_logging`, `test_observability` | `azure`: this run's request IDs found in Log Analytics; all alert rules enabled |
| 13 | Rollback / recovery | `deploy-staging` (automatic traffic rollback), PostgreSQL PITR (7-day staging retention) | none | `record`: a revision rollback drill and a PITR restore drill, both owner-approved |

## Open items (owner decisions; nothing done without approval)

1. **Bridge compute placement.**
   - Central US refused new Container Apps environments on 2026-10-01 (`ManagedEnvironmentCapacityHeavyUsageError`). The staging bridge needs one, with the same security topology.
   - Options:
     - **(a)** retry Central US later;
     - **(b)** place the bridge, and therefore production, in a region with capacity, as one unit: Container Apps, PostgreSQL, Key Vault and the private network together.
   - Option (b) changes only `location` in `params/staging.*` / `params/production.*`; the topology stays the same. West US 3 passes every platform check (PostgreSQL 18, zone-redundant HA, Front Door Private Link, availability zones).
   - Proving the AI jobs environment there (Option A step 1) is direct evidence of Container Apps capacity in that region.
   - Not an option: weakening the topology (public origin, public database) or sharing an environment with the AI plane.
2. **OIDC positive check.**
   - Staging uses the tenant's Entra issuer with audience `api://gorgona-staging`.
   - No app registration exists, so staff APIs fail closed.
   - Gate 2 needs one Entra app registration and a test staff identity linked with `gba-db link-user`. That is an identity-plane change that needs your approval.
3. **KA Nails origin.** Gate 9 needs the owner-approved KA public site origin. In staging it is approved on a FAKE tenant; the KA tenant stays `not_live`.
4. **Drills.** The revision rollback is quick. The PITR drill restores to a **new** server, which is billed while it exists, and is deleted afterwards. Both happen inside the approved staging window.
5. **Rate-limit probe.** The probe sends up to the configured number of FAKE writes; it is opt-in and runs inside the window.

## Runbook (inside an approved staging window)

1. **Deploy and prepare.**
   - Deploy staging (`stack-up.ps1 -Stack staging`), then approve the Front Door private endpoint.
   - Run bootstrap and migrate.
   - Seed two FAKE tenants with `gba-db seed-fake --slug fake-a --host <endpoint-1 host>` and `--slug fake-b --host <endpoint-2 host>`.
   - Approve the KA origin on `fake-a`.
2. **Run the http checks:** `bridge_acceptance.py http ... --rate-limit-probe 60 --staff-token-env BRIDGE_STAFF_TOKEN --out http.json`.
3. **Run the azure checks:** `bridge_acceptance.py azure --run-id <from http.json> ... --out azure.json`.
4. **Record the drills:** `bridge_acceptance.py record --gate rollback_recovery --check revision_rollback_drill ...` and the same for `postgres_pitr_drill`.
5. **Verify and store.**
   - Run `bridge_acceptance.py verify --image-digest <digest> http.json azure.json drill-*.json`.
   - Store the evidence in `docs/acceptance/<digest>/`.
6. **Tear down staging** (`staging-down.ps1`).
7. **Production, when you choose.**
   - Create the production stack from the same Bicep.
   - Record your authorization in `docs/plan/production-authorizations/PA-<date>-<slug>.md`, naming the digest.
   - Run `promote-production`.
