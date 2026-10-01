// Persistent AI learning plane (ADR-0013). Its own resource group and stack
// `gorgona-ai` (detachAll + denyDelete) plus a CanNotDelete lock. It is never part
// of the staging stack, holds no staging role assignments, and survives any staging
// teardown. Classes: 1 persistent (storage, AI PostgreSQL, Key Vault, private
// endpoints, ML workspace), 2 scale-to-zero (Service Bus, jobs environment),
// 3 on-demand (ML clusters at 0 nodes).
targetScope = 'subscription'

@description('Azure region (owner decision: centralus).')
param location string

@description('Globally unique suffix (lowercase alphanumeric).')
@minLength(3)
@maxLength(8)
param uniqueSuffix string

@description('VNet address plan (must not overlap platform environments).')
param addressPrefix string
param acaSubnetPrefix string
param peSubnetPrefix string

@description('Shared resources (from main-shared outputs).')
param appInsightsId string
param containerRegistryId string
param acrLoginServer string
param logAnalyticsWorkspaceId string

@description('AI worker image by digest (gorgona-ai@sha256:...). Empty = jobs not deployed yet.')
param aiWorkerImage string = ''

@description('AI PostgreSQL sizing (Burstable B1ms is in the free account for 12 months).')
param postgresSku string = 'Standard_B1ms'
@allowed(['Burstable', 'GeneralPurpose', 'MemoryOptimized'])
param postgresTier string = 'Burstable'
param postgresStorageGB int = 32

@description('Declare the GPU training cluster (needs GPU quota and pay-as-you-go).')
param deployGpuCluster bool = false

@description('CPU training cluster maximum nodes (bounded by the regional Azure ML vCPU quota).')
@minValue(1)
param cpuMaxNodes int = 2

@description('Deploy the AI jobs Container Apps environment. Part of the architecture; turn off only where the regional managed-environment quota cannot hold it next to the platform environment (see params).')
param deployJobsEnvironment bool = true

@description('Apply a CanNotDelete lock to the AI resource group.')
param lockResourceGroup bool = true

@description('Object ID of the deploying operator (Key Vault Secrets Officer on the vault of this environment only).')
param operatorPrincipalId string

@description('Generated at deploy time by scripts/stack-up.ps1; never committed.')
@secure()
param postgresAdminPassword string
@secure()
param ownerRolePassword string
@secure()
param workerRolePassword string

var namePrefix = 'gorgona-ai'
var tags = {
  app: 'gorgona'
  env: 'ai'
  lifecycle: 'persistent'
  managedBy: 'bicep-stack:gorgona-ai'
}
var dnsZones = [
  'privatelink.postgres.database.azure.com'
  'privatelink.vaultcore.azure.net'
  'privatelink.blob.${environment().suffixes.storage}'
]

resource rg 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: 'rg-gorgona-ai'
  location: location
  tags: tags
}

module network 'modules/network.bicep' = {
  name: 'ai-network'
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
}

module evidence 'modules/ai-evidence-storage.bicep' = {
  name: 'ai-evidence'
  scope: rg
  params: {
    location: location
    name: 'stevid${uniqueSuffix}gba'
    tags: tags
    peSubnetId: network.outputs.peSubnetId
    privateDnsZoneId: network.outputs.privateDnsZoneIds[2]
  }
}

module postgres 'modules/postgres.bicep' = {
  name: 'ai-postgres'
  scope: rg
  params: {
    location: location
    name: 'psql-${namePrefix}-${uniqueSuffix}'
    tags: tags
    skuName: postgresSku
    skuTier: postgresTier
    storageSizeGB: postgresStorageGB
    backupRetentionDays: 35
    geoRedundantBackup: true
    zoneRedundantHa: false
    administratorLogin: 'gbaaiadmin'
    administratorLoginPassword: postgresAdminPassword
    allowedExtensions: 'VECTOR'
    peSubnetId: network.outputs.peSubnetId
    privateDnsZoneId: network.outputs.privateDnsZoneIds[0]
  }
}

module vault 'modules/keyvault.bicep' = {
  name: 'ai-keyvault'
  scope: rg
  params: {
    location: location
    name: 'kv-gba-ai-${uniqueSuffix}'
    tags: tags
    peSubnetId: network.outputs.peSubnetId
    privateDnsZoneId: network.outputs.privateDnsZoneIds[1]
    operatorPrincipalId: operatorPrincipalId
    secrets: {
      'ai-postgres-admin-password': postgresAdminPassword
      'ai-owner-password': ownerRolePassword
      'ai-worker-password': workerRolePassword
      'ai-admin-database-url': 'postgresql://gbaaiadmin:${postgresAdminPassword}@${postgres.outputs.fqdn}:5432/postgres?sslmode=require'
      'ai-migration-database-url': 'postgresql://gai_owner:${ownerRolePassword}@${postgres.outputs.fqdn}:5432/gorgona_ai?sslmode=require'
      'ai-database-url': 'postgresql://gai_worker:${workerRolePassword}@${postgres.outputs.fqdn}:5432/gorgona_ai?sslmode=require'
    }
  }
}

var deployWorkers = deployJobsEnvironment && !empty(aiWorkerImage)
var registryRg = split(containerRegistryId, '/')[4]
var registryName = last(split(containerRegistryId, '/'))

module opsIdentity 'modules/user-identity.bicep' = {
  name: 'ai-ops-identity'
  scope: rg
  params: { location: location, name: 'id-${namePrefix}-ops', tags: tags }
}

module workerIdentity 'modules/user-identity.bicep' = {
  name: 'ai-worker-identity'
  scope: rg
  params: { location: location, name: 'id-${namePrefix}-worker', tags: tags }
}

module scalerIdentity 'modules/user-identity.bicep' = {
  name: 'ai-scaler-identity'
  scope: rg
  params: { location: location, name: 'id-${namePrefix}-scaler', tags: tags }
}

module queue 'modules/ai-servicebus.bicep' = {
  name: 'ai-servicebus'
  scope: rg
  params: {
    location: location
    name: 'sb-${namePrefix}-${uniqueSuffix}'
    tags: tags
    receiverPrincipalId: workerIdentity.outputs.principalId
    scalerPrincipalId: scalerIdentity.outputs.principalId
  }
}

module opsSecrets 'modules/secret-reader.bicep' = {
  name: 'ai-ops-secrets'
  scope: rg
  params: {
    vaultName: vault.outputs.name
    secretNames: ['ai-admin-database-url', 'ai-owner-password', 'ai-worker-password', 'ai-migration-database-url']
    principalId: opsIdentity.outputs.principalId
  }
}

module workerSecrets 'modules/secret-reader.bicep' = {
  name: 'ai-worker-secrets'
  scope: rg
  params: {
    vaultName: vault.outputs.name
    secretNames: ['ai-database-url']
    principalId: workerIdentity.outputs.principalId
  }
}

module opsPull 'modules/acr-pull.bicep' = {
  name: 'ai-ops-acrpull'
  scope: resourceGroup(registryRg)
  params: { registryName: registryName, principalId: opsIdentity.outputs.principalId }
}

module workerPull 'modules/acr-pull.bicep' = {
  name: 'ai-worker-acrpull'
  scope: resourceGroup(registryRg)
  params: { registryName: registryName, principalId: workerIdentity.outputs.principalId }
}

module jobs 'modules/ai-jobs-environment.bicep' = if (deployJobsEnvironment) {
  name: 'ai-jobs-environment'
  scope: rg
  params: {
    location: location
    namePrefix: namePrefix
    tags: tags
    infrastructureSubnetId: network.outputs.acaSubnetId
    logAnalyticsWorkspaceId: logAnalyticsWorkspaceId
  }
}

module workers 'modules/ai-jobs.bicep' = if (deployWorkers) {
  name: 'ai-jobs'
  scope: rg
  params: {
    location: location
    namePrefix: namePrefix
    tags: tags
    environmentId: jobs!.outputs.id
    image: aiWorkerImage
    registryServer: acrLoginServer
    opsIdentityId: opsIdentity.outputs.id
    workerIdentityId: workerIdentity.outputs.id
    workerIdentityClientId: workerIdentity.outputs.clientId
    scalerIdentityId: scalerIdentity.outputs.id
    keyVaultUri: vault.outputs.uri
    serviceBusNamespace: queue.outputs.name
    evidenceQueue: queue.outputs.evidenceQueueName
  }
  // Key Vault references and registry pulls resolve at creation time.
  dependsOn: [opsSecrets, workerSecrets, opsPull, workerPull]
}

module ml 'modules/ai-ml.bicep' = {
  name: 'ai-ml'
  scope: rg
  params: {
    location: location
    namePrefix: namePrefix
    uniqueSuffix: uniqueSuffix
    tags: tags
    appInsightsId: appInsightsId
    containerRegistryId: containerRegistryId
    deployGpuCluster: deployGpuCluster
    cpuMaxNodes: cpuMaxNodes
  }
}

module lock 'modules/rg-lock.bicep' = if (lockResourceGroup) {
  name: 'ai-lock'
  scope: rg
  params: {
    notes: 'Persistent GORGONA AI learning plane (ADR-0013). Never deleted with staging.'
  }
  dependsOn: [evidence, postgres, vault, queue, jobs, ml]
}

output resourceGroupName string = rg.name
output evidenceStorageName string = evidence.outputs.name
output aiPostgresFqdn string = postgres.outputs.fqdn
output trainingQueueName string = queue.outputs.queueName
output mlWorkspaceId string = ml.outputs.workspaceId
output aiJobNames array = deployWorkers ? workers!.outputs.jobNames : []
