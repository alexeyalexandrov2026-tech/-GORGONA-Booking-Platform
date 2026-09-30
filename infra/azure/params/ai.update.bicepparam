// Persistent AI learning plane — update. Secrets: read back from the AI Key Vault; never committed.
using '../main-ai.bicep'

param location = 'eastus2'
param uniqueSuffix = readEnvironmentVariable('GBA_UNIQUE_SUFFIX')
param addressPrefix = '10.30.0.0/16'
param acaSubnetPrefix = '10.30.0.0/23'
param peSubnetPrefix = '10.30.2.0/27'
param appInsightsId = readEnvironmentVariable('GBA_APPINSIGHTS_ID')
param containerRegistryId = readEnvironmentVariable('GBA_ACR_ID')
param operatorPrincipalId = readEnvironmentVariable('GBA_OPERATOR_OBJECT_ID')
param deployGpuCluster = false
// Deferred on this subscription: Container Apps allows 1 managed environment in eastus2
// (read 2026-09-30: "Managed Environment Count" limit 1), and that slot is the
// production-parity staging environment. It holds no jobs yet; turn on once the quota
// allows a second environment.
param deployJobsEnvironment = false
param lockResourceGroup = true
param postgresAdminPassword = az.getSecret(readEnvironmentVariable('GBA_SUBSCRIPTION_ID'), 'rg-gorgona-ai', readEnvironmentVariable('GBA_AI_KV_NAME'), 'ai-postgres-admin-password')
