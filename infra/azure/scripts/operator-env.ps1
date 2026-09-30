<#
.SYNOPSIS
  Set the non-secret GBA_* inputs that stack-up.ps1 and the .bicepparam files read.

.DESCRIPTION
  Dot-source it in the operator's PowerShell session:
      . .\operator-env.ps1 -Stage budgets
      . .\operator-env.ps1 -Stage staging -Image <acr>.azurecr.io/gorgona-api@sha256:...

  Every value is derived with read-only Azure calls or from the signed-in account; nothing
  is written to disk, and only variable NAMES are printed. Secrets are never set here:
  stack-up.ps1 generates database passwords in create mode, and update-mode parameter
  files read them from Key Vault.

  Naming: GBA_UNIQUE_SUFFIX is 'g' + 5 hex characters of SHA-256("gorgona:<subscription
  id>"), so it is stable for a subscription and does not reveal the subscription ID.

  Staff OIDC for staging: the tenant's public Microsoft Entra issuer and JWKS endpoints
  with the audience 'api://gorgona-staging'. No app registration is created, so no token
  can carry that audience and staff APIs reject every call (fail closed). The customer
  booking flow needs no staff token.
#>
[CmdletBinding()]
param(
    [ValidateSet('budgets', 'shared', 'ai', 'staging')] [string] $Stage = 'budgets',
    [int] $BudgetAmount = 100,
    [string] $BudgetStart = '2026-10-01',
    [string] $Image
)
$ErrorActionPreference = 'Stop'

function Get-AzValue([string[]] $Arguments) {
    $value = az @Arguments
    if ($LASTEXITCODE -ne 0) { throw "az $($Arguments[0..1] -join ' ') failed" }
    return $value
}

$account = Get-AzValue @('account', 'show', '-o', 'json') | Out-String | ConvertFrom-Json
$sha = [System.Security.Cryptography.SHA256]::Create()
try {
    $bytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes("gorgona:$($account.id)"))
} finally { $sha.Dispose() }
$suffix = 'g' + (-join ($bytes | ForEach-Object { $_.ToString('x2') })).Substring(0, 5)

$set = [ordered]@{
    GBA_SUBSCRIPTION_ID     = $account.id
    GBA_UNIQUE_SUFFIX       = $suffix
    GBA_SUBSCRIPTION_BUDGET = "$BudgetAmount"
    GBA_BUDGET_START        = $BudgetStart
    GBA_BUDGET_EMAIL        = $account.user.name
    GBA_ACR_NAME            = "gorgonaacr$suffix"
    GBA_AI_KV_NAME          = "kv-gba-ai-$suffix"
    GBA_STAGING_KV_NAME     = "kv-gba-stg-$suffix"
    GBA_PRODUCTION_KV_NAME  = "kv-gba-prd-$suffix"
}

if ($Stage -in @('ai', 'staging')) {
    $set.GBA_OPERATOR_OBJECT_ID = Get-AzValue @('ad', 'signed-in-user', 'show', '--query', 'id', '-o', 'tsv')
    $outputs = Get-AzValue @('stack', 'sub', 'show', '--name', 'gorgona-shared', '--query', 'outputs', '-o', 'json') |
        Out-String | ConvertFrom-Json
    $set.GBA_APPINSIGHTS_ID = $outputs.appInsightsId.value
    $set.GBA_ACR_ID = $outputs.acrId.value
    $set.GBA_ACR_LOGIN_SERVER = $outputs.acrLoginServer.value
    $set.GBA_LOG_WORKSPACE_ID = $outputs.workspaceId.value
}

if ($Stage -eq 'staging') {
    if (-not $Image -or $Image -notmatch '@sha256:[0-9a-f]{64}$') {
        throw 'Stage staging needs -Image <registry>/gorgona-api@sha256:<digest> (deploy by digest).'
    }
    $set.GBA_IMAGE = $Image
    $set.GBA_APPINSIGHTS_CONNECTION_STRING = Get-AzValue @(
        'resource', 'show', '--ids', $set.GBA_APPINSIGHTS_ID, '--query', 'properties.ConnectionString', '-o', 'tsv')
    $set.GBA_AUTH_ISSUER = "https://login.microsoftonline.com/$($account.tenantId)/v2.0"
    $set.GBA_AUTH_JWKS_URL = "https://login.microsoftonline.com/$($account.tenantId)/discovery/v2.0/keys"
    $set.GBA_AUTH_AUDIENCE = 'api://gorgona-staging'
}

foreach ($name in $set.Keys) {
    if (-not $set[$name]) { throw "could not derive $name" }
    Set-Item -Path "Env:$name" -Value $set[$name]
}
Write-Host "operator-env ($Stage): set $($set.Keys -join ', ')"
