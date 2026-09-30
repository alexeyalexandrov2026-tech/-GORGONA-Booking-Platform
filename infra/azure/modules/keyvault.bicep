// Key Vault (RBAC authorization, soft delete + purge protection, public access off)
// with a private endpoint. Secret values arrive only as secure parameters generated
// at deploy time by scripts/stack-up.ps1; they are never committed or output.
@description('Azure region.')
param location string

@description('Globally unique vault name (3-24 chars).')
@minLength(3)
@maxLength(24)
param name string

@description('Resource tags.')
param tags object

@description('Private endpoint subnet.')
param peSubnetId string

@description('privatelink.vaultcore.azure.net zone ID.')
param privateDnsZoneId string

@description('Secrets to create: name -> value. Values are secure.')
@secure()
param secrets object

@description('Soft-delete retention in days.')
@minValue(7)
@maxValue(90)
param softDeleteRetentionInDays int = 90

@description('Object ID of the deploying operator (user). Gets Secrets Officer on this vault only, so stack updates can pass secrets back via getSecret().')
param operatorPrincipalId string

var keyVaultSecretsOfficer = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
)

// Public network access is off. The trusted-services bypass exists only so that ARM
// template deployment (enabledForTemplateDeployment) can resolve getSecret() during
// stack updates; access still requires RBAC on the vault.
resource vault 'Microsoft.KeyVault/vaults@2026-02-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: softDeleteRetentionInDays
    enablePurgeProtection: true
    enabledForTemplateDeployment: true
    publicNetworkAccess: 'Disabled'
    networkAcls: { defaultAction: 'Deny', bypass: 'AzureServices' }
  }
}

resource operatorAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: vault
  name: guid(vault.id, operatorPrincipalId, keyVaultSecretsOfficer)
  properties: {
    principalId: operatorPrincipalId
    principalType: 'User'
    roleDefinitionId: keyVaultSecretsOfficer
  }
}

resource vaultSecrets 'Microsoft.KeyVault/vaults/secrets@2026-02-01' = [
  for secret in items(secrets): {
    parent: vault
    name: secret.key
    properties: { value: secret.value }
  }
]

module endpoint 'private-endpoint.bicep' = {
  name: '${name}-pe'
  params: {
    location: location
    name: 'pe-${name}'
    tags: tags
    subnetId: peSubnetId
    targetResourceId: vault.id
    groupId: 'vault'
    privateDnsZoneId: privateDnsZoneId
  }
}

output id string = vault.id
output name string = vault.name
output uri string = vault.properties.vaultUri
