param([switch]$StartHermesDashboard, [switch]$Online)
if (-not $Online -and -not $StartHermesDashboard) {
    & (Join-Path $PSScriptRoot 'start_offline.ps1')
    exit $LASTEXITCODE
}
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$hermes = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\Scripts\hermes.exe'

function Test-Http([string]$Url) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
        return $response.StatusCode -eq 200
    } catch { return $false }
}
function Test-Port([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $pending = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne(1000)) { return $false }
        $client.EndConnect($pending)
        return $true
    } catch { return $false } finally { $client.Dispose() }
}
function Wait-Http([string]$Url, [int]$Seconds = 60) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        if (Test-Http $Url) { return $true }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    return $false
}

# Serialize startup attempts; never terminate an existing service.
$mutex = New-Object System.Threading.Mutex($false, 'Local\AIPersonalAssistantPresentationStartup')
$locked = $false
Push-Location $projectRoot
try {
    $locked = $mutex.WaitOne(0)
    if (-not $locked) { throw 'Another presentation startup is already running.' }
    if (-not (Test-Path -LiteralPath $python)) { throw 'Project .venv Python is missing.' }
    try {
        & docker info --format '{{.ServerVersion}}' 2>$null
        if ($LASTEXITCODE -ne 0) { throw 'Unavailable' }
    } catch { throw 'Docker engine unavailable. Start Docker Desktop and retry.' }
    & docker compose up -d --no-recreate
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose startup failed.' }
    $imagePresent = $false
    try {
        & docker image inspect --format '{{.Id}}' hermes-validator-web:phase1 2>$null
        $imagePresent = $LASTEXITCODE -eq 0
    } catch { $imagePresent = $false }
    if (-not $imagePresent) {
        & docker build -f validation/Dockerfile.web -t hermes-validator-web:phase1 .
        if ($LASTEXITCODE -ne 0) { throw 'Validator image build failed.' }
    }
    if (-not (Test-Http 'http://127.0.0.1:8765/health')) {
        if (Test-Port 8765) { throw 'Port 8765 is occupied but its health check failed.' }
        Start-Process -FilePath $python -ArgumentList 'hermes_service.py' -WorkingDirectory $projectRoot -WindowStyle Hidden | Out-Null
        if (-not (Wait-Http 'http://127.0.0.1:8765/health')) { throw 'Router failed to become healthy.' }
    }
    $health = Invoke-RestMethod 'http://127.0.0.1:8765/health' -TimeoutSec 5
    if ($health.status -ne 'ok' -or $health.service -ne 'hermes-router') { throw 'Unexpected service on port 8765.' }
    Write-Host 'PASS: hermes-router health'
    if (-not (Wait-Http 'http://localhost:8000')) { throw 'Backend failed to respond.' }
    Write-Host 'PASS: backend HTTP'
    & docker compose ps
    & $python tests/check_codex_host.py
    if ($LASTEXITCODE -ne 0) { throw 'Codex preflight failed; see the status above.' }
    if ($StartHermesDashboard -and -not (Test-Port 9119)) {
        if (-not (Test-Path -LiteralPath $hermes)) { throw 'Hermes executable is missing.' }
        Start-Process -FilePath $hermes -ArgumentList 'dashboard --host 127.0.0.1 --port 9119 --no-open --skip-build' -WorkingDirectory $projectRoot -WindowStyle Hidden | Out-Null
        if (-not (Wait-Http 'http://127.0.0.1:9119')) { throw 'Dashboard unavailable. Check application-control policy and Hermes logs.' }
    }
    if (Test-Port 9119) { Write-Host 'Dashboard port 9119 is listening.' }
    else { Write-Host 'Dashboard is stopped; its Hermes executable needs Device Guard approval.' }
    Write-Host 'Open http://localhost:8000/static/builder.html'
    Write-Host 'ACP requires administrator approval of hermes.exe; startup does not bypass Device Guard.'
    Write-Host 'Live AI verification: .\.venv\Scripts\python.exe tests/check_presentation_flow.py'
} finally {
    Pop-Location
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
