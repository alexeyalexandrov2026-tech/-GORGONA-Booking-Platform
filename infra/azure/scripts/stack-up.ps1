<#
.SYNOPSIS
  Create or update one GORGONA deployment stack (budgets | shared | ai | staging | production).

.DESCRIPTION
  Without -Execute this prints the plan and the exact az commands and makes NO Azure
  call. -Preview runs a read-only `az deployment sub what-if`. -Execute creates or
  updates the stack after a typed confirmation. Every -Execute and -Preview needs the
  owner's explicit approval for that specific action (docs/plan/M4_PLAN.md).

  In create mode, database passwords are generated in memory with a CSPRNG, passed to
  the az child process through environment variables, and then removed. They are never
  written to disk or printed. Update mode reads them back from Key Vault via getSecret().

  The `budgets` stack (subscription budget + cost-anomaly alert, both free) must exist
  before any other stack is previewed or executed; the script checks this read-only.

  Non-secret inputs (GBA_UNIQUE_SUFFIX, GBA_IMAGE, GBA_ACR_*, GBA_AUTH_*, GBA_BUDGET_*,
  GBA_OPERATOR_OBJECT_ID, ...) must already be set in the environment.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [ValidateSet('budgets', 'shared', 'ai', 'staging', 'production')] [string] $Stack,
    [ValidateSet('create', 'update')] [string] $Mode = 'create',
    [switch] $Preview,
    [switch] $Execute,
    # Non-interactive confirmation: must equal the stack name (gorgona-<Stack>) exactly.
    [string] $ConfirmName
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$az = 'az'

$policy = @{
    budgets    = @{ Template = 'main-budgets.bicep';  ActionOnUnmanage = 'detachAll'; Deny = 'denyDelete' }
    shared     = @{ Template = 'main-shared.bicep';   ActionOnUnmanage = 'detachAll'; Deny = 'denyDelete' }
    ai         = @{ Template = 'main-ai.bicep';       ActionOnUnmanage = 'detachAll'; Deny = 'denyDelete' }
    staging    = @{ Template = 'main-platform.bicep'; ActionOnUnmanage = 'deleteAll'; Deny = 'none' }
    production = @{ Template = 'main-platform.bicep'; ActionOnUnmanage = 'detachAll'; Deny = 'denyDelete' }
}[$Stack]

$paramFile = if ($Stack -in @('budgets', 'shared')) { "$Stack.bicepparam" } else { "$Stack.$Mode.bicepparam" }
$stackName = "gorgona-$Stack"
$secretVars = switch ($Stack) {
    'ai'         { @('GBA_AI_PG_ADMIN_PASSWORD') }
    'staging'    { @('GBA_PG_ADMIN_PASSWORD', 'GBA_OWNER_ROLE_PASSWORD', 'GBA_APP_ROLE_PASSWORD') }
    'production' { @('GBA_PG_ADMIN_PASSWORD', 'GBA_OWNER_ROLE_PASSWORD', 'GBA_APP_ROLE_PASSWORD') }
    default      { @() }
}

function New-UrlSafePassword([int] $Length = 40) {
    # Letters, digits, '-' and '_' only, so DSNs need no escaping. It always contains
    # upper, lower and digit characters (Azure PostgreSQL complexity rules).
    $alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789-_'.ToCharArray()
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        do {
            $bytes = New-Object byte[] $Length
            $rng.GetBytes($bytes)
            $chars = foreach ($b in $bytes) { $alphabet[$b % $alphabet.Length] }
            $value = -join $chars
        } until ($value -cmatch '[A-Z]' -and $value -cmatch '[a-z]' -and $value -match '[0-9]')
        return $value
    } finally { $rng.Dispose() }
}

$region = if ($env:GBA_LOCATION) { $env:GBA_LOCATION } else { 'centralus' }
$common = @('--location', $region, '--template-file', (Join-Path $root $policy.Template),
            '--parameters', (Join-Path $root "params\$paramFile"))
$createArgs = @('stack', 'sub', 'create', '--name', $stackName) + $common + @(
    '--action-on-unmanage', $policy.ActionOnUnmanage, '--deny-settings-mode', $policy.Deny, '--yes',
    '--output', 'none')
if ($policy.Deny -ne 'none') { $createArgs += '--deny-settings-apply-to-child-scopes' }
$previewArgs = @('deployment', 'sub', 'what-if', '--name', "$stackName-preview") + $common

Write-Host "Stack         : $stackName ($Mode)"
Write-Host "Template      : $($policy.Template)  params: $paramFile"
Write-Host "On unmanage   : $($policy.ActionOnUnmanage)   deny settings: $($policy.Deny)"
Write-Host "Secrets       : $(if ($Mode -eq 'create' -and $secretVars) { 'generated in memory: ' + ($secretVars -join ', ') } elseif ($secretVars) { 'read from Key Vault (getSecret)' } else { 'none' })"
Write-Host "Preview cmd   : az $($previewArgs -join ' ')"
Write-Host "Execute cmd   : az $($createArgs -join ' ')"

if (-not $Preview -and -not $Execute) {
    Write-Host "`nDRY RUN: no Azure call was made. Use -Preview or -Execute only with owner approval."
    return
}

if ($Stack -ne 'budgets') {
    # Cost guardrails first: read-only check that the budgets stack exists and succeeded.
    # PowerShell 5.1 turns native stderr into a terminating error under 'Stop'.
    $ErrorActionPreference = 'Continue'
    $state = & $az stack sub show --name gorgona-budgets --query provisioningState -o tsv 2>$null
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($code -ne 0 -or "$state".Trim() -ne 'succeeded') {
        throw "Refusing: deploy the 'budgets' stack first (budget + cost-anomaly alert). State: '$state'"
    }
}

$generated = @()
try {
    if ($Mode -eq 'create') {
        foreach ($name in $secretVars) {
            Set-Item -Path "Env:$name" -Value (New-UrlSafePassword)
            $generated += $name
        }
    }
    if ($Preview) {
        & $az @previewArgs
        if ($LASTEXITCODE -ne 0) { throw "what-if failed ($LASTEXITCODE)" }
        return
    }
    $typed = if ($PSBoundParameters.ContainsKey('ConfirmName')) { $ConfirmName } else {
        Read-Host "Type the stack name '$stackName' to $Mode it in the CURRENT subscription"
    }
    if ($typed -cne $stackName) { throw 'Confirmation did not match; nothing was changed.' }
    & $az @createArgs
    if ($LASTEXITCODE -ne 0) { throw "stack $Mode failed ($LASTEXITCODE)" }
    # Summary without parameter values (they can include recipients) or resource IDs.
    & $az stack sub show --name $stackName --query "{state: provisioningState, managedResources: length(resources), outputs: keys(outputs || ``{}``)}" -o json
    if ($Stack -in @('staging', 'production')) {
        Write-Host "`nNext (owner-approved) steps:"
        Write-Host " 1. Approve the Front Door private endpoint on the Container Apps environment:"
        Write-Host "    az network private-endpoint-connection list --id <containerAppsEnvironmentId>"
        Write-Host "    az network private-endpoint-connection approve --id <connectionId> --description 'GORGONA Front Door'"
        Write-Host " 2. Run the bootstrap job once, then the migrate job (az containerapp job start ...)."
    }
} finally {
    foreach ($name in $generated) { Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue }
}
