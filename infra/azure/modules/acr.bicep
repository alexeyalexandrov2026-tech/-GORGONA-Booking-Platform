// Container registry for GORGONA images. Admin user and anonymous pull are off;
// pulls use managed identity (AcrPull). Deploy images by digest.
@description('Azure region.')
param location string

@description('Globally unique registry name (alphanumeric).')
@minLength(5)
@maxLength(50)
param name string

@description('Resource tags.')
param tags object

@allowed(['Basic', 'Standard', 'Premium'])
param sku string = 'Standard'

resource registry 'Microsoft.ContainerRegistry/registries@2025-11-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: sku }
  properties: {
    adminUserEnabled: false
    anonymousPullEnabled: false
    publicNetworkAccess: 'Enabled'
    policies: {
      retentionPolicy: sku == 'Premium' ? { days: 30, status: 'enabled' } : null
    }
  }
}

output id string = registry.id
output name string = registry.name
output loginServer string = registry.properties.loginServer
