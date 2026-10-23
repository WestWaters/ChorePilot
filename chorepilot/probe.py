"""`chorepilot probe`: what is this machine, really? stdlib only, works on a Pi or in Termux.

Reports total + *currently available* RAM (nameplate RAM lies once the OS and everything
else take their cut), CPU, GPU/accelerator, free disk, LAN address and link type, and a
memory-bandwidth figure (from a table, or measured with --measure).
"""
from __future__ import annotations

import ctypes
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time

from .profiles import WIFI, Device


def _run(cmd, timeout=5):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _sysctl(key):
    return _run(["sysctl", "-n", key]).strip()


# ---------------------------------------------------------------- memory
def ram_total_gb() -> float:
    try:
        if sys.platform == "darwin":
            return int(_sysctl("hw.memsize")) / 1e9
        if sys.platform == "win32":
            m = _win_mem()
            return m[0] / 1e9 if m else 0.0
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
    except (ValueError, OSError):
        return 0.0


def _win_mem():
    class _MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = _MS()
    m.dwLength = ctypes.sizeof(_MS)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):  # type: ignore[attr-defined]
        return int(m.ullTotalPhys), int(m.ullAvailPhys)
    return None


def ram_available_gb() -> float:
    """RAM we could take right now without forcing the OS to swap. Used by the memory guard."""
    try:
        if sys.platform == "darwin":
            out = _run(["vm_stat"])
            page = 16384
            m = re.search(r"page size of (\d+) bytes", out)
            if m:
                page = int(m.group(1))
            stats = {}
            for line in out.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    v = v.strip().rstrip(".")
                    if v.isdigit():
                        stats[k.strip()] = int(v)
            free = (stats.get("Pages free", 0) + stats.get("Pages inactive", 0)
                    + stats.get("Pages speculative", 0) + stats.get("Pages purgeable", 0))
            return free * page / 1e9
        if sys.platform == "win32":
            m = _win_mem()
            return m[1] / 1e9 if m else 0.0
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024 / 1e9
    except (OSError, ValueError):
        pass
    return 0.0


# ---------------------------------------------------------------- cpu / gpu
def cpu_info():
    cores = os.cpu_count() or 1
    name = platform.processor() or platform.machine()
    if sys.platform == "darwin":
        name = _sysctl("machdep.cpu.brand_string") or name
        perf = _sysctl("hw.perflevel0.physicalcpu")
        if perf.isdigit():
            cores = int(perf)  # llama.cpp decodes best on performance cores only
    elif os.path.exists("/proc/cpuinfo"):
        txt = open("/proc/cpuinfo").read()
        m = re.search(r"model name\s*:\s*(.+)", txt) or re.search(r"Model\s*:\s*(.+)", txt) \
            or re.search(r"Hardware\s*:\s*(.+)", txt)
        if m:
            name = m.group(1).strip()
    return name, cores


# Apple unified-memory bandwidth, GB/s (base chip; Pro/Max/Ultra matched by name).
_APPLE_BW = [("M4 Max", 546), ("M4 Pro", 273), ("M4", 120), ("M3 Ultra", 819), ("M3 Max", 400),
             ("M3 Pro", 150), ("M3", 100), ("M2 Ultra", 800), ("M2 Max", 400), ("M2 Pro", 200),
             ("M2", 100), ("M1 Ultra", 800), ("M1 Max", 400), ("M1 Pro", 200), ("M1", 68)]


def gpu_info():
    """(kind, name, vram_gb, bw_gbs). kind '' means CPU-only."""
    if sys.platform == "darwin" and platform.machine() == "arm64":
        chip = _sysctl("machdep.cpu.brand_string")
        bw = next((b for n, b in _APPLE_BW if n in chip), 100)
        return "metal", chip, 0.0, float(bw)
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used",
                "--format=csv,noheader,nounits"])
    if out.strip():
        name, total, used = [x.strip() for x in out.strip().splitlines()[0].split(",")]
        free = (float(total) - float(used)) / 1024
        return "cuda", name, round(free, 2), 400.0
    return "", "", 0.0, 0.0


# ---------------------------------------------------------------- network
def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent; just picks the LAN route
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def link_kind() -> str:
    """Best-effort: 'wifi5', 'gige', ... picks the WIFI table row for estimates."""
    try:
        if sys.platform == "darwin":
            out = _run(["route", "-n", "get", "default"])
            m = re.search(r"interface:\s*(\S+)", out)
            iface = m.group(1) if m else ""
            hw = _run(["networksetup", "-listallhardwareports"])
            blk = re.search(r"Hardware Port: ([^\n]+)\nDevice: " + re.escape(iface), hw) if iface else None
            if blk and "Wi-Fi" in blk.group(1):
                return "wifi6"
            return "gige" if iface else "loopback"
        if sys.platform.startswith("linux"):
            for iface in os.listdir("/sys/class/net"):
                if iface == "lo":
                    continue
                if os.path.isdir(f"/sys/class/net/{iface}/wireless"):
                    return "wifi5"
            return "gige"
    except OSError:
        pass
    return "wifi5"


# ---------------------------------------------------------------- bandwidth bench
def measure_mem_bw_gbs(max_mb: int = 256) -> float:
    """Tiny memcpy benchmark: a lower bound on what a decode loop can stream.

    Buffer is capped at ~5 % of available RAM so probing a 512 MB board is harmless."""
    avail = ram_available_gb()
    mb = int(max(8, min(max_mb, avail * 1000 * 0.05))) if avail else 32
    src = bytearray(mb * 1024 * 1024)
    dst = bytearray(len(src))
    mv_s, mv_d = memoryview(src), memoryview(dst)
    best = 0.0
    for _ in range(3):
        t = time.perf_counter()
        mv_d[:] = mv_s
        dt = time.perf_counter() - t
        if dt > 0:
            best = max(best, 2 * len(src) / dt / 1e9)  # read + write
    return round(best, 1)


def probe(measure: bool = False) -> Device:
    total = ram_total_gb()
    avail = ram_available_gb()
    cpu, cores = cpu_info()
    gkind, gname, vram, gbw = gpu_info()
    du = shutil.disk_usage(os.path.expanduser("~"))
    link = link_kind()
    is_android = "ANDROID_ROOT" in os.environ or "TERMUX_VERSION" in os.environ
    os_name = "android" if is_android else sys.platform
    if gkind == "metal":
        bw = gbw
    else:
        bw = measure_mem_bw_gbs() if measure else max(2.0, min(60.0, cores * 3.0))
    dev = Device(
        name=socket.gethostname().split(".")[0] or "this-machine",
        description=f"probed {platform.system()} {platform.machine()}",
        ram_gb=round(total, 2), avail_gb=round(avail, 2), cpu=cpu, cores=cores,
        mem_bw_gbs=bw, gpu=gkind, vram_gb=vram, gpu_bw_gbs=gbw,
        disk_free_gb=round(du.free / 1e9, 1), disk_read_mbs=2000 if sys.platform == "darwin" else 300,
        net_mbps=WIFI[link]["net_mbps"], rtt_ms=WIFI[link]["rtt_ms"], os=os_name,
        os_reserve_gb=2.0 if is_android else 0.0,
    )
    dev.extra.update({"probed": True, "lan_ip": lan_ip(), "link": link, "gpu_name": gname,
                      "mem_bw_measured": bool(measure and gkind != "metal")})
    if measure and gkind == "metal":
        dev.extra["memcpy_gbs_cpu"] = measure_mem_bw_gbs()
    return dev
