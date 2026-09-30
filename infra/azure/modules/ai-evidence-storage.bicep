// Evidence store for the AI learning plane: StorageV2 blob (no hierarchical
// namespace, so blob versioning works), GRS, versioning, change feed, soft delete,
// shared-key access off, public network off (private endpoint), and a container
// time-based immutability policy (left UNLOCKED; locking is irreversible and is an
// owner decision).
@description('Azure region.')
param location string

@description('Globally unique storage account name (3-24 lowercase alphanumeric).')
@minLength(3)
@maxLength(24)
param name string

@description('Resource tags.')
param tags object

@description('Private endpoint subnet.')
param peSubnetId string

@description('privatelink.blob.core.windows.net zone ID.')
param privateDnsZoneId string

@description('Immutability retention for evidence blobs, in days.')
param immutabilityDays int = 365

resource account 'Microsoft.Storage/storageAccounts@2026-04-01' = {
  name: name
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_GRS' }
  properties: {
    accessTier: 'Hot'
    isHnsEnabled: false
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Disabled'
    networkAcls: { defaultAction: 'Deny', bypass: 'None' }
  }
}

resource blob 'Microsoft.Storage/storageAccounts/blobServices@2026-04-01' = {
  parent: account
  name: 'default'
  properties: {
    isVersioningEnabled: true
    changeFeed: { enabled: true }
    deleteRetentionPolicy: { enabled: true, days: 30 }
    containerDeleteRetentionPolicy: { enabled: true, days: 30 }
  }
}

resource evidence 'Microsoft.Storage/storageAccounts/blobServices/containers@2026-04-01' = {
  parent: blob
  name: 'evidence'
  properties: { publicAccess: 'None' }
}

resource immutability 'Microsoft.Storage/storageAccounts/blobServices/containers/immutabilityPolicies@2026-04-01' = {
  parent: evidence
  name: 'default'
  properties: {
    immutabilityPeriodSinceCreationInDays: immutabilityDays
    allowProtectedAppendWrites: true
  }
}

module endpoint 'private-endpoint.bicep' = {
  name: '${name}-pe'
  params: {
    location: location
    name: 'pe-${name}-blob'
    tags: tags
    subnetId: peSubnetId
    targetResourceId: account.id
    groupId: 'blob'
    privateDnsZoneId: privateDnsZoneId
  }
}

output id string = account.id
output name string = account.name
