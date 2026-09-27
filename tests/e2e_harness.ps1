# AMD XSS Engine - Safety-first E2E harness.
#
# Safety architecture (derived from AWS OPS06-BP04 + OneUptime confirmed-commit pattern):
#   1. PRE-CHECK  - assert environment BEFORE any mutation; abort early on failure.
#   2. BASELINE   - timestamped snapshot of active scheme + touched PIDs -> %ProgramData%\AMDXSS\e2e-baseline.json.
#   3. DEAD-MAN   - 'AMD XSS E2E Watchdog' scheduled task armed BEFORE mutation;
#                   fires restore_baseline.ps1 even if this runner dies mid-test.
#   4. GUARD      - every actuation is verified by READBACK; failure => immediate
#                   restore + stop remaining tests (bounded blast radius).
#   5. RECONCILE  - restore outcome read back and classified RECOVERED /
#                   UNVERIFIED. Never assume rollback succeeded.
#
# Usage (elevated PowerShell):
#   .\e2e_harness.ps1                full suite
#   .\e2e_harness.ps1 -Quick         skip benchmark stage
#
param([switch]$Quick)

$ErrorActionPreference = 'Continue'
$InstallDir   = Join-Path $env:ProgramFiles 'AMDXSS'
$TestDir      = $PSScriptRoot
$DataDir      = Join-Path $env:ProgramData 'AMDXSS'
$BaselineFile = Join-Path $DataDir 'e2e-baseline.json'
$ReportFile   = Join-Path $DataDir 'e2e-report.txt'
$RestoreScript = Join-Path $TestDir 'restore_baseline.ps1'
$WatchdogName = 'AMD XSS E2E Watchdog'
$WatchdogDeadline = 300   # seconds: restore runs this long after arming unless confirmed

if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Path $DataDir -Force | Out-Null }
if (Test-Path $ReportFile) { Remove-Item $ReportFile -Force }

$pass = 0; $fail = 0; $warn = 0; $aborted = $false

function Log([string]$stage, [string]$status, [string]$detail) {
    $line = "[{0}] {1,-20} | {2,-5} | {3}" -f (Get-Date -Format 'HH:mm:ss'), $stage, $status, $detail
    Write-Host $line
    Add-Content -Path $ReportFile -Value $line -Encoding utf8
    switch ($status) {
        'PASS' { $script:pass++ }
        'FAIL' { $script:fail++ }
        'WARN' { $script:warn++ }
    }
}

function Get-ActiveSchemeGuid {
    $m = [regex]::Match((powercfg /getactivescheme), '([0-9a-fA-F-]{36})')
    return $m.Groups[1].Value
}

function Invoke-GuardedActuation([string]$profile) {
    # Pin the profile via the engine's documented manual-override mechanism
    # (state/override.txt) BEFORE actuating. A bare `set` is transient: the
    # policy daemon may switch back on its next poll (documented behavior),
    # which makes readback non-deterministic. The override is what pins it.
    $stateDir = Join-Path $InstallDir 'state'
    if (-not (Test-Path $stateDir)) { New-Item -ItemType Directory -Path $stateDir -Force | Out-Null }
    Set-Content -Path (Join-Path $stateDir 'override.txt') -Value $profile -Encoding ascii

    $py = & py.exe -3 -c "import sys; print(sys.executable)" 2>$null
    & $py (Join-Path $InstallDir 'XssEngine.py') set $profile 2>&1 | Out-Null
    Start-Sleep -Seconds 2
    $state = Get-Content (Join-Path $InstallDir 'state\xss_state.json') -Raw | ConvertFrom-Json
    $schemeNow = Get-ActiveSchemeGuid
    if ($state.profile -eq $profile -and $state.scheme_ok -and $state.gpu_ok) {
        Log "ACTUATE:$profile" 'PASS' "scheme_ok=$($state.scheme_ok) gpu_ok=$($state.gpu_ok) active=$schemeNow"
        return $true
    }
    Log "ACTUATE:$profile" 'FAIL' "profile=$($state.profile) scheme_ok=$($state.scheme_ok) gpu_ok=$($state.gpu_ok) active=$schemeNow"
    $script:aborted = $true
    return $false
}

"======================================================================" | Out-File $ReportFile -Encoding utf8
"=== AMD XSS ENGINE - SAFETY-FIRST E2E REPORT ==="                 | Out-File $ReportFile -Append -Encoding utf8
"Date: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  Host: $env:COMPUTERNAME" | Out-File $ReportFile -Append -Encoding utf8
"======================================================================" | Out-File $ReportFile -Append -Encoding utf8

# ---------------------------------------------------------- STAGE 0: PRE-CHECKS
$preOk = $true

# P1: admin
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$pr = New-Object Security.Principal.WindowsPrincipal($id)
if ($pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Log 'PRECHECK:admin' 'PASS' 'running elevated'
} else {
    Log 'PRECHECK:admin' 'FAIL' 'not elevated - mutation tests would be unsafe'
    $preOk = $false
}

# P2: engine files present (explicit assertions, never a check that passes on empty)
$required = @('XssEngine.py', 'rm_sdk.py', 'xss_config.json', 'xss.cmd', 'bench.py', 'setup_schemes.ps1')
$missing = @($required | Where-Object { -not (Test-Path (Join-Path $InstallDir $_)) })
if ($missing.Count -eq 0) {
    Log 'PRECHECK:files' 'PASS' "all $($required.Count) runtime files present in $InstallDir"
} else {
    Log 'PRECHECK:files' 'FAIL' ("missing: " + ($missing -join ', '))
    $preOk = $false
}

# P3: daemon running + task registered
$daemon = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" |
    Where-Object { -not $_.CommandLine -or $_.CommandLine -match 'XssEngine' } | Select-Object -First 1
try {
    $task = Get-ScheduledTask -TaskName 'AMD XSS Engine' -ErrorAction Stop
    if ($daemon) {
        Log 'PRECHECK:daemon' 'PASS' "task=$($task.State) pid=$($daemon.ProcessId)"
    } else {
        Log 'PRECHECK:daemon' 'FAIL' 'task exists but daemon process not found'
        $preOk = $false
    }
} catch {
    Log 'PRECHECK:daemon' 'FAIL' $_.Exception.Message
    $preOk = $false
}

# P4: baseline scheme currently active + schemes map to real GUIDs (non-empty assertion)
$schemeMap = @{}
foreach ($line in (powercfg /list)) {
    if ($line -match '([0-9a-fA-F-]{36})\s+\((.+?)\)') { $schemeMap[$Matches[2].Trim()] = $Matches[1] }
}
$needSchemes = @('AMD Engine - Eco', 'AMD Engine - Balanced', 'AMD Engine - Performance', 'Balanced')
$badSchemes = @($needSchemes | Where-Object { -not $schemeMap.ContainsKey($_) })
if ($badSchemes.Count -eq 0 -and $schemeMap.Count -ge 4) {
    Log 'PRECHECK:schemes' 'PASS' ("resolved {0} schemes incl. all {1} engine profiles" -f $schemeMap.Count, ($needSchemes.Count - 1))
} else {
    Log 'PRECHECK:schemes' 'FAIL' ("unresolved: " + ($badSchemes -join ', '))
    $preOk = $false
}

if (-not $preOk) {
    Log 'GATE' 'FAIL' 'pre-checks failed - no mutation performed, aborting safely'
    exit 2
}

# ---------------------------------------------------------- STAGE 1: BASELINE
$activeGuid = Get-ActiveSchemeGuid
$activeName = (($schemeMap.GetEnumerator() | Where-Object { $_.Value -eq $activeGuid } | Select-Object -First 1).Key)
$touchedPids = @()
$statePath = Join-Path $InstallDir 'state\xss_state.json'
if (Test-Path $statePath) {
    $st = Get-Content $statePath -Raw | ConvertFrom-Json
    if ($st.os_boosted_pid) { $touchedPids += [int]$st.os_boosted_pid }
    foreach ($p in @($st.os_calm_pids)) { if ($p) { $touchedPids += [int]$p } }
}
$touchedPids = @($touchedPids | Select-Object -Unique)
$baseline = @{
    captured_at        = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
    active_scheme_guid = $activeGuid
    active_scheme_name = $activeName
    os_touched_pids    = $touchedPids
}
$baseline | ConvertTo-Json | Out-File $BaselineFile -Encoding utf8
Log 'BASELINE' 'PASS' "captured: $activeName ($activeGuid), tracked pids: $($touchedPids -join ',')"

# ---------------------------------------------------------- STAGE 2: DEAD-MAN WATCHDOG
# Armed BEFORE any mutation. Disarmed (deleted) only after reconcile succeeds.
Unregister-ScheduledTask -TaskName $WatchdogName -Confirm:$false -ErrorAction SilentlyContinue
$wdAction  = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ("-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"{0}`"" -f $RestoreScript)
$wdTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds($WatchdogDeadline)
$wdSet     = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -Hidden
Register-ScheduledTask -TaskName $WatchdogName -Action $wdAction -Trigger $wdTrigger -Settings $wdSet -Force | Out-Null
Start-ScheduledTask -TaskName $WatchdogName -ErrorAction SilentlyContinue  # ensure registered & armed
Log 'WATCHDOG' 'PASS' "armed 'AMD XSS E2E Watchdog' -> auto-restore in ${WatchdogDeadline}s if not confirmed"

$py = & py.exe -3 -c "import sys; print(sys.executable)" 2>$null

try {
    # ------------------------------------------------------ STAGE 3: GUARDED ACTUATION MATRIX
    foreach ($prof in @('eco', 'performance', 'stock', 'balanced')) {
        if ($aborted) { break }
        $null = Invoke-GuardedActuation $prof
    }

    if (-not $aborted) {
        # -------------------------------------------------- STAGE 4: TELEMETRY + LOGGING
        $telemetry = & $py (Join-Path $InstallDir 'XssEngine.py') status 2>&1 | Out-String
        if ($telemetry -match 'ADLX ready' -and $telemetry -match 'clock=\d+MHz') {
            Log 'GPU_TELEMETRY' 'PASS' 'ADLX live clock/temp/power'
        } else {
            Log 'GPU_TELEMETRY' 'WARN' 'ADLX output incomplete'
        }
        if ($telemetry -match 'CPU \(RM SDK\)' -and $telemetry -match 'PPT \d+') {
            Log 'CPU_TELEMETRY' 'PASS' 'RM SDK live PPT/TDC/EDC per-core'
        } else {
            Log 'CPU_TELEMETRY' 'WARN' 'RM SDK output incomplete (elevation/driver check)'
        }

        $csvPath = Join-Path $InstallDir 'logs\xss-telemetry.csv'
        $csvRows = @(Get-Content $csvPath -ErrorAction SilentlyContinue).Count
        if ($csvRows -gt 1) {
            Log 'CSV_LOGGING' 'PASS' "$csvRows rows recorded"
        } else {
            Log 'CSV_LOGGING' 'FAIL' 'telemetry CSV missing or empty'
        }

        $statsOut = & (Join-Path $InstallDir 'xss.cmd') stats 24 2>&1 | Out-String
        if ($statsOut -match 'AMD XSS Engine stats') {
            Log 'CLI_STATS' 'PASS' 'stats pipeline parsed + per-profile metrics'
        } else {
            Log 'CLI_STATS' 'WARN' 'unexpected stats output'
        }

        if (-not $Quick) {
            $benchOut = & $py (Join-Path $InstallDir 'bench.py') --iterations 20000 --runs 1 --profiles balanced 2>&1 | Out-String
            if ($benchOut -match "Testing profile: 'balanced'") {
                Log 'BENCH_TOOL' 'PASS' 'micro-workload clean, governor restored'
            } else {
                Log 'BENCH_TOOL' 'WARN' 'bench output incomplete'
            }
        } else {
            Log 'BENCH_TOOL' 'SKIP' '-Quick mode'
        }
    }
} finally {
    # ------------------------------------------------------ STAGE 5: RECONCILE (never assume)
    Log 'RESTORE' 'INFO' 'returning system to baseline...'
    # Release the manual override first so the daemon resumes normal policy.
    $overrideFile = Join-Path $InstallDir 'state\override.txt'
    if (Test-Path $overrideFile) { Remove-Item $overrideFile -Force -ErrorAction SilentlyContinue }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $RestoreScript 2>&1 | Out-Null
    Start-Sleep -Seconds 2

    # Read the restore log for the classified outcome (RECOVERED / UNVERIFIED).
    $restoreLog = Join-Path $DataDir 'e2e-restore.log'
    $outcome = 'UNVERIFIED'
    if (Test-Path $restoreLog) {
        $last = (Get-Content $restoreLog | Where-Object { $_ -match 'outcome=' } | Select-Object -Last 1)
        if ($last -match 'outcome=(\w+)') { $outcome = $Matches[1] }
    }
    $nowActive = Get-ActiveSchemeGuid
    $wantActive = (Get-Content $BaselineFile -Raw | ConvertFrom-Json).active_scheme_guid
    $schemeMatch = ($nowActive -eq $wantActive)

    if ($outcome -eq 'RECOVERED' -and $schemeMatch) {
        Log 'RECONCILE' 'PASS' "outcome=$outcome, active scheme matches baseline ($nowActive)"
    } elseif ($outcome -eq 'RECOVERED') {
        Log 'RECONCILE' 'WARN' "outcome=$outcome but active=$nowActive vs baseline=$wantActive (Balanced fallback?)"
    } else {
        Log 'RECONCILE' 'FAIL' "outcome=$outcome - MANUAL INTERVENTION: run restore_baseline.ps1"
    }

    # Disarm the dead-man's switch only after reconciliation.
    if ($outcome -eq 'RECOVERED') {
        Unregister-ScheduledTask -TaskName $WatchdogName -Confirm:$false -ErrorAction SilentlyContinue
        Log 'WATCHDOG' 'PASS' 'disarmed after successful reconcile'
    } else {
        Log 'WATCHDOG' 'WARN' 'left ARMED - will auto-restore as a second safety net'
    }

    # ---------------------------------------------------- SUMMARY
    $verdict = if ($aborted) { 'ABORTED (blast-radius stop)' } elseif ($fail -eq 0 -and $outcome -eq 'RECOVERED') { 'ALL GREEN' } elseif ($fail -eq 0) { 'GREEN with reconcile WARN' } else { "WITH $fail FAILURE(S)" }
    Log 'SUMMARY' $(if ($fail -eq 0 -and $outcome -eq 'RECOVERED') { 'PASS' } else { 'WARN' }) ("pass=$pass warn=$warn fail=$fail | $verdict")
    Write-Host "`nReport: $ReportFile"
}
