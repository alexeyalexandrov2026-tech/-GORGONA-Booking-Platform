// Persistent shared foundation (class 1): resource group, Log Analytics, Application
// Insights, container registry, delete lock. The subscription budget and cost-anomaly
// alert are in main-budgets.bicep, deployed first.
// Deploy as the subscription-scoped deployment stack `gorgona-shared` with
// action-on-unmanage detachAll and deny-settings denyDelete (see scripts/stack-up.ps1).
targetScope = 'subscription'

@description('Azure region (owner decision: eastus2).')
param location string

@description('Globally unique ACR name (alphanumeric).')
param acrName string

@description('Log Analytics daily ingestion cap in GB (-1 = none).')
param logDailyQuotaGb int = 1

@description('Apply a CanNotDelete lock to the shared resource group.')
param lockResourceGroup bool = true

var tags = {
  app: 'gorgona'
  env: 'shared'
  lifecycle: 'persistent'
  managedBy: 'bicep-stack:gorgona-shared'
}

resource rg 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: 'rg-gorgona-shared'
  location: location
  tags: tags
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'shared-monitoring'
  scope: rg
  params: {
    location: location
    namePrefix: 'gorgona-shared'
    tags: tags
    dailyQuotaGb: logDailyQuotaGb
  }
}

module acr 'modules/acr.bicep' = {
  name: 'shared-acr'
  scope: rg
  params: {
    location: location
    name: acrName
    tags: tags
  }
}

module lock 'modules/rg-lock.bicep' = if (lockResourceGroup) {
  name: 'shared-lock'
  scope: rg
  params: {
    notes: 'Persistent shared GORGONA foundation. Never deleted with staging.'
  }
}

output resourceGroupName string = rg.name
output workspaceId string = monitoring.outputs.workspaceId
output appInsightsId string = monitoring.outputs.appInsightsId
output acrId string = acr.outputs.id
output acrLoginServer string = acr.outputs.loginServer
