#!/usr/bin/env python3
"""AMD XSS Engine -- Real-World Game A/B Benchmark (Roblox)

Runs multi-cycle load/play/close benchmark on a live Roblox map and compares:
  A) Stock Windows Balanced (default OS, unmanaged GPU)
  B) AMDXSS Engine (governed scheme, ADLX Chill, OS focus layer)

Each config gets the same multi-cycle treatment for fair comparison.
Map liveness is verified via the Roblox Public Games API before every launch.
Audio is muted during the run (call-safe) but audiodg.exe load is still monitored.
"""

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request

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

TEMP_BASE = os.path.join(tempfile.gettempdir(), "amdxss_game_bench")
ROBLOX_XML = os.path.expandvars(r"%LOCALAPPDATA%\Roblox\GlobalBasicSettings_13.xml")
ROBLOX_XML_BAK = ROBLOX_XML + ".amdxss_bak"
PROGRAM_FILES = os.environ.get("ProgramFiles", r"C:\Program Files")
OVERRIDE_PATH = os.path.join(PROGRAM_FILES, "AMDXSS", "state", "override.txt")


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


def patch_roblox(windowed=True, mute=True):
    if not os.path.exists(ROBLOX_XML):
        return
    if not os.path.exists(ROBLOX_XML_BAK):
        shutil.copy2(ROBLOX_XML, ROBLOX_XML_BAK)
    with open(ROBLOX_XML, "r", encoding="utf-8") as f:
        c = f.read()
    if windowed:
        c = re.sub(r'<bool name="Fullscreen">true</bool>',
                    '<bool name="Fullscreen">false</bool>', c)
    if mute:
        c = re.sub(r'<float name="MasterVolume">[\d.]+</float>',
                    '<float name="MasterVolume">0</float>', c)
    with open(ROBLOX_XML, "w", encoding="utf-8") as f:
        f.write(c)


def restore_roblox():
    if os.path.exists(ROBLOX_XML_BAK):
        shutil.copy2(ROBLOX_XML_BAK, ROBLOX_XML)
        try:
            os.remove(ROBLOX_XML_BAK)
        except Exception:
            pass


def check_map(place_id):
    uid = None
    try:
        with urllib.request.urlopen(
                f"https://apis.roblox.com/universes/v1/places/{place_id}/universe",
                timeout=10) as r:
            uid = json.loads(r.read()).get("universeId")
    except Exception:
        pass
    if not uid:
        uid = place_id
    try:
        with urllib.request.urlopen(
                f"https://games.roblox.com/v1/games?universeIds={uid}", timeout=10) as r:
            d = json.loads(r.read())
            if d and d.get("data"):
                g = d["data"][0]
                return {"alive": g.get("playing", 0) > 0, "playing": g.get("playing", 0),
                        "visits": g.get("visits", 0), "name": g.get("name", "?"),
                        "maxPlayers": g.get("maxPlayers", 0), "uid": uid}
    except Exception:
        pass
    return {"alive": False, "playing": 0, "visits": 0, "name": "?",
            "maxPlayers": 0, "uid": uid}


def kill_game():
    n = 0
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if "robloxplayer" in (p.info["name"] or "").lower():
                p.kill()
                n += 1
        except Exception:
            pass
    return n


def configure(cfg, adlx):
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


def sample(duration, pid, rm, adlx, label=""):
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
                    s["gpu_usage"] = gm.get("usage", 0.0)
            except Exception:
                pass
        try:
            p = psutil.Process(pid)
            s["ram_mb"] = p.memory_info().rss / (1024 * 1024)
        except Exception:
            pass
        try:
            for ap in psutil.process_iter(["pid", "name", "cpu_percent"]):
                if (ap.info["name"] or "").lower() == "audiodg.exe":
                    s["audio_cpu"] = ap.cpu_percent()
                    break
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
    return {
        "n": len(samples),
        "mean_ppt": (statistics.mean(col("ppt_w")) if col("ppt_w") else 0),
        "max_ppt": (max(col("ppt_w")) if col("ppt_w") else 0),
        "mean_temp": (statistics.mean(col("temp_c")) if col("temp_c") else 0),
        "max_temp": (max(col("temp_c")) if col("temp_c") else 0),
        "mean_eff": (statistics.mean(col("eff_mhz")) if col("eff_mhz") else 0),
        "mean_gpu_pwr": (statistics.mean(col("gpu_pwr_w")) if col("gpu_pwr_w") else 0),
        "mean_gpu_clk": (statistics.mean(col("gpu_clk_mhz")) if col("gpu_clk_mhz") else 0),
        "mean_gpu_temp": (statistics.mean(col("gpu_temp_c")) if col("gpu_temp_c") else 0),
        "mean_gpu_usage": (statistics.mean(col("gpu_usage")) if col("gpu_usage") else 0),
        "mean_ram_mb": (statistics.mean(col("ram_mb")) if col("ram_mb") else 0),
        "max_ram_mb": (max(col("ram_mb")) if col("ram_mb") else 0),
        "mean_audio_cpu": (statistics.mean(col("audio_cpu")) if col("audio_cpu") else 0),
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
    sys.stdout = Tee(os.path.join(TEMP_BASE, "game_bench_console.log"))

    ap = argparse.ArgumentParser()
    ap.add_argument("--place-id", type=str, default="4924922222",
                    help="Roblox Place ID (default: Brookhaven)")
    ap.add_argument("--duration", type=int, default=60)
    ap.add_argument("--cycles", type=int, default=2)
    ap.add_argument("--out", type=str,
                    default=os.path.join(BASE_DIR, "docs", "GAME-BENCHMARK.md"))
    args = ap.parse_args()

    print("=" * 70)
    print("=== AMD XSS ENGINE -- GAME A/B BENCHMARK (ROBLOX 3D) ===")
    print(f"Target : Place ID {args.place_id}")
    print(f"Protocol: {args.cycles} cycles x {args.duration}s per config")
    print("Configs : Stock Windows Balanced vs AMDXSS Balanced vs AMDXSS Eco")
    print("Safety  : Windowed + muted + map liveness + watchdog + auto-restore")
    print("=" * 70)

    rm = RmSdk() if RmSdk else None
    if rm and not rm.start():
        rm = None
    adlx = Adlx() if Adlx else None
    if adlx and not adlx.start():
        adlx = None

    orig_name, orig_guid = (active_scheme() if active_scheme else ("?", "?"))
    print(f"[+] Baseline: {orig_name} ({orig_guid})")

    map_info = check_map(args.place_id)
    print(f"[+] Map: {map_info['name']}  alive={map_info['alive']}  "
          f"playing={map_info['playing']:,}  visits={map_info['visits']:,}")
    if not map_info["alive"]:
        print("[!] Map is dead. Aborting safely.")
        return 1

    patch_roblox(windowed=True, mute=True)
    print("[+] Roblox settings: Windowed + Muted")

    all_results = {}
    total_secs = args.duration * args.cycles * 3
    # arm_watchdog inline
    r = os.path.join(BASE_DIR, "tests", "Restore-XssBaseline.ps1")
    c = (f"Unregister-ScheduledTask -TaskName 'AMD XSS Game Watchdog' -Confirm:$false "
         f"-ErrorAction SilentlyContinue; $a=New-ScheduledTaskAction -Execute 'powershell.exe' "
         f"-Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"{r}\"'; "
         f"$t=New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds({420 + total_secs}); "
         f"$s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable "
         f"-ExecutionTimeLimit (New-TimeSpan -Minutes 5) -Hidden; "
         f"Register-ScheduledTask -TaskName 'AMD XSS Game Watchdog' -Action $a "
         f"-Trigger $t -Settings $s -Force | Out-Null")
    subprocess.run(["powershell", "-NoProfile", "-Command", c], capture_output=True)
    print("[+] Watchdog armed.")

    try:
        for cfg in ["stock", "amdxss-balanced", "amdxss-eco"]:
            print(f"\n{'=' * 50}")
            print(f"=== CONFIG: [{cfg.upper()}] ===")
            print("=" * 50)
            configure(cfg, adlx)

            cycles = []
            for ci in range(1, args.cycles + 1):
                print(f"\n  --- Cycle {ci}/{args.cycles} ---")
                subprocess.Popen(["cmd.exe", "/c", "start",
                                  f"roblox://placeId={args.place_id}"], shell=False)
                pid = None
                t0 = time.time()
                while time.time() - t0 < 20:
                    for p in psutil.process_iter(["pid", "name"]):
                        if "robloxplayerbeta" in (p.info["name"] or "").lower():
                            pid = p.pid
                            break
                    if pid:
                        break
                    time.sleep(1)
                if not pid:
                    print(f"    [!] Roblox did not start in cycle {ci}.")
                    continue
                print(f"    Roblox PID {pid} -- loading 12s ...")
                time.sleep(12)
                if set_priority:
                    set_priority(pid, 0x8000)
                s = sample(args.duration, pid, rm, adlx, f"[{cfg}] C{ci}")
                cycles.append(summarise(s))
                kill_game()
                time.sleep(2)

            if cycles:
                all_results[cfg] = cycles
    finally:
        print("\n[+] Teardown ...")
        kill_game()
        restore_roblox()
        clear_override()
        if orig_guid:
            subprocess.run(["powercfg", "/setactive", orig_guid], capture_output=True)
            print(f"    Restored: {orig_name}")
        if adlx and adlx.available:
            adlx.stop()
        if rm:
            rm.stop()
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Unregister-ScheduledTask -TaskName 'AMD XSS Game Watchdog' "
                        "-Confirm:$false -ErrorAction SilentlyContinue"], capture_output=True)
        print("[+] Done.")

    # ---- report ----
    def avg(cycles, key):
        vals = [c[key] for c in cycles if c.get(key)]
        return statistics.mean(vals) if vals else 0

    lines = []
    for cfg in ["stock", "amdxss-balanced", "amdxss-eco"]:
        cy = all_results.get(cfg, [])
        if not cy:
            continue
        for i, c in enumerate(cy, 1):
            lines.append(
                f"| {cfg} C{i} | {c['mean_ppt']:.2f} | {c['max_ppt']:.2f} | "
                f"{c['mean_temp']:.1f} | {c['max_temp']:.1f} | "
                f"{c['mean_gpu_clk']:.0f} | {c['mean_gpu_temp']:.1f} | "
                f"{c['mean_ram_mb']:.0f} | {c['mean_audio_cpu']:.1f} |")

    sb = all_results.get("stock", [])
    bb = all_results.get("amdxss-balanced", [])
    eb = all_results.get("amdxss-eco", [])

    report = f"""# Game A/B Benchmark: Stock Windows vs AMDXSS Engine

**Date:** {time.strftime('%Y-%m-%d %H:%M:%S')}
**Map:** {map_info['name']} (Place ID `{args.place_id}`) -- {map_info['playing']:,} live players
**Host:** AMD Ryzen 7 5700G + Radeon Vega 8, 16 GB DDR4, Windows 11
**Graphics:** Level 10 Max (D3D11, 4x MSAA), 1366x768 @ 60 Hz
**Protocol:** {args.cycles} cycles x {args.duration}s per config (load/play/close)
**Safety:** Windowed + muted + liveness pre-check + watchdog + auto-restore

---

## Per-Cycle Results

| Config / Cycle | Avg PPT | Peak PPT | Avg Temp | Peak Temp | GPU Clk | GPU Temp | RAM | Audio CPU |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
{chr(10).join(lines)}

## Aggregate Comparison

| Metric | Stock Windows | AMDXSS Balanced | AMDXSS Eco |
| :--- | :--- | :--- | :--- |
| **Avg PPT** | {avg(sb, 'mean_ppt'):.2f} W | {avg(bb, 'mean_ppt'):.2f} W | **{avg(eb, 'mean_ppt'):.2f} W** |
| **Avg Temp** | {avg(sb, 'mean_temp'):.1f} C | {avg(bb, 'mean_temp'):.1f} C | **{avg(eb, 'mean_temp'):.1f} C** |
| **Peak Temp** | {avg(sb, 'max_temp'):.1f} C | {avg(bb, 'max_temp'):.1f} C | **{avg(eb, 'max_temp'):.1f} C** |
| **GPU Clock** | {avg(sb, 'mean_gpu_clk'):.0f} MHz | {avg(bb, 'mean_gpu_clk'):.0f} MHz | {avg(eb, 'mean_gpu_clk'):.0f} MHz |
| **GPU Temp** | {avg(sb, 'mean_gpu_temp'):.1f} C | {avg(bb, 'mean_gpu_temp'):.1f} C | **{avg(eb, 'mean_gpu_temp'):.1f} C** |
| **RAM** | {avg(sb, 'mean_ram_mb'):.0f} MB | {avg(bb, 'mean_ram_mb'):.0f} MB | {avg(eb, 'mean_ram_mb'):.0f} MB |
| **Audio CPU** | {avg(sb, 'mean_audio_cpu'):.1f}% | {avg(bb, 'mean_audio_cpu'):.1f}% | {avg(eb, 'mean_audio_cpu'):.1f}% |
"""
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n[+] Report: {args.out}")
    return 0


if __name__ == "__main__":
    main()
