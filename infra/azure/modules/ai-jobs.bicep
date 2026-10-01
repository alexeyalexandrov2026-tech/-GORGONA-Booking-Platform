// AI learning-plane workers (ADR-0013) as Container Apps Jobs in the dedicated AI
// environment. One image (gba-ai), separate jobs per execution path:
//   ai-bootstrap / ai-migrate  manual, one-off (roles + database; schema)  [ops identity]
//   ai-tick                    schedule: periodic dataset cut + RAG refresh, then drains
//                              train -> evaluate -> verify and due retries  [worker identity]
//   ai-health                  schedule: the execution fails when the plane is unhealthy
//   ai-intake                  event: scales from 0 on the evidence-events queue and pumps
//                              messages into durable runs                [worker identity]
// Least privilege: the ops identity reads only the admin/owner secrets; the worker
// identity reads only the RLS-bound worker DSN and may only receive from evidence-events;
// the scaler identity (Data Owner on that queue, for KEDA metrics) is never available to
// containers. No staging or production principal has any role here.
func kvSecret(name string, vaultUri string, identity string) object => {
  name: name
  keyVaultUrl: '${vaultUri}secrets/${name}'
  identity: identity
}

@description('Azure region.')
param location string

@description('Name prefix, e.g. gorgona-ai.')
param namePrefix string

@description('Resource tags.')
param tags object

@description('AI Container Apps environment ID.')
param environmentId string

@description('AI worker image by digest, e.g. <acr>.azurecr.io/gorgona-ai@sha256:...')
param image string

@description('Shared registry login server.')
param registryServer string

@description('Identities: ops (bootstrap/migrate), worker (runtime), scaler (KEDA only).')
param opsIdentityId string
param workerIdentityId string
param workerIdentityClientId string
param scalerIdentityId string

@description('AI Key Vault URI (https://<name>.vault.azure.net/).')
param keyVaultUri string

@description('Service Bus namespace name and the evidence queue.')
param serviceBusNamespace string
param evidenceQueue string

@description('Tick schedule (cron, UTC).')
param tickCron string = '*/15 * * * *'

var resources = { cpu: json('0.5'), memory: '1Gi' }
var workerEnv = [
  { name: 'GAI_DATABASE_URL', secretRef: 'ai-database-url' }
  { name: 'AZURE_CLIENT_ID', value: workerIdentityClientId }
]
var jobs = [
  {
    key: 'bootstrap'
    trigger: 'Manual'
    cron: ''
    identity: opsIdentityId
    args: ['bootstrap']
    secrets: ['ai-admin-database-url', 'ai-owner-password', 'ai-worker-password']
    env: [
      { name: 'GAI_ADMIN_DATABASE_URL', secretRef: 'ai-admin-database-url' }
      { name: 'GAI_OWNER_PASSWORD', secretRef: 'ai-owner-password' }
      { name: 'GAI_WORKER_PASSWORD', secretRef: 'ai-worker-password' }
      { name: 'GAI_DATABASE_NAME', value: 'gorgona_ai' }
    ]
  }
  {
    key: 'migrate'
    trigger: 'Manual'
    cron: ''
    identity: opsIdentityId
    args: ['migrate']
    secrets: ['ai-migration-database-url']
    env: [{ name: 'GAI_MIGRATION_DATABASE_URL', secretRef: 'ai-migration-database-url' }]
  }
  {
    key: 'tick'
    trigger: 'Schedule'
    cron: tickCron
    identity: workerIdentityId
    args: ['tick']
    secrets: ['ai-database-url']
    env: workerEnv
  }
  {
    key: 'health'
    trigger: 'Schedule'
    cron: '5,35 * * * *'
    identity: workerIdentityId
    args: ['health']
    secrets: ['ai-database-url']
    env: workerEnv
  }
  {
    key: 'intake'
    trigger: 'Event'
    cron: ''
    identity: workerIdentityId
    args: ['pump-evidence']
    secrets: ['ai-database-url']
    env: concat(workerEnv, [
      { name: 'GAI_SERVICEBUS_NAMESPACE', value: '${serviceBusNamespace}.servicebus.windows.net' }
      { name: 'GAI_EVIDENCE_QUEUE', value: evidenceQueue }
    ])
  }
]

resource job 'Microsoft.App/jobs@2026-01-01' = [
  for j in jobs: {
    name: 'caj-${namePrefix}-${j.key}'
    location: location
    tags: tags
    identity: {
      type: 'UserAssigned'
      userAssignedIdentities: j.trigger == 'Event'
        ? { '${j.identity}': {}, '${scalerIdentityId}': {} }
        : { '${j.identity}': {} }
    }
    properties: {
      environmentId: environmentId
      workloadProfileName: 'Consumption'
      configuration: {
        identitySettings: j.trigger == 'Event'
          ? [
              { identity: j.identity, lifecycle: 'All' }
              { identity: scalerIdentityId, lifecycle: 'None' }
            ]
          : [{ identity: j.identity, lifecycle: 'All' }]
        triggerType: j.trigger
        replicaTimeout: 1800
        replicaRetryLimit: 1
        manualTriggerConfig: j.trigger == 'Manual' ? { parallelism: 1, replicaCompletionCount: 1 } : null
        scheduleTriggerConfig: j.trigger == 'Schedule'
          ? { cronExpression: j.cron, parallelism: 1, replicaCompletionCount: 1 }
          : null
        eventTriggerConfig: j.trigger == 'Event'
          ? {
              parallelism: 1
              replicaCompletionCount: 1
              scale: {
                minExecutions: 0
                maxExecutions: 2
                pollingInterval: 30
                rules: [
                  {
                    name: 'evidence-queue'
                    type: 'azure-servicebus'
                    metadata: { queueName: evidenceQueue, namespace: serviceBusNamespace, messageCount: '1' }
                    identity: scalerIdentityId
                  }
                ]
              }
            }
          : null
        registries: [{ server: registryServer, identity: j.identity }]
        secrets: map(j.secrets, s => kvSecret(s, keyVaultUri, j.identity))
      }
      template: {
        containers: [
          {
            name: j.key
            image: image
            args: j.args
            resources: resources
            env: j.env
          }
        ]
      }
    }
  }
]

output jobNames array = [for (j, i) in jobs: job[i].name]
