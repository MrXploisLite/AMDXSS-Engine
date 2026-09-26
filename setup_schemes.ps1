# AMD XSS Engine - create the power schemes referenced by xss_config.json:
#   AMD Engine - Eco / Balanced / Performance
#
# Idempotent: schemes that already exist are kept and their values verified,
# not recreated. Run from an elevated PowerShell:
#
#   powershell -ExecutionPolicy Bypass -File .\setup_schemes.ps1 [-Activate]
param([switch]$Activate)

$ErrorActionPreference = 'Stop'

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$pr = New-Object Security.Principal.WindowsPrincipal($id)
if (-not $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'This script must run from an elevated PowerShell.'
}

# PROCTHROTTLEMIN / PROCTHROTTLEMAX are percent; PERFBOOSTMODE: 1=Enabled 2=Aggressive
# 3=Efficient Enabled; SYSCOOLPOL: 0=passive 1=active.
$profiles = [ordered]@{
    'AMD Engine - Eco'         = @{ MIN = 5;  MAX = 100; BOOST = 3; COOL = 1 }
    'AMD Engine - Balanced'    = @{ MIN = 5;  MAX = 100; BOOST = 1; COOL = 1 }
    'AMD Engine - Performance' = @{ MIN = 10; MAX = 100; BOOST = 2; COOL = 1 }
}

function Get-SchemeMap {
    $map = @{}
    foreach ($line in (powercfg /list)) {
        if ($line -match '([0-9a-fA-F-]{36})\s+\((.+?)\)') { $map[$Matches[2].Trim()] = $Matches[1] }
    }
    return $map
}

function Get-Setting([string]$guid, [string]$alias) {
    $blocks = ((powercfg /q $guid SUB_PROCESSOR 2>&1) -join "`n") -split 'Power Setting GUID:'
    foreach ($b in $blocks) {
        if ($b -match ('GUID Alias:\s*' + [regex]::Escape($alias) + '\s*(\r?\n|$)')) {
            $m = [regex]::Match($b, 'Current AC Power Setting Index:\s*(0x[0-9a-fA-F]+|None)')
            if (-not $m.Success) { return 'N/A' }
            if ($m.Groups[1].Value -eq 'None') { return $null }
            return [Convert]::ToInt32($m.Groups[1].Value, 16)
        }
    }
    return 'N/A'
}

$fail = 0
$map = Get-SchemeMap
foreach ($name in $profiles.Keys) {
    $want = $profiles[$name]
    if ($map.ContainsKey($name)) {
        $guid = $map[$name]
        Write-Host "[=] $name already exists ($guid) - verifying values"
    } else {
        $out = (powercfg /duplicatescheme SCHEME_BALANCED) -join ' '
        if ($out -notmatch '([0-9a-fA-F-]{36})') { throw "duplicatescheme failed: $out" }
        $guid = $Matches[1]
        powercfg /changename $guid $name 'AMD XSS Engine profile' | Out-Null
        Write-Host "[+] $name created ($guid)"
    }

    foreach ($pair in @(@('PROCTHROTTLEMIN', $want.MIN), @('PROCTHROTTLEMAX', $want.MAX),
                        @('PERFBOOSTMODE', $want.BOOST), @('SYSCOOLPOL', $want.COOL))) {
        $alias = $pair[0]
        $value = [int]$pair[1]
        # PERFBOOSTMODE and SYSCOOLPOL are hidden settings; setacvalueindex fails
        # silently on them until they are unhidden. Do it for all four - harmless.
        powercfg /attributes SUB_PROCESSOR $alias -ATTRIB_HIDE 2>$null | Out-Null
        powercfg /setacvalueindex $guid SUB_PROCESSOR $alias $value | Out-Null
        powercfg /setdcvalueindex $guid SUB_PROCESSOR $alias $value | Out-Null
        $rb = Get-Setting $guid $alias
        if ("$rb" -ne "$value") {
            powercfg /attributes SUB_PROCESSOR $alias -ATTRIB_HIDE 2>$null | Out-Null
            powercfg /setacvalueindex $guid SUB_PROCESSOR $alias $value | Out-Null
            $rb = Get-Setting $guid $alias
        }
        if ("$rb" -eq "$value") {
            Write-Host ("    {0,-16} = {1,-4} OK" -f $alias, $value)
        } else {
            Write-Host ("    {0,-16} = {1,-4} FAILED (readback: {2})" -f $alias, $value, $rb)
            $fail++
        }
    }
}

if ($Activate) {
    $map = Get-SchemeMap
    powercfg /setactive $map['AMD Engine - Balanced'] | Out-Null
    Write-Host "Activated 'AMD Engine - Balanced'."
}

if ($fail -gt 0) {
    Write-Host "$fail setting(s) failed to apply."
    exit 1
}
Write-Host 'Done.'
