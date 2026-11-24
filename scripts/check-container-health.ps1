param(
    [Parameter(Mandatory = $true)]
    [string]$DockerExe,
    [Parameter(Mandatory = $true)]
    [string]$Image
)

$ErrorActionPreference = "Stop"
$containerName = "aceest-health-" + [Guid]::NewGuid().ToString("N")
$containerStarted = $false

try {
    & $DockerExe run --detach --name $containerName --publish "127.0.0.1::5000" $Image
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to start the runtime container."
    }
    $containerStarted = $true

    $portOutput = & $DockerExe port $containerName "5000/tcp"
    if ($LASTEXITCODE -ne 0 -or
        $portOutput -notmatch ":(?<port>[0-9]+)$") {
        throw "Unable to determine the runtime container's published port."
    }
    $port = [int]$Matches.port

    $deadline = (Get-Date).AddSeconds(90)
    $healthStatus = ""
    do {
        $healthStatus = & $DockerExe inspect --format "{{.State.Health.Status}}" $containerName
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to inspect runtime container health."
        }
        if ($healthStatus -eq "healthy") {
            break
        }
        if ($healthStatus -eq "unhealthy") {
            throw "The runtime container reported an unhealthy status."
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)

    if ($healthStatus -ne "healthy") {
        throw "The runtime container did not become healthy within 90 seconds."
    }

    $response = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 5
    if ($response.status -ne "healthy") {
        throw "The runtime /health endpoint returned an unexpected status."
    }
    Write-Output "Runtime image health check passed."
} finally {
    if ($containerStarted) {
        & $DockerExe rm --force --volumes $containerName | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Unable to remove the temporary health-check container."
        }
    }
}
