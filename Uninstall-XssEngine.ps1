# AMD XSS Engine — uninstall script (Uninstall-XssEngine.ps1)
# Stops the daemon, removes the scheduled task and custom power schemes,
# restores default Windows Balanced scheme, and cleans PATH / files.
#
# Run from an elevated PowerShell:
#   .\Uninstall-XssEngine.ps1
#   .\Uninstall-XssEngine.ps1 -RemoveFiles
#
param(
    [switch]$RemoveFiles,
    [switch]$KeepLogs
)

$ErrorActionPreference = 'Stop'

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$pr = New-Object Security.Principal.WindowsPrincipal($id)
if (-not $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'This script must run from an elevated PowerShell (Run as Administrator).'
}

$TaskName = 'AMD XSS Engine'
$ScriptDir = $PSScriptRoot

Write-Host '[+] Stopping running daemon processes...'
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { -not $_.CommandLine -or $_.CommandLine -match 'xss_engine' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "    Stopped process PID $($_.ProcessId)"
    }
Start-Sleep -Seconds 1

Write-Host "[+] Removing scheduled task '$TaskName'..."
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Write-Host '    Task unregistered.'

Write-Host '[+] Restoring default Windows Balanced scheme...'
try {
    powercfg /setactive SCHEME_BALANCED | Out-Null
    Write-Host '    SCHEME_BALANCED activated.'
} catch {
    Write-Warning ("Failed to activate SCHEME_BALANCED: " + $_.Exception.Message)
}

Write-Host '[+] Cleaning up AMD Engine power schemes...'
$map = @{}
foreach ($line in (powercfg /list)) {
    if ($line -match '([0-9a-fA-F-]{36})\s+\((.+?)\)') {
        $map[$Matches[2].Trim()] = $Matches[1]
    }
}

$targets = @('AMD Engine - Eco', 'AMD Engine - Balanced', 'AMD Engine - Performance')
foreach ($name in $targets) {
    if ($map.ContainsKey($name)) {
        $guid = $map[$name]
        powercfg /delete $guid | Out-Null
        Write-Host "    [-] Deleted scheme: $name ($guid)"
    }
}

# Remove from System PATH
Write-Host '[+] Checking System PATH...'
try {
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $paths = $machinePath -split ';' | Where-Object { $_ -ne '' }
    $filtered = $paths | Where-Object {
        $_ -ne $ScriptDir -and $_ -ne (Join-Path $env:ProgramFiles 'AMDXSS')
    }
    if ($filtered.Count -ne $paths.Count) {
        $newPath = $filtered -join ';'
        [Environment]::SetEnvironmentVariable('Path', $newPath, 'Machine')
        Write-Host '    Removed AMDXSS from System PATH.'
    } else {
        Write-Host '    AMDXSS not in System PATH (skipped).'
    }
} catch {
    Write-Warning "Could not update System PATH: $($_.Exception.Message)"
}

# Optional removal of installed files if installed in ProgramFiles
$defaultProgramFiles = Join-Path $env:ProgramFiles 'AMDXSS'
if ($RemoveFiles -or ($ScriptDir -ieq $defaultProgramFiles)) {
    if (Test-Path $defaultProgramFiles) {
        Write-Host "[+] Removing installed directory: $defaultProgramFiles"
        if ($KeepLogs) {
            Get-ChildItem -Path $defaultProgramFiles -Exclude 'logs' | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
            Write-Host '    Files removed, logs preserved.'
        } else {
            Remove-Item -Path $defaultProgramFiles -Recurse -Force -ErrorAction SilentlyContinue
            Write-Host '    Installation folder completely removed.'
        }
    }
}

Write-Host ""
Write-Host "[+] Uninstall complete."
