// Cost budget with actual and forecast notifications. Deployable at subscription or
// resource-group scope (targetScope is set by the caller's module scope).
targetScope = 'subscription'

@description('Budget name.')
param name string

@description('Monthly budget amount in the billing currency.')
@minValue(1)
param amount int

@description('First day of the budget month, yyyy-MM-01.')
param startDate string

@description('Email recipients for budget notifications (supplied at deploy time).')
@minLength(1)
param contactEmails array

@description('Optional resource-group filter (empty = whole subscription).')
param resourceGroupName string = ''

resource budget 'Microsoft.Consumption/budgets@2026-06-01' = {
  name: name
  properties: {
    category: 'Cost'
    amount: amount
    timeGrain: 'Monthly'
    timePeriod: { startDate: startDate }
    filter: empty(resourceGroupName)
      ? null
      : {
          dimensions: { name: 'ResourceGroupName', operator: 'In', values: [resourceGroupName] }
        }
    notifications: {
      actual25: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 25
        thresholdType: 'Actual'
        contactEmails: contactEmails
      }
      actual50: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 50
        thresholdType: 'Actual'
        contactEmails: contactEmails
      }
      actual80: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 80
        thresholdType: 'Actual'
        contactEmails: contactEmails
      }
      actual100: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        thresholdType: 'Actual'
        contactEmails: contactEmails
      }
      forecast100: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        thresholdType: 'Forecasted'
        contactEmails: contactEmails
      }
    }
  }
}
