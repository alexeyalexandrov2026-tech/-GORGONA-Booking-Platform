// Azure Database for PostgreSQL Flexible Server, PostgreSQL 18 (never downgraded),
// public network access disabled, reached only through a private endpoint.
@description('Azure region.')
param location string

@description('Globally unique server name.')
param name string

@description('Resource tags.')
param tags object

@description('Compute SKU, e.g. Standard_D2ds_v5 or Standard_B1ms.')
param skuName string

@allowed(['Burstable', 'GeneralPurpose', 'MemoryOptimized'])
param skuTier string

@description('Storage size in GiB.')
param storageSizeGB int

@description('Backup retention (7-35 days).')
@minValue(7)
@maxValue(35)
param backupRetentionDays int

@description('Geo-redundant backup. Can only be chosen at server creation.')
param geoRedundantBackup bool

@description('Zone-redundant high availability.')
param zoneRedundantHa bool

@description('Server administrator login (not a PostgreSQL superuser on Azure).')
param administratorLogin string

@description('Server administrator password (generated at deploy time).')
@secure()
param administratorLoginPassword string

@description('Allowlisted extensions, e.g. BTREE_GIST or VECTOR.')
param allowedExtensions string

@description('Private endpoint subnet.')
param peSubnetId string

@description('privatelink.postgres.database.azure.com zone ID.')
param privateDnsZoneId string

@description('Maintenance window: day of week (0 = Sunday).')
param maintenanceDayOfWeek int = 0

@description('Maintenance window start hour (UTC).')
param maintenanceStartHour int = 8

resource server 'Microsoft.DBforPostgreSQL/flexibleServers@2025-08-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: skuName, tier: skuTier }
  properties: {
    version: '18'
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    authConfig: { passwordAuth: 'Enabled', activeDirectoryAuth: 'Disabled' }
    storage: { storageSizeGB: storageSizeGB, autoGrow: 'Enabled' }
    backup: {
      backupRetentionDays: backupRetentionDays
      geoRedundantBackup: geoRedundantBackup ? 'Enabled' : 'Disabled'
    }
    highAvailability: { mode: zoneRedundantHa ? 'ZoneRedundant' : 'Disabled' }
    network: { publicNetworkAccess: 'Disabled' }
    maintenanceWindow: {
      customWindow: 'Enabled'
      dayOfWeek: maintenanceDayOfWeek
      startHour: maintenanceStartHour
      startMinute: 0
    }
  }
}

resource extensions 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2025-08-01' = {
  parent: server
  name: 'azure.extensions'
  properties: { value: allowedExtensions, source: 'user-override' }
}

module endpoint 'private-endpoint.bicep' = {
  name: '${name}-pe'
  params: {
    location: location
    name: 'pe-${name}'
    tags: tags
    subnetId: peSubnetId
    targetResourceId: server.id
    groupId: 'postgresqlServer'
    privateDnsZoneId: privateDnsZoneId
  }
  dependsOn: [extensions]
}

output id string = server.id
output fqdn string = server.properties.fullyQualifiedDomainName
