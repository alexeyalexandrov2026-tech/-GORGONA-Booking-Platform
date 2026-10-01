// Persistent AI learning plane — update. Secrets: read back from the AI Key Vault; never committed.
using '../main-ai.bicep'

param location = 'centralus'
param uniqueSuffix = readEnvironmentVariable('GBA_UNIQUE_SUFFIX')
param addressPrefix = '10.30.0.0/16'
param acaSubnetPrefix = '10.30.0.0/23'
param peSubnetPrefix = '10.30.2.0/27'
param appInsightsId = readEnvironmentVariable('GBA_APPINSIGHTS_ID')
param containerRegistryId = readEnvironmentVariable('GBA_ACR_ID')
param operatorPrincipalId = readEnvironmentVariable('GBA_OPERATOR_OBJECT_ID')
param deployGpuCluster = false
// Azure ML quota read 2026-10-01 (centralus): standardDDSv5Family 4 vCPUs; D4ds_v5 has 4.
param cpuMaxNodes = 1
// Dedicated AI jobs environment (ADR-0013). Container Apps environment quota is 20 per
// region since the pay-as-you-go upgrade (read 2026-10-01).
param deployJobsEnvironment = true
param lockResourceGroup = true
param postgresAdminPassword = az.getSecret(readEnvironmentVariable('GBA_SUBSCRIPTION_ID'), 'rg-gorgona-ai', readEnvironmentVariable('GBA_AI_KV_NAME'), 'ai-postgres-admin-password')
