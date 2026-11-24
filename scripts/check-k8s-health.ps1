param(
    [string]$DeploymentName = "aceest-rolling",
    [string]$ServiceName = "aceest-rolling-service",
    [string]$Selector = "app.kubernetes.io/name=aceest-rolling"
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

Invoke-Kubectl @(
    "rollout", "status", "deployment/$DeploymentName", "--timeout=180s"
) | Out-Host

$podName = Invoke-Kubectl @(
    "get", "pods", "--selector=$Selector", "-o=jsonpath={.items[0].metadata.name}"
)
if ([string]::IsNullOrWhiteSpace($podName)) {
    throw "No pod is available for selector $Selector."
}

$hasEndpoint = $false
for ($attempt = 0; $attempt -lt 15; $attempt++) {
    $endpointData = (Invoke-Kubectl @(
        "get", "endpoints", $ServiceName, "-o", "json"
    )) | ConvertFrom-Json
    if ($endpointData.subsets | Where-Object {
        $_.addresses -and $_.addresses.Count -gt 0
    }) {
        $hasEndpoint = $true
        break
    }
    Start-Sleep -Seconds 2
}
if (-not $hasEndpoint) {
    throw "Service $ServiceName has no ready endpoints."
}

$healthCheck = "import json,urllib.request; response=json.load(urllib.request.urlopen('http://127.0.0.1:5000/health',timeout=5)); assert response.get('status') == 'healthy'"
Invoke-Kubectl @("exec", $podName, "--", "python", "-c", $healthCheck) | Out-Host

Write-Output "Deployment $DeploymentName is ready and its /health endpoint is healthy."
