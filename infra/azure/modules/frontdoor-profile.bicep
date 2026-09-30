// Front Door Premium profile, endpoints and WAF policy. Split from routing so the
// app can receive the profile ID (X-Azure-FDID) before its origin is declared.
@description('Name prefix, e.g. gorgona-staging.')
param namePrefix string

@description('Resource tags.')
param tags object

@description('Endpoint names. Each gets its own <name>-<hash>.azurefd.net host; staging uses two for two FAKE tenants without DNS.')
param endpointNames array

@description('WAF mode.')
@allowed(['Detection', 'Prevention'])
param wafMode string = 'Prevention'

@description('Max POSTs to booking write routes per client IP per minute (initial; tune from the load baseline).')
param bookingWriteRateLimit int = 30

@description('Max requests to /v1/ per client IP per minute (initial).')
param apiRateLimit int = 600

resource profile 'Microsoft.Cdn/profiles@2025-12-01' = {
  name: 'afd-${namePrefix}'
  location: 'global'
  tags: tags
  sku: { name: 'Premium_AzureFrontDoor' }
  properties: { originResponseTimeoutSeconds: 60 }
}

resource endpoints 'Microsoft.Cdn/profiles/afdEndpoints@2025-12-01' = [
  for endpointName in endpointNames: {
    parent: profile
    name: endpointName
    location: 'global'
    tags: tags
    properties: { enabledState: 'Enabled' }
  }
]

resource waf 'Microsoft.Network/FrontDoorWebApplicationFirewallPolicies@2025-11-01' = {
  name: 'waf${replace(namePrefix, '-', '')}'
  location: 'global'
  tags: tags
  sku: { name: 'Premium_AzureFrontDoor' }
  properties: {
    policySettings: {
      enabledState: 'Enabled'
      mode: wafMode
      requestBodyCheck: 'Enabled'
    }
    managedRules: {
      managedRuleSets: [
        { ruleSetType: 'Microsoft_DefaultRuleSet', ruleSetVersion: '2.1', ruleSetAction: 'Block' }
        { ruleSetType: 'Microsoft_BotManagerRuleSet', ruleSetVersion: '1.1' }
      ]
    }
    customRules: {
      rules: [
        {
          name: 'BookingWriteRateLimit'
          priority: 100
          enabledState: 'Enabled'
          ruleType: 'RateLimitRule'
          rateLimitDurationInMinutes: 1
          rateLimitThreshold: bookingWriteRateLimit
          groupBy: [{ variableName: 'SocketAddr' }]
          matchConditions: [
            {
              matchVariable: 'RequestMethod'
              operator: 'Equal'
              matchValue: ['POST']
            }
            {
              matchVariable: 'RequestUri'
              operator: 'Contains'
              transforms: ['Lowercase']
              matchValue: ['/v1/customer/holds', '/confirm']
            }
          ]
          action: 'Block'
        }
        {
          name: 'ApiRateLimit'
          priority: 200
          enabledState: 'Enabled'
          ruleType: 'RateLimitRule'
          rateLimitDurationInMinutes: 1
          rateLimitThreshold: apiRateLimit
          groupBy: [{ variableName: 'SocketAddr' }]
          matchConditions: [
            {
              matchVariable: 'RequestUri'
              operator: 'Contains'
              transforms: ['Lowercase']
              matchValue: ['/v1/']
            }
          ]
          action: 'Block'
        }
      ]
    }
  }
}

output profileName string = profile.name
output frontDoorId string = profile.properties.frontDoorId
output wafPolicyId string = waf.id
output endpointIds array = [for (e, i) in endpointNames: endpoints[i].id]
output endpointHostNames array = [for (e, i) in endpointNames: endpoints[i].properties.hostName]
