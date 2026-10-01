// AI jobs plane in its own region (owner decision 2026-10-01, Option A: Central US could
// not provision a Container Apps environment - ManagedEnvironmentCapacityHeavyUsageError).
// Only the Container Apps part of the AI plane (ADR-0013) lives here; the data plane
// (AI PostgreSQL, evidence storage, Service Bus, Key Vaults, Azure ML, identities and
// their role assignments) stays in Central US in stack `gorgona-ai` and is not modified.
//
// Stack `gorgona-ai-jobs`, applied in owner-approved steps:
//   1. environment only     deployConnectivity=false, netcheckImage='', deployJobs=false
//   2. private connectivity deployConnectivity=true  (+ netcheckImage: one manual probe job)
//   3. the 5 AI jobs        deployJobs=true (needs aiWorkerImage)
//
// Connectivity: the jobs VNet is peered (both directions, no transit, no gateways) with
// the Central US AI VNet, and the existing private DNS zones of the AI private endpoints
// are linked to the jobs VNet, so Azure-provided DNS resolves them to private addresses.
// No resolver, forwarder or public fallback: PostgreSQL, Key Vault and storage keep public
// network access disabled. Service Bus (Basic) and the registry (Standard) have no private
// endpoint at their tiers and are reached on their public endpoints with Entra auth only,
// exactly as from Central US (see docs/plan/M4_REPORT.md).
targetScope = 'subscription'

@description('Region of the jobs plane (jobs_region).')
param jobsLocation string

@description('Name suffix of the Central US AI plane (GBA_UNIQUE_SUFFIX for centralus).')
param uniqueSuffix string

@description('Jobs VNet address plan; must not overlap 10.20/16 (staging), 10.30/16 (AI), 10.40/16 (production).')
param addressPrefix string = '10.31.0.0/16'
param acaSubnetPrefix string = '10.31.0.0/23'

@description('Existing AI plane resource group and VNet (Central US).')
param aiResourceGroupName string = 'rg-gorgona-ai'
param aiVnetName string = 'vnet-gorgona-ai'

@description('Shared resources (from main-shared outputs).')
param acrLoginServer string
param logAnalyticsWorkspaceId string

@description('Step 2: VNet peering (both directions) and private DNS zone links.')
param deployConnectivity bool = false

@description('Step 2: image for the one-off network probe job (gorgona-ai@sha256:...); empty = no probe.')
param netcheckImage string = ''

@description('Step 3: deploy the 5 AI jobs (bootstrap, migrate, tick, health, intake).')
param deployJobs bool = false

@description('AI worker image by digest (gorgona-ai@sha256:...).')
param aiWorkerImage string = ''

var namePrefix = 'gorgona-ai'
var tags = {
  app: 'gorgona'
  env: 'ai'
  lifecycle: 'persistent'
  managedBy: 'bicep-stack:gorgona-ai-jobs'
}
var dnsZones = [
  'privatelink.postgres.database.azure.com'
  'privatelink.vaultcore.azure.net'
  'privatelink.blob.${environment().suffixes.storage}'
]
var vaultName = 'kv-gba-ai-${uniqueSuffix}'
var serviceBusName = 'sb-${namePrefix}-${uniqueSuffix}'
var netcheckTargets = join(
  [
    'psql-${namePrefix}-${uniqueSuffix}.postgres.database.azure.com:5432:private'
    '${vaultName}${environment().suffixes.keyvaultDns}:443:private'
    'stevid${uniqueSuffix}gba.blob.${environment().suffixes.storage}:443:private'
    '${serviceBusName}.servicebus.windows.net:5671:public'
    '${acrLoginServer}:443:public'
  ],
  ','
)

resource aiRg 'Microsoft.Resources/resourceGroups@2025-04-01' existing = {
  name: aiResourceGroupName
}

resource aiVnet 'Microsoft.Network/virtualNetworks@2025-09-01' existing = {
  scope: aiRg
  name: aiVnetName
}

resource opsIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  scope: aiRg
  name: 'id-${namePrefix}-ops'
}

resource workerIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  scope: aiRg
  name: 'id-${namePrefix}-worker'
}

resource scalerIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  scope: aiRg
  name: 'id-${namePrefix}-scaler'
}

resource vault 'Microsoft.KeyVault/vaults@2026-02-01' existing = {
  scope: aiRg
  name: vaultName
}

resource rg 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: 'rg-gorgona-ai-jobs'
  location: jobsLocation
  tags: tags
}

module network 'modules/aca-vnet.bicep' = {
  name: 'ai-jobs-network'
  scope: rg
  params: {
    location: jobsLocation
    name: 'vnet-${namePrefix}-jobs'
    tags: tags
    addressPrefix: addressPrefix
    acaSubnetPrefix: acaSubnetPrefix
  }
}

module jobsEnvironment 'modules/ai-jobs-environment.bicep' = {
  name: 'ai-jobs-environment'
  scope: rg
  params: {
    location: jobsLocation
    namePrefix: namePrefix
    name: 'cae-${namePrefix}-jobs'
    tags: tags
    infrastructureSubnetId: network.outputs.acaSubnetId
    logAnalyticsWorkspaceId: logAnalyticsWorkspaceId
  }
}

module peerJobsToAi 'modules/vnet-peering.bicep' = if (deployConnectivity) {
  name: 'ai-jobs-peering-out'
  scope: rg
  params: { localVnetName: network.outputs.name, name: 'peer-ai-jobs-to-ai', remoteVnetId: aiVnet.id }
}

module peerAiToJobs 'modules/vnet-peering.bicep' = if (deployConnectivity) {
  name: 'ai-jobs-peering-in'
  scope: aiRg
  params: { localVnetName: aiVnetName, name: 'peer-ai-to-ai-jobs', remoteVnetId: network.outputs.id }
}

module dnsLinks 'modules/private-dns-links.bicep' = if (deployConnectivity) {
  name: 'ai-jobs-dns-links'
  scope: aiRg
  params: { zoneNames: dnsZones, linkName: 'link-${namePrefix}-jobs', vnetId: network.outputs.id, tags: tags }
}

module netcheck 'modules/ai-netcheck-job.bicep' = if (deployConnectivity && !empty(netcheckImage)) {
  name: 'ai-jobs-netcheck'
  scope: rg
  params: {
    location: jobsLocation
    name: 'caj-${namePrefix}-netcheck'
    tags: tags
    environmentId: jobsEnvironment.outputs.id
    image: netcheckImage
    registryServer: acrLoginServer
    workerIdentityId: workerIdentity.id
    workerIdentityClientId: workerIdentity.properties.clientId
    keyVaultUri: vault.properties.vaultUri
    targets: netcheckTargets
  }
  dependsOn: [peerJobsToAi, peerAiToJobs, dnsLinks]
}

module workers 'modules/ai-jobs.bicep' = if (deployConnectivity && deployJobs && !empty(aiWorkerImage)) {
  name: 'ai-jobs'
  scope: rg
  params: {
    location: jobsLocation
    namePrefix: namePrefix
    tags: tags
    environmentId: jobsEnvironment.outputs.id
    image: aiWorkerImage
    registryServer: acrLoginServer
    opsIdentityId: opsIdentity.id
    workerIdentityId: workerIdentity.id
    workerIdentityClientId: workerIdentity.properties.clientId
    scalerIdentityId: scalerIdentity.id
    keyVaultUri: vault.properties.vaultUri
    serviceBusNamespace: serviceBusName
    evidenceQueue: 'evidence-events'
  }
  dependsOn: [peerJobsToAi, peerAiToJobs, dnsLinks]
}

output resourceGroupName string = rg.name
output environmentId string = jobsEnvironment.outputs.id
output netcheckJobName string = (deployConnectivity && !empty(netcheckImage)) ? netcheck!.outputs.name : ''
output aiJobNames array = (deployConnectivity && deployJobs && !empty(aiWorkerImage)) ? workers!.outputs.jobNames : []
