# Game A/B Benchmark: Stock Windows vs AMDXSS Engine

**Date:** 2026-09-29 01:06:42
**Map:** Brookhaven 🏡RP (Place ID `4924922222`) -- 344,686 live players
**Host:** AMD Ryzen 7 5700G + Radeon Vega 8, 16 GB DDR4, Windows 11
**Graphics:** Level 10 Max (D3D11, 4x MSAA), 1366x768 @ 60 Hz
**Protocol:** 2 cycles x 60s per config (load/play/close)
**Safety:** Windowed + muted + liveness pre-check + watchdog + auto-restore

---

## Per-Cycle Results

| Config / Cycle | Avg PPT | Peak PPT | Avg Temp | Peak Temp | GPU Clk | GPU Temp | RAM | Audio CPU |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| stock C1 | 46.60 | 49.72 | 60.4 | 64.3 | 1825 | 53.5 | 2535 | 0.0 |
| stock C2 | 47.31 | 50.30 | 65.0 | 68.9 | 1742 | 58.1 | 2612 | 0.0 |
| amdxss-balanced C1 | 36.17 | 52.39 | 62.3 | 68.5 | 1459 | 56.3 | 2617 | 0.0 |
| amdxss-balanced C2 | 35.52 | 49.29 | 62.8 | 71.6 | 1402 | 56.4 | 2635 | 0.0 |
| amdxss-eco C1 | 26.72 | 48.58 | 58.6 | 66.5 | 901 | 53.3 | 2542 | 0.0 |
| amdxss-eco C2 | 25.69 | 31.71 | 57.4 | 65.2 | 930 | 52.0 | 2597 | 0.0 |

## Aggregate Comparison

| Metric | Stock Windows | AMDXSS Balanced | AMDXSS Eco |
| :--- | :--- | :--- | :--- |
| **Avg PPT** | 46.96 W | 35.84 W | **26.20 W** |
| **Avg Temp** | 62.7 C | 62.5 C | **58.0 C** |
| **Peak Temp** | 66.6 C | 70.1 C | **65.9 C** |
| **GPU Clock** | 1783 MHz | 1431 MHz | 916 MHz |
| **GPU Temp** | 55.8 C | 56.3 C | **52.6 C** |
| **RAM** | 2573 MB | 2626 MB | 2570 MB |
| **Audio CPU** | 0.0% | 0.0% | 0.0% |
