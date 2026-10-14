$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
function Check-Exit([string]$Label) {
    if ($LASTEXITCODE -ne 0) { throw "FAIL: $Label" }
    Write-Host "PASS: $Label"
}
function Get-Health([string]$Url) {
    try { return Invoke-RestMethod -Uri $Url -TimeoutSec 5 } catch { return $null }
}
function Wait-Health([string]$Url) {
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $value = Get-Health $Url
        if ($null -ne $value) { return $value }
        Start-Sleep -Seconds 2
    }
    throw "FAIL: service unavailable at $Url"
}
Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath $python)) { throw 'FAIL: install the project .venv first.' }
    # Read only the public port; never print the private environment file.
    $backendPort = & $python -c "import os; from dotenv import load_dotenv; load_dotenv(); p=int(os.getenv('BACKEND_PORT','8000')); assert 1 <= p <= 65535, 'Invalid BACKEND_PORT'; assert len(os.getenv('PROJECT_BUILDER_TOKEN','')) >= 32, 'Set PROJECT_BUILDER_TOKEN in .env (at least 32 characters)'; print(p)"
    Check-Exit 'Local environment and project authentication configured'
    $backendUrl = "http://127.0.0.1:$backendPort"
    & docker info --format '{{.ServerVersion}}'
    Check-Exit 'Docker engine'
    & docker image inspect --format '{{.Id}}' postgres:15 hermes-validator-web:phase1
    Check-Exit 'Cached database and validator images'
    & ollama --version
    Check-Exit 'Ollama CLI'
    $ollamaModels = & $python -c "from dotenv import load_dotenv; load_dotenv(); from app.ollama_client import model_names; print(','.join(model_names()))"
    Check-Exit 'Ollama model configuration'
    $tags = Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 10
    $installedModels = @($tags.models.name)
    $missingModels = @($ollamaModels -split ',' | Where-Object { $_ -notin $installedModels })
    if ($missingModels.Count -gt 0) {
        throw "FAIL: Install both configured local models before offline use: $($missingModels -join ', ')"
    }
    Write-Host "PASS: Ollama service and local models $ollamaModels"
    # Keep the backend transport and project helper on the local Ollama stack.
    $env:AI_PROVIDER = 'ollama'
    $env:OLLAMA_MODELS = $ollamaModels
    $env:OLLAMA_BASE_URL = 'http://host.docker.internal:11434'
    $env:OLLAMA_HOST_BASE_URL = 'http://127.0.0.1:11434'
    $env:HERMES_ROUTER_URL = 'http://host.docker.internal:8766'
    & docker compose config --quiet
    Check-Exit 'Compose configuration'
    & docker compose up -d --pull never --no-build db backend
    Check-Exit 'Core containers started without downloads'
    & docker compose exec -T db sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
    Check-Exit 'PostgreSQL'
    $null = Wait-Health "$backendUrl/"
    Write-Host 'PASS: backend HTTP'
    & docker compose exec -T backend python -c "from app.ai import call_assistant; r=call_assistant([], 'Reply exactly: LOCAL MODEL WORKING'); assert r['provider']=='ollama' and r['response'].strip(); print(r['response'])"
    Check-Exit 'Backend -> local Ollama inference'
    $health = Get-Health 'http://127.0.0.1:8766/health'
    if ($null -eq $health) {
        Start-Process -FilePath $python -ArgumentList 'offline_host.py' -WorkingDirectory $projectRoot -WindowStyle Hidden | Out-Null
        $health = Wait-Health 'http://127.0.0.1:8766/health'
    }
    if ($health.project_engine -ne 'ollama' -or $health.chat_provider -ne 'ollama') {
        throw 'FAIL: port 8766 is not the local Ollama helper.'
    }
    Write-Host 'PASS: local project/validation/repair helper'
    # A dedicated origin serves generated websites; backend continues to show files as text.
    $previewUrl = 'http://127.0.0.1:8050/health'
    if ($null -eq (Get-Health $previewUrl)) {
        Start-Process -FilePath $python -ArgumentList 'preview_local.py' -WorkingDirectory $projectRoot -WindowStyle Hidden | Out-Null
    }
    $previewHealth = Wait-Health $previewUrl
    if ($previewHealth.service -ne 'local-project-preview') { throw 'FAIL: unexpected preview service on port 8050.' }
    Write-Host 'PASS: local project preview'
    $ui = Invoke-WebRequest -UseBasicParsing "$backendUrl/static/builder.html" -TimeoutSec 10
    if ($ui.StatusCode -ne 200) { throw 'FAIL: Web UI' }
    Write-Host 'PASS: Web UI'
    & docker compose ps
    Check-Exit 'Core service status'
    Write-Host "READY: http://localhost:$backendPort/static/builder.html"
    Write-Host 'Local readiness does not prove Internet is disconnected.'
} catch {
    Write-Error $_
    exit 1
} finally { Pop-Location }
