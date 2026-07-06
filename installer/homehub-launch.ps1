<#
.SYNOPSIS
    Windows runtime launcher for the FREE Home Hub (Type A: BYO-LLM).
    The Scheduled Task / Startup shortcut runs this at logon.

.DESCRIPTION
    Starts the qwen-stack gateway (auth/proxy) and the Home Hub (UI), each from
    its own venv, reading host/port from the staged .env files. Does NOT start
    Ollama and downloads nothing (Type A). Both are launched hidden in the
    background; the gateway is skipped if its port already answers.

    UNTESTED ON THIS BUILD HOST (Linux). Authored to be correct + commented.
#>
[CmdletBinding()]
param(
    [switch]$Open,        # also open the default browser at the hub URL
    [switch]$NoGateway    # skip starting the gateway
)
$ErrorActionPreference = 'SilentlyContinue'

$InstallDir = if ($env:HOMEHUB_DIR) { $env:HOMEHUB_DIR } else { Join-Path $env:LOCALAPPDATA 'HomeHub' }
$HubDir = Join-Path $InstallDir 'home-hub'
$GwDir  = Join-Path $InstallDir 'qwen-stack'
$LogDir = Join-Path $InstallDir 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Read-EnvVal { param($File,$Key,$Default)
    if (Test-Path $File) {
        $line = Get-Content $File | Where-Object { $_ -match "^$Key=" } | Select-Object -Last 1
        if ($line) { return ($line -split '=',2)[1] }
    }
    return $Default
}

$HubHost = Read-EnvVal (Join-Path $HubDir '.env') 'HUB_HOST'     '0.0.0.0'
$HubPort = Read-EnvVal (Join-Path $HubDir '.env') 'HUB_PORT'     '8090'
$GwHost  = Read-EnvVal (Join-Path $GwDir  '.env') 'GATEWAY_HOST' '127.0.0.1'
$GwPort  = Read-EnvVal (Join-Path $GwDir  '.env') 'GATEWAY_PORT' '8080'

# --- gateway ---
if (-not $NoGateway) {
    $gwUp = $false
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:$GwPort/healthz" | Out-Null; $gwUp = $true } catch {}
    if (-not $gwUp) {
        $gwPy = Join-Path $GwDir '.venv\Scripts\uvicorn.exe'
        if (Test-Path $gwPy) {
            Start-Process -FilePath $gwPy `
                -ArgumentList @('app.main:app','--host',$GwHost,'--port',$GwPort) `
                -WorkingDirectory $GwDir -WindowStyle Hidden `
                -RedirectStandardOutput (Join-Path $LogDir 'gateway.log') `
                -RedirectStandardError  (Join-Path $LogDir 'gateway.err.log')
        }
    }
}

# --- hub ---
$hubUv = Join-Path $HubDir '.venv\Scripts\uvicorn.exe'
if (-not (Test-Path $hubUv)) { Write-Error "hub venv missing at $HubDir\.venv"; exit 1 }
Start-Process -FilePath $hubUv `
    -ArgumentList @('app.main:app','--host',$HubHost,'--port',$HubPort) `
    -WorkingDirectory $HubDir -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $LogDir 'hub.log') `
    -RedirectStandardError  (Join-Path $LogDir 'hub.err.log')

if ($Open) {
    for ($i=0; $i -lt 30; $i++) {
        try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 "http://127.0.0.1:$HubPort/" | Out-Null; break } catch { Start-Sleep -Milliseconds 500 }
    }
    Start-Process "http://localhost:$HubPort"
}
