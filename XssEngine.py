#!/usr/bin/env python3
"""AMD XSS Engine - adaptive power and performance daemon for AMD systems on Windows.

Reads hardware telemetry, picks a profile from the foreground application and user
idle time, and applies it:

  CPU/SoC  -> Windows Processor Power Management schemes (powercfg), created by
              setup_schemes.ps1.
  GPU      -> Radeon Chill / Frame Rate Target Control via ADLX (AMD Device Library
              eXtra) through the official amd-adlx Python binding. Optional: without
              it the engine still switches schemes and logs telemetry as far as the
              ADLX part is unavailable.

Designed to run as a hidden logon task (install_task.ps1). Every action is
reversible: `stock` restores stock power handling, `-Uninstall` removes the task.
"""

import ctypes
import csv
import datetime as dt
import fnmatch
import json
import os
import re
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE, 'logs')
STATE_DIR = os.path.join(BASE, 'state')
CFG_PATH = os.path.join(BASE, 'xss_config.json')
STATE_PATH = os.path.join(STATE_DIR, 'xss_state.json')
OVERRIDE_PATH = os.path.join(STATE_DIR, 'override.txt')
TELEMETRY_CSV = os.path.join(LOG_DIR, 'xss-telemetry.csv')
TELEMETRY_HEADER = ['ts', 'fg', 'idle_s', 'profile', 'fg_pid', 'scheme_ok', 'gpu_ok', 'os_ok',
                    'gpu_clock', 'gpu_usage', 'gpu_temp', 'gpu_power', 'fps',
                    'cpu_ppt_w', 'cpu_tdc_vdd_a', 'cpu_edc_vdd_a', 'cpu_vddcr_vdd_w',
                    'cpu_temp_c', 'cpu_eff_mhz']
MUTEX_NAME = 'Global\\AmdXssEngineDaemon'

VERSION = '0.2.0'

# ADLX is a pybind11 module; dropping its objects during interpreter shutdown can
# segfault. Keep references alive for the process lifetime and exit via os._exit().
_KEEPALIVE = []


# --------------------------------------------------------------------- util --
def ensure_dirs():
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)


def log(msg, level='INFO'):
    line = '[%s][%s] %s' % (dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), level, msg)
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        ensure_dirs()
        fname = os.path.join(LOG_DIR, 'xss-engine-%s.log' % dt.date.today().strftime('%Y%m%d'))
        with open(fname, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def load_cfg():
    with open(CFG_PATH, encoding='utf-8') as f:
        return json.load(f)


def save_state(state):
    ensure_dirs()
    state['ts'] = dt.datetime.now().isoformat(timespec='seconds')
    with open(STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)
    return state


def read_state():
    try:
        with open(STATE_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def single_instance():
    k = ctypes.windll.kernel32
    h = k.CreateMutexW(None, False, MUTEX_NAME)
    return (k.GetLastError() != 183), h   # 183 = ERROR_ALREADY_EXISTS


# ------------------------------------------------------------ win32 helpers --
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


def foreground_exe():
    try:
        hwnd = user32.GetForegroundWindow()
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if not h:
            return ''
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value)
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        pass
    return ''


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint)]


def idle_seconds():
    try:
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(lii)
        if not user32.GetLastInputInfo(ctypes.byref(lii)):
            return 0.0
        delta = (kernel32.GetTickCount() - lii.dwTime) & 0x7FFFFFFF
        return delta / 1000.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------- powercfg --
def get_schemes():
    """Return {scheme name: guid} from `powercfg /list`."""
    out = {}
    try:
        r = subprocess.run(['powercfg', '/list'], capture_output=True, text=True, timeout=15)
        for line in r.stdout.splitlines():
            m = re.search(r'([0-9a-fA-F-]{36})\s+\((.+?)\)', line)
            if m:
                out[m.group(2).strip()] = m.group(1)
    except Exception as e:
        log('get_schemes failed: %s' % e, 'WARN')
    return out


def active_scheme():
    try:
        r = subprocess.run(['powercfg', '/getactivescheme'], capture_output=True, text=True, timeout=15)
        m = re.search(r'([0-9a-fA-F-]{36})\s+\((.+?)\)', r.stdout)
        if m:
            return m.group(2).strip(), m.group(1)
    except Exception:
        pass
    return '?', ''


def set_scheme(name):
    g = get_schemes().get(name)
    if not g:
        log('scheme not found: %s' % name, 'WARN')
        return False
    r = subprocess.run(['powercfg', '/setactive', g], capture_output=True, text=True, timeout=20)
    if r.returncode == 0:
        return True
    log('setactive failed (admin rights?): %s' % (r.stderr.strip() or r.stdout.strip()), 'WARN')
    return False


# -------------------------------------------------------------------- ADLX --
try:
    from rm_sdk import RmSdk          # optional: AMD Ryzen Master Monitoring SDK
except Exception:
    RmSdk = None


class Adlx:
    def __init__(self):
        self.helper = None
        self.system = None
        self.gpu = None
        self.gpu_name = '?'
        self.pm = None
        self.s3 = None
        self.available = False

    def start(self):
        try:
            from adlx import ADLX
            h = ADLX.ADLXHelper()
            ret = h.Initialize()
            if 'ADLX_OK' not in str(ret):
                raise RuntimeError('Initialize -> %s' % ret)
            self.helper = h
            self.system = h.GetSystemServices()
            gpus = self.system.GetGPUs()
            if not gpus:
                raise RuntimeError('no GPUs reported')
            self.gpu = gpus[0]
            self.gpu_name = self.gpu.Name()
            self.pm = self.system.GetPerformanceMonitoringServices()
            self.s3 = self.system.Get3DSettingsServices()
            self.available = True
            _KEEPALIVE.append(self)
            log('ADLX ready | GPU: %s | v%s' % (self.gpu_name, h.QueryVersion()))
            return True
        except Exception as e:
            log('ADLX unavailable: %s' % e, 'WARN')
            self.available = False
            return False

    def metrics(self):
        if not self.available:
            return None
        try:
            m = self.pm.GetCurrentGPUMetrics(self.gpu)
            f = self.pm.GetCurrentFPS()
            return {
                'clock': m.GPUClockSpeed(),
                'usage': m.GPUUsage(),
                'temp': m.GPUTemperature(),
                'power': m.GPUPower(),
                'vram': m.GPUVRAM(),
                'fps': (f.FPS() if f else None),
            }
        except Exception as e:
            log('metrics read failed: %s' % e, 'WARN')
            return None

    def chill_frtc_states(self):
        out = {}
        if not self.available:
            return out
        try:
            ch = self.s3.GetChill(self.gpu)
            out['chill'] = {'supported': ch.IsSupported(), 'enabled': ch.IsEnabled(),
                            'min': ch.GetMinFPS(), 'max': ch.GetMaxFPS(), 'range': ch.GetFPSRange()}
        except Exception as e:
            out['chill'] = {'error': str(e)}
        try:
            fr = self.s3.GetFrameRateTargetControl(self.gpu)
            out['frtc'] = {'supported': fr.IsSupported(), 'enabled': fr.IsEnabled(),
                           'fps': fr.GetFPS(), 'range': fr.GetFPSRange()}
        except Exception as e:
            out['frtc'] = {'error': str(e)}
        return out

    def apply_3d(self, prof):
        """Apply {'chill': {...}, 'frtc': {...}} to the GPU and read the result back."""
        if not self.available:
            return False, ['ADLX unavailable']
        notes = []
        okall = True
        try:
            ch = self.s3.GetChill(self.gpu)
            if ch and ch.IsSupported() and 'chill' in prof:
                want = prof['chill']
                if bool(ch.IsEnabled()) != bool(want['enabled']):
                    ch.SetEnabled(bool(want['enabled']))
                if want['enabled']:
                    lo, hi = ch.GetFPSRange()['minValue'], ch.GetFPSRange()['maxValue']
                    ch.SetMinFPS(max(lo, min(int(want['min']), hi)))
                    ch.SetMaxFPS(max(lo, min(int(want['max']), hi)))
                st = ch.IsEnabled()
                notes.append('Chill=%s %s-%s (readback %s)' % (want['enabled'], want.get('min'), want.get('max'), st))
                if bool(st) != bool(want['enabled']):
                    okall = False
        except Exception as e:
            okall = False
            notes.append('Chill error: %s' % e)
        try:
            fr = self.s3.GetFrameRateTargetControl(self.gpu)
            if fr and fr.IsSupported() and 'frtc' in prof:
                want = prof['frtc']
                if bool(fr.IsEnabled()) != bool(want['enabled']):
                    fr.SetEnabled(bool(want['enabled']))
                if want['enabled']:
                    lo, hi = fr.GetFPSRange()['minValue'], fr.GetFPSRange()['maxValue']
                    fr.SetFPS(max(lo, min(int(want['fps']), hi)))
                st = fr.IsEnabled()
                notes.append('FRTC=%s %s (readback %s)' % (want['enabled'], want.get('fps'), st))
                if bool(st) != bool(want['enabled']):
                    okall = False
        except Exception as e:
            okall = False
            notes.append('FRTC error: %s' % e)
        # Driver-global posture (v0.3): sharpening / anti-lag / enhanced sync.
        # RSR intentionally excluded — unsupported on this iGPU (see probe).
        # Each key optional in profile; absent = leave untouched.
        for key, getter in (('sharpening', 'GetImageSharpening'),
                            ('antilag', 'GetAntiLag'),
                            ('enhanced_sync', 'GetEnhancedSync')):
            if key not in prof:
                continue
            try:
                o = getattr(self.s3, getter)(self.gpu)
                if not o or not o.IsSupported():
                    notes.append('%s not supported, skipped' % key)
                    continue
                want = prof[key]
                if bool(o.IsEnabled()) != bool(want['enabled']):
                    o.SetEnabled(bool(want['enabled']))
                if want['enabled'] and 'sharpness' in want and hasattr(o, 'SetSharpness'):
                    lo = o.GetSharpnessRange()['minValue']
                    hi = o.GetSharpnessRange()['maxValue']
                    step = o.GetSharpnessRange().get('step', 1) or 1
                    v = max(lo, min(int(want['sharpness']), hi))
                    v = lo + round((v - lo) / step) * step
                    o.SetSharpness(v)
                st = o.IsEnabled()
                detail = ''
                if want['enabled'] and hasattr(o, 'GetSharpness'):
                    try:
                        detail = ' sharpness=%s' % o.GetSharpness()
                    except Exception:
                        pass
                notes.append('%s=%s%s (readback %s)' % (key, want['enabled'], detail, st))
                if bool(st) != bool(want['enabled']):
                    okall = False
            except Exception as e:
                okall = False
                notes.append('%s error: %s' % (key, e))
        return okall, notes

    def stop(self):
        # No Terminate() and no ref clearing: releasing ADLX objects here segfaults
        # the pybind11 layer. The process exits via os._exit() in main(), which
        # skips teardown entirely and lets the OS reclaim everything.
        pass


# ------------------------------------------------------------------ POLICY --
def match_list(exe, patterns):
    e = (exe or '').lower()
    return any(fnmatch.fnmatch(e, p.lower()) for p in patterns)


def read_override(cfg):
    try:
        with open(OVERRIDE_PATH, encoding='utf-8') as f:
            v = f.read().strip().lower()
        if v in cfg['profiles']:
            return v
    except Exception:
        pass
    return None


def decide(cfg, fg, idle_s):
    ov = read_override(cfg)
    if ov:
        return ov, 'override'
    rules = cfg['rules']
    if match_list(fg, rules.get('games', [])):
        return rules.get('game_profile', 'performance'), 'game: %s' % fg
    if match_list(fg, rules.get('calls', [])):
        return rules.get('call_profile', 'eco'), 'call: %s' % fg
    idle_min = cfg.get('idle_to_eco_minutes', 0)
    if idle_min and idle_s > idle_min * 60:
        return rules.get('idle_profile', 'eco'), 'idle %.0f min' % (idle_s / 60)
    return rules.get('default_profile', 'balanced'), 'default'


def apply_profile(cfg, adlx, name, reason, state):
    prof = cfg['profiles'].get(name)
    if not prof:
        log('unknown profile: %s' % name, 'WARN')
        return False
    log('SWITCH -> %s (%s)' % (name, reason))
    ok_scheme = set_scheme(prof['scheme'])
    ok_gpu, notes = adlx.apply_3d(prof)
    for n in notes:
        log('   gpu: %s' % n)
    ok_os = False
    if name == 'stock':
        ok_os = os_release(state)   # stock also undoes every OS tweak
    else:
        ok_os = apply_os_tweaks(cfg, adlx, name, reason, state)
    state['profile'] = name
    state['reason'] = reason
    state['scheme_ok'] = ok_scheme
    state['gpu_ok'] = ok_gpu
    state['os_ok'] = bool(ok_os)
    save_state(state)
    return ok_scheme or ok_gpu or ok_os


# ------------------------------------------------- OS-level focus tuning --
# Generic layer: whatever is in the foreground gets a fair shot at the CPU
# and GPU. No per-app lists: the engine boosts the focused process and calms
# the loudest background ones, purely by measured CPU share. Works for
# browsers, editors, renderers and games alike.
PROCESS_QUERY_LIMITED = 0x1000
PROCESS_SET_INFO = 0x0200
ABOVE_NORMAL = 0x8000
BELOW_NORMAL = 0x4000
ECO_QOS_LEVEL = 1  # PROCESS_POWER_THROTTLING_EXECUTION_SPEED


class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [('Version', ctypes.c_uint),
                ('ControlMask', ctypes.c_uint),
                ('StateMask', ctypes.c_uint)]


def _fg_pid():
    try:
        hwnd = user32.GetForegroundWindow()
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value or None
    except Exception:
        return None


def _open(pid, access):
    try:
        h = kernel32.OpenProcess(access, False, pid)
        return h or None
    except Exception:
        return None


def set_efficiency_mode(pid, enable):
    """Toggle Windows efficiency (EcoQoS) mode for a process. Best effort."""
    h = _open(pid, PROCESS_QUERY_LIMITED | PROCESS_SET_INFO)
    if not h:
        return False
    try:
        st = PROCESS_POWER_THROTTLING_STATE()
        st.Version = 1
        st.ControlMask = ECO_QOS_LEVEL
        st.StateMask = ECO_QOS_LEVEL if enable else 0
        return bool(kernel32.SetProcessInformation(h, 4, ctypes.byref(st), ctypes.sizeof(st)))
    except Exception:
        return False
    finally:
        kernel32.CloseHandle(h)


def set_priority(pid, cls):
    h = _open(pid, PROCESS_QUERY_LIMITED | PROCESS_SET_INFO)
    if not h:
        return False
    try:
        return bool(kernel32.SetPriorityClass(h, cls))
    except Exception:
        return False
    finally:
        kernel32.CloseHandle(h)


# Processes the background calmer must never touch (system-critical, no .exe).
CALM_DENYLIST = frozenset([
    'smss', 'csrss', 'wininit', 'services', 'lsass',
    'winlogon', 'fontdrvhost', 'dwm', 'sihost',
    'taskhostw', 'ctfmon', 'audiodg', 'conhost',
    'svchost', 'searchindexer', 'searchhost',
    'shellexperiencehost', 'startmenuexperiencehost',
    'applicationframehost', 'runtimebroker', 'spoolsv',
    'systemsettings', 'crossdeviceresume',
])

_CALM_CACHE = {'tasklist': None, 'ts': 0.0}


def _tasklist_map(max_age=30.0):
    """Process name (lowercased, no .exe, trailing #N stripped) -> [pids].

    Keys match perf-counter instance names (which carry no .exe suffix).
    One `tasklist` call, cached for max_age seconds, so the calmer never
    spawns a subprocess per candidate name."""
    now = time.time()
    if _CALM_CACHE['tasklist'] is not None and now - _CALM_CACHE['ts'] < max_age:
        return _CALM_CACHE['tasklist']
    m = {}
    try:
        q = subprocess.run(['tasklist', '/fo', 'csv', '/nh'],
                           capture_output=True, text=True, timeout=10)
        for line in q.stdout.splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2:
                try:
                    pid = int(parts[1])
                except ValueError:
                    continue
                base = re.sub(r'\.exe$', '', parts[0].lower())
                m.setdefault(base, []).append(pid)
    except Exception as e:
        log('tasklist map failed: %s' % e, 'WARN')
    _CALM_CACHE['tasklist'] = m
    _CALM_CACHE['ts'] = now
    return m


def top_cpu_pids(exclude=(), limit=3, budget=8.0, skip_names=()):
    """PIDs with the highest recent CPU share.

    Bounded by design: one cached tasklist + one counter sample, system
    PIDs (0/4) and CALM_DENYLIST names skipped, bails out past budget."""
    t0 = time.time()
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-Command',
                            "Get-Counter '\\Process(*)\\% Processor Time' -SampleInterval 1 -MaxSamples 2 | "
                            "Select-Object -Expand CounterSamples | Group-Object InstanceName | "
                            "ForEach-Object { $s = $_.Group.CookedValue; if ($s.Count -gt 1) { $d = $s[1]-$s[0]; "
                            "if ($d -gt 0) { [pscustomobject]@{N=$_.Name; C=$d} } } } | "
                            "Sort-Object C -Descending | Select-Object -First 12 | "
                            "ForEach-Object { $_.N }"],
                           capture_output=True, text=True, timeout=10)
        names = [l.strip().lower() for l in r.stdout.splitlines() if l.strip()]
    except Exception as e:
        log('top_cpu_pids counter failed: %s' % e, 'WARN')
        return []
    if time.time() - t0 > budget:
        log('top_cpu_pids over budget, skipping', 'WARN')
        return []
    skip = set(s.lower() for s in skip_names)
    pidmap = _tasklist_map()
    me = os.getpid()
    out = []
    for n in names:
        base = re.sub(r'#\d+$', '', n)
        if base in ('idle', '_total', 'system') or base in CALM_DENYLIST or base in skip:
            continue
        for pid in pidmap.get(base, []):
            if pid in (0, 4, me) or pid in exclude:
                continue
            out.append(pid)
            break
        if len(out) >= limit or time.time() - t0 > budget:
            break
    return out


def _pid_alive(pid):
    if not pid:
        return False
    h = _open(pid, PROCESS_QUERY_LIMITED)
    if not h:
        return False
    kernel32.CloseHandle(h)
    return True


def apply_os_tweaks(cfg, adlx, profile_name, reason, state):
    """Focus boost + background calm + optional debounce. Returns True if ran."""
    oscfg = cfg.get('os_tuning', {})
    if not oscfg.get('enabled', True):
        return False
    fg_pid = _fg_pid()
    boosted = state.get('os_boosted_pid')
    # Watchdog (v0.5 item, cheap so shipped early): a boosted PID that no
    # longer exists is stale state — clear it so restore never targets a
    # recycled PID.
    if boosted and boosted != fg_pid and not _pid_alive(boosted):
        log('   os: stale boosted pid %s cleared' % boosted)
        boosted = None
        state['os_boosted_pid'] = None
    # Debounce: only touch priorities when the foreground app actually changed.
    if oscfg.get('debounce', True) and fg_pid == boosted:
        return True
    notes = []
    if fg_pid and oscfg.get('boost_foreground', True):
        if set_priority(fg_pid, ABOVE_NORMAL):
            notes.append('fg pid %s -> above-normal' % fg_pid)
        else:
            notes.append('fg pid %s boost failed' % fg_pid)
        if boosted and boosted != fg_pid and oscfg.get('restore_previous', True):
            if _pid_alive(boosted):
                set_priority(boosted, 0x20)  # NORMAL_PRIORITY_CLASS
            else:
                notes.append('prev pid %s already gone, skip restore' % boosted)
    if oscfg.get('calm_background', True):
        try:
            skip = set(oscfg.get('calm_exclude', []))
            bg = top_cpu_pids(exclude={fg_pid} if fg_pid else set(), limit=2,
                              budget=float(oscfg.get('calm_budget_seconds', 8.0)),
                              skip_names=skip)
            for pid in bg:
                if pid != boosted and set_efficiency_mode(pid, True):
                    notes.append('bg pid %s -> efficiency mode' % pid)
                    seen = state.setdefault('os_calm_pids', [])
                    if pid not in seen:
                        seen.append(pid)
                        del seen[:-8]   # keep the list bounded
        except Exception as e:
            notes.append('calm failed: %s' % e)
    state['os_boosted_pid'] = fg_pid
    for n in notes:
        log('   os: %s' % n)
    return True


def os_release(state):
    """Undo every OS tweak the engine made: priorities back to normal,
    efficiency mode off. Used by the stock profile and daemon shutdown."""
    notes = []
    boosted = state.get('os_boosted_pid')
    if boosted:
        if _pid_alive(boosted):
            set_priority(boosted, 0x20)   # NORMAL_PRIORITY_CLASS
            notes.append('pid %s priority -> normal' % boosted)
        state['os_boosted_pid'] = None
    for pid in list(state.get('os_calm_pids') or []):
        if _pid_alive(pid) and set_efficiency_mode(pid, False):
            notes.append('pid %s efficiency mode -> off' % pid)
    state['os_calm_pids'] = []
    save_state(state)
    for n in notes:
        log('   os-release: %s' % n)
    return True


# ------------------------------------------------------------------ MODES --
def cmd_status(cfg):
    st = read_state()
    name, guid = active_scheme()
    fg = foreground_exe()
    idle_s = idle_seconds()
    print('=== AMD XSS Engine v%s - status ===' % VERSION)
    print('Active scheme : %s [%s]' % (name, guid))
    print('Foreground    : %s' % (fg or '-'))
    print('Idle input    : %.0f s' % idle_s)
    print('Last state    : %s' % json.dumps(st, indent=2, ensure_ascii=False))
    a = Adlx()
    if a.start():
        print('GPU           : %s' % a.gpu_name)
        print('Metrics (x3):')
        for _ in range(3):
            m = a.metrics()
            if m:
                print('  clock=%sMHz usage=%s%% temp=%sC power=%sW fps=%s vram=%sMB' %
                      (m['clock'], m['usage'], m['temp'], m['power'], m['fps'], m['vram']))
            time.sleep(0.6)
        print('3D settings   : %s' % json.dumps(a.chill_frtc_states(), ensure_ascii=False))
        a.stop()
    rm = RmSdk() if RmSdk else None
    if rm and rm.start():
        print('CPU (RM SDK)  : %s' % rm.cpu_name)
        c = rm.metrics()
        if c:
            print('  PPT %.1f/%.1fW | TDC(VDD) %.1f/%.1fA | EDC(VDD) %.1f/%.1fA' % (
                c['ppt_w'], c['ppt_limit_w'], c['tdc_vdd_a'], c['tdc_vdd_limit_a'],
                c['edc_vdd_a'], c['edc_vdd_limit_a']))
            print('  VDDCR_VDD %.1fW | SOC %.1fW | VDD %.3fV | SoC %.3fV | %.1fC | Fmax %.0fMHz | FCLK %.0fMHz' % (
                c['vddcr_vdd_w'], c['vddcr_soc_w'], c['vdd_v'], c['soc_v'],
                c['temp_c'], c['fmax_mhz'], c['fclk_mhz']))
            effs = [x['eff_mhz'] for x in c['cores'] if x.get('eff_mhz')]
            c0s = [x['c0'] for x in c['cores'] if x.get('c0') is not None]
            tmax = [x['temp'] for x in c['cores'] if x.get('temp') is not None]
            print('  cores: avg eff %.0fMHz | avg C0 %.0f%% | max %.1fC (%d cores)' % (
                sum(effs) / max(1, len(effs)), sum(c0s) / max(1, len(c0s)),
                max(tmax) if tmax else 0, len(c['cores'])))
        rm.stop()
    return 0


def cmd_probe(cfg):
    a = Adlx()
    if not a.start():
        return 1
    ts = a.system.GetGPUTuningServices()
    print('=== ADLX support matrix ===')
    for m in ['IsAtFactory', 'IsSupportedAutoTuning', 'IsSupportedPresetTuning',
              'IsSupportedManualPowerTuning', 'IsSupportedManualGFXTuning',
              'IsSupportedManualVRAMTuning', 'IsSupportedManualFanTuning']:
        try:
            print('  %-32s %s' % (m, getattr(ts, m)(a.gpu)))
        except Exception as e:
            print('  %-32s ERR %s' % (m, e))
    print(json.dumps(a.chill_frtc_states(), indent=2, ensure_ascii=False))
    a.stop()
    return 0


def cmd_set(cfg, profile):
    a = Adlx()
    a.start()
    state = read_state()
    ok = apply_profile(cfg, a, profile, 'manual set', state)
    a.stop()
    print('set %s -> %s' % (profile, 'OK' if ok else 'FAILED (check admin rights and logs)'))
    return 0 if ok else 2


def cmd_telemetry(cfg, seconds=20):
    a = Adlx()
    a.start()
    ensure_dirs()
    new = not os.path.exists(TELEMETRY_CSV)
    prof_now = read_state().get('profile', '')
    rm = RmSdk() if RmSdk else None
    if rm and not rm.start():
        rm = None
    with open(TELEMETRY_CSV, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        if new:
            w.writerow(TELEMETRY_HEADER)
        t0 = time.time()
        while time.time() - t0 < seconds:
            m = a.metrics() or {}
            c = (rm.metrics() if rm else None) or {}
            effs = [x['eff_mhz'] for x in c.get('cores', []) if x.get('eff_mhz')]
            eff_avg = round(sum(effs) / len(effs), 1) if effs else ''
            row = [dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), foreground_exe(),
                   '%.0f' % idle_seconds(), prof_now, '', '', '', '',
                   m.get('clock'), m.get('usage'), m.get('temp'), m.get('power'), m.get('fps'),
                   c.get('ppt_w'), c.get('tdc_vdd_a'), c.get('edc_vdd_a'),
                   c.get('vddcr_vdd_w'), c.get('temp_c'), eff_avg]
            w.writerow(row)
            f.flush()
            print(' | '.join(str(x) for x in row))
            time.sleep(2)
    a.stop()
    if rm:
        rm.stop()
    print('CSV: %s' % TELEMETRY_CSV)
    return 0


def cmd_daemon(cfg, max_seconds=0):
    ok, _mutex = single_instance()
    if not ok:
        log('daemon already running (mutex held); exiting.', 'WARN')
        return 1
    ensure_dirs()
    log('=== AMD XSS Engine v%s daemon start (poll %ss) ===' % (VERSION, cfg.get('poll_seconds', 5)))
    a = Adlx()
    a.start()
    rm = RmSdk() if RmSdk else None
    if rm:
        if rm.start():
            log('RM SDK ready | CPU: %s' % rm.cpu_name)
        else:
            log('RM SDK unavailable: %s' % rm.last_error, 'WARN')
            rm = None
    state = read_state()
    state.update({'engine': VERSION, 'pid': os.getpid(), 'started': dt.datetime.now().isoformat(timespec='seconds')})
    current = state.get('profile')
    if current not in cfg['profiles']:
        current = cfg['rules'].get('default_profile', 'balanced')
        apply_profile(cfg, a, current, 'startup', state)
    else:
        save_state(state)
    new = not os.path.exists(TELEMETRY_CSV)
    t0 = time.time()
    last_verify = time.time()
    last_switch = 0.0
    try:
        while True:
            if max_seconds and (time.time() - t0) >= max_seconds:
                break
            fg = foreground_exe()
            idle_s = idle_seconds()
            want, reason = decide(cfg, fg, idle_s)
            if want != current:
                # Dwell guard: automatic switches are rate-limited so the log
                # never flaps. Manual override always wins immediately.
                now_ts = time.time()
                dwell = float(cfg.get('min_switch_interval_seconds', 30))
                if read_override(cfg) or (now_ts - last_switch) >= dwell:
                    apply_profile(cfg, a, want, reason, state)
                    current = want
                    last_switch = now_ts
            elif time.time() - last_verify > 1800:   # re-apply every 30 min against drift
                apply_profile(cfg, a, want, 're-verify', state)
                last_verify = time.time()
            else:
                # OS focus layer runs every poll (cheap: debounced internally,
                # only touches priorities when the foreground app changed).
                try:
                    prev_boosted = state.get('os_boosted_pid')
                    os_ok = apply_os_tweaks(cfg, a, want, 'poll', state)
                    state['os_ok'] = bool(os_ok)
                    if state.get('os_boosted_pid') != prev_boosted:
                        save_state(state)
                except Exception as e:
                    log('os tweaks poll failed: %s' % e, 'WARN')
            m = a.metrics() or {}
            c = (rm.metrics() if rm else None) or {}
            effs = [x['eff_mhz'] for x in c.get('cores', []) if x.get('eff_mhz')]
            eff_avg = round(sum(effs) / len(effs), 1) if effs else ''
            with open(TELEMETRY_CSV, 'a', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                if new:
                    # Old rows (pre-v0.2) have 9 columns; mark the break so the
                    # CSV stays parseable, then continue with the full header.
                    if os.path.getsize(TELEMETRY_CSV) > 0:
                        w.writerow(['--- schema v3 below: ' + ','.join(TELEMETRY_HEADER) + ' ---'])
                    else:
                        w.writerow(TELEMETRY_HEADER)
                    new = False
                w.writerow([dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), fg, '%.0f' % idle_s,
                            current, state.get('os_boosted_pid', ''), state.get('scheme_ok', ''),
                            state.get('gpu_ok', ''), state.get('os_ok', ''),
                            m.get('clock'), m.get('usage'), m.get('temp'),
                            m.get('power'), m.get('fps'),
                            c.get('ppt_w'), c.get('tdc_vdd_a'), c.get('edc_vdd_a'),
                            c.get('vddcr_vdd_w'), c.get('temp_c'), eff_avg])
            time.sleep(max(2, int(cfg.get('poll_seconds', 5))))
    except KeyboardInterrupt:
        log('daemon stopped (Ctrl+C)')
    finally:
        try:
            os_release(state)   # leave the OS as we found it
        except Exception:
            pass
        a.stop()
    log('daemon exit')
    return 0


def cmd_stats(cfg, hours=24):
    """Switch rate + profile share + power/thermal averages from the CSV.
    This is the soak-test instrument: the budget is <= 10 switches/hour."""
    if not os.path.exists(TELEMETRY_CSV):
        print('no telemetry yet')
        return 1
    cutoff = dt.datetime.now() - dt.timedelta(hours=hours)
    rows = []
    with open(TELEMETRY_CSV, newline='', encoding='utf-8') as f:
        for rec in csv.reader(f):
            if len(rec) < 12 or rec[0].startswith('---') or rec[0] == 'ts':
                continue
            try:
                ts = dt.datetime.strptime(rec[0], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                continue
            if ts >= cutoff:
                rows.append(rec)
    if not rows:
        print('no rows in the last %d h' % hours)
        return 0

    def col(i):
        out = []
        for r in rows:
            if len(r) > i and r[i] not in ('', 'None'):
                try:
                    out.append(float(r[i]))
                except ValueError:
                    pass
        return out

    profiles = {}
    switches = 0
    prev = None
    for r in rows:
        p = r[3] if len(r) > 3 else '?'
        profiles[p] = profiles.get(p, 0) + 1
        if prev is not None and p != prev:
            switches += 1
        prev = p
    span_h = max((dt.datetime.strptime(rows[-1][0], '%Y-%m-%d %H:%M:%S') -
                  dt.datetime.strptime(rows[0][0], '%Y-%m-%d %H:%M:%S')).total_seconds() / 3600.0, 0.01)
    ppt, cput, eff = col(13), col(17), col(18)
    gpup, gput = col(11), col(10)
    print('=== AMD XSS Engine stats (last %dh) ===' % hours)
    print('rows %d | span %.1f h | %s .. %s' % (len(rows), span_h, rows[0][0], rows[-1][0]))
    print('switches: %d (%.1f/h)   budget <= 10/h' % (switches, switches / span_h))
    print('profiles: ' + ' | '.join(
        '%s %.0f%%' % (p, 100.0 * n / len(rows)) for p, n in sorted(profiles.items(), key=lambda x: -x[1])))
    if ppt:
        print('CPU   : PPT avg %.1fW max %.1fW | temp avg %.1fC max %.1fC | eff avg %.0fMHz' % (
            sum(ppt) / len(ppt), max(ppt),
            sum(cput) / len(cput) if cput else 0, max(cput) if cput else 0,
            sum(eff) / len(eff) if eff else 0))
    if gpup:
        print('GPU   : power avg %.1fW | temp avg %.1fC' % (
            sum(gpup) / len(gpup), sum(gput) / len(gput) if gput else 0))
    return 0


def _run():
    cfg = load_cfg()
    args = sys.argv[1:]
    mode = args[0].lower() if args else 'status'
    if mode == 'status':
        return cmd_status(cfg)
    if mode == 'probe':
        return cmd_probe(cfg)
    if mode == 'telemetry':
        secs = int(args[1]) if len(args) > 1 else 20
        return cmd_telemetry(cfg, secs)
    if mode == 'stats':
        hrs = int(args[1]) if len(args) > 1 and args[1].isdigit() else 24
        return cmd_stats(cfg, hrs)
    if mode == 'set':
        if len(args) < 2 or args[1].lower() not in cfg['profiles']:
            print('usage: XssEngine.py set <%s>' % '|'.join(cfg['profiles']))
            return 2
        return cmd_set(cfg, args[1].lower())
    if mode in ('daemon', 'run'):
        maxsec = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
        return cmd_daemon(cfg, maxsec)
    print('AMD XSS Engine v%s\n'
          '  status             show active scheme, GPU/CPU and last state\n'
          '  probe              ADLX support matrix\n'
          '  telemetry [secs]   sample metrics into logs/xss-telemetry.csv\n'
          '  stats [hours]      switch rate + power/thermal summary (default 24)\n'
          '  set <profile>      %s\n'
          '  daemon [secs]      policy loop (started by the logon task)\n' %
          (VERSION, '|'.join(cfg['profiles'])))
    return 0


def main():
    # Line-buffer stdout so output survives the os._exit() below even when piped.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    ret = 0
    try:
        ret = _run()
    except SystemExit as e:
        ret = e.code if isinstance(e.code, int) else 0
    except Exception as e:
        log('fatal: %s' % e, 'ERROR')
        ret = 1
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(ret if isinstance(ret, int) else 0)


if __name__ == '__main__':
    main()
