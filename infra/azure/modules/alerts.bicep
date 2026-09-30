// Operator alerts for one platform environment (AZURE_ARCHITECTURE section 10).
// Metric alerts use platform metrics verified against the Azure Monitor supported-metrics
// reference; log alerts query the shared Application Insights, filtered to this
// environment's service name (cloud_RoleName = OTEL_SERVICE_NAME). Thresholds are initial
// and are re-tuned from the staging load baseline. Budget alerts live in budget.bicep.
@description('Azure region for log alert rules.')
param location string

@description('Name prefix, e.g. gorgona-staging.')
param namePrefix string

@description('Resource tags.')
param tags object

@description('Operator e-mail recipients (owner of every alert).')
param contactEmails array

@description('Front Door profile, PostgreSQL server and API container app resource IDs.')
param frontDoorProfileId string
param postgresServerId string
param apiContainerAppId string

@description('Shared Application Insights resource ID and this environment\'s service name.')
param appInsightsId string
param serviceName string

@description('Connection budget: maxReplicas * pool + migration(1) + ops reserve(15).')
param connectionBudget int

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-${namePrefix}-operator'
  location: 'Global'
  tags: tags
  properties: {
    groupShortName: take(replace(namePrefix, 'gorgona-', 'gba-'), 12)
    enabled: true
    emailReceivers: [
      for (email, i) in contactEmails: {
        name: 'operator-${i}'
        emailAddress: email
        useCommonAlertSchema: true
      }
    ]
  }
}

var metricAlerts = [
  {
    key: 'api-unavailable'
    description: 'Origin health below 50% for 5 min. Action: check revision health; roll back the revision.'
    severity: 1
    scope: frontDoorProfileId
    namespace: 'Microsoft.Cdn/profiles'
    metric: 'OriginHealthPercentage'
    operator: 'LessThan'
    threshold: 50
    aggregation: 'Average'
    window: 'PT5M'
    frequency: 'PT1M'
  }
  {
    key: 'error-rate'
    description: '5xx share above 2% for 5 min. Action: inspect traces; roll back if release-related.'
    severity: 2
    scope: frontDoorProfileId
    namespace: 'Microsoft.Cdn/profiles'
    metric: 'Percentage5XX'
    operator: 'GreaterThan'
    threshold: 2
    aggregation: 'Average'
    window: 'PT5M'
    frequency: 'PT1M'
  }
  {
    key: 'database-unavailable'
    description: 'Database down for about 2 of the last 5 min. Action: check server/HA state; follow the failover/restore runbook.'
    severity: 1
    scope: postgresServerId
    namespace: 'Microsoft.DBforPostgreSQL/flexibleServers'
    metric: 'is_db_alive'
    operator: 'LessThan'
    threshold: json('0.6')
    aggregation: 'Average'
    window: 'PT5M'
    frequency: 'PT1M'
  }
  {
    key: 'storage-near-full'
    description: 'Storage above 80%. Action: confirm autogrow; review growth.'
    severity: 3
    scope: postgresServerId
    namespace: 'Microsoft.DBforPostgreSQL/flexibleServers'
    metric: 'storage_percent'
    operator: 'GreaterThan'
    threshold: 80
    aggregation: 'Average'
    window: 'PT15M'
    frequency: 'PT5M'
  }
  {
    key: 'pool-saturation'
    description: 'Active connections above the connection budget for 15 min. Action: lower replicas or pool size; evaluate PgBouncer.'
    severity: 2
    scope: postgresServerId
    namespace: 'Microsoft.DBforPostgreSQL/flexibleServers'
    metric: 'active_connections'
    operator: 'GreaterThan'
    threshold: connectionBudget
    aggregation: 'Minimum'
    window: 'PT15M'
    frequency: 'PT5M'
  }
  {
    key: 'replica-restarts'
    description: 'A replica restarted more than 3 times (cumulative per replica). Action: keep traffic on the previous revision; inspect logs.'
    severity: 2
    scope: apiContainerAppId
    namespace: 'Microsoft.App/containerApps'
    metric: 'RestartCount'
    operator: 'GreaterThan'
    threshold: 3
    aggregation: 'Maximum'
    window: 'PT15M'
    frequency: 'PT5M'
  }
]

resource metricRules 'Microsoft.Insights/metricAlerts@2026-01-01' = [
  for a in metricAlerts: {
    name: 'alert-${namePrefix}-${a.key}'
    location: 'global'
    tags: tags
    properties: {
      description: a.description
      severity: a.severity
      enabled: true
      scopes: [a.scope]
      evaluationFrequency: a.frequency
      windowSize: a.window
      autoMitigate: true
      criteria: {
        'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
        allOf: [
          {
            criterionType: 'StaticThresholdCriterion'
            name: a.metric
            metricNamespace: a.namespace
            metricName: a.metric
            operator: a.operator
            threshold: a.threshold
            timeAggregation: a.aggregation
          }
        ]
      }
      actions: [{ actionGroupId: actionGroup.id }]
    }
  }
]

// Classic Application Insights tables (the rules are scoped to the component).
var ours = 'cloud_RoleName == "${serviceName}"'
var logAlerts = [
  {
    key: 'latency-p95'
    description: 'p95 API latency above 1.5 s over 10 min. Action: check database CPU and slow queries.'
    severity: 3
    query: 'requests | where ${ours} | summarize p95 = percentile(duration, 95)'
    measure: 'p95'
    aggregation: 'Average'
    threshold: 1500
    window: 'PT10M'
    frequency: 'PT5M'
    range: ''
  }
  {
    key: 'confirmation-failures'
    description: 'More than 5 server errors on booking confirmation in 10 min. Action: investigate; this is not normal customer error.'
    severity: 2
    query: 'requests | where ${ours} and name endswith "/confirm" and toint(resultCode) >= 500'
    measure: ''
    aggregation: 'Count'
    threshold: 5
    window: 'PT10M'
    frequency: 'PT5M'
    range: ''
  }
  {
    // TENANT_NOT_FOUND counts both unknown hosts and Front Door ID refusals.
    key: 'tenant-resolution-anomaly'
    description: 'TENANT_NOT_FOUND in the last hour above 10x the hourly baseline of the previous 47 h (floor 50). Action: check for scans or misrouting; review WAF rules.'
    severity: 3
    query: join(
      [
        'let refused = customMetrics | where ${ours} and name == "gorgona.domain_errors" and tostring(customDimensions.code) == "TENANT_NOT_FOUND";'
        'let recent = toscalar(refused | where timestamp > ago(1h) | summarize sum(valueSum));'
        'let baseline = toscalar(refused | where timestamp between (ago(2d) .. ago(1h)) | summarize sum(valueSum) / 47.0);'
        'print recent = coalesce(recent, 0.0), baseline = coalesce(baseline, 0.0)'
        '| where recent > max_of(10.0 * baseline, 50.0)'
      ],
      '\n'
    )
    measure: ''
    aggregation: 'Count'
    threshold: 0
    window: 'PT1H'
    frequency: 'PT1H'
    range: 'P2D'
  }
]

resource logRules 'Microsoft.Insights/scheduledQueryRules@2026-03-01' = [
  for a in logAlerts: {
    name: 'alert-${namePrefix}-${a.key}'
    location: location
    tags: tags
    kind: 'LogAlert'
    properties: {
      description: a.description
      severity: a.severity
      enabled: true
      scopes: [appInsightsId]
      evaluationFrequency: a.frequency
      windowSize: a.window
      overrideQueryTimeRange: empty(a.range) ? null : a.range
      autoMitigate: true
      criteria: {
        allOf: [
          {
            query: a.query
            metricMeasureColumn: empty(a.measure) ? null : a.measure
            timeAggregation: a.aggregation
            operator: 'GreaterThan'
            threshold: a.threshold
            failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
          }
        ]
      }
      actions: { actionGroups: [actionGroup.id] }
    }
  }
]

output actionGroupId string = actionGroup.id
var alertNames = concat(
  map(metricAlerts, a => 'alert-${namePrefix}-${a.key}'),
  map(logAlerts, a => 'alert-${namePrefix}-${a.key}')
)
output alertNames array = alertNames
