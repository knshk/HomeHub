<#
.SYNOPSIS
    Uninstall the FREE Home Hub (Type A) on Windows.

.DESCRIPTION
    Stops + removes the 'HomeHub' Scheduled Task (and any Startup-folder shortcut),
    kills stray hub/gateway uvicorn processes, and removes the install dir after
    confirmation.

    UNTESTED ON THIS BUILD HOST (Linux). Authored to be correct + commented.

.PARAMETER Yes
    Skip the confirmation prompt before deleting the install directory.
#>
[CmdletBinding()]
param([switch]$Yes)
$ErrorActionPreference = 'SilentlyContinue'

function Say ($m) { Write-Host "==> $m" -ForegroundColor Cyan }

$InstallDir = if ($env:HOMEHUB_DIR) { $env:HOMEHUB_DIR } else { Join-Path $env:LOCALAPPDATA 'HomeHub' }

# --- 1. Scheduled Task + Startup shortcut ------------------------------------
Say "removing Scheduled Task 'HomeHub'"
Stop-ScheduledTask -TaskName 'HomeHub' -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'HomeHub' -Confirm:$false -ErrorAction SilentlyContinue

$lnk = Join-Path ([System.Environment]::GetFolderPath('Startup')) 'HomeHub.lnk'
if (Test-Path $lnk) { Say "removing Startup shortcut"; Remove-Item -Force $lnk }

# --- 2. Kill stray processes -------------------------------------------------
# Match uvicorn processes whose command line references the install dir.
Say "stopping stray hub/gateway processes"
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='uvicorn.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -match 'app\.main:app' -and $_.CommandLine -match [regex]::Escape($InstallDir) } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# --- 3. Remove install dir (with confirmation) -------------------------------
if (Test-Path $InstallDir) {
    if (-not $Yes) {
        $reply = Read-Host "Delete install dir and ALL local data?`n  $InstallDir`nType 'yes' to confirm"
        if ($reply -ne 'yes') { Say "left $InstallDir in place. Service removed; nothing deleted."; exit 0 }
    }
    Say "removing $InstallDir"
    Remove-Item -Recurse -Force $InstallDir
} else {
    Say "install dir not found ($InstallDir); nothing to remove."
}
Say "uninstall complete."
