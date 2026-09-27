# AMDXSS Engine — Roadmap

Goal: adaptive power and performance for everyday heavy workloads on AMD APUs
(Ryzen 7 5700G + Vega iGPU reference machine) — browsers, editors, renderers,
games — with three promises in order: **optimized, stable, power-efficient**.
Generic layers first; per-game or per-app special cases only when the generic
layer provably cannot cover them.

## Where we are: v0.1 + OS layer (current)

- [x] Power-scheme switching (Eco / Balanced / Performance) via powercfg,
      idempotent setup, readback-verified.
- [x] GPU telemetry + Radeon Chill / FRTC via ADLX (`amd-adlx`).
- [x] Policy daemon: foreground-app rules, idle fallback, manual override,
      hidden logon task, telemetry CSV.
- [x] OS focus layer (new): foreground process boosted to above-normal
      priority with debounce + restore; optional background calming via
      efficiency mode (EcoQoS) for the loudest background PIDs by measured
      CPU share. Off by default for calming (`calm_background: false`) until
      measured safe.
- [x] Repo public: AMDXSS-Engine (MIT), SECURITY.md, audit archive in docs/.

## v0.2 — Measure the OS layer (next, small)

1. Telemetry columns: add `profile`, `fg_pid_boosted`, `scheme_ok/gpu_ok/os_ok`
   to the CSV so every poll is auditable.
2. A/B test: 1-hour normal use with `os_tuning.enabled` true vs false.
   Compare: foreground frame pacing (PresentMon, on demand), idle power
   samples, switch counts in the log. Ship the numbers in docs/.
3. Decide `calm_background` default from data, not guesswork. If any
   background app (updaters, sync clients) misbehaves under EcoQoS, add an
   exclusion list — still generic (by behavior class), never per-game.
4. Harden `top_cpu_pids`: cache tasklist output, skip system PIDs, bound
   runtime under 3 s.

## v0.3 — Driver-global GPU layer (medium) [x] DONE 2026-09-27

Nothing per-game; everything at driver scope so all 3D apps benefit:

- [x] Read current driver-global state via ADLX + registry: RSR, Radeon Image
  Sharpening, Chill global, Enhanced Sync, power-gating flags.
  Result: Sharpening/AntiLag/EnhancedSync supported; **RSR NOT supported on
  Vega iGPU** (probed, excluded by design); power-gating flags parked (need
  driver reload/reboot -> v2.0).
- [x] Profiles map to global GPU posture too (eco = all off, balanced =
  sharpening on, performance = sharpening + antilag on) — readback-verified.
- [x] RSR requirement documented in README (exclusive fullscreen +
  below-native resolution) — N/A on this iGPU, noted honestly.

## v0.4 — CPU telemetry (medium, unlocks honesty) [x] DONE 2026-09-27

AMD Ryzen Master Monitoring SDK (public, read-only): PPT/TDC/EDC,
temperature, voltage, effective frequency per core at 1 Hz. Add columns to
CSV + `status`. This is what proves the eco claim (watts, not vibes) and
detects throttling under sustained load (browser compiles, renders, games).

- [x] SDK extracted from the official package (the MSI custom action rolls
  back with error 1603 when a reboot is pending or an older
  `AMDRyzenMasterDriverV20` service exists; the files themselves are fine).
- [x] `rm_sdk.py` bridges Platform.dll through the documented C++ vtable
  (ICPUEx). One layout ambiguity (MSVC destructor slots) resolved at runtime
  with a safe calibration probe.
- [x] Manual component setup: copy to `C:\Program Files\AMD\
  RyzenMasterMonitoringSDK`, register `AMDRyzenMasterDriverV32` (demand,
  official signed driver), set the InstallationPath registry value. Fully
  reversible: `sc stop/delete AMDRyzenMasterDriverV32` + remove the folder.
- [x] Validated against AMD's own sample app: PPT/TDC/EDC limits and values,
  VDDCR rails, per-core freq/C0/temp all match. CSV gains 6 CPU columns.

## v0.5 — Stability proof (medium)

1. Soak test: daemon running 7 days, zero crashes, zero stuck priorities
   (watchdog: on every poll, verify the boosted PID still exists, else clear
   state).
2. Switch-count budget: debounce tuning so profile switches stay under ~10
   per hour in normal use (log evidence).
3. `stock` profile restores OS tweaks too (priorities back to normal,
   efficiency mode cleared) — full reversibility test.

## v1.0 — Done criteria (not a date)

- One-command setup, one-command uninstall, both verified on a clean boot.
- Docs prove all three promises with numbers: performance (no regression vs
  stock), stability (soak + switch budget), power (watt samples per profile).
- No per-game code paths unless a generic mechanism demonstrably fails for a
  whole class of apps — and then the exception is documented with evidence.

## v2.0 — Ideas only (do not start)

Hardware power-limit control (PPT/TDC/EDC, Curve Optimizer) via the Ryzen
Master driver path; render-resolution helpers; anything requiring kernel
drivers or reboots. Parked until v1.0 criteria are met.
