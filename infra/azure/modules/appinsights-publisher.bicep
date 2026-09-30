// Monitoring Metrics Publisher for a managed identity on the shared Application Insights
// (local auth is disabled, so ingestion is Entra-authenticated). Deployed into the shared
// resource group; owned (and removed on teardown) by the calling environment's stack.
@description('Application Insights component name in this resource group.')
param appInsightsName string

@description('Principal (managed identity) object ID.')
param principalId string

var metricsPublisher = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '3913510d-42f4-4e42-8a64-420c390055eb'
)

resource component 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource grant 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: component
  name: guid(component.id, principalId, metricsPublisher)
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: metricsPublisher
  }
}

output appInsightsId string = component.id
