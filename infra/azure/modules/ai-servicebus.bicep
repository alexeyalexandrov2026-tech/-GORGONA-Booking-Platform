// Service Bus (Basic): the evidence-events queue (intake, consumed by the ai-intake job)
// and the training-requests queue (reserved for Azure ML training jobs), both with
// dead-lettering. Local (SAS) auth is disabled; producers/consumers use managed-identity
// RBAC. The AI jobs identity may only receive from evidence-events. The KEDA scaler needs
// Manage access to read queue metrics, so a separate scaler identity (not available to
// containers) gets Data Owner on that one queue.
@description('Azure region.')
param location string

@description('Globally unique namespace name.')
param name string

@description('Resource tags.')
param tags object

@description('Principal allowed to receive from evidence-events (AI jobs identity); empty = none.')
param receiverPrincipalId string = ''

@description('Scaler principal (Data Owner on evidence-events only); empty = none.')
param scalerPrincipalId string = ''

var dataOwner = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '090c5cfd-751d-490a-894a-3ce6f1109419'
)

var dataReceiver = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0'
)

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

resource evidenceQueue 'Microsoft.ServiceBus/namespaces/queues@2026-01-01' = {
  parent: namespace
  name: 'evidence-events'
  properties: {
    maxDeliveryCount: 5
    lockDuration: 'PT5M'
    deadLetteringOnMessageExpiration: true
    defaultMessageTimeToLive: 'P14D'
  }
}

resource receiver 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(receiverPrincipalId)) {
  scope: evidenceQueue
  name: guid(evidenceQueue.id, receiverPrincipalId, dataReceiver)
  properties: {
    principalId: receiverPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: dataReceiver
  }
}

resource scaler 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(scalerPrincipalId)) {
  scope: evidenceQueue
  name: guid(evidenceQueue.id, scalerPrincipalId, dataOwner)
  properties: {
    principalId: scalerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: dataOwner
  }
}

output id string = namespace.id
output name string = namespace.name
output queueName string = trainingQueue.name
output evidenceQueueName string = evidenceQueue.name
