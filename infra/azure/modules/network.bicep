// VNet with a delegated Container Apps subnet (workload profiles need /27 minimum;
// /23 leaves room for zone redundancy and scale) and a private-endpoint subnet,
// plus the private DNS zones the private endpoints register into.
@description('Azure region.')
param location string

@description('Name prefix, e.g. gorgona-staging.')
param namePrefix string

@description('Resource tags.')
param tags object

@description('VNet address space, e.g. 10.20.0.0/16. Must not overlap other environments.')
param addressPrefix string

@description('Container Apps infrastructure subnet (/23 recommended).')
param acaSubnetPrefix string

@description('Private endpoint subnet (/27 or larger).')
param peSubnetPrefix string

@description('Private DNS zones to create and link, e.g. privatelink.postgres.database.azure.com.')
param privateDnsZoneNames array

resource vnet 'Microsoft.Network/virtualNetworks@2025-09-01' = {
  name: 'vnet-${namePrefix}'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: [addressPrefix] }
    subnets: [
      {
        name: 'snet-aca'
        properties: {
          addressPrefix: acaSubnetPrefix
          delegations: [
            {
              name: 'aca'
              properties: { serviceName: 'Microsoft.App/environments' }
            }
          ]
        }
      }
      {
        name: 'snet-pe'
        properties: {
          addressPrefix: peSubnetPrefix
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

resource zones 'Microsoft.Network/privateDnsZones@2024-06-01' = [
  for zone in privateDnsZoneNames: {
    name: zone
    location: 'global'
    tags: tags
  }
]

resource links 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [
  for (zone, i) in privateDnsZoneNames: {
    parent: zones[i]
    name: 'link-${namePrefix}'
    location: 'global'
    tags: tags
    properties: {
      registrationEnabled: false
      virtualNetwork: { id: vnet.id }
    }
  }
]

output vnetId string = vnet.id
output acaSubnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, 'snet-aca')
output peSubnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, 'snet-pe')
output privateDnsZoneIds array = [for (zone, i) in privateDnsZoneNames: zones[i].id]
