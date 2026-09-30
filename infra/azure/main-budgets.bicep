// Cost guardrails (class 1, free): the subscription budget and a daily cost-anomaly
// alert. Deployed FIRST, as its own stack `gorgona-budgets`, before any resource that can
// cost money; scripts/stack-up.ps1 refuses every other stack until this one exists.
// Covers the whole subscription, including resources that are not GORGONA's.
targetScope = 'subscription'

@description('Monthly subscription budget amount in the billing currency (owner-approved).')
@minValue(1)
param subscriptionBudgetAmount int

@description('Budget start month, yyyy-MM-01 (the current month).')
param budgetStartDate string

@description('Recipients for budget and anomaly e-mails (supplied at deploy time, not committed).')
@minLength(1)
@maxLength(5)
param contactEmails array

@description('Deployment time; the anomaly alert runs for one year from it.')
param now string = utcNow('yyyy-MM-ddTHH:mm:ssZ')

module budget 'modules/budget.bicep' = {
  name: 'gorgona-subscription-budget'
  params: {
    name: 'budget-gorgona-subscription'
    amount: subscriptionBudgetAmount
    startDate: budgetStartDate
    contactEmails: contactEmails
  }
}

// Cost anomaly alert: Cost Management e-mails when it detects an unusual daily cost change
// (subscription scope, daily frequency; no e-mail when there is no anomaly).
resource anomalyAlert 'Microsoft.CostManagement/scheduledActions@2026-06-01' = {
  name: 'gorgona-cost-anomaly'
  kind: 'InsightAlert'
  properties: {
    displayName: 'GORGONA cost anomaly'
    status: 'Enabled'
    viewId: subscriptionResourceId('Microsoft.CostManagement/views', 'ms:DailyAnomalyByResourceGroup')
    notificationEmail: contactEmails[0]
    notification: {
      to: contactEmails
      subject: 'GORGONA: unusual Azure cost detected'
    }
    schedule: {
      frequency: 'Daily'
      startDate: now
      endDate: dateTimeAdd(now, 'P1Y')
    }
  }
}

output budgetName string = 'budget-gorgona-subscription'
output anomalyAlertName string = anomalyAlert.name
