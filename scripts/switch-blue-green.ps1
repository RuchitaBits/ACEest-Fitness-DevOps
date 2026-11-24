param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("blue", "green")]
    [string]$Target,
    [Parameter(Mandatory = $true)]
    [ValidatePattern(":sha-[0-9a-f]{40}$")]
    [string]$Image
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

function Wait-ForNoPods {
    param([string]$Color)

    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        $pods = Invoke-Kubectl @(
            "get", "pods", "--selector=app.kubernetes.io/name=aceest,app.kubernetes.io/track=$Color",
            "-o=jsonpath={.items[*].metadata.name}"
        )
        if ([string]::IsNullOrWhiteSpace($pods)) {
            return
        }
        Start-Sleep -Seconds 2
    }
    throw "Pods for $Color did not terminate; refusing to attach the shared SQLite volume."
}

$imageTag = $Image.Substring($Image.LastIndexOf(":") + 1)
$service = (Invoke-Kubectl @("get", "service", "aceest-service", "-o", "json")) |
    ConvertFrom-Json
$previous = $service.spec.selector."app.kubernetes.io/track"
if ($previous -notin @("blue", "green")) {
    throw "Service selector has no known active color."
}

$targetDeployment = "aceest-$Target"
$previousDeployment = "aceest-$previous"
$targetSelector = "app.kubernetes.io/name=aceest,app.kubernetes.io/track=$Target"
$previousSelector = "app.kubernetes.io/name=aceest,app.kubernetes.io/track=$previous"
$previousEndpointData = (Invoke-Kubectl @(
    "get", "endpoints", "aceest-service", "-o", "json"
)) | ConvertFrom-Json
$previousIsReady = [bool]($previousEndpointData.subsets | Where-Object {
    $_.addresses -and $_.addresses.Count -gt 0
})
if ($Target -eq $previous -and $previousIsReady) {
    throw "Traffic already targets a ready $Target deployment; use the rolling deployment for an in-place update."
}

$servicePatch = {
    param([string]$Color)
    @{
        spec = @{
            selector = @{
                "app.kubernetes.io/name" = "aceest"
                "app.kubernetes.io/track" = $Color
            }
        }
    } | ConvertTo-Json -Depth 5 -Compress
}

$config = Invoke-Kubectl @("get", "configmap", "aceest-config", "-o", "name")
$secret = Invoke-Kubectl @("get", "secret", "aceest-secrets", "-o", "name")

try {
    Invoke-Kubectl @("scale", "deployment/$targetDeployment", "--replicas=0") | Out-Host
    Wait-ForNoPods -Color $Target
    Invoke-Kubectl @(
        "set", "image", "deployment/$targetDeployment", "aceest=$Image"
    ) | Out-Host
    Invoke-Kubectl @(
        "set", "env", "deployment/$targetDeployment", "APP_VERSION=$imageTag"
    ) | Out-Host

    if ($previous -ne $Target) {
        Invoke-Kubectl @(
            "scale", "deployment/$previousDeployment", "--replicas=0"
        ) | Out-Host
        Wait-ForNoPods -Color $previous
    }

    Invoke-Kubectl @(
        "scale", "deployment/$targetDeployment", "--replicas=1"
    ) | Out-Host
    Invoke-Kubectl @(
        "rollout", "status", "deployment/$targetDeployment", "--timeout=180s"
    ) | Out-Host

    if ($previous -ne $Target) {
        $patch = & $servicePatch $Target
        Invoke-Kubectl @(
            "patch", "service", "aceest-service", "--type=merge", "-p", $patch
        ) | Out-Host
    }

    & (Join-Path $PSScriptRoot "check-k8s-health.ps1") `
        -DeploymentName $targetDeployment `
        -ServiceName "aceest-service" `
        -Selector $targetSelector
} catch {
    $failure = $_
    Write-Warning "Switch or health verification failed."
    try {
        Invoke-Kubectl @(
            "scale", "deployment/$targetDeployment", "--replicas=0"
        ) | Out-Host
        Wait-ForNoPods -Color $Target

        if ($previous -ne $Target) {
            Invoke-Kubectl @(
                "scale", "deployment/$previousDeployment", "--replicas=1"
            ) | Out-Host
            Invoke-Kubectl @(
                "rollout", "status", "deployment/$previousDeployment", "--timeout=180s"
            ) | Out-Host

            $rollbackPatch = & $servicePatch $previous
            Invoke-Kubectl @(
                "patch", "service", "aceest-service", "--type=merge", "-p", $rollbackPatch
            ) | Out-Host
            & (Join-Path $PSScriptRoot "check-k8s-health.ps1") `
                -DeploymentName $previousDeployment `
                -ServiceName "aceest-service" `
                -Selector $previousSelector
        } else {
            throw "No prior ready deployment is available for rollback."
        }
    } catch {
        throw "Switch failed: $($failure.Exception.Message) Rollback failed: $($_.Exception.Message)"
    }
    throw $failure
}

Write-Output "Traffic now targets $Target using $Image."
