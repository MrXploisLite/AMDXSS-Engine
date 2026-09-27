"""AMD Ryzen Master Monitoring SDK bridge (optional module).

Reads CPU power telemetry (PPT/TDC/EDC, voltages, temperature, per-core
frequency) through AMD's official monitoring SDK (Platform.dll + Device.dll
talking to the AMDRyzenMasterDriverV32 kernel service).

Layout of the C++ vtables follows the official SDK headers
(include/IPlatform.h, IDeviceManager.h, IDevice.h, ICPUEx.h). The ICPUEx
vtable has an ambiguity of one slot (virtual destructor placement), so the
first call calibrates which layout this build uses - safely, by passing a
scratch buffer that both candidate functions accept.

Usage rule from AMD docs: call GetCPUParameters at most once per second.

This module is optional for the engine: if the SDK is not installed it
reports available=False and the engine continues without CPU columns.
"""

import ctypes
import os
import struct
import winreg

k32 = ctypes.windll.kernel32
k32.LoadLibraryExW.restype = ctypes.c_void_p
k32.LoadLibraryExW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32]

LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR = 0x00000100
LOAD_WITH_ALTERED_SEARCH_PATH = 0x00000008

SDK_REGISTRY = r'Software\AMD\RyzenMasterMonitoringSDK'
SDK_FALLBACK_DIRS = [
    r'C:\Program Files\AMD\RyzenMasterMonitoringSDK',
    r'C:\Program Files (x86)\AMD\RyzenMasterMonitoringSDK',
]

# vtable slot maps for the two possible MSVC destructor placements.
# base IDevice: Init, UnInit, GetName, GetDescription, GetVendor, GetRole,
# GetClassName, GetType, GetIndex  (0..8)  [dtor slot(s)]  then ICPUEx adds:
# GetL1DataCache, GetL1InstructionCache, GetL2Cache, GetL3Cache,
# GetCoreCount, GetCorePark, GetPackage, GetCPUParameters, GetChipsetName,
# GetFamily, GetStepping, GetModel
_SLOTS_A = {'name': 2, 'core_count': 15, 'core_park': 16, 'package': 17,
            'cpu_params': 18}   # two destructor slots
_SLOTS_B = {'name': 2, 'core_count': 14, 'core_park': 15, 'package': 16,
            'cpu_params': 17}   # one destructor slot


class OCMode(ctypes.Union):
    _fields_ = [('uOCMode', ctypes.c_uint32)]


class EffectiveFreqData(ctypes.Structure):
    _fields_ = [('uLength', ctypes.c_uint32),
                ('dFreq', ctypes.POINTER(ctypes.c_double)),
                ('dState', ctypes.POINTER(ctypes.c_double)),
                ('dCurrentFreq', ctypes.POINTER(ctypes.c_double)),
                ('dCurrentTemp', ctypes.POINTER(ctypes.c_double))]


class CPUParameters(ctypes.Structure):
    _fields_ = [
        ('eMode', OCMode),
        ('stFreqData', EffectiveFreqData),
        ('dPeakCoreVoltage', ctypes.c_double),
        ('dPeakCoreVoltage_1', ctypes.c_double),
        ('dSocVoltage', ctypes.c_double),
        ('dTemperature', ctypes.c_double),
        ('dAvgCoreVoltage', ctypes.c_double),
        ('dAvgCoreVoltage_1', ctypes.c_double),
        ('dPeakSpeed', ctypes.c_double),
        ('fPPTLimit', ctypes.c_float),
        ('fPPTValue', ctypes.c_float),
        ('fTDCLimit_VDD', ctypes.c_float),
        ('fTDCValue_VDD', ctypes.c_float),
        ('fTDCValue_VDD_1', ctypes.c_float),
        ('fEDCLimit_VDD', ctypes.c_float),
        ('fEDCValue_VDD', ctypes.c_float),
        ('fEDCValue_VDD_1', ctypes.c_float),
        ('fcHTCLimit', ctypes.c_float),
        ('fFCLKP0Freq', ctypes.c_float),
        ('fCCLK_Fmax', ctypes.c_float),
        ('fTDCLimit_SOC', ctypes.c_float),
        ('fTDCValue_SOC', ctypes.c_float),
        ('fEDCLimit_SOC', ctypes.c_float),
        ('fEDCValue_SOC', ctypes.c_float),
        ('fVDDCR_VDD_Power', ctypes.c_float),
        ('fVDDCR_SOC_Power', ctypes.c_float),
        ('fTDCLimit_CCD', ctypes.c_float),
        ('fTDCValue_CCD', ctypes.c_float),
        ('fEDCLimit_CCD', ctypes.c_float),
        ('fEDCValue_CCD', ctypes.c_float),
    ]


def find_sdk_dir():
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, SDK_REGISTRY) as k:
            val, _ = winreg.QueryValueEx(k, 'InstallationPath')
            if val and os.path.isfile(os.path.join(val, 'bin', 'Platform.dll')):
                return val.rstrip('\\/')
    except OSError:
        pass
    env = os.environ.get('AMDRMMONITORSDKPATH', '')
    for d in [env] + SDK_FALLBACK_DIRS:
        if d and os.path.isfile(os.path.join(d, 'bin', 'Platform.dll')):
            return d.rstrip('\\/')
    return None


class RmSdk:
    """CPU power telemetry via the AMD Ryzen Master Monitoring SDK."""

    def __init__(self):
        self.available = False
        self.cpu_name = '?'
        self._slots = None
        self._cpu = None
        self._keepalive = []

    def start(self):
        self.last_error = ''
        sdk = find_sdk_dir()
        if not sdk:
            self.last_error = 'SDK not found'
            return False
        dll = os.path.join(sdk, 'bin', 'Platform.dll')
        try:
            bin_dir = os.path.join(sdk, 'bin')
            try:
                os.add_dll_directory(bin_dir)   # py3.8+: helps dependency resolution
            except (OSError, AttributeError):
                pass
            dll_mod = ctypes.WinDLL(dll, winmode=LOAD_WITH_ALTERED_SEARCH_PATH)
            get_platform = dll_mod.GetPlatform
            get_platform.restype = ctypes.c_void_p
            get_platform.argtypes = []
            plat = get_platform()
            if not plat:
                self.last_error = 'GetPlatform returned null'
                return False
            init = self._method(plat, 0, ctypes.c_bool, [ctypes.c_char_p, ctypes.c_bool])
            if not init(plat, None, False):
                self.last_error = 'IPlatform::Init failed (driver V32 running?)'
                return False
            mgr_fn = self._method(plat, 2, ctypes.c_void_p, [])
            mgr = mgr_fn(plat)
            if not mgr:
                self.last_error = 'GetIDeviceManager returned null'
                return False
            get_dev = self._method(mgr, 2, ctypes.c_void_p, [ctypes.c_int, ctypes.c_uint32])
            self._cpu = get_dev(mgr, 0, 0)          # dtCPU = 0
            if not self._cpu:
                self.last_error = 'GetDevice(dtCPU, 0) returned null'
                return False
            self._keepalive += [dll_mod, plat, mgr]
            self._slots = self._calibrate()
            name_fn = self._method(self._cpu, _SLOTS_A['name'], ctypes.c_wchar_p, [])
            self.cpu_name = name_fn(self._cpu) or '?'
            self.available = True
            return True
        except Exception as e:
            self.available = False
            self.last_error = repr(e)
            return False

    @staticmethod
    def _method(obj, idx, restype, argtypes):
        vptr = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vptr[idx])

    def _calibrate(self):
        """Determine this build's ICPUEx vtable layout with one safe probe.

        Slot 17 is GetPackage() (no args) in layout A and
        GetCPUParameters(CPUParameters&) in layout B. Passing a 4 KiB buffer
        as the second argument is harmless for both."""
        buf = ctypes.create_string_buffer(4096)
        fn = self._method(self._cpu, 17, ctypes.c_void_p, [ctypes.c_void_p])
        res = fn(self._cpu, ctypes.byref(buf))
        if res and 0x10000 < res < 0x00007FFFFFFFFFFF:
            try:
                s = ctypes.wstring_at(res, 8)
                if s.lower().startswith(('socket', 'fp', 'am', 'sp')):
                    return dict(_SLOTS_A)
            except Exception:
                pass
        # fall back to layout B; confirm the probe filled sane floats
        raw = struct.unpack_from('<f', buf, 104)[0]   # fPPTLimit in layout B
        if 50.0 <= raw <= 400.0:
            return dict(_SLOTS_B)
        return dict(_SLOTS_A)

    def metrics(self):
        """CPUParameters snapshot as a dict. At most once per second (AMD rule)."""
        if not self.available or not self._slots:
            return None
        try:
            st = CPUParameters()
            fn = self._method(self._cpu, self._slots['cpu_params'], ctypes.c_int,
                              [ctypes.c_void_p])
            ret = fn(self._cpu, ctypes.byref(st))
            if ret != 0:
                return None
            n = st.stFreqData.uLength
            cores = []
            for i in range(min(n, 16)):
                cores.append({
                    'mhz': st.stFreqData.dCurrentFreq[i] if st.stFreqData.dCurrentFreq else None,
                    'eff_mhz': st.stFreqData.dFreq[i] if st.stFreqData.dFreq else None,
                    'c0': st.stFreqData.dState[i] if st.stFreqData.dState else None,
                    'temp': st.stFreqData.dCurrentTemp[i] if st.stFreqData.dCurrentTemp else None,
                })
            return {
                'ppt_limit_w': st.fPPTLimit, 'ppt_w': st.fPPTValue,
                'edc_vdd_limit_a': st.fEDCLimit_VDD, 'edc_vdd_a': st.fEDCValue_VDD,
                'tdc_vdd_limit_a': st.fTDCLimit_VDD, 'tdc_vdd_a': st.fTDCValue_VDD,
                'edc_soc_limit_a': st.fEDCLimit_SOC, 'edc_soc_a': st.fEDCValue_SOC,
                'tdc_soc_limit_a': st.fTDCLimit_SOC, 'tdc_soc_a': st.fTDCValue_SOC,
                'vddcr_vdd_w': st.fVDDCR_VDD_Power, 'vddcr_soc_w': st.fVDDCR_SOC_Power,
                'vdd_v': st.dAvgCoreVoltage, 'soc_v': st.dSocVoltage,
                'temp_c': st.dTemperature, 'chtclimit_c': st.fcHTCLimit,
                'fmax_mhz': st.fCCLK_Fmax, 'peak_speed_mhz': st.dPeakSpeed,
                'fclk_mhz': st.fFCLKP0Freq, 'cores': cores,
            }
        except Exception:
            return None

    def stop(self):
        pass  # same teardown policy as ADLX: let os._exit() reclaim
