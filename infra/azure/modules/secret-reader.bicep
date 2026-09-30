// Grants one principal "Key Vault Secrets User" on specific secrets only (not the
// whole vault), so the API identity cannot read migration or admin credentials.
@description('Key Vault name.')
param vaultName string

@description('Secret names this principal may read.')
param secretNames array

@description('Principal (managed identity) object ID.')
param principalId string

var keyVaultSecretsUser = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4633458b-17de-408a-b874-0445c86b69e6'
)

resource vault 'Microsoft.KeyVault/vaults@2026-02-01' existing = {
  name: vaultName
}

resource secrets 'Microsoft.KeyVault/vaults/secrets@2026-02-01' existing = [
  for secretName in secretNames: {
    parent: vault
    name: secretName
  }
]

resource grants 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for (secretName, i) in secretNames: {
    scope: secrets[i]
    name: guid(secrets[i].id, principalId, keyVaultSecretsUser)
    properties: {
      principalId: principalId
      principalType: 'ServicePrincipal'
      roleDefinitionId: keyVaultSecretsUser
    }
  }
]
