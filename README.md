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

## Empirical Benchmarks: Stock Windows vs AMD XSS Engine

All benchmarks run on **AMD Ryzen 7 5700G** (8C/16T Cezanne APU, Radeon Vega 8 iGPU, 16 GB DDR4) under Windows 11.  
Telemetry sampled at 1.0 s via **AMD Ryzen Master Monitoring SDK** (SMU Package Temp, Socket PPT, Effective Clocks) and **AMD ADLX API** (GPU Clock/Power/Temp).

### A. Real-World Browser — 10 Live Websites

10 tabs opened simultaneously in an isolated Edge instance: **YouTube, Reddit, Twitter/X, Instagram, Wikipedia, GitHub, Stack Overflow, Amazon, CNN, Twitch**. No synthetic pages — every tab hits the live internet.

| Metric | Stock Windows | AMDXSS Balanced | AMDXSS Eco | Eco vs Stock |
| :--- | :--- | :--- | :--- | :--- |
| **Mean PPT** | 10.86 W | 9.93 W | **10.00 W** | **−8.0%** |
| **Total Energy (30s)** | 326 J | 298 J | **300 J** | **−8.0%** |
| **Avg CPU Temp** | 44.4 °C | 43.2 °C | **43.8 °C** | **−0.6 °C** |
| **GPU Power** | 11.0 W | 9.2 W | **10.1 W** | **−0.9 W** |

> Note: AMDXSS profiles show slightly higher momentary peak PPT (C-state deep idle → sharper boost transients) but sustain lower mean power and temperature across the session.

### B. Roblox 3D Game — Brookhaven RP (344K Live Players)

Multi-cycle A/B benchmark: **2 cycles × 60s** per config (load/play/close), grafis **Level 10 Rata Kanan** (D3D11, 4x MSAA) at 1366x768 @ 60 Hz. Map liveness verified via Roblox Public API before launch.

| Config | Avg PPT | Avg Temp | Peak Temp | GPU Temp | GPU Clock | RAM |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Stock Windows** | 46.96 W | 62.7 °C | 68.9 °C | 55.8 °C | 1783 MHz | 2573 MB |
| **AMDXSS Balanced** | 35.84 W | 62.5 °C | 70.1 °C | 56.3 °C | 1431 MHz | 2626 MB |
| **AMDXSS Eco** | **26.20 W** | **58.0 °C** | **65.9 °C** | **52.6 °C** | 916 MHz | 2570 MB |

**Eco vs Stock: −44.2% power, −4.7 °C CPU temp, −3.2 °C GPU temp.** Memory stable across all cycles (zero leaks). Audio pipeline (`audiodg.exe`) monitored as first-class telemetry.

### Safety & Reproducibility

- Isolated `--user-data-dir` browser profile — personal data untouched
- Windowed + muted game execution — call-safe, no audio interference
- Map dead/alive pre-check before every game launch
- Dead-man watchdog + automated rollback + baseline restore
- Full reports: [`docs/BROWSER-BENCHMARK.md`](docs/BROWSER-BENCHMARK.md) · [`docs/GAME-BENCHMARK.md`](docs/GAME-BENCHMARK.md)

Run any benchmark with one command:
```powershell
xss bench-browser                          # real-website A/B (3 configs)
xss bench-game 4924922222 60 2            # Brookhaven, 60s x 2 cycles
xss bench-game 920587237 120 3            # Adopt Me, 120s x 3 cycles
```
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
bench_game.py       automated Roblox 3D game benchmark (multi-cycle, map liveness)
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
