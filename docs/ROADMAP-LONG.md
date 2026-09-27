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

## v0.5 — Stability proof (medium) [x] mechanisms DONE 2026-09-27, soak running

1. [x] Watchdog (shipped early with the harden pass): stale boosted PIDs are
   cleared every poll, restore skips dead processes.
2. [x] Switch-count budget: automatic switches rate-limited to one per
   `min_switch_interval_seconds` (default 30; override bypasses). Evidence
   tool: `XssEngine.py stats [hours]` — switch rate with the <= 10/h budget,
   profile share, CPU/GPU power and thermal averages.
3. [x] `stock` restores OS tweaks too (priorities back to normal, efficiency
   mode cleared on every tracked pid); daemon shutdown releases everything.
4. [ ] Soak: 7 days zero crashes — running since 2026-09-27; verdict = run
   `stats 168` and check the engine log for restarts.

## v0.6 — Power evidence per profile (small) [x] DONE 2026-09-27

`stats` gains a per-profile breakdown: PPT avg/max, temp avg/max, eff avg for
each of eco/balanced/performance/stock. First real numbers (24 h window):
**eco PPT avg 7.1W vs balanced 13.8W** — the eco claim in watts, one command,
no manual CSV digging.

## v0.7 — One-command setup + uninstall (medium)

`install_task.ps1` + `setup_schemes.ps1` verified end-to-end from a clean
state; new `uninstall.ps1` removes the task, the schemes and the state (logs
kept), verified by a reinstall round-trip. Both documented in README.

## v0.8 — No-regression proof (medium)

Performance evidence under a fixed synthetic CPU load, engine on (performance)
vs `stock`: completion time must not regress, with avg PPT recorded alongside.
Numbers shipped in `docs/PERF-EVIDENCE.md`.

## v0.9 — Release polish (small)

README/docs final pass, v1.0 checklist review, version bump, GitHub release
tag. Nothing new lands here — only proofreading and packaging.

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
