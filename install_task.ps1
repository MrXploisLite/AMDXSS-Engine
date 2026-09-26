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
param(
    [switch]$Uninstall,
    [switch]$Start,
    [switch]$Status,
    [string]$Python = ''
)

$TaskName = 'AMD XSS Engine'
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Resolve-Python {
    if ($Python) {
        if (-not (Test-Path $Python)) { throw "Python path not found: $Python" }
        return $Python
    }
    foreach ($name in @('pythonw.exe', 'python.exe')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    throw 'Python not found on PATH. Install Python 3.10+ or pass -Python <path to pythonw.exe>.'
}

if ($Status) {
    try {
        Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State | Format-List
    } catch {
        'Task not registered.'
    }
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -match 'XssEngine' } |
        Select-Object ProcessId, CreationDate | Format-Table -AutoSize
    exit 0
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -match 'XssEngine' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    'AMD XSS Engine: task and daemon removed.'
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
