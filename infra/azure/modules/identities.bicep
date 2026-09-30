// User-assigned managed identities: one for the API, one for migrate/bootstrap jobs.
@description('Azure region.')
param location string

@description('Name prefix, e.g. gorgona-staging.')
param namePrefix string

@description('Resource tags.')
param tags object

resource api 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: 'id-${namePrefix}-api'
  location: location
  tags: tags
}

resource jobs 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: 'id-${namePrefix}-jobs'
  location: location
  tags: tags
}

output apiId string = api.id
output apiPrincipalId string = api.properties.principalId
output apiClientId string = api.properties.clientId
output jobsId string = jobs.id
output jobsPrincipalId string = jobs.properties.principalId
