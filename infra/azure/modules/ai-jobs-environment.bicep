// Consumption-only Container Apps environment for event-driven AI-plane jobs
// (validation/orchestration). No apps or jobs are declared yet; with nothing
// running it has no compute cost.
@description('Azure region.')
param location string

@description('Name prefix.')
param namePrefix string

@description('Resource tags.')
param tags object

@description('Delegated infrastructure subnet ID.')
param infrastructureSubnetId string

resource environment 'Microsoft.App/managedEnvironments@2026-01-01' = {
  name: 'cae-${namePrefix}'
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

output id string = environment.id
