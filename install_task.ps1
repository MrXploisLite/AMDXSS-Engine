# AMD XSS Engine - scheduled task installer.
# Registers the daemon to start hidden at logon with highest privileges, so
# scheme switching and GPU control work without UAC prompts.
#
#   .\install_task.ps1 -Start      install + start now
#   .\install_task.ps1 -Status     show task state and running daemon
#   .\install_task.ps1 -Uninstall  remove task and stop the daemon
#
# Run from an elevated PowerShell. Optionally pass -Python <path to pythonw.exe>
# when Python is not on PATH.
#
#   .\install_task.ps1 [-Start]          install + start now
#   .\install_task.ps1 -Status           show task state and running daemon
#   .\uninstall.ps1                      remove task, schemes and daemon

param([switch]$Start, [switch]$Status, [string]$Python = '')

$TaskName = 'AMD XSS Engine'
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Resolve-Python {
    if ($Python) {
        if (-not (Test-Path $Python)) { throw "Python path not found: $Python" }
        return $Python
    }
    # 1. Check official Python Launcher for Windows (py.exe)
    $pyLauncher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $pyPath = & py.exe -3 -c "import sys; print(sys.executable)" 2>$null
        if ($pyPath -and (Test-Path $pyPath)) {
            $w = Join-Path (Split-Path $pyPath) 'pythonw.exe'
            if (Test-Path $w) { return $w }
            return $pyPath
        }
    }
    # 2. Check standard user local python installs (dynamic, no hardcoded username)
    $localPy = Join-Path $env:LOCALAPPDATA 'Programs\Python'
    if (Test-Path $localPy) {
        $cand = Get-ChildItem -Path $localPy -Filter 'pythonw.exe' -Recurse -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1
        if ($cand) { return $cand.FullName }
        $candExe = Get-ChildItem -Path $localPy -Filter 'python.exe' -Recurse -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1
        if ($candExe) { return $candExe.FullName }
    }
    # 3. Check system Program Files
    foreach ($pf in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($pf -and (Test-Path $pf)) {
            $cand = Get-ChildItem -Path $pf -Filter 'pythonw.exe' -Recurse -ErrorAction SilentlyContinue -Depth 3 |
                Select-Object -First 1
            if ($cand) { return $cand.FullName }
        }
    }
    throw 'Python not found on PATH or standard install locations. Install Python 3.10-3.12 or pass -Python <path>.'
}

if ($Status) {
    try {
        Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Select-Object TaskName, State | Format-List
    } catch {
        Write-Host 'Task not registered.'
    }
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -match 'XssEngine' } |
        Select-Object ProcessId, CreationDate | Format-Table -AutoSize
    exit 0
}

$py = Resolve-Python
if ([IO.Path]::GetFileName($py) -ieq 'python.exe') {
    Write-Warning "Resolved '$py' (not pythonw.exe): the daemon will show a console window."
}

$action = New-ScheduledTaskAction -Execute $py `
    -Argument ('"{0}" daemon' -f (Join-Path $Dir 'XssEngine.py')) -WorkingDirectory $Dir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -Hidden -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description 'AMD XSS Engine - adaptive power and performance daemon' -Force | Out-Null
"AMD XSS Engine: task registered (at logon, hidden, highest privileges)."

if ($Start) {
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 3
    $proc = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -match 'XssEngine' }
    if ($proc) { "Daemon running: PID $($proc.ProcessId)" } else { 'Daemon not detected; check logs\xss-engine-*.log' }
}
