"""What this machine is, and what it is doing right now.

Two readings, with very different costs and very different lifetimes:

* **`static_info()`** -- the CPU, the memory, and every graphics adapter
  Windows knows about, plus spirula's own Vulkan verdict on them
  (`sam devices`, CLAUDE.md section 7.4). Read once per process and cached: a
  CPU does not change under a running server, and the one subprocess in here
  should not be paid per page load. That is `version.py`'s argument for the
  same shape.
* **`live_sample()`** -- CPU %, RAM, per-adapter GPU % and VRAM, at **1.4 ms a
  poll** (measured, below). Cheap enough to sit under a gauge that ticks while
  a 956-second training run goes.

**Nothing here is a new dependency**, which is CLAUDE.md section 2.1 and the
reason this module is 100 % ctypes and `winreg` rather than three lines of
`psutil`. Every number below was measured on this workstation on 2026-09-01,
and where a second source could confirm one, it was asked:

| Reading | Source | Cost | Cross-check |
|---|---|---|---|
| CPU % | `GetSystemTimes` (kernel32) | 0.26 ms | -- |
| RAM | `GlobalMemoryStatusEx` (kernel32) | 0.02 ms | -- |
| GPU % | PDH `\\GPU Engine(*)\\Utilization Percentage` | 1.4 ms | same magnitude as `nvidia-smi`, see below |
| VRAM | PDH `\\GPU Adapter Memory(*)\\Dedicated Usage` | (same poll) | `nvidia-smi` 955 MB against 960.4 MB |
| Adapter names | `HKLM\\SOFTWARE\\Microsoft\\DirectX` | 1.5 ms | -- |
| Driver versions | the display class key | 0.36 ms | -- |

**PDH is what makes this vendor-neutral, and that is the whole point of
choosing it over `nvidia-smi`.** This app exists because spirula is Vulkan and
runs on Intel, AMD and Apple silicon as readily as on NVIDIA (section 1); a
panel that could only draw a gauge for the NVIDIA card would contradict the
project on its own setup screen. PDH reports **both** GPUs in this machine --
the RTX 4060 and the UHD 770 -- through one counter and one code path, and
`nvidia-smi` is used here only as the thing that proved the numbers right.

**The VRAM cross-check is exact and the utilisation one deliberately is not.**
PDH's dedicated usage read 960.4 MB against `nvidia-smi`'s 955 MB for the same
adapter, which is the same number. Utilisation cannot be compared that way and
saying it matched would be the wrong claim: `nvidia-smi`'s `utilization.gpu` is
an **instantaneous** sample and PDH's is an **average over the interval between
two collections**, so three simultaneous readings answered 51/22/46 % against
25.9/20.4/27.9 %. They track the same load and disagree by construction. The
averaged number is the one a gauge wants -- an instantaneous sample under a
one-second bar is a strobe light.

Three findings that shape the code rather than merely decorate it:

1. **An integrated GPU's VRAM is *shared*, not dedicated.** The UHD 770 reports
   128 MB dedicated -- a stub -- against 12 142 MB of shared system memory,
   which is also what `spirula sam devices` calls its "11.9 G". So the gauge
   reads `Dedicated Usage / DedicatedVideoMemory` for a discrete part and
   `Shared Usage / SharedSystemMemory` for an integrated one, or the Intel card
   would show a full bar the moment anything drew a window.
2. **The DirectX registry carries stale adapters.** Six LUID keys here for
   three live adapters: a driver update writes a new key and leaves the old one
   behind. PDH reports the LUIDs that actually exist, so the live counters are
   the filter and the registry is only the name lookup.
3. **Utilisation is the *max* over engine types, never the sum.** The counter
   is per process *and* per engine (3D, Compute, Copy, VideoDecode), and adding
   them up passes 100 % on any machine doing two things at once. Summing per
   engine type and taking the largest is what Task Manager reports, and it is
   what agreed with `nvidia-smi` above.

On anything that is not Windows every entry point answers `available: false`
with the reason, and no caller has to care.
"""

from __future__ import annotations

import ctypes
import platform
import re
import subprocess
import sys
from collections import defaultdict
from typing import Any

IS_WINDOWS = sys.platform == "win32"

_MB = 1024 * 1024

# The display adapter class GUID -- where driver versions and dates live.
_DISPLAY_CLASS = (
    r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
)

# Adapters that exist so software has something to render into. They are real
# entries with real LUIDs and no hardware behind them, so they are reported and
# never gauged.
_SOFTWARE_ADAPTERS = ("basic render driver", "basic display adapter")


# ---------------------------------------------------------------------------
# ctypes plumbing
# ---------------------------------------------------------------------------

if IS_WINDOWS:
    import winreg
    from ctypes import wintypes as w

    class _FILETIME(ctypes.Structure):
        _fields_ = [("lo", w.DWORD), ("hi", w.DWORD)]

    class _MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", w.DWORD),
            ("dwMemoryLoad", w.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    # PDH_FMT_COUNTERVALUE_ITEM_W. The union is 8-byte aligned, so ctypes
    # inserts the pad after CStatus itself -- writing that pad by hand is what
    # made the first attempt at this segfault.
    class _PDH_VALUE(ctypes.Union):
        _fields_ = [
            ("longValue", ctypes.c_long),
            ("doubleValue", ctypes.c_double),
            ("largeValue", ctypes.c_longlong),
            ("AnsiStringValue", ctypes.c_char_p),
            ("WideStringValue", ctypes.c_wchar_p),
        ]

    class _PDH_COUNTERVALUE(ctypes.Structure):
        _fields_ = [("CStatus", w.DWORD), ("value", _PDH_VALUE)]

    class _PDH_ITEM(ctypes.Structure):
        _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _PDH_COUNTERVALUE)]

    _PDH_FMT_DOUBLE = 0x00000200
    _PDH_FMT_LARGE = 0x00000400
    _PDH_MORE_DATA = 0x800007D2


def _kernel32() -> Any:
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _filetime(value: Any) -> int:
    return (value.hi << 32) | value.lo


# ---------------------------------------------------------------------------
# Static: CPU
# ---------------------------------------------------------------------------


def _physical_cores() -> int | None:
    """Cores, not threads, via GetLogicalProcessorInformationEx.

    The buffer is a run of variable-length records; `RelationProcessorCore` is
    relationship 0 and each such record is one physical core. Measured here:
    8 cores behind 12 logical processors.
    """
    if not IS_WINDOWS:
        return None
    try:
        k32 = _kernel32()
        size = ctypes.c_ulong(0)
        k32.GetLogicalProcessorInformationEx(0, None, ctypes.byref(size))
        if not size.value:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if not k32.GetLogicalProcessorInformationEx(0, buf, ctypes.byref(size)):
            return None
        cores, offset = 0, 0
        while offset + 8 <= size.value:
            relationship = ctypes.c_ulong.from_buffer(buf, offset).value
            record = ctypes.c_ulong.from_buffer(buf, offset + 4).value
            if record == 0:  # malformed; stop rather than spin
                break
            if relationship == 0:
                cores += 1
            offset += record
        return cores or None
    except Exception:
        return None


def _cpu_static() -> dict[str, Any]:
    info: dict[str, Any] = {
        "name": platform.processor() or None,
        "architecture": platform.machine() or None,
        "physical_cores": _physical_cores(),
        "logical_cores": None,
        "base_clock_mhz": None,
        "identifier": None,
        "vendor": None,
    }
    if not IS_WINDOWS:
        return info
    try:
        root = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor"
        )
        info["logical_cores"] = winreg.QueryInfoKey(root)[0] or None
        cpu0 = winreg.OpenKey(root, "0")
        for key, field in (
            ("name", "ProcessorNameString"),
            ("identifier", "Identifier"),
            ("vendor", "VendorIdentifier"),
        ):
            try:
                info[key] = str(winreg.QueryValueEx(cpu0, field)[0]).strip() or info[key]
            except OSError:
                pass
        try:
            info["base_clock_mhz"] = int(winreg.QueryValueEx(cpu0, "~MHz")[0])
        except OSError:
            pass
    except OSError:
        pass
    return info


# ---------------------------------------------------------------------------
# Static: graphics adapters
# ---------------------------------------------------------------------------


def _driver_table() -> dict[str, dict[str, Any]]:
    """DriverDesc -> version / date / board memory, from the display class key.

    `HardwareInformation.qwMemorySize` is the board's own total -- 8188 MB for
    the RTX 4060 here, which is exactly what `nvidia-smi` calls `memory.total`,
    and 231 MB more than the DXGI budget the gauge divides by. Both are true
    about different things, so both are reported and the gauge says which.
    """
    out: dict[str, dict[str, Any]] = {}
    if not IS_WINDOWS:
        return out
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS)
    except OSError:
        return out
    for index in range(winreg.QueryInfoKey(root)[0]):
        try:
            name = winreg.EnumKey(root, index)
            if not name.isdigit():
                continue
            sub = winreg.OpenKey(root, name)
        except OSError:
            continue

        def value(field: str, key: Any = None) -> Any:
            try:
                return winreg.QueryValueEx(key, field)[0]
            except OSError:
                return None

        desc = value("DriverDesc", sub)
        if not desc:
            continue
        board = value("HardwareInformation.qwMemorySize", sub)
        out[str(desc).strip().lower()] = {
            "driver_version": value("DriverVersion", sub),
            "driver_date": value("DriverDate", sub),
            "board_memory_bytes": int(board) if isinstance(board, int) else None,
        }
    return out


def _adapters_static() -> list[dict[str, Any]]:
    """Every DXGI adapter, keyed by the LUID the PDH counters use.

    `HKLM\\SOFTWARE\\Microsoft\\DirectX` is the only place that pairs an
    adapter's LUID with its name, which is what lets a counter instance called
    `luid_0x00000000_0x00010E54` be drawn as "NVIDIA GeForce RTX 4060 Laptop
    GPU". It also accumulates stale keys -- six here for three live adapters --
    so `live` is set from what PDH actually reports and the gauges filter on
    that rather than on the registry.
    """
    if not IS_WINDOWS:
        return []
    drivers = _driver_table()
    live_luids = _pdh_live_luids()
    adapters: list[dict[str, Any]] = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\DirectX")
    except OSError:
        return []
    for index in range(winreg.QueryInfoKey(root)[0]):
        try:
            sub = winreg.OpenKey(root, winreg.EnumKey(root, index))
        except OSError:
            continue

        def value(field: str, key: Any = None) -> Any:
            try:
                return winreg.QueryValueEx(key, field)[0]
            except OSError:
                return None

        desc, luid = value("Description", sub), value("AdapterLuid", sub)
        if not desc or not isinstance(luid, int):
            continue
        desc = str(desc).strip()
        dedicated = value("DedicatedVideoMemory", sub) or 0
        shared = value("SharedSystemMemory", sub) or 0
        software = any(tag in desc.lower() for tag in _SOFTWARE_ADAPTERS)

        # An integrated part reports a stub of dedicated memory (128 MB on the
        # UHD 770) and does its real work in shared system memory, so which
        # pool the gauge divides by follows from this classification.
        if software:
            kind = "software"
        elif dedicated >= 512 * _MB:
            kind = "discrete"
        else:
            kind = "integrated"

        driver = drivers.get(desc.lower(), {})
        adapters.append(
            {
                "luid": luid,
                "luid_hex": f"0x{luid >> 32:08X}_0x{luid & 0xFFFFFFFF:08X}",
                "name": desc,
                "kind": kind,
                "dedicated_video_memory": int(dedicated),
                "shared_system_memory": int(shared),
                # Which pool this adapter's VRAM gauge reads, decided once here
                # so the route and the UI cannot disagree about it.
                "memory_pool": "shared" if kind == "integrated" else "dedicated",
                "memory_budget": int(shared if kind == "integrated" else dedicated),
                "live": luid in live_luids,
                "driver_version": driver.get("driver_version"),
                "driver_date": driver.get("driver_date"),
                "board_memory_bytes": driver.get("board_memory_bytes"),
            }
        )
    # Live adapters first, then the biggest -- the card that trains is the one
    # worth reading first.
    adapters.sort(key=lambda a: (not a["live"], -a["dedicated_video_memory"]))
    return adapters


# ---------------------------------------------------------------------------
# Static: spirula's own verdict
# ---------------------------------------------------------------------------

_DEVICE_ROW = re.compile(
    r"^\s*(\d+)\s+(.+?)\s{2,}(integrated|discrete|virtual|cpu|other)\s+"
    r"([\d.]+\s*[KMGT]?)\s+(\S+)\s*$",
    re.IGNORECASE,
)


def spirula_devices(spirula_path: str | None) -> dict[str, Any]:
    """`spirula sam devices` -- the Vulkan baseline, read off the tool itself.

    CLAUDE.md section 7.4: this is how the setup panel proves the GPU baseline,
    and section 1's whole claim ("runs on NVIDIA, AMD, Intel and Apple
    silicon") is this command's output. `--lang en` is pinned like every other
    invocation (section 7.0), or the header row comes back in French on this
    workstation and the parser matches nothing.

    `sam --help` exits 2 rather than 0 (section 7.4), so the return code is not
    the test here either -- the parsed rows are.
    """
    if not spirula_path:
        return {"available": False, "reason": "No spirula path configured.", "devices": []}
    try:
        proc = subprocess.run(
            [str(spirula_path), "--lang", "en", "sam", "devices"],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "reason": str(exc), "devices": []}

    devices = []
    for line in (proc.stdout or "").splitlines():
        match = _DEVICE_ROW.match(line)
        if not match:
            continue
        index, name, kind, vram, status = match.groups()
        devices.append(
            {
                "index": int(index),
                "name": name.strip(),
                "type": kind.lower(),
                "vram": vram.strip(),
                "status": status.strip(),
            }
        )
    return {
        "available": bool(devices),
        "reason": None if devices else "spirula listed no device.",
        "devices": devices,
    }


# ---------------------------------------------------------------------------
# Live: PDH
# ---------------------------------------------------------------------------

# `luid_0x00000000_0x00010E54_phys_0_eng_1_engtype_3D`, optionally with a
# `pid_1234_` in front -- the per-process instances the utilisation counter
# reports.
_ENGINE_INSTANCE = re.compile(r"luid_0x([0-9a-f]+)_0x([0-9a-f]+).*?_engtype_(\w+)", re.IGNORECASE)
_MEMORY_INSTANCE = re.compile(r"luid_0x([0-9a-f]+)_0x([0-9a-f]+)", re.IGNORECASE)


def _luid_of(match: re.Match[str]) -> int:
    return (int(match.group(1), 16) << 32) | int(match.group(2), 16)


class _PdhQuery:
    """One PDH query, opened once and collected per poll.

    A utilisation counter is a rate: it needs two collections to mean anything,
    and the interval between them is what it averages over. Opening a query per
    request would therefore force a `sleep` in the middle of every poll --
    500 ms of the request, for a worse number. Kept open, the previous poll
    *is* the first sample, so a poll costs the 1.4 ms measured above and
    averages over the polling interval, which is exactly the window the gauge
    draws.
    """

    def __init__(self) -> None:
        self.ok = False
        self.reason: str | None = None
        self._pdh: Any = None
        self._query: Any = None
        self._counters: dict[str, Any] = {}
        if not IS_WINDOWS:
            self.reason = "GPU counters are a Windows facility."
            return
        try:
            self._open()
            self.ok = True
        except OSError as exc:
            self.reason = str(exc)

    def _open(self) -> None:
        self._pdh = ctypes.WinDLL("pdh.dll")
        for fn in (
            "PdhOpenQueryW",
            "PdhAddEnglishCounterW",
            "PdhCollectQueryData",
            "PdhGetFormattedCounterArrayW",
            "PdhCloseQuery",
        ):
            getattr(self._pdh, fn).restype = ctypes.c_ulong

        query = w.HANDLE()
        if self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(query)):
            raise OSError("PdhOpenQueryW failed")
        self._query = query

        # English counter names, not localized ones -- the same reasoning as
        # `--lang en` on every spirula call (section 7.0). PdhAddEnglishCounterW
        # resolves the English path on a French Windows; PdhAddCounterW would
        # need the localized name here and match nothing.
        for key, path in (
            ("util", r"\GPU Engine(*)\Utilization Percentage"),
            ("dedicated", r"\GPU Adapter Memory(*)\Dedicated Usage"),
            ("shared", r"\GPU Adapter Memory(*)\Shared Usage"),
        ):
            counter = w.HANDLE()
            if not self._pdh.PdhAddEnglishCounterW(
                query, ctypes.c_wchar_p(path), 0, ctypes.byref(counter)
            ):
                self._counters[key] = counter
        if "util" not in self._counters:
            raise OSError("No GPU Engine performance counter on this machine.")
        self._pdh.PdhCollectQueryData(query)

    def _read(self, key: str, fmt: int) -> list[tuple[str, float]]:
        counter = self._counters.get(key)
        if counter is None:
            return []
        size, count = ctypes.c_ulong(0), ctypes.c_ulong(0)
        rc = self._pdh.PdhGetFormattedCounterArrayW(
            counter, fmt, ctypes.byref(size), ctypes.byref(count), None
        )
        if rc != _PDH_MORE_DATA or not size.value:
            return []
        buf = ctypes.create_string_buffer(size.value)
        if self._pdh.PdhGetFormattedCounterArrayW(
            counter, fmt, ctypes.byref(size), ctypes.byref(count), buf
        ):
            return []
        items = ctypes.cast(buf, ctypes.POINTER(_PDH_ITEM))
        out: list[tuple[str, float]] = []
        for index in range(count.value):
            item = items[index]
            if not item.szName:
                continue
            value = (
                item.FmtValue.value.doubleValue
                if fmt == _PDH_FMT_DOUBLE
                else float(item.FmtValue.value.largeValue)
            )
            out.append((item.szName, value))
        return out

    def sample(self) -> dict[int, dict[str, Any]]:
        """Per-LUID utilisation and memory, or {} when the query is not usable."""
        if not self.ok or self._pdh.PdhCollectQueryData(self._query):
            return {}

        # Sum each engine type across processes, then take the *largest* engine
        # type as the adapter's utilisation. Adding the engines together counts
        # a decode and a draw as 200 %.
        per_engine: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for name, value in self._read("util", _PDH_FMT_DOUBLE):
            match = _ENGINE_INSTANCE.search(name)
            if match:
                per_engine[_luid_of(match)][match.group(3)] += value

        memory: dict[int, dict[str, float]] = defaultdict(dict)
        for key in ("dedicated", "shared"):
            for name, value in self._read(key, _PDH_FMT_LARGE):
                match = _MEMORY_INSTANCE.search(name)
                if match:
                    memory[_luid_of(match)][key] = value

        out: dict[int, dict[str, Any]] = {}
        for luid in set(per_engine) | set(memory):
            engines = {k: round(v, 2) for k, v in per_engine.get(luid, {}).items() if v >= 0.01}
            out[luid] = {
                "utilization_pct": round(min(max(engines.values(), default=0.0), 100.0), 1),
                "engines": dict(sorted(engines.items(), key=lambda kv: -kv[1])),
                "dedicated_used": int(memory.get(luid, {}).get("dedicated", 0)),
                "shared_used": int(memory.get(luid, {}).get("shared", 0)),
            }
        return out

    def live_luids(self) -> set[int]:
        return set(self.sample())

    def close(self) -> None:
        if self.ok and self._query is not None:
            self._pdh.PdhCloseQuery(self._query)
            self.ok = False


_query: _PdhQuery | None = None


def _pdh() -> _PdhQuery:
    global _query
    if _query is None:
        _query = _PdhQuery()
    return _query


def _pdh_live_luids() -> set[int]:
    try:
        return _pdh().live_luids()
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# Live: CPU and RAM
# ---------------------------------------------------------------------------


class _CpuTicks:
    """CPU load between two calls, from the kernel's own idle/kernel/user tally.

    The first call has no previous sample to difference against and answers
    None rather than 0 -- a gauge that opens at "0 %" is stating something it
    does not know.
    """

    def __init__(self) -> None:
        self._previous: tuple[int, int, int] | None = None

    def _read(self) -> tuple[int, int, int] | None:
        if not IS_WINDOWS:
            return None
        idle, kernel, user = _FILETIME(), _FILETIME(), _FILETIME()
        if not _kernel32().GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        ):
            return None
        return _filetime(idle), _filetime(kernel), _filetime(user)

    def sample(self) -> float | None:
        now = self._read()
        if now is None:
            return None
        previous, self._previous = self._previous, now
        if previous is None:
            return None
        idle = now[0] - previous[0]
        # `kernel` already includes idle, so the total is kernel + user.
        total = (now[1] - previous[1]) + (now[2] - previous[2])
        if total <= 0:
            return None
        return round(max(0.0, min(100.0, 100.0 * (total - idle) / total)), 1)


_cpu_ticks = _CpuTicks()


def _memory() -> dict[str, Any]:
    empty = {
        "total": None,
        "available": None,
        "used": None,
        "load_pct": None,
        "commit_total": None,
        "commit_available": None,
    }
    if not IS_WINDOWS:
        return empty
    status = _MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(status)
    if not _kernel32().GlobalMemoryStatusEx(ctypes.byref(status)):
        return empty
    return {
        "total": int(status.ullTotalPhys),
        "available": int(status.ullAvailPhys),
        "used": int(status.ullTotalPhys - status.ullAvailPhys),
        "load_pct": float(status.dwMemoryLoad),
        "commit_total": int(status.ullTotalPageFile),
        "commit_available": int(status.ullAvailPageFile),
    }


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------

_static_cache: dict[str, Any] | None = None


def static_info(spirula_path: str | None = None, refresh: bool = False) -> dict[str, Any]:
    """Everything about this machine that a running server cannot see change."""
    global _static_cache
    if _static_cache is not None and not refresh:
        return _static_cache

    if not IS_WINDOWS:
        _static_cache = {
            "available": False,
            "reason": (
                f"Hardware readings are implemented for Windows; this is {sys.platform}."
            ),
            "platform": {"system": platform.system(), "release": platform.release()},
            "cpu": _cpu_static(),
            "memory": _memory(),
            "adapters": [],
            "spirula": spirula_devices(spirula_path),
            "gpu_counters": {"available": False, "reason": "Not Windows.", "source": None},
        }
        return _static_cache

    _static_cache = {
        "available": True,
        "reason": None,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "node": platform.node(),
            "python": platform.python_version(),
        },
        "cpu": _cpu_static(),
        "memory": _memory(),
        "adapters": _adapters_static(),
        "spirula": spirula_devices(spirula_path),
        "gpu_counters": {
            "available": _pdh().ok,
            "reason": _pdh().reason,
            "source": "PDH GPU Engine and GPU Adapter Memory",
        },
    }
    return _static_cache


def live_sample() -> dict[str, Any]:
    """CPU %, RAM and per-adapter GPU % / VRAM -- the gauge payload.

    Every field is nullable and the caller draws what it got: a machine with no
    GPU counters still has a CPU bar, and the very first poll has no CPU delta
    to report yet.
    """
    gpus: list[dict[str, Any]] = []
    if IS_WINDOWS:
        readings = _pdh().sample()
        for adapter in static_info().get("adapters", []):
            reading = readings.get(adapter["luid"])
            if reading is None or adapter["kind"] == "software":
                continue
            pool = adapter["memory_pool"]
            used = reading["dedicated_used" if pool == "dedicated" else "shared_used"]
            budget = adapter["memory_budget"] or 0
            gpus.append(
                {
                    "luid": adapter["luid"],
                    "name": adapter["name"],
                    "kind": adapter["kind"],
                    "utilization_pct": reading["utilization_pct"],
                    "engines": reading["engines"],
                    "memory_pool": pool,
                    "memory_used": used,
                    "memory_total": budget,
                    "memory_pct": round(100.0 * used / budget, 1) if budget else None,
                    "dedicated_used": reading["dedicated_used"],
                    "shared_used": reading["shared_used"],
                }
            )
        gpus.sort(key=lambda g: (g["kind"] != "discrete", -g["utilization_pct"]))

    return {
        "available": IS_WINDOWS,
        "cpu_pct": _cpu_ticks.sample(),
        "memory": _memory(),
        "gpus": gpus,
    }
