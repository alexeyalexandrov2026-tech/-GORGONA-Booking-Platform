// GORGONA platform environment: staging (class 4, ephemeral, stack deleteAll) or
// production (class 5, stack detachAll + denyDelete + RG lock). Production-parity
// topology: Front Door Premium + WAF -> Private Link -> Container Apps (VNet, public
// access off) -> private PostgreSQL 18 + Key Vault. Deploy only via
// scripts/stack-up.ps1 after explicit owner approval.
targetScope = 'subscription'

@description('Environment.')
@allowed(['staging', 'production'])
param env string

@description('Azure region (owner decision: centralus).')
param location string

@description('Globally unique suffix for server/vault names (lowercase alphanumeric).')
@minLength(3)
@maxLength(8)
param uniqueSuffix string

@description('VNet address plan (non-overlapping per environment).')
param addressPrefix string
param acaSubnetPrefix string
param peSubnetPrefix string

@description('Front Door endpoint names (staging: two FAKE-tenant hosts, no DNS needed).')
param frontDoorEndpointNames array

@description('Image by digest in the shared ACR.')
param image string

@description('Shared resources (from main-shared outputs).')
param sharedResourceGroupName string
param acrName string
param acrLoginServer string
param logAnalyticsWorkspaceId string
param appInsightsConnectionString string
@description('Shared Application Insights component name (main-shared: appi-gorgona-shared).')
param appInsightsName string = 'appi-gorgona-shared'

@description('OIDC provider for staff APIs (public values). Required by the staging start guard.')
param authIssuer string
param authAudience string
param authJwksUrl string

@description('PostgreSQL sizing.')
param postgresSku string
@allowed(['Burstable', 'GeneralPurpose', 'MemoryOptimized'])
param postgresTier string
param postgresStorageGB int
param postgresBackupRetentionDays int
param postgresGeoRedundantBackup bool
param postgresZoneRedundantHa bool

@description('Replica bounds and per-replica pool size.')
param minReplicas int
param maxReplicas int
param dbPoolMaxSize int

@description('Environment budget (staging) amount and operator recipients (budget and alerts).')
param budgetAmount int = 100
param budgetStartDate string
param budgetContactEmails array

@description('Object ID of the deploying operator (Key Vault Secrets Officer on the vault of this environment only).')
param operatorPrincipalId string

@description('Generated at deploy time by scripts/stack-up.ps1 (URL-safe); never committed.')
@secure()
param postgresAdminPassword string
@secure()
param ownerRolePassword string
@secure()
param appRolePassword string

var namePrefix = 'gorgona-${env}'
var isProduction = env == 'production'
var tags = {
  app: 'gorgona'
  env: env
  lifecycle: isProduction ? 'production' : 'ephemeral'
  managedBy: 'bicep-stack:${namePrefix}'
}
var adminLogin = 'gbaadmin'
var databaseName = 'gorgona_booking'
var dnsZones = [
  'privatelink.postgres.database.azure.com'
  'privatelink.vaultcore.azure.net'
]

resource rg 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: 'rg-${namePrefix}'
  location: location
  tags: tags
}

resource sharedRg 'Microsoft.Resources/resourceGroups@2025-04-01' existing = {
  name: sharedResourceGroupName
}

module network 'modules/network.bicep' = {
  name: '${namePrefix}-network'
  scope: rg
  params: {
    location: location
    namePrefix: namePrefix
    tags: tags
    addressPrefix: addressPrefix
    acaSubnetPrefix: acaSubnetPrefix
    peSubnetPrefix: peSubnetPrefix
    privateDnsZoneNames: dnsZones
  }
  // Fail fast and cheap: Front Door is the resource most likely to be refused (for
  // example on Free Trial subscriptions). Everything billable (PostgreSQL, private
  // endpoints, Key Vault, Container Apps) sits behind the network, so nothing billable
  // is created unless the Front Door profile succeeds first. Ordering only.
  dependsOn: [frontDoor]
}

module identities 'modules/identities.bicep' = {
  name: '${namePrefix}-identities'
  scope: rg
  params: { location: location, namePrefix: namePrefix, tags: tags }
}

module postgres 'modules/postgres.bicep' = {
  name: '${namePrefix}-postgres'
  scope: rg
  params: {
    location: location
    name: 'psql-${namePrefix}-${uniqueSuffix}'
    tags: tags
    skuName: postgresSku
    skuTier: postgresTier
    storageSizeGB: postgresStorageGB
    backupRetentionDays: postgresBackupRetentionDays
    geoRedundantBackup: postgresGeoRedundantBackup
    zoneRedundantHa: postgresZoneRedundantHa
    administratorLogin: adminLogin
    administratorLoginPassword: postgresAdminPassword
    allowedExtensions: 'BTREE_GIST'
    peSubnetId: network.outputs.peSubnetId
    privateDnsZoneId: network.outputs.privateDnsZoneIds[0]
  }
}

var pgHost = postgres.outputs.fqdn
module vault 'modules/keyvault.bicep' = {
  name: '${namePrefix}-keyvault'
  scope: rg
  params: {
    location: location
    name: 'kv-gba-${env == 'production' ? 'prd' : 'stg'}-${uniqueSuffix}'
    tags: tags
    peSubnetId: network.outputs.peSubnetId
    privateDnsZoneId: network.outputs.privateDnsZoneIds[1]
    operatorPrincipalId: operatorPrincipalId
    secrets: {
      'database-url': 'postgresql://gba_app:${appRolePassword}@${pgHost}:5432/${databaseName}?sslmode=require'
      'migration-database-url': 'postgresql://gba_owner:${ownerRolePassword}@${pgHost}:5432/${databaseName}?sslmode=require'
      'admin-database-url': 'postgresql://${adminLogin}:${postgresAdminPassword}@${pgHost}:5432/postgres?sslmode=require'
      'postgres-admin-password': postgresAdminPassword
      'owner-password': ownerRolePassword
      'app-password': appRolePassword
    }
  }
}

module apiSecrets 'modules/secret-reader.bicep' = {
  name: '${namePrefix}-api-secrets'
  scope: rg
  params: {
    vaultName: vault.outputs.name
    secretNames: ['database-url']
    principalId: identities.outputs.apiPrincipalId
  }
}

module jobSecrets 'modules/secret-reader.bicep' = {
  name: '${namePrefix}-job-secrets'
  scope: rg
  params: {
    vaultName: vault.outputs.name
    secretNames: ['migration-database-url', 'admin-database-url', 'owner-password', 'app-password']
    principalId: identities.outputs.jobsPrincipalId
  }
}

module apiPull 'modules/acr-pull.bicep' = {
  name: '${namePrefix}-api-acrpull'
  scope: sharedRg
  params: { registryName: acrName, principalId: identities.outputs.apiPrincipalId }
}

module jobsPull 'modules/acr-pull.bicep' = {
  name: '${namePrefix}-jobs-acrpull'
  scope: sharedRg
  params: { registryName: acrName, principalId: identities.outputs.jobsPrincipalId }
}

module apiTelemetry 'modules/appinsights-publisher.bicep' = {
  name: '${namePrefix}-api-telemetry'
  scope: sharedRg
  params: { appInsightsName: appInsightsName, principalId: identities.outputs.apiPrincipalId }
}

module frontDoor 'modules/frontdoor-profile.bicep' = {
  name: '${namePrefix}-frontdoor'
  scope: rg
  params: { namePrefix: namePrefix, tags: tags, endpointNames: frontDoorEndpointNames }
}

module apps 'modules/containerapps.bicep' = {
  name: '${namePrefix}-containerapps'
  scope: rg
  params: {
    location: location
    namePrefix: namePrefix
    tags: tags
    infrastructureSubnetId: network.outputs.acaSubnetId
    zoneRedundant: isProduction
    logAnalyticsWorkspaceId: logAnalyticsWorkspaceId
    appInsightsConnectionString: appInsightsConnectionString
    image: image
    registryServer: acrLoginServer
    apiIdentityId: identities.outputs.apiId
    apiIdentityClientId: identities.outputs.apiClientId
    jobsIdentityId: identities.outputs.jobsId
    keyVaultUri: vault.outputs.uri
    gbaEnv: env
    frontDoorId: frontDoor.outputs.frontDoorId
    authIssuer: authIssuer
    authAudience: authAudience
    authJwksUrl: authJwksUrl
    minReplicas: minReplicas
    maxReplicas: maxReplicas
    dbPoolMaxSize: dbPoolMaxSize
    databaseName: databaseName
  }
  // Key Vault references and registry pulls are resolved at creation time.
  dependsOn: [apiSecrets, jobSecrets, apiPull, jobsPull]
}

module routing 'modules/frontdoor-routing.bicep' = {
  name: '${namePrefix}-frontdoor-routing'
  scope: rg
  params: {
    location: location
    profileName: frontDoor.outputs.profileName
    endpointNames: frontDoorEndpointNames
    endpointIds: frontDoor.outputs.endpointIds
    wafPolicyId: frontDoor.outputs.wafPolicyId
    originFqdn: apps.outputs.apiFqdn
    environmentId: apps.outputs.environmentId
  }
}

module alerts 'modules/alerts.bicep' = {
  name: '${namePrefix}-alerts'
  scope: rg
  params: {
    location: location
    namePrefix: namePrefix
    tags: tags
    contactEmails: budgetContactEmails
    frontDoorProfileId: frontDoor.outputs.profileId
    postgresServerId: postgres.outputs.id
    apiContainerAppId: apps.outputs.apiId
    appInsightsId: apiTelemetry.outputs.appInsightsId
    serviceName: apps.outputs.apiServiceName
    connectionBudget: maxReplicas * dbPoolMaxSize + 16
  }
}

module budget 'modules/budget.bicep' = if (!isProduction) {
  name: '${namePrefix}-budget'
  params: {
    name: 'budget-${namePrefix}'
    amount: budgetAmount
    startDate: budgetStartDate
    contactEmails: budgetContactEmails
    resourceGroupName: rg.name
  }
}

module lock 'modules/rg-lock.bicep' = if (isProduction) {
  name: '${namePrefix}-lock'
  scope: rg
  params: { notes: 'Production GORGONA platform. Deletion requires removing this lock deliberately.' }
}

output resourceGroupName string = rg.name
output frontDoorEndpointHostNames array = frontDoor.outputs.endpointHostNames
output containerAppsEnvironmentId string = apps.outputs.environmentId
output migrateJobName string = apps.outputs.migrateJobName
output bootstrapJobName string = apps.outputs.bootstrapJobName
output postgresFqdn string = postgres.outputs.fqdn
output alertNames array = alerts.outputs.alertNames
