# ADR-0012: Azure hosting foundation

- Status: Accepted as direction (2026-09-30, M4 checkpoint A). Supersedes ADR-0006. Nothing is provisioned.

## Decision

- **Azure** is the production target for GORGONA. The primary region is **Central US** (owner decision 2026-10-01). It replaced East US 2, the original choice, because PostgreSQL Flexible Server is offer-restricted for this subscription in East US 2 (`OfferRestricted`; read after provider registration). Central US offers PostgreSQL 18 with both planned SKUs, zone-redundant HA, three availability zones, Front Door Private Link to Container Apps, and East US 2 as its paired region. OCI, Supabase and Cloudflare are no longer part of the target design. The separate camera/OCI infrastructure (`gorgona-node`) is unrelated and untouched.
- **Architecture.** The architecture stays a modular monolith; M4 adds no microservices and no AKS.
  - Compute: Azure Container Apps in a workload-profiles environment, VNet-integrated, with public network access disabled.
  - The FastAPI app scales horizontally as identical replicas. Schema migrations run as a separate manual Container Apps job, never at replica start.
- **Ingress.** Azure Front Door Premium with WAF (managed default rule set, bot manager and rate-limit rules), connected to the Container Apps environment through Private Link. Direct origin access is impossible.
- **Tenant boundary behind Front Door.** The origin Host header is the Container Apps FQDN. The tenant host arrives in `X-Forwarded-Host`, which Front Door overwrites.
  - The app accepts `X-Forwarded-Host` only in an explicit trusted-proxy mode, and only when `X-Azure-FDID` equals the configured Front Door profile ID. Otherwise the request is rejected.
  - Browser-supplied tenant IDs remain meaningless (ADR-0009).
- **Database.** Azure Database for PostgreSQL Flexible Server, **PostgreSQL 18**. No downgrade.
  - Reached through a private endpoint and private DNS, with public network access disabled.
  - `btree_gist` is allowlisted in `azure.extensions`.
  - Existing roles and RLS are unchanged: owner role for migrations, runtime role for the API, `gba_runtime` group.
- **Secrets and identity.** User-assigned managed identities for ACR pull and Key Vault. Key Vault uses RBAC, purge protection and a private endpoint. Database role passwords are the only application secrets, and live only in Key Vault. Entra token authentication for PostgreSQL is a later hardening step.
- **Framing.** A governed per-tenant allowlist of embedding origins, emitted by the application as an HTTP `Content-Security-Policy: frame-ancestors` header. It is never a `<meta>` tag or `X-Frame-Options: ALLOW-FROM`.
- **Infrastructure as code: Bicep only**, with Azure deployment stacks per lifecycle boundary: shared, AI plane, staging, production. There is no second IaC implementation.
- **Environments.**
  - Staging is production-parity but **ephemeral**: created for an acceptance window, then deleted as one stack.
  - Production is created only after staging evidence and explicit owner authorization.
  - Resource groups are per environment; the IaC also works with separate subscriptions later.
- **Start guard.** `GBA_ENV=staging` is allowed only when OIDC, trusted-proxy mode and the framing policy are configured. `GBA_ENV=production` stays refused in code until a production milestone is authorized.

## Consequences

- Front Door Premium ($330/month base) is the largest fixed cost. It cannot scale to zero, and it is why staging is time-boxed.
- The trusted-proxy host mode and the framing allowlist are new application code with their own tests (M4 checkpoint B). Until then, production iframe embedding stays blocked (M3 security finding).
- `gba-db bootstrap` must be proven against the Azure admin, which is not a superuser, in staging before production. Any fix goes through tests; migrations 0001–0006 stay unchanged.
