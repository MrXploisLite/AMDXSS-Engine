# AMD XSS Engine — Master Installer
# Installs the engine to a dedicated directory (default: C:\Program Files\AMDXSS),
# creates power schemes, registers the scheduled logon task, and adds to PATH.
#
# Usage (Run from an elevated PowerShell):
#   .\install.ps1                  # Standard dedicated install to Program Files
#   .\install.ps1 -InPlace         # Portable / Developer mode (runs from current folder)
#   .\install.ps1 -NoStart         # Install without starting daemon immediately
#
param(
    [string]$InstallDir = '',
    [switch]$InPlace,
    [switch]$NoPath,
    [switch]$NoStart,
    [string]$Python = ''
)

$ErrorActionPreference = 'Stop'

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$pr = New-Object Security.Principal.WindowsPrincipal($id)
if (-not $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'This script must run from an elevated PowerShell (Run as Administrator).'
}

$TaskName = 'AMD XSS Engine'
$SourceDir = $PSScriptRoot

if ($InPlace) {
    $TargetDir = $SourceDir
    Write-Host "[+] Mode: In-Place / Portable ($TargetDir)"
} else {
    if (-not $InstallDir) {
        $TargetDir = Join-Path $env:ProgramFiles 'AMDXSS'
    } else {
        $TargetDir = $InstallDir
    }
    Write-Host "[+] Target directory: $TargetDir"
}

# --- 1. Python Discovery ---
function Resolve-PythonEngine {
    param([string]$OverridePath)
    if ($OverridePath) {
        if (-not (Test-Path $OverridePath)) { throw "Specified Python path not found: $OverridePath" }
        return $OverridePath
    }
    # Check official Windows Python Launcher (py.exe)
    $pyLauncher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $pyPath = & py.exe -3 -c "import sys; print(sys.executable)" 2>$null
        if ($pyPath -and (Test-Path $pyPath)) {
            $w = Join-Path (Split-Path $pyPath) 'pythonw.exe'
            if (Test-Path $w) { return $w }
            return $pyPath
        }
    }
    # Check standard per-user Python installations
    $localPy = Join-Path $env:LOCALAPPDATA 'Programs\Python'
    if (Test-Path $localPy) {
        $cand = Get-ChildItem -Path $localPy -Filter 'pythonw.exe' -Recurse -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1
        if ($cand) { return $cand.FullName }
        $candExe = Get-ChildItem -Path $localPy -Filter 'python.exe' -Recurse -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1
        if ($candExe) { return $candExe.FullName }
    }
    # Check system Program Files
    foreach ($pf in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($pf -and (Test-Path $pf)) {
            $cand = Get-ChildItem -Path $pf -Filter 'pythonw.exe' -Recurse -ErrorAction SilentlyContinue -Depth 3 |
                Select-Object -First 1
            if ($cand) { return $cand.FullName }
        }
    }
    # Check PATH as fallback
    foreach ($name in @('pythonw.exe', 'python.exe')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    throw 'Python 3.10-3.12 not found. Install Python or pass -Python <path to pythonw.exe>.'
}

$py = Resolve-PythonEngine -OverridePath $Python
Write-Host "[+] Python resolved: $py"

# --- 2. Copy files if dedicated install ---
if (-not $InPlace) {
    if (-not (Test-Path $TargetDir)) {
        New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
    }
    $filesToCopy = @(
        'XssEngine.py',
        'rm_sdk.py',
        'xss_config.json',
        'xss.cmd',
        'xss-daemon.cmd',
        'setup_schemes.ps1',
        'uninstall.ps1'
    )
    foreach ($f in $filesToCopy) {
        $src = Join-Path $SourceDir $f
        if (Test-Path $src) {
            $dst = Join-Path $TargetDir $f
            if ($f -eq 'xss_config.json' -and (Test-Path $dst)) {
                Write-Host "    [=] Keeping existing config: $dst"
            } else {
                Copy-Item -Path $src -Destination $dst -Force
                Write-Host "    [+] Copied: $f"
            }
        }
    }
}

# --- 3. Setup Power Schemes ---
Write-Host '[+] Setting up power schemes...'
$setupScript = Join-Path $TargetDir 'setup_schemes.ps1'
if (-not (Test-Path $setupScript)) {
    $setupScript = Join-Path $SourceDir 'setup_schemes.ps1'
}
& $setupScript -Activate

# --- 4. Register Scheduled Task ---
Write-Host "[+] Registering scheduled task '$TaskName'..."
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$engineScript = Join-Path $TargetDir 'XssEngine.py'
$action = New-ScheduledTaskAction -Execute $py `
    -Argument ("`"{0}`" daemon" -f $engineScript) -WorkingDirectory $TargetDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -Hidden -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description 'AMD XSS Engine - adaptive power and performance daemon' -Force | Out-Null
Write-Host '    Task registered (at logon, hidden, highest privileges).'

# --- 5. Add to PATH (Machine level) ---
if (-not $NoPath) {
    try {
        $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
        $paths = $machinePath -split ';' | Where-Object { $_ -ne '' }
        if ($paths -notcontains $TargetDir) {
            $newPath = ($paths + $TargetDir) -join ';'
            [Environment]::SetEnvironmentVariable('Path', $newPath, 'Machine')
            Write-Host "[+] Added to System PATH: $TargetDir"
            $env:Path = "$env:Path;$TargetDir"
        } else {
            Write-Host "[=] Already in System PATH: $TargetDir"
        }
    } catch {
        Write-Warning "Could not update System PATH: $($_.Exception.Message)"
    }
}

# --- 6. Start Daemon ---
if (-not $NoStart) {
    Write-Host '[+] Starting daemon...'
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 3
    $proc = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'XssEngine' }
    if ($proc) {
        Write-Host "    [OK] Daemon running: PID $($proc.ProcessId)"
    } else {
        Write-Warning "Daemon not detected in process list. Check logs in: $(Join-Path $TargetDir 'logs')"
    }
}

Write-Host "`n[+] Installation complete!"
Write-Host "You can now run 'xss status' or 'xss stats 24' from any shell."
