// One direction of a VNet peering (deploy once per side). Private traffic only: no
// gateway transit and no forwarded traffic, so neither VNet becomes a transit path.
@description('Name of the local VNet (in this module scope).')
param localVnetName string

@description('Peering name.')
param name string

@description('Resource ID of the remote VNet (any region).')
param remoteVnetId string

resource local 'Microsoft.Network/virtualNetworks@2025-09-01' existing = {
  name: localVnetName
}

resource peering 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2025-09-01' = {
  parent: local
  name: name
  properties: {
    remoteVirtualNetwork: { id: remoteVnetId }
    allowVirtualNetworkAccess: true
    allowForwardedTraffic: false
    allowGatewayTransit: false
    useRemoteGateways: false
  }
}

output id string = peering.id
