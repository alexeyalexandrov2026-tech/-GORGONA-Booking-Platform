// CanNotDelete lock on the resource group this module is deployed into.
@description('Why the lock exists (shown in the portal).')
param notes string

resource lock 'Microsoft.Authorization/locks@2020-05-01' = {
  name: 'no-delete'
  properties: {
    level: 'CanNotDelete'
    notes: notes
  }
}
