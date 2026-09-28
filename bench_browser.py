#!/usr/bin/env python3
"""AMD XSS Engine -- Real-World Browser A/B Power Benchmark

Opens 10 REAL high-traffic websites in an isolated Microsoft Edge instance and
compares power/thermal behaviour across three configs:
  A) Stock Windows Balanced (default OS, unmanaged GPU)
  B) AMDXSS Balanced (governed scheme, ADLX Chill 60-120, OS focus layer)
  C) AMDXSS Eco (aggressive power saving, ADLX Chill 30-60)

Every tab hits the live internet -- YouTube, Reddit, Twitter/X, Instagram,
Wikipedia, GitHub, Stack Overflow, Amazon, CNN, Twitch.  No synthetic pages.
"""

import argparse
import datetime as dt
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time

import psutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, r"C:\Program Files\AMDXSS")

try:
    from rm_sdk import RmSdk
except ImportError:
    RmSdk = None

try:
    from xss_engine import Adlx, get_schemes, active_scheme, set_priority, os_release
except ImportError:
    Adlx = None
    get_schemes = None
    active_scheme = None
    set_priority = None
    os_release = None

TEMP_BASE = os.path.join(tempfile.gettempdir(), "amdxss_browser_bench")
PROFILE_DIR = os.path.join(TEMP_BASE, "edge_realweb_profile")
PROGRAM_FILES = os.environ.get("ProgramFiles", r"C:\Program Files")
OVERRIDE_PATH = os.path.join(PROGRAM_FILES, "AMDXSS", "state", "override.txt")
EDGE_EXE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

REAL_URLS = [
    "https://www.youtube.com",
    "https://www.reddit.com",
    "https://twitter.com",
    "https://www.instagram.com",
    "https://en.wikipedia.org/wiki/Ryzen",
    "https://github.com",
    "https://stackoverflow.com/questions",
    "https://www.amazon.com",
    "https://www.cnn.com",
    "https://www.twitch.tv",
]


def set_override(name):
    os.makedirs(os.path.dirname(OVERRIDE_PATH), exist_ok=True)
    with open(OVERRIDE_PATH, "w", encoding="ascii") as f:
        f.write(name)


def clear_override():
    if os.path.exists(OVERRIDE_PATH):
        try:
            os.remove(OVERRIDE_PATH)
        except Exception:
            pass


def configure_system(cfg, adlx):
    print(f"\n[+] Actuating '{cfg}' ...")
    schemes = get_schemes() if get_schemes else {}
    if cfg == "stock":
        set_override("stock")
        subprocess.run(["powercfg", "/setactive",
                        "381b4222-f694-41f0-9685-ff5bb260df2e"], capture_output=True)
        if adlx and adlx.available:
            adlx.apply_3d({"chill": {"enabled": False},
                           "sharpening": {"enabled": False}})
        if os_release:
            os_release({})
    elif cfg == "amdxss-balanced":
        set_override("balanced")
        guid = schemes.get("AMD Engine - Balanced",
                           "13ab5296-2fd0-486c-b1fe-537712f9f21d")
        subprocess.run(["powercfg", "/setactive", guid], capture_output=True)
        if adlx and adlx.available:
            adlx.apply_3d({"chill": {"enabled": True, "min": 60, "max": 120},
                           "sharpening": {"enabled": True, "sharpness": 80}})
    elif cfg == "amdxss-eco":
        set_override("eco")
        guid = schemes.get("AMD Engine - Eco",
                           "bd212f23-d38f-49f4-a2f4-56c76f412d87")
        subprocess.run(["powercfg", "/setactive", guid], capture_output=True)
        if adlx and adlx.available:
            adlx.apply_3d({"chill": {"enabled": True, "min": 30, "max": 60},
                           "sharpening": {"enabled": False}})
    time.sleep(3)


def launch_edge(urls):
    return subprocess.Popen([
        EDGE_EXE,
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-sync", "--disable-component-update",
        "--window-position=-30000,-30000",
        "--window-size=1280,720",
    ] + urls)


def close_edge():
    killed = 0
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if "msedge" in (p.info["name"] or "").lower():
                cmd = " ".join(p.info["cmdline"] or [])
                if "edge_realweb_profile" in cmd:
                    p.kill()
                    killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if killed:
        print(f"    [Cleanup] killed {killed} Edge procs.")
    time.sleep(0.5)


def arm_watchdog(secs=600):
    r = os.path.join(BASE_DIR, "tests", "Restore-XssBaseline.ps1")
    c = (f"Unregister-ScheduledTask -TaskName 'AMD XSS Bench Watchdog' -Confirm:$false "
         f"-ErrorAction SilentlyContinue; $a=New-ScheduledTaskAction -Execute 'powershell.exe' "
         f"-Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"{r}\"'; "
         f"$t=New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds({secs}); "
         f"$s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable "
         f"-ExecutionTimeLimit (New-TimeSpan -Minutes 5) -Hidden; "
         f"Register-ScheduledTask -TaskName 'AMD XSS Bench Watchdog' -Action $a "
         f"-Trigger $t -Settings $s -Force | Out-Null")
    subprocess.run(["powershell", "-NoProfile", "-Command", c], capture_output=True)
    print(f"[+] Watchdog armed ({secs}s).")


def disarm_watchdog():
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Unregister-ScheduledTask -TaskName 'AMD XSS Bench Watchdog' "
                    "-Confirm:$false -ErrorAction SilentlyContinue"], capture_output=True)
    print("[+] Watchdog disarmed.")


def sample(duration, rm, adlx, label=""):
    samples = []
    t0 = time.time()
    print(f"    Sampling {label} ({duration}s) ...", end="", flush=True)
    while time.time() - t0 < duration:
        tick = time.time()
        s = {}
        if rm:
            try:
                m = rm.metrics()
                if m:
                    s["ppt_w"] = m.get("ppt_w", 0.0)
                    s["temp_c"] = m.get("temp_c", 0.0)
                    effs = [c["eff_mhz"] for c in m.get("cores", []) if c.get("eff_mhz")]
                    s["eff_mhz"] = (sum(effs) / len(effs)) if effs else 0.0
            except Exception:
                pass
        if adlx and adlx.available:
            try:
                gm = adlx.metrics()
                if gm:
                    s["gpu_pwr_w"] = gm.get("power", 0.0)
                    s["gpu_clk_mhz"] = gm.get("clock", 0)
                    s["gpu_temp_c"] = gm.get("temp", 0.0)
            except Exception:
                pass
        samples.append(s)
        print(".", end="", flush=True)
        time.sleep(max(0.1, 1.0 - (time.time() - tick)))
    print(" done.")
    return samples


def summarise(samples):
    def col(k):
        return [s[k] for s in samples if s.get(k) is not None]
    ppts, temps, effs = col("ppt_w"), col("temp_c"), col("eff_mhz")
    gp, gc, gt = col("gpu_pwr_w"), col("gpu_clk_mhz"), col("gpu_temp_c")
    return {
        "n": len(samples),
        "mean_ppt": statistics.mean(ppts) if ppts else 0,
        "min_ppt": min(ppts) if ppts else 0,
        "max_ppt": max(ppts) if ppts else 0,
        "energy_joules": sum(ppts),
        "mean_temp": statistics.mean(temps) if temps else 0,
        "max_temp": max(temps) if temps else 0,
        "mean_eff": statistics.mean(effs) if effs else 0,
        "mean_gpu_pwr": statistics.mean(gp) if gp else 0,
        "mean_gpu_clk": statistics.mean(gc) if gc else 0,
        "mean_gpu_temp": statistics.mean(gt) if gt else 0,
    }


class Tee:
    def __init__(self, path):
        self.t = sys.stdout
        self.f = open(path, "w", encoding="utf-8", buffering=1)
    def write(self, m):
        self.t.write(m); self.f.write(m)
    def flush(self):
        self.t.flush(); self.f.flush()


def main():
    os.makedirs(TEMP_BASE, exist_ok=True)
    sys.stdout = Tee(os.path.join(TEMP_BASE, "browser_bench_console.log"))

    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=30)
    ap.add_argument("--idle-seconds", type=int, default=15)
    ap.add_argument("--out", type=str,
                    default=os.path.join(BASE_DIR, "docs", "BROWSER-BENCHMARK.md"))
    ap.add_argument("--json-out", type=str,
                    default=os.path.join(TEMP_BASE, "browser_bench_data.json"))
    args = ap.parse_args()

    print("=" * 70)
    print("=== AMD XSS ENGINE -- REAL-WORLD BROWSER A/B BENCHMARK ===")
    print("Tabs   : YouTube, Reddit, Twitter/X, Instagram, Wikipedia,")
    print("         GitHub, Stack Overflow, Amazon, CNN, Twitch")
    print(f"Phases : {args.idle_seconds}s idle + {args.duration}s real browsing per config")
    print("Configs: Stock Windows Balanced / AMDXSS Balanced / AMDXSS Eco")
    print("=" * 70)

    rm = RmSdk() if RmSdk else None
    if rm and not rm.start():
        rm = None
    adlx = Adlx() if Adlx else None
    if adlx and not adlx.start():
        adlx = None

    orig_name, orig_guid = (active_scheme() if active_scheme else ("?", "?"))
    print(f"[+] Baseline scheme: {orig_name} ({orig_guid})")
    arm_watchdog(420 + (args.duration + args.idle_seconds) * 3)

    results = {}
    try:
        for cfg in ["stock", "amdxss-balanced", "amdxss-eco"]:
            print(f"\n{'=' * 50}")
            print(f"=== CONFIG: [{cfg.upper()}] ===")
            print("=" * 50)
            configure_system(cfg, adlx)

            print("\n[*] Idle phase ...")
            time.sleep(3)
            idle = summarise(sample(args.idle_seconds, rm, adlx, f"[{cfg}] IDLE"))
            print(f"    PPT {idle['mean_ppt']:.2f}W  Temp {idle['mean_temp']:.1f}C  "
                  f"Clk {idle['mean_eff']:.0f}MHz")

            print("\n[*] Opening 10 real websites ...")
            proc = launch_edge(REAL_URLS)
            print(f"    Edge PID {proc.pid} -- waiting 15s for pages to load ...")
            time.sleep(15)

            if cfg == "amdxss-balanced" and set_priority:
                set_priority(proc.pid, 0x8000)
                print("    [OS-Booster] Edge -> ABOVE_NORMAL")

            br = summarise(sample(args.duration, rm, adlx, f"[{cfg}] REAL BROWSING"))
            print(f"    PPT {br['mean_ppt']:.2f}W  Energy {br['energy_joules']:.0f}J  "
                  f"Temp {br['mean_temp']:.1f}C  GPU {br['mean_gpu_pwr']:.1f}W")

            close_edge()
            results[cfg] = {"idle": idle, "browsing": br}
            time.sleep(3)
    finally:
        print("\n[+] Teardown ...")
        close_edge()
        clear_override()
        if orig_guid:
            subprocess.run(["powercfg", "/setactive", orig_guid], capture_output=True)
            print(f"    Restored scheme: {orig_name}")
        if adlx and adlx.available:
            adlx.apply_3d({"chill": {"enabled": True, "min": 60, "max": 120},
                           "sharpening": {"enabled": True, "sharpness": 80}})
            adlx.stop()
        if rm:
            rm.stop()
        disarm_watchdog()

    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump({"timestamp": dt.datetime.now().isoformat(),
                    "results": results}, f, indent=2)

    si = results.get("stock", {}).get("idle", {})
    bi = results.get("amdxss-balanced", {}).get("idle", {})
    ei = results.get("amdxss-eco", {}).get("idle", {})
    sb = results.get("stock", {}).get("browsing", {})
    bb = results.get("amdxss-balanced", {}).get("browsing", {})
    eb = results.get("amdxss-eco", {}).get("browsing", {})

    def dl(a, b, u="W"):
        if not b:
            return "N/A"
        return f"{a - b:+.2f} {u} ({(a - b) / b * 100:+.1f}%)"

    report = f"""# Real-World Browser Benchmark: Stock Windows vs AMDXSS Engine

**Date:** {time.strftime('%Y-%m-%d %H:%M:%S')}
**Host:** AMD Ryzen 7 5700G + Radeon Vega 8 iGPU, 16 GB DDR4, Windows 11
**Workload:** 10 real websites opened simultaneously -- YouTube, Reddit, Twitter/X,
Instagram, Wikipedia, GitHub, Stack Overflow, Amazon, CNN, Twitch.
**Browser:** Microsoft Edge in isolated `--user-data-dir`, off-screen window.
**Sampling:** 1.0 s via AMD Ryzen Master SDK (SMU) + AMD ADLX (GPU).

---

## Idle Baseline ({args.idle_seconds}s)

| Config | PPT (W) | Min--Max PPT | Temp (C) | Clock (MHz) | GPU W / MHz |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Stock Windows | {si.get('mean_ppt', 0):.2f} | {si.get('min_ppt', 0):.2f} -- {si.get('max_ppt', 0):.2f} | {si.get('mean_temp', 0):.1f} | {si.get('mean_eff', 0):.0f} | {si.get('mean_gpu_pwr', 0):.1f} / {si.get('mean_gpu_clk', 0):.0f} |
| AMDXSS Balanced | **{bi.get('mean_ppt', 0):.2f}** | {bi.get('min_ppt', 0):.2f} -- {bi.get('max_ppt', 0):.2f} | {bi.get('mean_temp', 0):.1f} | {bi.get('mean_eff', 0):.0f} | {bi.get('mean_gpu_pwr', 0):.1f} / {bi.get('mean_gpu_clk', 0):.0f} |
| AMDXSS Eco | **{ei.get('mean_ppt', 0):.2f}** | {ei.get('min_ppt', 0):.2f} -- {ei.get('max_ppt', 0):.2f} | **{ei.get('mean_temp', 0):.1f}** | {ei.get('mean_eff', 0):.0f} | {ei.get('mean_gpu_pwr', 0):.1f} / {ei.get('mean_gpu_clk', 0):.0f} |

## 10-Tab Real-World Browsing ({args.duration}s)

| Metric | Stock Windows | AMDXSS Balanced | AMDXSS Eco | Eco Delta vs Stock |
| :--- | :--- | :--- | :--- | :--- |
| **Mean PPT** | {sb.get('mean_ppt', 0):.2f} W | {bb.get('mean_ppt', 0):.2f} W | **{eb.get('mean_ppt', 0):.2f} W** | **{dl(eb.get('mean_ppt', 0), sb.get('mean_ppt', 0))}** |
| **Total Energy** | {sb.get('energy_joules', 0):.0f} J | {bb.get('energy_joules', 0):.0f} J | **{eb.get('energy_joules', 0):.0f} J** | **{dl(eb.get('energy_joules', 0), sb.get('energy_joules', 0), 'J')}** |
| **Peak PPT** | {sb.get('max_ppt', 0):.2f} W | {bb.get('max_ppt', 0):.2f} W | **{eb.get('max_ppt', 0):.2f} W** | **{dl(eb.get('max_ppt', 0), sb.get('max_ppt', 0))}** |
| **Avg Temp** | {sb.get('mean_temp', 0):.1f} C | {bb.get('mean_temp', 0):.1f} C | **{eb.get('mean_temp', 0):.1f} C** | **{eb.get('mean_temp', 0) - sb.get('mean_temp', 0):+.1f} C** |
| **Peak Temp** | {sb.get('max_temp', 0):.1f} C | {bb.get('max_temp', 0):.1f} C | **{eb.get('max_temp', 0):.1f} C** | **{eb.get('max_temp', 0) - sb.get('max_temp', 0):+.1f} C** |
| **Clock** | {sb.get('mean_eff', 0):.0f} MHz | {bb.get('mean_eff', 0):.0f} MHz | {eb.get('mean_eff', 0):.0f} MHz | {eb.get('mean_eff', 0) - sb.get('mean_eff', 0):+.0f} MHz |
| **GPU Power** | {sb.get('mean_gpu_pwr', 0):.1f} W | {bb.get('mean_gpu_pwr', 0):.1f} W | **{eb.get('mean_gpu_pwr', 0):.1f} W** | {eb.get('mean_gpu_pwr', 0) - sb.get('mean_gpu_pwr', 0):+.1f} W |
| **GPU Temp** | {sb.get('mean_gpu_temp', 0):.1f} C | {bb.get('mean_gpu_temp', 0):.1f} C | **{eb.get('mean_gpu_temp', 0):.1f} C** | {eb.get('mean_gpu_temp', 0) - sb.get('mean_gpu_temp', 0):+.1f} C |
"""
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n[+] Report: {args.out}")
    return 0


if __name__ == "__main__":
    main()
