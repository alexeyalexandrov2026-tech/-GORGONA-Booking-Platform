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
param lockResourceGroup = true
param postgresAdminPassword = az.getSecret(readEnvironmentVariable('GBA_SUBSCRIPTION_ID'), 'rg-gorgona-ai', readEnvironmentVariable('GBA_AI_KV_NAME'), 'ai-postgres-admin-password')
