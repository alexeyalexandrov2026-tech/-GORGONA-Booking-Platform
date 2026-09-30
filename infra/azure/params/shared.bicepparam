// Persistent shared foundation. No secrets. Values without defaults must be set in the
// operator's environment by scripts/stack-up.ps1 (never committed).
using '../main-shared.bicep'

param location = 'eastus2'
param acrName = readEnvironmentVariable('GBA_ACR_NAME')
param subscriptionBudgetAmount = int(readEnvironmentVariable('GBA_SUBSCRIPTION_BUDGET', '150'))
param budgetStartDate = readEnvironmentVariable('GBA_BUDGET_START')
param budgetContactEmails = [readEnvironmentVariable('GBA_BUDGET_EMAIL')]
param logDailyQuotaGb = 1
param lockResourceGroup = true
