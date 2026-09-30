<#
.SYNOPSIS
  Register the Azure resource providers GORGONA needs (a free, subscription-level change).

.DESCRIPTION
  Without -Execute this prints each provider's current registration state (read-only) and
  the exact commands, and changes nothing. -Execute registers only the providers that are
  not yet registered, after a typed confirmation. Registration itself creates no billable
  resource, but it is a subscription change and needs the owner's explicit approval.

  -Scope selects the set: 'foundation' (budgets need none; shared + staging platform) or
  'all' (adds the persistent AI learning plane).
#>
[CmdletBinding()]
param(
    [ValidateSet('foundation', 'all')] [string] $Scope = 'foundation',
    [switch] $Execute
)
$ErrorActionPreference = 'Stop'

$foundation = @(
    'Microsoft.App',                  # Container Apps environment, app, jobs
    'Microsoft.Cdn',                  # Front Door Premium + WAF
    'Microsoft.Network',              # VNet, private endpoints, private DNS, WAF policy
    'Microsoft.DBforPostgreSQL',      # PostgreSQL 18 Flexible Server
    'Microsoft.KeyVault',             # Key Vault
    'Microsoft.ContainerRegistry',    # ACR
    'Microsoft.OperationalInsights',  # Log Analytics
    'Microsoft.Insights'              # Application Insights, alerts, action groups, diagnostics
)
$ai = @('Microsoft.Storage', 'Microsoft.ServiceBus', 'Microsoft.MachineLearningServices')
$wanted = if ($Scope -eq 'all') { $foundation + $ai } else { $foundation }

$pending = @()
foreach ($ns in $wanted) {
    $state = az provider show --namespace $ns --query registrationState -o tsv
    if ($LASTEXITCODE -ne 0) { throw "could not read $ns" }
    Write-Host ('{0,-32} {1}' -f $ns, $state)
    if ($state -ne 'Registered') { $pending += $ns }
}
if (-not $pending) { Write-Host "`nAll $Scope providers are registered."; return }

Write-Host "`nWould run:"
foreach ($ns in $pending) { Write-Host "  az provider register --namespace $ns --wait" }
if (-not $Execute) {
    Write-Host "`nDRY RUN: nothing was changed. Use -Execute only with owner approval."
    return
}
$sub = az account show --query name -o tsv
$typed = Read-Host "Type 'register' to register $($pending.Count) provider(s) in subscription '$sub'"
if ($typed -ne 'register') { throw 'Confirmation did not match; nothing was changed.' }
foreach ($ns in $pending) {
    az provider register --namespace $ns --wait
    if ($LASTEXITCODE -ne 0) { throw "registration of $ns failed" }
    Write-Host "registered $ns"
}
