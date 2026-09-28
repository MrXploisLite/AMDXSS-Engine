# Real-World Browser Benchmark: Stock Windows vs AMDXSS Engine

**Date:** 2026-09-29 00:41:19
**Host:** AMD Ryzen 7 5700G + Radeon Vega 8 iGPU, 16 GB DDR4, Windows 11
**Workload:** 10 real websites opened simultaneously -- YouTube, Reddit, Twitter/X,
Instagram, Wikipedia, GitHub, Stack Overflow, Amazon, CNN, Twitch.
**Browser:** Microsoft Edge in isolated `--user-data-dir`, off-screen window.
**Sampling:** 1.0 s via AMD Ryzen Master SDK (SMU) + AMD ADLX (GPU).

---

## Idle Baseline (15s)

| Config | PPT (W) | Min--Max PPT | Temp (C) | Clock (MHz) | GPU W / MHz |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Stock Windows | 8.14 | 7.62 -- 10.06 | 41.5 | 90 | 7.7 / 400 |
| AMDXSS Balanced | **9.36** | 7.74 -- 17.74 | 42.9 | 147 | 8.4 / 507 |
| AMDXSS Eco | **8.20** | 7.86 -- 9.64 | **41.6** | 91 | 7.9 / 507 |

## 10-Tab Real-World Browsing (30s)

| Metric | Stock Windows | AMDXSS Balanced | AMDXSS Eco | Eco Delta vs Stock |
| :--- | :--- | :--- | :--- | :--- |
| **Mean PPT** | 10.86 W | 9.93 W | **10.00 W** | **-0.87 W (-8.0%)** |
| **Total Energy** | 326 J | 298 J | **300 J** | **-25.99 J (-8.0%)** |
| **Peak PPT** | 24.07 W | 27.27 W | **31.61 W** | **+7.54 W (+31.3%)** |
| **Avg Temp** | 44.4 C | 43.2 C | **43.8 C** | **-0.6 C** |
| **Peak Temp** | 51.3 C | 50.6 C | **55.6 C** | **+4.3 C** |
| **Clock** | 236 MHz | 185 MHz | 202 MHz | -34 MHz |
| **GPU Power** | 11.0 W | 9.2 W | **10.1 W** | -0.9 W |
| **GPU Temp** | 41.5 C | 40.6 C | **41.1 C** | -0.4 C |
