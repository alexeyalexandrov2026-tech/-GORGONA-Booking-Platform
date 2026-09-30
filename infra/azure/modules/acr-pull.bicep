// AcrPull for a managed identity on the shared registry. Deployed into the shared
// resource group; owned (and removed on teardown) by the calling environment's stack.
@description('Registry name in this resource group.')
param registryName string

@description('Principal (managed identity) object ID.')
param principalId string

var acrPull = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)

resource registry 'Microsoft.ContainerRegistry/registries@2025-11-01' existing = {
  name: registryName
}

resource grant 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, principalId, acrPull)
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPull
  }
}
