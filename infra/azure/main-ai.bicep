// Persistent AI learning plane (ADR-0013). Its own resource group and stack
// `gorgona-ai` (detachAll + denyDelete) plus a CanNotDelete lock. It is never part
// of the staging stack, holds no staging role assignments, and survives any staging
// teardown. Classes: 1 persistent (storage, AI PostgreSQL, Key Vault, private
// endpoints, ML workspace), 2 scale-to-zero (Service Bus, jobs environment),
// 3 on-demand (ML clusters at 0 nodes).
targetScope = 'subscription'

@description('Azure region (owner decision: eastus2).')
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

@description('AI PostgreSQL sizing (Burstable B1ms is in the free account for 12 months).')
param postgresSku string = 'Standard_B1ms'
@allowed(['Burstable', 'GeneralPurpose', 'MemoryOptimized'])
param postgresTier string = 'Burstable'
param postgresStorageGB int = 32

@description('Declare the GPU training cluster (needs GPU quota and pay-as-you-go).')
param deployGpuCluster bool = false

@description('Apply a CanNotDelete lock to the AI resource group.')
param lockResourceGroup bool = true

@description('Object ID of the deploying operator (Key Vault Secrets Officer on the vault of this environment only).')
param operatorPrincipalId string

@description('Generated at deploy time by scripts/stack-up.ps1; never committed.')
@secure()
param postgresAdminPassword string

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
    }
  }
}

module queue 'modules/ai-servicebus.bicep' = {
  name: 'ai-servicebus'
  scope: rg
  params: { location: location, name: 'sb-${namePrefix}-${uniqueSuffix}', tags: tags }
}

module jobs 'modules/ai-jobs-environment.bicep' = {
  name: 'ai-jobs-environment'
  scope: rg
  params: {
    location: location
    namePrefix: namePrefix
    tags: tags
    infrastructureSubnetId: network.outputs.acaSubnetId
  }
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
