param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(0, 50)]
    [int]$CanaryPercent
)

$ErrorActionPreference = "Stop"

function Invoke-Kubectl {
    param([string[]]$Arguments)

    $output = & kubectl @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "kubectl failed: $($Arguments -join ' ')`n$($output -join "`n")"
    }
    return ($output -join "`n").Trim()
}

function New-CanaryPatch {
    param(
        [int]$StablePercent,
        [int]$CandidatePercent
    )

    @{
        spec = @{
            http = @(
                @{
                    route = @(
                        @{
                            destination = @{
                                host = "aceest-canary"
                                subset = "stable"
                            }
                            weight = $StablePercent
                        },
                        @{
                            destination = @{
                                host = "aceest-canary"
                                subset = "canary"
                            }
                            weight = $CandidatePercent
                        }
                    )
                }
            )
        }
    } | ConvertTo-Json -Depth 8 -Compress
}

function Assert-ReadOnlyDeployment {
    param([string]$DeploymentName)

    $deployment = (Invoke-Kubectl @(
        "get", "deployment", $DeploymentName, "-o", "json"
    )) | ConvertFrom-Json
    if ($deployment.spec.replicas -lt 1 -or $deployment.status.readyReplicas -lt 1) {
        throw "Deployment $DeploymentName must have a ready replica before traffic is routed."
    }

    $container = $deployment.spec.template.spec.containers[0]
    $shadowMode = ($container.env | Where-Object {
        $_.name -eq "SHADOW_MODE"
    }).value
    if ($shadowMode -ne "true" -or
        $deployment.spec.template.spec.volumes |
            Where-Object { $_.persistentVolumeClaim }) {
        throw "Canary routing is limited to SHADOW_MODE replicas with isolated storage."
    }
}

if ($CanaryPercent -gt 0) {
    Assert-ReadOnlyDeployment -DeploymentName "aceest-canary-candidate"
}
if ($CanaryPercent -lt 100) {
    Assert-ReadOnlyDeployment -DeploymentName "aceest-canary-stable"
}

$virtualService = (Invoke-Kubectl @(
    "get", "virtualservice", "aceest-canary", "-o", "json"
)) | ConvertFrom-Json
$oldRoutes = $virtualService.spec.http[0].route
$oldStable = [int]($oldRoutes | Where-Object {
    $_.destination.subset -eq "stable"
}).weight
$oldCanary = [int]($oldRoutes | Where-Object {
    $_.destination.subset -eq "canary"
}).weight
$stablePercent = 100 - $CanaryPercent

try {
    $patch = New-CanaryPatch -StablePercent $stablePercent -CandidatePercent $CanaryPercent
    Invoke-Kubectl @(
        "patch", "virtualservice", "aceest-canary", "--type=merge", "-p", $patch
    ) | Out-Host

    $updated = (Invoke-Kubectl @(
        "get", "virtualservice", "aceest-canary", "-o", "json"
    )) | ConvertFrom-Json
    $actual = $updated.spec.http[0].route
    $actualStable = [int]($actual | Where-Object {
        $_.destination.subset -eq "stable"
    }).weight
    $actualCanary = [int]($actual | Where-Object {
        $_.destination.subset -eq "canary"
    }).weight
    if ($actualStable -ne $stablePercent -or $actualCanary -ne $CanaryPercent) {
        throw "The VirtualService did not retain the requested traffic weights."
    }
    Write-Output "Traffic split updated: stable=$stablePercent%, canary=$CanaryPercent%."
} catch {
    $failure = $_
    try {
        $rollback = New-CanaryPatch -StablePercent $oldStable -CandidatePercent $oldCanary
        Invoke-Kubectl @(
            "patch", "virtualservice", "aceest-canary", "--type=merge", "-p", $rollback
        ) | Out-Host
    } catch {
        throw "Canary routing update failed: $($failure.Exception.Message) Rollback failed: $($_.Exception.Message)"
    }
    throw $failure
}
