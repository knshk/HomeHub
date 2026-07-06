<#
.SYNOPSIS
    FREE one-click installer for the Home Hub (Type A: UI/integration only, BYO-LLM)
    on Windows (PowerShell 5.1+ / PowerShell 7).

.DESCRIPTION
    Installs and runs the Home Hub (FastAPI UI) + the qwen-stack auth/proxy gateway.
    Does NOT download Ollama or any model weights -- the user connects their OWN LLM
    later (their local Ollama, or a future cloud key).

    Mirrors install.sh: ensures Python >= 3.10 (via the 'py' launcher or winget),
    creates per-component venvs, installs requirements, writes FREE-mode .env files
    with freshly generated secrets, mints the hub's gateway key, registers a
    Scheduled Task (at logon) to autostart, starts it, and opens the browser.

    UNTESTED ON THIS BUILD HOST (Linux): authored to be correct + heavily
    commented. Validate on a real Windows box before shipping.

.PARAMETER Src
    Local source dir to copy FROM instead of downloading the release tarball.
    Must contain 'home-hub' and 'qwen-stack' subfolders. Used for testing.

.NOTES
    Env overrides honored: HOMEHUB_DIR, HUB_PORT, GATEWAY_PORT, HUB_NAME,
    RELEASE_URL, NO_AUTOSTART, NO_BROWSER. -Src param overrides $env:SRC.
#>
[CmdletBinding()]
param(
    [string]$Src = $env:SRC
)
$ErrorActionPreference = 'Stop'

function Say  ($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Warn ($m) { Write-Warning $m }
function Die  ($m) { Write-Error $m; exit 1 }

# --- Config / defaults -------------------------------------------------------
$InstallDir  = if ($env:HOMEHUB_DIR) { $env:HOMEHUB_DIR } else { Join-Path $env:LOCALAPPDATA 'HomeHub' }
$HubPort     = if ($env:HUB_PORT)     { $env:HUB_PORT }     else { '8090' }
$GatewayPort = if ($env:GATEWAY_PORT) { $env:GATEWAY_PORT } else { '8080' }
$HubName     = if ($env:HUB_NAME)     { $env:HUB_NAME }     else { 'Home Hub' }
# Placeholder release URL; MUST be replaced at release time (see RELEASE.md) or
# overridden via $env:RELEASE_URL. The installer refuses to download from this
# placeholder so a mis-shipped build fails loudly instead of with a 404.
$ReleaseUrlPlaceholder = 'https://downloads.example.com/homehub/homehub-latest.tar.gz'
$ReleaseUrl  = if ($env:RELEASE_URL)  { $env:RELEASE_URL }  else { $ReleaseUrlPlaceholder }

$HubDir   = Join-Path $InstallDir 'home-hub'
$GwDir    = Join-Path $InstallDir 'qwen-stack'
$LogDir   = Join-Path $InstallDir 'logs'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Say "Home Hub FREE installer (Type A: BYO-LLM) -- Windows"
Say "install dir: $InstallDir"

# --- 1. Ensure Python >= 3.10 ------------------------------------------------
# Prefer the 'py' launcher; fall back to 'python'; offer to install via winget.
function Get-PythonExe {
    foreach ($cand in @('py -3','python','python3')) {
        $parts = $cand.Split(' ')
        $exe = Get-Command $parts[0] -ErrorAction SilentlyContinue
        if ($exe) {
            try {
                $args = @()
                if ($parts.Length -gt 1) { $args += $parts[1] }
                $args += @('-c','import sys;print("%d.%d"%sys.version_info[:2])')
                $ver = & $exe.Source @args 2>$null
                if ($ver -and [version]$ver -ge [version]'3.10') {
                    return @{ Exe = $exe.Source; Pre = ($parts | Select-Object -Skip 1) }
                }
            } catch { }
        }
    }
    return $null
}

$py = Get-PythonExe
if (-not $py) {
    Say "Python 3.10+ not found. Attempting install via winget..."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
        # winget alters PATH for new processes; re-probe.
        $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
                    [System.Environment]::GetEnvironmentVariable('Path','User')
        $py = Get-PythonExe
    }
    if (-not $py) {
        Die "Python 3.10+ is required. Install it from https://www.python.org/downloads/windows/ (check 'Add python.exe to PATH'), then re-run."
    }
}
# Helper to invoke the chosen interpreter with its prefix args (e.g. 'py -3').
function Invoke-Py { param([Parameter(ValueFromRemainingArguments)]$a) & $py.Exe @($py.Pre + $a) }
Say "python OK: $(Invoke-Py -c 'import sys;print(sys.version.split()[0])')"

# --- 2. Obtain source --------------------------------------------------------
New-Item -ItemType Directory -Force -Path $InstallDir,$LogDir | Out-Null

function Copy-Component { param($SrcDir,$DstDir)
    if (-not (Test-Path $SrcDir)) { Die "source component not found: $SrcDir" }
    New-Item -ItemType Directory -Force -Path $DstDir | Out-Null
    # robocopy mirrors and excludes the heavy/host-specific dirs + .env.
    $exclDirs  = @('.venv','data','logs','__pycache__')
    robocopy $SrcDir $DstDir /MIR /XD @exclDirs /XF '.env' '*.pyc' | Out-Null
    # robocopy exit codes 0-7 are success; >=8 is an error.
    if ($LASTEXITCODE -ge 8) { Die "robocopy failed copying $SrcDir (code $LASTEXITCODE)" }
}

if ($Src) {
    Say "using local SRC: $Src"
    Copy-Component (Join-Path $Src 'home-hub')   $HubDir
    Copy-Component (Join-Path $Src 'qwen-stack') $GwDir
} else {
    if ($ReleaseUrl -eq $ReleaseUrlPlaceholder) {
        Die @"
this installer was shipped without a real release URL configured.

RELEASE_URL is still the placeholder:
  $ReleaseUrlPlaceholder

Do one of the following:
  - Install from a local source dir (no download needed):
      `$env:SRC = 'C:\path\to\LLMs'; .\install.ps1
  - Point at the real release tarball explicitly:
      `$env:RELEASE_URL = 'https://.../homehub-latest.tar.gz'; .\install.ps1

(Maintainers: set the real RELEASE_URL at release time; see RELEASE.md.)
"@
    }
    Say "downloading release tarball: $ReleaseUrl"
    $tmp = New-Item -ItemType Directory -Force -Path (Join-Path $env:TEMP ("homehub_" + [guid]::NewGuid()))
    $tgz = Join-Path $tmp 'homehub.tgz'
    Invoke-WebRequest -Uri $ReleaseUrl -OutFile $tgz
    # tar.exe ships with Windows 10+; extract then locate home-hub/qwen-stack.
    tar -xzf $tgz -C $tmp
    $hub = Get-ChildItem -Path $tmp -Recurse -Directory -Filter 'home-hub' | Select-Object -First 1
    if (-not $hub) { Die "release tarball did not contain a home-hub directory." }
    $base = $hub.Parent.FullName
    Copy-Component (Join-Path $base 'home-hub')   $HubDir
    Copy-Component (Join-Path $base 'qwen-stack') $GwDir
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

# Stage the launcher (a .ps1 the Scheduled Task runs).
$LauncherDst = Join-Path $InstallDir 'homehub-launch.ps1'
Copy-Item (Join-Path $ScriptDir 'homehub-launch.ps1') $LauncherDst -Force -ErrorAction SilentlyContinue

# --- 3. Per-component venv + deps -------------------------------------------
function Setup-Venv { param($Dir)
    $venv = Join-Path $Dir '.venv'
    $req  = Join-Path $Dir 'requirements.txt'
    if (-not (Test-Path $req)) { Die "missing requirements.txt in $Dir" }
    $vpy  = Join-Path $venv 'Scripts\python.exe'
    if (Test-Path $vpy) {
        Say "  reusing venv: $venv"
    } else {
        Say "  creating venv: $venv"
        # On Windows the bundled python includes pip; no --without-pip dance needed.
        Invoke-Py -m venv $venv
    }
    Say "  installing requirements ($req)"
    & $vpy -m pip install --upgrade pip | Out-Null
    & $vpy -m pip install -r $req
    return $vpy
}

Say "setting up gateway (auth/proxy) venv"
$GwPy  = Setup-Venv $GwDir
Say "setting up hub (UI) venv"
$HubPy = Setup-Venv $HubDir

# --- 4. Data dirs ------------------------------------------------------------
New-Item -ItemType Directory -Force -Path `
    (Join-Path $HubDir 'data\uploads'),(Join-Path $HubDir 'logs'),`
    (Join-Path $GwDir 'data'),(Join-Path $GwDir 'logs') | Out-Null

# --- 5. Secrets + FREE-mode .env files --------------------------------------
function New-Token { param($Prefix) "$Prefix" + (Invoke-Py -c 'import secrets;print(secrets.token_hex(20))') }
function Read-EnvVal { param($File,$Key)
    if (Test-Path $File) {
        $line = Get-Content $File | Where-Object { $_ -match "^$Key=" } | Select-Object -Last 1
        if ($line) { return ($line -split '=',2)[1] }
    }
    return $null
}

$GwEnv  = Join-Path $GwDir '.env'
$HubEnv = Join-Path $HubDir '.env'

# Reuse existing ADMIN_TOKEN on re-install so tokens don't rotate.
$AdminToken = Read-EnvVal $GwEnv 'ADMIN_TOKEN'
if (-not $AdminToken -or $AdminToken -eq 'REPLACE_ME_WITH_GENERATED_TOKEN') { $AdminToken = New-Token 'qwadm-' }

Say "writing gateway .env (FREE mode)"
@"
# Auto-generated by the Home Hub FREE installer (Type A: BYO-LLM) [Windows].
GATEWAY_HOST=0.0.0.0
GATEWAY_PORT=$GatewayPort
# BYO-LLM: your own local Ollama. Nothing is downloaded by this installer.
OLLAMA_BASE_URL=http://127.0.0.1:11434
DB_PATH=$($GwDir -replace '\\','/')/data/gateway.db
ADMIN_TOKEN=$AdminToken
DEFAULT_RPM=60
"@ | Set-Content -Encoding ASCII $GwEnv

# Mint the hub's gateway key via the gateway's own CLI (creates schema on demand).
$HubGwKey = Read-EnvVal $HubEnv 'HUB_GATEWAY_KEY'
if (-not ($HubGwKey -like 'qwsk-*')) {
    Say "minting a gateway API key for the hub"
    Push-Location $GwDir
    $env:DB_PATH = "$GwDir\data\gateway.db"
    $mint = & $GwPy 'adminctl.py' 'create' '--name' 'home-hub' 2>$null
    Pop-Location
    $HubGwKey = ($mint | Select-String -Pattern 'qwsk-[0-9a-f]{40}' | Select-Object -First 1).Matches.Value
    if (-not $HubGwKey) { Warn "could not mint a gateway key; set HUB_GATEWAY_KEY in $HubEnv later." }
}

$BootToken = Read-EnvVal $HubEnv 'HUB_BOOTSTRAP_TOKEN'
if (-not $BootToken) { $BootToken = New-Token 'hubboot-' }

# Best-effort LAN IP for friendly URLs.
$LanIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
          Where-Object { $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -ne '127.0.0.1' } |
          Select-Object -First 1).IPAddress
if (-not $LanIp) { $LanIp = '127.0.0.1' }

Say "writing hub .env (FREE mode)"
@"
# Auto-generated by the Home Hub FREE installer (Type A: BYO-LLM) [Windows].
HUB_NAME=$HubName
HUB_HOST=0.0.0.0
HUB_PORT=$HubPort
GATEWAY_URL=http://127.0.0.1:$GatewayPort
HUB_GATEWAY_KEY=$HubGwKey
HUB_ADMIN_TOKEN=$AdminToken
HUB_BOOTSTRAP_TOKEN=$BootToken
OLLAMA_URL=http://127.0.0.1:11434
EMBED_MODEL=nomic-embed-text
VISION_MODEL=moondream
DB_PATH=$($HubDir -replace '\\','/')/data/hub.db
LAN_IP=$LanIp
"@ | Set-Content -Encoding ASCII $HubEnv

# --- 6. Autostart: Scheduled Task at logon (preferred) + start now ----------
$HubUrl = "http://localhost:$HubPort"
if ($env:NO_AUTOSTART -ne '1') {
    Say "registering Scheduled Task 'HomeHub' (runs at logon)"
    try {
        $action  = New-ScheduledTaskAction -Execute 'powershell.exe' `
                   -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$LauncherDst`""
        $trigger = New-ScheduledTaskTrigger -AtLogOn
        $set     = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
        Register-ScheduledTask -TaskName 'HomeHub' -Action $action -Trigger $trigger -Settings $set -Force | Out-Null
        Start-ScheduledTask -TaskName 'HomeHub'
    } catch {
        Warn "Scheduled Task registration failed ($_). Falling back to a Startup-folder shortcut."
        # Fallback: a .lnk in the user's Startup folder.
        $startup = [System.Environment]::GetFolderPath('Startup')
        $wsh = New-Object -ComObject WScript.Shell
        $lnk = $wsh.CreateShortcut((Join-Path $startup 'HomeHub.lnk'))
        $lnk.TargetPath = 'powershell.exe'
        $lnk.Arguments  = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$LauncherDst`""
        $lnk.Save()
        # Start it now too.
        Start-Process powershell -ArgumentList "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$LauncherDst`""
    }
} else {
    Say "NO_AUTOSTART=1: starting once via launcher"
    Start-Process powershell -ArgumentList "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$LauncherDst`""
}

# --- 7. Open the browser -----------------------------------------------------
if ($env:NO_BROWSER -ne '1') {
    Say "opening $HubUrl"
    for ($i=0; $i -lt 30; $i++) {
        try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 "http://127.0.0.1:$HubPort/" | Out-Null; break } catch { Start-Sleep -Milliseconds 500 }
    }
    Start-Process $HubUrl
}

# --- 8. Next steps -----------------------------------------------------------
Write-Host @"

============================================================================
 HOME HUB INSTALLED  (FREE / Type A: BYO-LLM)  [Windows]
============================================================================
 Hub UI         : $HubUrl
 On your LAN    : http://$LanIp`:$HubPort
 Install dir    : $InstallDir
 First-admin bootstrap token: $BootToken

 CONNECT YOUR LLM (no model is bundled in the FREE build):
   A) Install Ollama yourself (https://ollama.com/download), then:
        ollama pull qwen2.5:7b-instruct-q4_K_M
        (optional) ollama pull nomic-embed-text ; ollama pull moondream
      Ollama serves http://127.0.0.1:11434 by default -- already wired.
   B) Or point $GwDir\.env -> OLLAMA_BASE_URL at a remote OpenAI-compatible API.

 Manage: Task Scheduler -> 'HomeHub'  (or re-run the launcher).
 Uninstall: .\uninstall.ps1
============================================================================
"@
