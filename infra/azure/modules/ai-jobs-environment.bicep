// Consumption-only Container Apps environment for the AI-plane jobs (ai-jobs.bicep).
// Internal (VNet only, no ingress, no inbound private endpoint); it reaches the AI
// database, vault and evidence store through their private endpoints (directly or over
// VNet peering). Logs go to the shared Log Analytics workspace.
@description('Azure region.')
param location string

@description('Name prefix.')
param namePrefix string

@description('Environment name (default cae-<namePrefix>).')
param name string = 'cae-${namePrefix}'

@description('Resource tags.')
param tags object

@description('Delegated infrastructure subnet ID.')
param infrastructureSubnetId string

@description('Log Analytics workspace ID for diagnostics.')
param logAnalyticsWorkspaceId string

resource environment 'Microsoft.App/managedEnvironments@2026-01-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    vnetConfiguration: { infrastructureSubnetId: infrastructureSubnetId, internal: true }
    publicNetworkAccess: 'Disabled'
    zoneRedundant: false
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
    appLogsConfiguration: { destination: 'azure-monitor' }
  }
}

// No newer GA version supports categoryGroup (2016-09-01 predates it).
#disable-next-line use-recent-api-versions
resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: environment
  name: 'to-log-analytics'
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [{ categoryGroup: 'allLogs', enabled: true }]
    metrics: [{ category: 'AllMetrics', enabled: true }]
  }
}

output id string = environment.id
