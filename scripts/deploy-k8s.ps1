param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern(":sha-[0-9a-f]{40}$")]
    [string]$Image
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Get-Command kubectl -ErrorAction SilentlyContinue)) {
    throw "kubectl is not installed or not on PATH."
}

if (-not (Test-Path -LiteralPath "k8s/config.yaml")) {
    throw "The Kubernetes ConfigMap manifest is missing."
}

$secret = & kubectl get secret aceest-secrets -o name 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($secret)) {
    throw "The cluster must contain the aceest-secrets Secret before deployment."
}

$manifest = Get-Content -LiteralPath "k8s/rolling-update.yaml" -Raw
$imageMatches = [regex]::Matches($manifest, "(?m)^\s*image:\s*\S+")
if ($imageMatches.Count -ne 1) {
    throw "Expected exactly one application image in k8s/rolling-update.yaml."
}

$imageTag = $Image.Substring($Image.LastIndexOf(":") + 1)
$renderedManifest = $manifest -replace `
    "(?m)^(\s*image:\s*)\S+", "`$1$Image"
$renderedManifest = $renderedManifest -replace `
    'sha-0{40}', $imageTag
$temporaryManifest = Join-Path ([System.IO.Path]::GetTempPath()) (
    "aceest-rolling-" + [Guid]::NewGuid().ToString("N") + ".yaml"
)
$deploymentAttempted = $false

try {
    [System.IO.File]::WriteAllText(
        $temporaryManifest,
        $renderedManifest,
        [System.Text.UTF8Encoding]::new($false)
    )

    & kubectl apply -f k8s/config.yaml
    if ($LASTEXITCODE -ne 0) { throw "Unable to apply the ConfigMap." }

    & kubectl apply -f k8s/pvc.yaml
    if ($LASTEXITCODE -ne 0) { throw "Unable to apply the SQLite PVC." }

    $deploymentAttempted = $true
    & kubectl apply -f $temporaryManifest
    if ($LASTEXITCODE -ne 0) { throw "Unable to apply the rolling deployment." }

    & kubectl rollout status deployment/aceest-rolling --timeout=180s
    if ($LASTEXITCODE -ne 0) { throw "The rolling deployment did not become ready." }

    & (Join-Path $PSScriptRoot "check-k8s-health.ps1") `
        -DeploymentName "aceest-rolling" `
        -ServiceName "aceest-rolling-service" `
        -Selector "app.kubernetes.io/name=aceest-rolling"
} catch {
    if ($deploymentAttempted) {
        Write-Warning "Deployment or health verification failed; attempting rollback."
        try {
            & (Join-Path $PSScriptRoot "rollback-k8s.ps1") `
                -DeploymentName "aceest-rolling" `
                -ServiceName "aceest-rolling-service" `
                -Selector "app.kubernetes.io/name=aceest-rolling"
        } catch {
            Write-Warning "Rollback also failed: $($_.Exception.Message)"
        }
    }
    throw
} finally {
    if (Test-Path -LiteralPath $temporaryManifest) {
        Remove-Item -LiteralPath $temporaryManifest -Force
    }
}

Write-Output "Deployment $Image is ready and health-verified."
