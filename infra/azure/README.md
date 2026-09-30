# Azure infrastructure (Bicep)

Design: [`docs/architecture/AZURE_ARCHITECTURE.md`](../../docs/architecture/AZURE_ARCHITECTURE.md). Plan, costs and approvals: [`docs/plan/M4_PLAN.md`](../../docs/plan/M4_PLAN.md).

**Nothing here has been deployed.** Every Azure action needs the owner's explicit approval for that specific action.

| Entry point | Stack | Lifecycle | On unmanage / deny |
|---|---|---|---|
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

1. `stack-up.ps1 -Stack shared`: budgets, Log Analytics, App Insights, ACR.
2. `stack-up.ps1 -Stack ai`: the persistent learning plane.
3. Build and push the image; record its digest in `GBA_IMAGE`.
4. `stack-up.ps1 -Stack staging`. Then:
   - approve the Front Door private endpoint on the Container Apps environment;
   - run the bootstrap job once, then the migrate job;
   - seed the FAKE tenants, host mappings and embed origins.
5. Run the staging acceptance.
6. Tear down with `staging-down.ps1 -Execute`.
7. Production is created only after staging evidence and owner authorization.

## Secrets

- **Create mode.** `stack-up.ps1` generates URL-safe database passwords with a CSPRNG. They exist only in the az child process environment, and Bicep writes them only to the environment's Key Vault as DSN secrets.
- **Update mode.** The `*.update.bicepparam` files read them back with `getSecret()`. The vault allows ARM template deployment through the trusted-services bypass; public network access stays disabled.
- **Least privilege.** The API identity can read only `database-url`. The jobs identity can read only the migration, admin and role-password secrets.

## Known compatibility items (prove in staging)

- `gba-db bootstrap` against the Azure PostgreSQL admin, which is not a superuser, under PostgreSQL 16+ CREATEROLE rules.
- Application settings consumed by checkpoint B code, which do not exist in the app yet: `GBA_TRUSTED_PROXY`, `GBA_FRONT_DOOR_ID`, `APPLICATIONINSIGHTS_CONNECTION_STRING`.
- The image entrypoint and the `gba-db` console script, which the checkpoint B Dockerfile provides.
