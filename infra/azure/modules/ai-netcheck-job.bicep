// Manual probe job (`gba-ai netcheck`) that proves, from inside a jobs environment, that
// every dependency resolves and connects as designed: private-endpoint names resolve to
// private addresses through the linked zones (a public answer fails the run, so there is
// no silent public fallback), TCP connects, the registry pull works (the job starts at
// all), and the platform fetched the worker's Key Vault-referenced secret (presence only;
// the value is never printed). Runs as the worker identity; it reads nothing else.
@description('Azure region.')
param location string

@description('Job name.')
param name string

@description('Resource tags.')
param tags object

@description('Container Apps environment ID.')
param environmentId string

@description('Image by digest.')
param image string

@description('Registry login server.')
param registryServer string

@description('Worker identity resource ID and client ID.')
param workerIdentityId string
param workerIdentityClientId string

@description('Vault URI (https://<name>.vault.azure.net/).')
param keyVaultUri string

@description('Targets, host:port:private|public, comma separated.')
param targets string

resource job 'Microsoft.App/jobs@2026-01-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${workerIdentityId}': {} }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      identitySettings: [{ identity: workerIdentityId, lifecycle: 'All' }]
      triggerType: 'Manual'
      replicaTimeout: 300
      replicaRetryLimit: 0
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      registries: [{ server: registryServer, identity: workerIdentityId }]
      secrets: [
        {
          name: 'ai-database-url'
          keyVaultUrl: '${keyVaultUri}secrets/ai-database-url'
          identity: workerIdentityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'netcheck'
          image: image
          args: ['netcheck']
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          env: [
            { name: 'GAI_DATABASE_URL', secretRef: 'ai-database-url' }
            { name: 'AZURE_CLIENT_ID', value: workerIdentityClientId }
            { name: 'GAI_NETCHECK_TARGETS', value: targets }
            { name: 'GAI_NETCHECK_SECRETS', value: 'GAI_DATABASE_URL' }
          ]
        }
      ]
    }
  }
}

output name string = job.name
