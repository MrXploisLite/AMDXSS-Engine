# AMD XSS Engine - E2E baseline recovery (dead-man's switch + manual recovery).
#
# Restores the pre-test power scheme and clears engine OS tweaks (priority /
# EcoQoS). Idempotent and safe to run at any time, from any state.
#
# This script is registered as the scheduled task 'AMD XSS E2E Watchdog' before
# any system mutation begins, so a crash in the test runner (or in the agent
# driving it) still returns the machine to its baseline.
#
#   .\Restore-XssBaseline.ps1                 restore from $env:ProgramData\AMDXSS\e2e-baseline.json
#   .\Restore-XssBaseline.ps1 -ClearOnly      only clear OS tweaks, leave scheme alone
#
param(
    [string]$BaselineFile = (Join-Path $env:ProgramData 'AMDXSS\e2e-baseline.json'),
    [switch]$ClearOnly
)

$ErrorActionPreference = 'Continue'
$LogDir = Join-Path $env:ProgramData 'AMDXSS'
$LogFile = Join-Path $LogDir 'e2e-restore.log'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

function W([string]$msg, [string]$level = 'INFO') {
    $line = "[{0}][{1}] {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $level, $msg
    Write-Host $line
    try { Add-Content -Path $LogFile -Value $line -Encoding utf8 } catch {}
}

$outcome = 'UNVERIFIED'

try {
    W '=== E2E baseline restore starting ==='

    # 1. Clear engine OS tweaks: any process still boosted goes back to normal.
    #    We only touch processes the engine recorded, never arbitrary ones.
    $cleared = 0
    if (Test-Path $BaselineFile) {
        try {
            $b = Get-Content $BaselineFile -Raw | ConvertFrom-Json
            foreach ($pidToFix in @($b.os_touched_pids)) {
                if ($pidToFix -and (Get-Process -Id $pidToFix -ErrorAction SilentlyContinue)) {
                    try {
                        $p = Get-Process -Id $pidToFix -ErrorAction Stop
                        $p.PriorityClass = 'Normal'
                        $cleared++
                        W "  pid $pidToFix priority -> Normal"
                    } catch {
                        W "  pid $pidToFix priority restore skipped: $($_.Exception.Message)"
                    }
                }
            }
        } catch {
            W "  baseline parse failed: $($_.Exception.Message)"
        }
    } else {
        W "  no baseline file at $BaselineFile (nothing recorded to undo)"
    }
    W "  cleared $cleared tracked process priority tweak(s)"

    if ($ClearOnly) { $outcome = 'RECOVERED'; return }

    # 2. Restore the baseline power scheme (if we know one).
    if (Test-Path $BaselineFile) {
        $b = Get-Content $BaselineFile -Raw | ConvertFrom-Json
        $wantGuid = $b.active_scheme_guid
        $wantName = $b.active_scheme_name
        if ($wantGuid) {
            # The scheme must still exist before we activate it.
            $list = powercfg /list
            if ($list -match [regex]::Escape($wantGuid)) {
                powercfg /setactive $wantGuid 2>&1 | Out-Null
                W "  setactive -> $wantName ($wantGuid)"
            } else {
                # Baseline scheme is gone (deleted by a test). Fall back to Windows
                # Balanced rather than leaving whatever the test left behind.
                powercfg /setactive SCHEME_BALANCED 2>&1 | Out-Null
                W "  baseline scheme missing, fell back to SCHEME_BALANCED" 'WARN'
            }
        }
    } else {
        powercfg /setactive SCHEME_BALANCED 2>&1 | Out-Null
        W '  no baseline recorded, set SCHEME_BALANCED'
    }

    # 3. RECONCILE - do not assume the write worked. Read the actual state back.
    Start-Sleep -Seconds 1
    $actual = (powercfg /getactivescheme)
    $want = if (Test-Path $BaselineFile) { (Get-Content $BaselineFile -Raw | ConvertFrom-Json).active_scheme_guid } else { $null }
    if ($want -and ($actual -match [regex]::Escape($want))) {
        $outcome = 'RECOVERED'
        W "  reconcile OK: $actual"
    } elseif ($actual -match '381b4222-f694-41f0-9685-ff5bb260df2e|SCHEME_BALANCED') {
        $outcome = 'RECOVERED'
        W "  reconcile OK (Balanced fallback): $actual"
    } else {
        $outcome = 'UNVERIFIED'
        W "  reconcile FAILED: active scheme is '$actual' and does not match the baseline" 'WARN'
    }
} catch {
    $outcome = 'UNVERIFIED'
    W "  restore errored: $($_.Exception.Message)" 'WARN'
} finally {
    W "=== restore finished: outcome=$outcome ==="
}
