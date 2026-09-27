# AMD XSS Engine

Adaptive power and performance daemon for AMD systems on Windows.

It watches what the machine is doing (foreground app, user idle time), reads GPU
telemetry, and switches between power profiles accordingly: CPU/SoC power schemes
through Windows power management, GPU frame-rate handling through AMD's ADLX API.
Runs hidden at logon. Nothing it does requires a reboot, and everything is
reversible.

Built and measured on a Ryzen 7 5700G (Cezanne APU, Radeon Vega iGPU) under
Windows 11. The ADLX path works on any system with the AMD graphics driver; the
power-scheme path works on any Windows machine.

## How it works

```
foreground app + idle time ─┐
manual override file ───────┼──► policy ──► power scheme switch (powercfg)
GPU telemetry (ADLX) ───────┘              └► Radeon Chill / FRTC (ADLX)
```

- **Telemetry**: GPU clock, usage, temperature, power, VRAM, FPS via `amd-adlx`.
  Sampled once per poll (default 5 s) and appended to `logs/xss-telemetry.csv`.
- **Policy** (`xss_config.json`): process-name rules pick a profile — a game in
  the foreground gets `performance`, a call app gets `eco`, everything else stays
  on your `default_profile`. An optional idle rule falls back to `eco`. Writing a
  profile name to `state/override.txt` pins that profile until the file changes.
- **Actuation**: the profile's scheme is activated with `powercfg /setactive`,
  and Radeon Chill / Frame Rate Target Control are set for the GPU. Every write
  is read back and logged; a failed readback is reported in the log.
- **OS focus layer** (`os_tuning` in config): the focused process is boosted to
  above-normal priority (debounced — only on foreground change, previous app
  restored to normal), with optional efficiency-mode calming for the loudest
  background processes by measured CPU share. Generic: no per-app lists.
- **Driver-global GPU posture** (per profile): Radeon Image Sharpening,
  Anti-Lag and Enhanced Sync toggled via ADLX with readback. RSR is not
  exposed on Vega-class iGPUs, so the engine does not pretend to manage it.
- **CPU power telemetry** (optional, via the AMD Ryzen Master Monitoring SDK):
  PPT/TDC/EDC with limits, VDDCR rails, voltages, temperature, Fmax/FCLK and
  per-core frequency/C0-residency/temperature — sampled at most once per
  second per AMD's guidance, appended to the telemetry CSV.

## Empirical Benchmark: Stock Windows vs AMD XSS Engine

Measured directly on target hardware: **AMD Ryzen 7 5700G** (8 Cores / 16 Threads, Cezanne APU, Radeon Vega iGPU, 16GB DDR4) under Windows 11 Home (Build 26200).  
Telemetry sampled at 1.0 s intervals directly from AMD hardware registers via the **AMD Ryzen Master Monitoring SDK** (SMU Package Temp, Socket PPT Watts, Effective Clocks) and **AMD ADLX API** (GPU Clock/Power).

### Workload A: System Idle (Quiescent Background State)

| Metric | Stock Windows Balanced | AMDXSS Balanced | AMDXSS Eco (Power Saver) | Delta / Benefit |
| :--- | :--- | :--- | :--- | :--- |
| **Mean Socket Power (PPT)** | 7.79 W | **7.75 W** | 7.84 W | Stable baseline idle floor |
| **Peak Power Spike** | 14.18 W | **9.54 W** | 10.80 W | **-4.64 W (-32.7% peak spike suppression)** |
| **Average CPU Temperature** | 45.1 °C | 44.4 °C | **44.3 °C** | **-0.8 °C cooler** |
| **Effective Core Clock** | 95 MHz | 99 MHz | 99 MHz | Rapid C-state residency entry |
| **GPU Clock / Power** | 960 MHz / 7.3 W | 560 MHz / 7.5 W | 560 MHz / 7.5 W | Idle clock clamped via ADLX Chill |

### Workload B: 10-Tab Web Browsing (Active Multitasking)
Simulates intensive daily browsing using 10 concurrent tabs in an isolated Microsoft Edge instance (rich encyclopedic articles, syntax-highlighted code viewer, SVG live charts, 2D Canvas particle simulation, e-commerce catalog, Todo SPA, 500-row scrollable table, Web Worker background calculator, and news stream).

| Metric | Stock Windows Balanced | AMDXSS Balanced (Responsiveness) | AMDXSS Eco (Max Efficiency) | Delta vs Stock |
| :--- | :--- | :--- | :--- | :--- |
| **Mean Socket Power (PPT)** | 7.80 W | 7.83 W | **7.75 W** | **-0.05 W (-0.5%)** |
| **Total Energy Consumed** | 233.9 Joules | 234.9 Joules | **232.7 Joules** | **-1.28 Joules saved** |
| **Peak Socket Power** | 11.00 W | 11.52 W | **10.68 W** | **-0.32 W (-2.9%)** |
| **Average CPU Temperature** | 44.7 °C | 44.3 °C | **44.1 °C** | **-0.6 °C cooler** |
| **Peak CPU Temperature** | 48.9 °C | 48.7 °C | **48.3 °C** | **-0.6 °C cooler** |
| **Effective Core Clock** | 103 MHz | 102 MHz | **97 MHz** | **-6 MHz (cleaner C-states)** |
| **GPU Power** | 7.4 W | 7.3 W | **7.3 W** | **-0.1 W** |

### Key Architectural Takeaways

1. **Suppression of Micro-burst Spikes:** Stock Windows Balanced runs `PERFBOOSTMODE=2` (Aggressive), triggering momentary voltage and clock spikes up to 14.18 W on minor background JavaScript wakeups. AMDXSS caps transient overshoot at 9.54 W without perceived latency.
2. **Foreground Priority vs Background Calming:** AMDXSS dynamically elevates the foreground interactive window to Above-Normal thread quantum while background tabs remain throttled, preventing background jitter from interrupting user tasks.
3. **Reproducibility & Safety:** Zero modifications to personal browser profiles (run in isolated sandbox profile), zero driver restarts, zero reboots, and 100% reversible via baseline snapshot and dead-man's watchdog.
4. **Full Technical Data & Sources:** Complete methodology, raw telemetry CSV, and grounded citations available in [`docs/BROWSER-BENCHMARK.md`](docs/BROWSER-BENCHMARK.md).

## Requirements

- Windows 10 or 11
- Python 3.10+ (64-bit) on PATH
- Administrator rights for `New-XssSchemes.ps1` and `Install-XssEngine.ps1`
- Optional: `pip install -r requirements.txt` for the ADLX binding (GPU telemetry
  and frame-rate control). Without it, the engine still switches power schemes
  and runs the CPU-side policy normally.
- Optional: [AMD Ryzen Master Monitoring SDK](https://www.amd.com/en/developer/ryzen-master-monitoring-sdk.html)
  for CPU power telemetry (`rm_sdk.py`). The engine finds it via the
  `HKLM\Software\AMD\RyzenMasterMonitoringSDK` registry key or the default
  install path; its `AMDRyzenMasterDriverV32` service must be running.

## Usage

```powershell
.\xss.cmd status              # active scheme, foreground app, GPU metrics, last state
.\xss.cmd set eco             # switch to eco once (the daemon may switch again on next poll)
echo balanced > state\override.txt  # pin a profile until the file changes (delete the file to release)
.\xss.cmd telemetry 30        # sample metrics for 30 s into logs/xss-telemetry.csv
.\xss.cmd stats 24            # switch rate + profile share + power/thermal averages
.\xss.cmd probe               # what the ADLX driver exposes on this GPU
.\xss-daemon.cmd              # run the policy loop in the foreground (debug)

# management (elevated)
powershell -ExecutionPolicy Bypass -File .\Manage-XssTask.ps1 -Status
powershell -ExecutionPolicy Bypass -File .\Uninstall-XssEngine.ps1
```

Profiles:

| Profile | Scheme | Chill |
|---|---|---|
| `eco` | AMD Engine - Eco (min 5%) | on, 30-60 fps |
| `balanced` | AMD Engine - Balanced (min 5%) | on, 60-120 fps |
| `performance` | AMD Engine - Performance (min 10%) | off |
| `stock` | Windows Balanced | default |

## What the engine does (in 30s)

The daemon monitors your active window. When it detects a call or game, it
switches to an eco or performance profile that changes power plans and driver
settings. Everything is reversible via `stock`, manual override, or uninstall.

Automatic switches are rate-limited by default (`min_switch_interval_seconds`
= 30 s); override file can pin a profile until deleted. OS tweaks (boosted
foreground priority, optional background calming) apply on every poll and
undo cleanly on shutdown or when you switch back.

---

## Quick start (CLI, no manual steps)

Run from **PowerShell elevated (Run as Administrator)**:

```powershell
# 0) Optional: GPU telemetry/control support
python -m pip install -r requirements.txt

# 1) One-command install: copies to C:\Program Files\AMDXSS, sets up power schemes,
#    registers the logon task with highest privileges, adds to PATH, and starts the daemon:
.\Install-XssEngine.ps1

# Or portable / developer mode (runs in-place from the repo directory, no files copied):
.\Install-XssEngine.ps1 -InPlace

# 2) Verify it's running and check stats (works from any shell since it's on PATH):
xss status
xss stats 24                  # switch rate + profile share + per-profile power (v0.6)

# 3) Clean uninstall whenever needed:
.\Uninstall-XssEngine.ps1 -RemoveFiles  # stops daemon, deletes task, restores Balanced, deletes custom schemes
```

The engine will automatically switch profiles based on your active application
(call, game, idle, default). See `xss_config.json` for the rule table.

## Files

```
xss_engine.py       daemon, CLI, policy, ADLX wrapper (PEP 8 module name)
rm_sdk.py           AMD Ryzen Master Monitoring SDK bridge (CPU telemetry)
bench.py            performance & no-regression benchmark runner (v0.8)
bench_browser.py    isolated 10-tab browser & idle A/B power benchmark
xss_config.json     profiles, rules, poll interval
xss.cmd             console launcher
xss-daemon.cmd      hidden daemon launcher
Install-XssEngine.ps1     one-command master installer (dedicated or in-place)
Uninstall-XssEngine.ps1   clean uninstaller (task, schemes, PATH, files)
New-XssSchemes.ps1        creates and verifies the power schemes (idempotent)
Manage-XssTask.ps1        logon task install / status
tests/              E2E harness + baseline recovery (Invoke-E2eTests.ps1)
docs/               roadmap, benchmarks, audit archive
logs/               engine logs + telemetry CSV (not tracked)
state/              runtime state + override file (not tracked)
```

## Notes

- The scheduled task runs with highest privileges because switching power
  schemes requires admin rights. Review the scripts before installing; the
  code is short and does nothing else.
- ADLX teardown: the `amd-adlx` binding can segfault during interpreter
  shutdown, so the engine keeps its ADLX references alive and exits via
  `os._exit()`. This is deliberate; see `stop()` in `xss_engine.py`.
- Measured on the test machine (Ryzen 7 5700G, Vega iGPU): per-profile
  benchmarks stayed within +/-1% of each other, and GPU idle power samples
  read 9-13 W at the 400 MHz idle clock. Raw samples land in
  `logs/xss-telemetry.csv` (not tracked).

## License

MIT - see `LICENSE`.
