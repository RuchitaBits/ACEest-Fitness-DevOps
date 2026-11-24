param(
    [string]$DeploymentName = "aceest-rolling",
    [string]$ServiceName = "aceest-rolling-service",
    [string]$Selector = "app.kubernetes.io/name=aceest-rolling"
)

$ErrorActionPreference = "Stop"

& kubectl rollout undo "deployment/$DeploymentName"
if ($LASTEXITCODE -ne 0) {
    throw "Could not roll back deployment $DeploymentName."
}

& kubectl rollout status "deployment/$DeploymentName" --timeout=180s
if ($LASTEXITCODE -ne 0) {
    throw "Rollback of $DeploymentName did not become ready."
}

& (Join-Path $PSScriptRoot "check-k8s-health.ps1") `
    -DeploymentName $DeploymentName `
    -ServiceName $ServiceName `
    -Selector $Selector

Write-Output "Rollback completed and the deployment is ready."
