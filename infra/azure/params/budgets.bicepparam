// Cost guardrails, deployed first. No secrets. The amount and recipients are set in the
// operator's environment at deploy time (never committed).
using '../main-budgets.bicep'

param subscriptionBudgetAmount = int(readEnvironmentVariable('GBA_SUBSCRIPTION_BUDGET'))
param budgetStartDate = readEnvironmentVariable('GBA_BUDGET_START')
param contactEmails = [readEnvironmentVariable('GBA_BUDGET_EMAIL')]
