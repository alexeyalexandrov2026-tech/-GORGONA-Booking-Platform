# Azure infrastructure (Bicep)

Design: [`docs/architecture/AZURE_ARCHITECTURE.md`](../../docs/architecture/AZURE_ARCHITECTURE.md). Plan, costs and approvals: [`docs/plan/M4_PLAN.md`](../../docs/plan/M4_PLAN.md).

**Nothing here has been deployed.** Every Azure action needs the owner's explicit approval for that specific action.

| Entry point | Stack | Lifecycle | On unmanage / deny |
|---|---|---|---|
| `main-budgets.bicep` | `gorgona-budgets` | Cost guardrails (free), **deployed first** | detachAll / denyDelete |
| `main-shared.bicep` | `gorgona-shared` | Persistent | detachAll / denyDelete + RG lock |
| `main-ai.bicep` | `gorgona-ai` | Persistent AI learning plane | detachAll / denyDelete + RG lock |
| `main-platform.bicep` (`env=staging`) | `gorgona-staging` | Ephemeral, production-parity | **deleteAll** / none |
| `main-platform.bicep` (`env=production`) | `gorgona-production` | Production-only | detachAll / denyDelete + RG lock |

## Validate locally (no Azure calls)

```powershell
az bicep lint --file main-shared.bicep     # likewise main-ai.bicep, main-platform.bicep
az bicep build-params --file params/staging.create.bicepparam --outfile $env:TEMP/staging.json
.\scripts\stack-up.ps1 -Stack staging        # dry run: prints plan and commands only
.\scripts\staging-down.ps1                   # dry run
```

`bicepconfig.json` turns security-relevant linter rules into errors: secrets in outputs, secure defaults, hard-coded locations and unused parameters. The parameter files read every environment-specific value with `readEnvironmentVariable`, so the repository holds no subscription IDs, emails or secrets.

## Order (each step separately approved)

0. The owner upgrades the subscription to pay-as-you-go (Front Door is not available on the Free Trial; see M4_REPORT).
1. `stack-up.ps1 -Stack budgets`: a subscription budget (actual 25/50/80/100%, forecast 100%) and a daily cost-anomaly alert, both free. `stack-up.ps1` refuses every other stack until `gorgona-budgets` has succeeded.
2. `register-providers.ps1` (dry run lists the state; `-Execute` registers). This is a free subscription-level change; use `-Scope all` only when the AI plane is approved.
3. `stack-up.ps1 -Stack shared`: Log Analytics, App Insights, ACR.
4. `stack-up.ps1 -Stack ai`: the persistent learning plane.
5. Build and push the image; record its digest in `GBA_IMAGE`.
6. `stack-up.ps1 -Stack staging`. Then:
   - approve the Front Door private endpoint on the Container Apps environment;
   - run the bootstrap job once, then the migrate job;
   - seed the FAKE tenants, host mappings and embed origins.
7. Run the staging acceptance.
8. Tear down with `staging-down.ps1 -Execute`.
9. Production is created only after staging evidence and owner authorization.

## Secrets

- **Create mode.** `stack-up.ps1` generates URL-safe database passwords with a CSPRNG. They exist only in the az child process environment, and Bicep writes them only to the environment's Key Vault as DSN secrets.
- **Update mode.** The `*.update.bicepparam` files read them back with `getSecret()`. The vault allows ARM template deployment through the trusted-services bypass; public network access stays disabled.
- **Least privilege.** The API identity can read only `database-url`. The jobs identity can read only the migration, admin and role-password secrets.

## Alerts and telemetry

- `modules/alerts.bicep` deploys, per environment:
  - one operator action group (the budget e-mail recipients);
  - six metric alerts: Front Door origin health and 5xx share, PostgreSQL `is_db_alive`, storage and connection budget, and replica restarts;
  - three log alerts on the shared Application Insights: p95 latency, confirmation 5xx, and a `TENANT_NOT_FOUND` anomaly.
- Log alerts filter on `cloud_RoleName`. The API sets `OTEL_SERVICE_NAME=gorgona-api-<env>`.
- Application Insights disables local (key) auth. Each environment's stack grants its API identity **Monitoring Metrics Publisher** on the shared component (`modules/appinsights-publisher.bicep`), and the app authenticates with `AZURE_CLIENT_ID`.

## Releases

- `.github/workflows/deploy-staging.yml` is dormant. It uses a manual trigger, the `staging` GitHub environment with required reviewers, OIDC federation (no Azure secret in GitHub), a digest-pinned image scanner, and `GBA_STAGING_WINDOW_OPEN=true`.
- It runs build -> scan -> push by digest -> migrate job -> new revision -> Front Door smoke -> automatic traffic rollback on failure.
- `promote-production.yml` refuses by design.
- A stack **update** must pass the currently deployed image digest in `GBA_IMAGE`; otherwise it reverts the revision.

## Known compatibility items (prove in staging)

- `gba-db bootstrap` against the Azure PostgreSQL admin, which is not a superuser, under PostgreSQL 16+ CREATEROLE rules.
- `max_connections` of the chosen SKU against the connection budget used by the `pool-saturation` alert.
- Alert queries against real telemetry (table and column names are validated by Azure only at rule creation).
