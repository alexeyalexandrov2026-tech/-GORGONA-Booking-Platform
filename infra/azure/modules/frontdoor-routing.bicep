// Front Door origin (Private Link to the Container Apps environment), routes, HSTS
// rule set and WAF association. The private endpoint connection must be approved on
// the environment after deployment (scripts/stack-up.ps1 prints the command).
@description('Azure region of the origin (Private Link location).')
param location string

@description('Existing Front Door profile name.')
param profileName string

@description('Endpoint names created by frontdoor-profile.bicep.')
param endpointNames array

@description('Endpoint resource IDs (same order).')
param endpointIds array

@description('WAF policy ID.')
param wafPolicyId string

@description('Container app ingress FQDN (origin host and origin Host header).')
param originFqdn string

@description('Container Apps environment ID (Private Link target).')
param environmentId string

resource profile 'Microsoft.Cdn/profiles@2025-12-01' existing = {
  name: profileName
}

resource originGroup 'Microsoft.Cdn/profiles/originGroups@2025-12-01' = {
  parent: profile
  name: 'og-api'
  properties: {
    loadBalancingSettings: {
      sampleSize: 4
      successfulSamplesRequired: 3
      additionalLatencyInMilliseconds: 50
    }
    healthProbeSettings: {
      probePath: '/health/ready'
      probeRequestType: 'GET'
      probeProtocol: 'Https'
      probeIntervalInSeconds: 30
    }
    sessionAffinityState: 'Disabled'
  }
}

resource origin 'Microsoft.Cdn/profiles/originGroups/origins@2025-12-01' = {
  parent: originGroup
  name: 'aca-api'
  properties: {
    hostName: originFqdn
    originHostHeader: originFqdn
    httpPort: 80
    httpsPort: 443
    priority: 1
    weight: 1000
    enabledState: 'Enabled'
    enforceCertificateNameCheck: true
    sharedPrivateLinkResource: {
      privateLink: { id: environmentId }
      groupId: 'managedEnvironments'
      privateLinkLocation: location
      requestMessage: 'GORGONA Front Door Private Link'
    }
  }
}

resource ruleSet 'Microsoft.Cdn/profiles/ruleSets@2025-12-01' = {
  parent: profile
  name: 'security'
}

// HSTS without includeSubDomains: tenant apex/sibling domains are not ours to pin.
resource hsts 'Microsoft.Cdn/profiles/ruleSets/rules@2025-12-01' = {
  parent: ruleSet
  name: 'hsts'
  properties: {
    order: 1
    conditions: []
    actions: [
      {
        name: 'ModifyResponseHeader'
        parameters: {
          typeName: 'DeliveryRuleHeaderActionParameters'
          headerAction: 'Overwrite'
          headerName: 'Strict-Transport-Security'
          value: 'max-age=31536000'
        }
      }
    ]
    matchProcessingBehavior: 'Continue'
  }
}

// No cacheConfiguration: HTML and /v1/* are never cached at the edge.
resource routes 'Microsoft.Cdn/profiles/afdEndpoints/routes@2025-12-01' = [
  for endpointName in endpointNames: {
    name: '${profileName}/${endpointName}/default'
    properties: {
      originGroup: { id: originGroup.id }
      ruleSets: [{ id: ruleSet.id }]
      supportedProtocols: ['Http', 'Https']
      patternsToMatch: ['/*']
      forwardingProtocol: 'HttpsOnly'
      httpsRedirect: 'Enabled'
      linkToDefaultDomain: 'Enabled'
      enabledState: 'Enabled'
    }
    dependsOn: [origin, hsts]
  }
]

resource securityPolicy 'Microsoft.Cdn/profiles/securityPolicies@2025-12-01' = {
  parent: profile
  name: 'waf'
  properties: {
    parameters: {
      type: 'WebApplicationFirewall'
      wafPolicy: { id: wafPolicyId }
      associations: [
        {
          domains: [for endpointId in endpointIds: { id: endpointId }]
          patternsToMatch: ['/*']
        }
      ]
    }
  }
}
