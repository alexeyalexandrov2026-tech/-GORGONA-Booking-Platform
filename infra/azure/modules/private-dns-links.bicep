// Link existing private DNS zones (in this module scope) to another VNet so that VNet
// resolves the zones' private-endpoint records. Azure-provided DNS honours linked zones,
// so no resolver or forwarder is needed. No auto-registration.
@description('Existing private DNS zone names.')
param zoneNames array

@description('Link name.')
param linkName string

@description('VNet to link (any region).')
param vnetId string

@description('Resource tags.')
param tags object

resource zones 'Microsoft.Network/privateDnsZones@2024-06-01' existing = [for zone in zoneNames: { name: zone }]

resource links 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [
  for (zone, i) in zoneNames: {
    parent: zones[i]
    name: linkName
    location: 'global'
    tags: tags
    properties: {
      registrationEnabled: false
      virtualNetwork: { id: vnetId }
    }
  }
]
