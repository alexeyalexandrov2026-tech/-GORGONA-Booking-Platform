// Service Bus (Basic) with the training/validation request queue and dead-lettering.
// Local (SAS) auth is disabled; producers/consumers use managed identity RBAC.
@description('Azure region.')
param location string

@description('Globally unique namespace name.')
param name string

@description('Resource tags.')
param tags object

resource namespace 'Microsoft.ServiceBus/namespaces@2026-01-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'Basic', tier: 'Basic' }
  properties: {
    disableLocalAuth: true
    minimumTlsVersion: '1.2'
  }
}

resource trainingQueue 'Microsoft.ServiceBus/namespaces/queues@2026-01-01' = {
  parent: namespace
  name: 'training-requests'
  properties: {
    maxDeliveryCount: 5
    lockDuration: 'PT5M'
    deadLetteringOnMessageExpiration: true
    defaultMessageTimeToLive: 'P14D'
  }
}

output id string = namespace.id
output queueName string = trainingQueue.name
