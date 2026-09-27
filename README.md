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

## Requirements

- Windows 10 or 11
- Python 3.10+ (64-bit) on PATH
- Administrator rights for `setup_schemes.ps1` and `install_task.ps1`
- Optional: `pip install -r requirements.txt` for the ADLX binding (GPU telemetry
  and frame-rate control). Without it, the engine still switches power schemes
  and runs the CPU-side policy normally.
- Optional: [AMD Ryzen Master Monitoring SDK](https://www.amd.com/en/developer/ryzen-master-monitoring-sdk.html)
  for CPU power telemetry (`rm_sdk.py`). The engine finds it via the
  `HKLM\Software\AMD\RyzenMasterMonitoringSDK` registry key or the default
  install path; its `AMDRyzenMasterDriverV32` service must be running.

## Setup

```powershell
# 1. Optional: GPU telemetry/control support
python -m pip install -r requirements.txt

# 2. Create the power schemes used by the profiles (elevated)
powershell -ExecutionPolicy Bypass -File .\setup_schemes.ps1 -Activate

# 3. Register the logon task and start the daemon (elevated)
powershell -ExecutionPolicy Bypass -File .\install_task.ps1 -Start
```

## Usage

```powershell
.\xss.cmd status              # active scheme, foreground app, GPU metrics, last state
.\xss.cmd set eco             # switch to eco once (the daemon may switch again on next poll)
echo balanced > state\override.txt  # pin a profile until the file changes (delete the file to release)
.\xss.cmd telemetry 30        # sample metrics for 30 s into logs/xss-telemetry.csv
.\xss.cmd stats 24           # switch rate + profile share + power/thermal averages
.\xss.cmd probe               # what the ADLX driver exposes on this GPU
.\xss-daemon.cmd              # run the policy loop in the foreground (debug)

# management
powershell -ExecutionPolicy Bypass -File .\install_task.ps1 -Status
powershell -ExecutionPolicy Bypass -File .\install_task.ps1 -Uninstall
```

Profiles:

| Profile | Scheme | Chill |
|---|---|---|
| `eco` | AMD Engine - Eco (min 5%) | on, 30-60 fps |
| `balanced` | AMD Engine - Balanced (min 5%) | on, 60-120 fps |
| `performance` | AMD Engine - Performance (min 10%) | off |
| `stock` | Windows Balanced | default |

Tuning values live in `xss_config.json`; edit freely, the daemon re-reads it on
restart. Automatic profile switches are rate-limited
(`min_switch_interval_seconds`, default 30 s; manual override bypasses it), and
`stock` also undoes every OS focus tweak (priorities back to normal, efficiency
mode off). Long-term plan: `docs/ROADMAP-LONG.md`.

## Files

```
XssEngine.py        daemon, CLI, policy, ADLX wrapper
rm_sdk.py           optional AMD Ryzen Master Monitoring SDK bridge (CPU telemetry)
xss_config.json     profiles, rules, poll interval
setup_schemes.ps1   creates the power schemes (idempotent)
install_task.ps1    logon task install/status/uninstall
xss.cmd             console launcher
xss-daemon.cmd      hidden daemon launcher
logs/               engine logs + telemetry CSV (not tracked)
state/              runtime state + override file (not tracked)
```

## Notes

- The scheduled task runs with highest privileges because switching power
  schemes requires admin rights. Review the scripts before installing; the
  code is short and does nothing else.
- ADLX teardown: the `amd-adlx` binding can segfault during interpreter
  shutdown, so the engine keeps its ADLX references alive and exits via
  `os._exit()`. This is deliberate; see `stop()` in `XssEngine.py`.
- Measured on the test machine (Ryzen 7 5700G, Vega iGPU): per-profile
  benchmarks stayed within +/-1% of each other, and GPU idle power samples
  read 9-13 W at the 400 MHz idle clock. Raw samples land in
  `logs/xss-telemetry.csv` (not tracked).

## License

MIT - see `LICENSE`.
