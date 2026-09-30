// Persistent shared foundation. No secrets. Values without defaults must be set in the
// operator's environment by scripts/stack-up.ps1 (never committed).
using '../main-shared.bicep'

param location = 'centralus'
param acrName = readEnvironmentVariable('GBA_ACR_NAME')
param logDailyQuotaGb = 1
param lockResourceGroup = true
