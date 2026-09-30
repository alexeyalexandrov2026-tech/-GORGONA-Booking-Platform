// Azure Machine Learning workspace (registry, experiments, evaluations) with its own
// system storage and Key Vault, and compute clusters that idle at 0 nodes.
// It holds ML metadata only; evidence stays in the private evidence store. Before
// sensitive training data flows, move to a managed-VNet workspace (scale later).
@description('Azure region.')
param location string

@description('Name prefix.')
param namePrefix string

@description('Globally unique suffix (lowercase alphanumeric).')
param uniqueSuffix string

@description('Resource tags.')
param tags object

@description('Application Insights ID (shared).')
param appInsightsId string

@description('Container registry ID (shared).')
param containerRegistryId string

@description('CPU cluster VM size (must be an Azure ML-supported size in the region; D4s_v5 is not, D4ds_v5 is).')
param cpuVmSize string = 'Standard_D4ds_v5'

@description('CPU cluster max nodes.')
param cpuMaxNodes int = 2

@description('Declare a GPU cluster (requires GPU quota and pay-as-you-go).')
param deployGpuCluster bool = false

@description('GPU cluster VM size.')
param gpuVmSize string = 'Standard_NC4as_T4_v3'

resource mlStorage 'Microsoft.Storage/storageAccounts@2026-04-01' = {
  name: 'stml${uniqueSuffix}gba'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource mlVault 'Microsoft.KeyVault/vaults@2026-02-01' = {
  name: 'kv-gba-ml-${uniqueSuffix}'
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true
  }
}

resource workspace 'Microsoft.MachineLearningServices/workspaces@2026-05-01' = {
  name: 'mlw-${namePrefix}-${uniqueSuffix}'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  sku: { name: 'Basic', tier: 'Basic' }
  properties: {
    friendlyName: 'GORGONA AI learning plane'
    storageAccount: mlStorage.id
    keyVault: mlVault.id
    applicationInsights: appInsightsId
    containerRegistry: containerRegistryId
    publicNetworkAccess: 'Enabled'
    v1LegacyMode: false
  }
}

resource cpuCluster 'Microsoft.MachineLearningServices/workspaces/computes@2026-05-01' = {
  parent: workspace
  name: 'cpu-train'
  location: location
  properties: {
    computeType: 'AmlCompute'
    properties: {
      vmSize: cpuVmSize
      vmPriority: 'Dedicated'
      scaleSettings: {
        minNodeCount: 0
        maxNodeCount: cpuMaxNodes
        nodeIdleTimeBeforeScaleDown: 'PT15M'
      }
    }
  }
}

resource gpuCluster 'Microsoft.MachineLearningServices/workspaces/computes@2026-05-01' = if (deployGpuCluster) {
  parent: workspace
  name: 'gpu-train'
  location: location
  properties: {
    computeType: 'AmlCompute'
    properties: {
      vmSize: gpuVmSize
      vmPriority: 'Dedicated'
      scaleSettings: {
        minNodeCount: 0
        maxNodeCount: 1
        nodeIdleTimeBeforeScaleDown: 'PT15M'
      }
    }
  }
}

output workspaceId string = workspace.id
