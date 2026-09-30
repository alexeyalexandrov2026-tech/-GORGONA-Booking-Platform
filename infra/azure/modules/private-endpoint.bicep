// Private endpoint for one PaaS resource, registered in its private DNS zone.
@description('Azure region.')
param location string

@description('Endpoint name.')
param name string

@description('Resource tags.')
param tags object

@description('Subnet for the endpoint NIC.')
param subnetId string

@description('Target resource ID.')
param targetResourceId string

@description('Target sub-resource (group ID), e.g. postgresqlServer, vault, blob.')
param groupId string

@description('Private DNS zone ID for the group.')
param privateDnsZoneId string

resource endpoint 'Microsoft.Network/privateEndpoints@2025-09-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    subnet: { id: subnetId }
    privateLinkServiceConnections: [
      {
        name: name
        properties: {
          privateLinkServiceId: targetResourceId
          groupIds: [groupId]
        }
      }
    ]
  }
}

resource dnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2025-09-01' = {
  parent: endpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: groupId
        properties: { privateDnsZoneId: privateDnsZoneId }
      }
    ]
  }
}

output id string = endpoint.id
