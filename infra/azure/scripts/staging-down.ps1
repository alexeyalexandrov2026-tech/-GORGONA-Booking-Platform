<#
.SYNOPSIS
  Tear down the ephemeral staging stack, and only that stack.

.DESCRIPTION
  Refuses unless all of the following hold:
   - the stack is named exactly gorgona-staging;
   - rg-gorgona-staging carries the tags env=staging and lifecycle=ephemeral;
   - every resource the stack manages is inside rg-gorgona-staging, except its own
     AcrPull role assignments on the shared registry and its own budget.
  Anything else (the AI plane, production, shared resources) makes it stop.
  Without -Execute it makes no change. -TestResourceIds checks the guard offline.
#>
[CmdletBinding()]
param(
    [switch] $Execute,
    # Non-interactive confirmation: must equal 'gorgona-staging' exactly.
    [string] $ConfirmName,
    [string[]] $TestResourceIds,
    [hashtable] $TestTags
)
$ErrorActionPreference = 'Stop'
$stackName = 'gorgona-staging'
$stagingRg = 'rg-gorgona-staging'

function Test-StagingOnly([string[]] $ResourceIds) {
    $violations = @()
    foreach ($id in $ResourceIds) {
        $lower = $id.ToLowerInvariant()
        $inStaging = $lower -match "/resourcegroups/$stagingRg(/|$)"
        $isOwnAcrPull = $lower -match '/resourcegroups/rg-gorgona-shared/providers/microsoft.containerregistry/registries/[^/]+/providers/microsoft.authorization/roleassignments/'
        $isOwnBudget = $lower -match '^/subscriptions/[^/]+/providers/microsoft.consumption/budgets/budget-gorgona-staging$'
        if (-not ($inStaging -or $isOwnAcrPull -or $isOwnBudget)) { $violations += $id }
    }
    return , $violations
}

function Test-StagingTags([hashtable] $Tags) {
    return ($Tags['env'] -eq 'staging' -and $Tags['lifecycle'] -eq 'ephemeral')
}

if ($TestResourceIds -or $TestTags) {
    $tagOk = Test-StagingTags $(if ($TestTags) { $TestTags } else { @{} })
    $violations = Test-StagingOnly $(if ($TestResourceIds) { $TestResourceIds } else { @() })
    Write-Host "OFFLINE GUARD CHECK: tags ok=$tagOk violations=$($violations.Count)"
    $violations | ForEach-Object { Write-Host "  REFUSE: $_" }
    if (-not $tagOk -or $violations.Count -gt 0) { exit 2 } else { exit 0 }
}

Write-Host "Will delete stack '$stackName' with action-on-unmanage deleteAll, after guard checks."
if (-not $Execute) {
    Write-Host 'DRY RUN: no Azure call was made. Use -Execute only with owner approval.'
    return
}

$tagsJson = az group show --name $stagingRg --query tags -o json
if ($LASTEXITCODE -ne 0) { throw "cannot read $stagingRg" }
$tags = @{}
($tagsJson | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $tags[$_.Name] = $_.Value }
if (-not (Test-StagingTags $tags)) { throw "REFUSED: $stagingRg is not tagged env=staging, lifecycle=ephemeral" }

$ids = az stack sub show --name $stackName --query 'resources[].id' -o tsv
if ($LASTEXITCODE -ne 0) { throw "cannot read stack $stackName" }
$violations = Test-StagingOnly @($ids | Where-Object { $_ })
if ($violations.Count -gt 0) {
    $violations | ForEach-Object { Write-Host "  REFUSE: $_" }
    throw 'REFUSED: the staging stack manages resources outside the staging boundary.'
}

$typed = if ($PSBoundParameters.ContainsKey('ConfirmName')) { $ConfirmName } else {
    Read-Host "Type '$stackName' to permanently delete staging resources"
}
if ($typed -cne $stackName) { throw 'Confirmation did not match; nothing was changed.' }
az stack sub delete --name $stackName --action-on-unmanage deleteAll --yes
if ($LASTEXITCODE -ne 0) { throw "stack delete failed ($LASTEXITCODE)" }
Write-Host 'Staging stack deleted. Verify: az group exists --name rg-gorgona-staging'
