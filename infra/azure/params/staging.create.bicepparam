// GORGONA platform — staging, create. Non-secret values come from the operator environment
// set by scripts/stack-up.ps1; secrets are generated at deploy time. Nothing secret is committed.
using '../main-platform.bicep'

param env = 'staging'
param location = 'eastus2'
param uniqueSuffix = readEnvironmentVariable('GBA_UNIQUE_SUFFIX')
param addressPrefix = '10.20.0.0/16'
param acaSubnetPrefix = '10.20.0.0/23'
param peSubnetPrefix = '10.20.2.0/27'
param frontDoorEndpointNames = ['gba-stg-a', 'gba-stg-b']
param image = readEnvironmentVariable('GBA_IMAGE')
param sharedResourceGroupName = 'rg-gorgona-shared'
param acrName = readEnvironmentVariable('GBA_ACR_NAME')
param acrLoginServer = readEnvironmentVariable('GBA_ACR_LOGIN_SERVER')
param logAnalyticsWorkspaceId = readEnvironmentVariable('GBA_LOG_WORKSPACE_ID')
param appInsightsConnectionString = readEnvironmentVariable('GBA_APPINSIGHTS_CONNECTION_STRING')
param authIssuer = readEnvironmentVariable('GBA_AUTH_ISSUER')
param authAudience = readEnvironmentVariable('GBA_AUTH_AUDIENCE')
param authJwksUrl = readEnvironmentVariable('GBA_AUTH_JWKS_URL')
param postgresSku = 'Standard_D2ds_v5'
param postgresTier = 'GeneralPurpose'
param postgresStorageGB = 64
param postgresBackupRetentionDays = 7
param postgresGeoRedundantBackup = false
param postgresZoneRedundantHa = false
param minReplicas = 1
param maxReplicas = 3
param dbPoolMaxSize = 10
param budgetAmount = 100
param budgetStartDate = readEnvironmentVariable('GBA_BUDGET_START')
param budgetContactEmails = [readEnvironmentVariable('GBA_BUDGET_EMAIL')]
param operatorPrincipalId = readEnvironmentVariable('GBA_OPERATOR_OBJECT_ID')
param postgresAdminPassword = readEnvironmentVariable('GBA_PG_ADMIN_PASSWORD')
param ownerRolePassword = readEnvironmentVariable('GBA_OWNER_ROLE_PASSWORD')
param appRolePassword = readEnvironmentVariable('GBA_APP_ROLE_PASSWORD')
