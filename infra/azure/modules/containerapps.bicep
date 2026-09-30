// Container Apps: workload-profiles environment (VNet, public access disabled — only
// Front Door Premium reaches it through Private Link), the API app, and manual
// migrate/bootstrap jobs. Replicas never run migrations.
@description('Azure region.')
param location string

@description('Name prefix, e.g. gorgona-staging.')
param namePrefix string

@description('Resource tags.')
param tags object

@description('Delegated infrastructure subnet ID.')
param infrastructureSubnetId string

@description('Zone-redundant environment (production).')
param zoneRedundant bool

@description('Log Analytics workspace ID for diagnostics.')
param logAnalyticsWorkspaceId string

@description('Application Insights connection string (the app exports OpenTelemetry to it).')
param appInsightsConnectionString string

@description('Image reference by digest, e.g. <acr>.azurecr.io/gorgona-api@sha256:...')
param image string

@description('ACR login server.')
param registryServer string

@description('User-assigned identity resource ID for the API app.')
param apiIdentityId string

@description('Client ID of the API identity (AZURE_CLIENT_ID: Entra auth for telemetry ingestion).')
param apiIdentityClientId string

@description('User-assigned identity resource ID for the migrate/bootstrap jobs.')
param jobsIdentityId string

@description('Key Vault URI (https://<name>.vault.azure.net/).')
param keyVaultUri string

@description('GBA_ENV value for the API.')
@allowed(['staging', 'production'])
param gbaEnv string

@description('Front Door profile ID (X-Azure-FDID) trusted by the API.')
param frontDoorId string

@description('OIDC issuer / audience / JWKS URL for staff APIs (public values).')
param authIssuer string
param authAudience string
param authJwksUrl string

@description('Replica bounds.')
param minReplicas int
param maxReplicas int

@description('Per-replica pool maximum; budget: maxReplicas * pool + 16 <= max_connections.')
param dbPoolMaxSize int

@description('HTTP concurrent requests per replica before scaling out.')
param httpConcurrency int = 50

@description('Booking database name created by bootstrap.')
param databaseName string = 'gorgona_booking'

var registries = [{ server: registryServer, identity: apiIdentityId }]
var jobRegistries = [{ server: registryServer, identity: jobsIdentityId }]
var resources = { cpu: json('0.5'), memory: '1Gi' }

resource environment 'Microsoft.App/managedEnvironments@2026-01-01' = {
  name: 'cae-${namePrefix}'
  location: location
  tags: tags
  properties: {
    vnetConfiguration: { infrastructureSubnetId: infrastructureSubnetId, internal: false }
    publicNetworkAccess: 'Disabled'
    zoneRedundant: zoneRedundant
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
    // The managed OpenTelemetry agent is preview-only; the app exports to
    // Application Insights itself (APPLICATIONINSIGHTS_CONNECTION_STRING below).
    appLogsConfiguration: { destination: 'azure-monitor' }
  }
}

// No newer GA version supports categoryGroup (2016-09-01 predates it).
#disable-next-line use-recent-api-versions
resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: environment
  name: 'to-log-analytics'
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [{ categoryGroup: 'allLogs', enabled: true }]
    metrics: [{ category: 'AllMetrics', enabled: true }]
  }
}

resource api 'Microsoft.App/containerApps@2026-01-01' = {
  name: 'ca-${namePrefix}-api'
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${apiIdentityId}': {} } }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Multiple'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
        traffic: [{ latestRevision: true, weight: 100 }]
      }
      registries: registries
      secrets: [
        {
          name: 'database-url'
          keyVaultUrl: '${keyVaultUri}secrets/database-url'
          identity: apiIdentityId
        }
      ]
    }
    template: {
      terminationGracePeriodSeconds: 30
      containers: [
        {
          name: 'api'
          image: image
          resources: resources
          env: [
            { name: 'GBA_ENV', value: gbaEnv }
            { name: 'GBA_HOST', value: '0.0.0.0' }
            { name: 'GBA_PORT', value: '8000' }
            { name: 'GBA_DATABASE_URL', secretRef: 'database-url' }
            { name: 'GBA_DB_POOL_MAX_SIZE', value: string(dbPoolMaxSize) }
            { name: 'GBA_CUSTOMER_WEB_DIR', value: '/app/web' }
            { name: 'GBA_TRUSTED_PROXY', value: 'azure_front_door' }
            { name: 'GBA_FRONT_DOOR_ID', value: frontDoorId }
            { name: 'GBA_AUTH_ISSUER', value: authIssuer }
            { name: 'GBA_AUTH_AUDIENCE', value: authAudience }
            { name: 'GBA_AUTH_JWKS_URL', value: authJwksUrl }
            { name: 'OTEL_SERVICE_NAME', value: 'gorgona-api-${gbaEnv}' }
            { name: 'AZURE_CLIENT_ID', value: apiIdentityClientId }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
          ]
          probes: [
            {
              type: 'Startup'
              httpGet: { path: '/health/live', port: 8000 }
              periodSeconds: 5
              failureThreshold: 24
            }
            {
              type: 'Liveness'
              httpGet: { path: '/health/live', port: 8000 }
              periodSeconds: 10
              timeoutSeconds: 2
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: { path: '/health/ready', port: 8000 }
              periodSeconds: 10
              timeoutSeconds: 3
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          { name: 'http', http: { metadata: { concurrentRequests: string(httpConcurrency) } } }
        ]
      }
    }
  }
}

resource migrate 'Microsoft.App/jobs@2026-01-01' = {
  name: 'caj-${namePrefix}-migrate'
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${jobsIdentityId}': {} } }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 900
      replicaRetryLimit: 0
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      registries: jobRegistries
      secrets: [
        {
          name: 'migration-database-url'
          keyVaultUrl: '${keyVaultUri}secrets/migration-database-url'
          identity: jobsIdentityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'migrate'
          image: image
          command: ['gba-db']
          args: ['migrate']
          resources: resources
          env: [{ name: 'GBA_MIGRATION_DATABASE_URL', secretRef: 'migration-database-url' }]
        }
      ]
    }
  }
}

resource bootstrap 'Microsoft.App/jobs@2026-01-01' = {
  name: 'caj-${namePrefix}-bootstrap'
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${jobsIdentityId}': {} } }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 600
      replicaRetryLimit: 0
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      registries: jobRegistries
      secrets: [
        {
          name: 'admin-database-url'
          keyVaultUrl: '${keyVaultUri}secrets/admin-database-url'
          identity: jobsIdentityId
        }
        {
          name: 'owner-password'
          keyVaultUrl: '${keyVaultUri}secrets/owner-password'
          identity: jobsIdentityId
        }
        {
          name: 'app-password'
          keyVaultUrl: '${keyVaultUri}secrets/app-password'
          identity: jobsIdentityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'bootstrap'
          image: image
          command: ['gba-db']
          args: ['bootstrap']
          resources: resources
          env: [
            { name: 'GBA_ADMIN_DATABASE_URL', secretRef: 'admin-database-url' }
            { name: 'GBA_OWNER_PASSWORD', secretRef: 'owner-password' }
            { name: 'GBA_APP_PASSWORD', secretRef: 'app-password' }
            { name: 'GBA_DATABASE_NAME', value: databaseName }
          ]
        }
      ]
    }
  }
}

output environmentId string = environment.id
output apiFqdn string = api.properties.configuration.ingress.fqdn
output apiName string = api.name
output apiId string = api.id
output apiServiceName string = 'gorgona-api-${gbaEnv}'
output migrateJobName string = migrate.name
output bootstrapJobName string = bootstrap.name
