// VNet holding only a delegated Container Apps infrastructure subnet (no private
// endpoints of its own): workloads reach private endpoints in a peered VNet.
@description('Azure region.')
param location string

@description('VNet name.')
param name string

@description('Resource tags.')
param tags object

@description('VNet address space. Must not overlap any peered or platform VNet.')
param addressPrefix string

@description('Container Apps infrastructure subnet (workload profiles: /27 minimum).')
param acaSubnetPrefix string

resource vnet 'Microsoft.Network/virtualNetworks@2025-09-01' = {
  name: name
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
    ]
  }
}

output id string = vnet.id
output name string = vnet.name
output acaSubnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, 'snet-aca')
