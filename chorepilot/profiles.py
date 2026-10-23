"""Device presets: what a target machine looks like when we can't (or don't want to) probe it.

Numbers are deliberately conservative "real world" figures, not datasheet peaks: memory
bandwidth is what a CPU decode loop actually streams, Wi-Fi is real TCP goodput, not PHY
rate. Sources for each figure are in docs/RESEARCH.md. Users can drop their own JSON
profiles in ./profiles/ or ~/.config/chorepilot/profiles/ (same keys as below).
"""
from __future__ import annotations

import json
import os

from .env import config_dir
from dataclasses import asdict, dataclass, field


@dataclass
class Device:
    name: str
    description: str = ""
    ram_gb: float = 0.0            # total RAM (unified memory on Apple)
    avail_gb: float = 0.0          # RAM free for us right now (probe) or assumed (preset)
    cpu: str = ""
    cores: int = 1
    mem_bw_gbs: float = 1.0        # realistic sustained memory bandwidth for decode
    gpu: str = ""                  # "" | "metal" | "cuda" | "vulkan" ...
    vram_gb: float = 0.0           # dedicated VRAM (0 on unified / CPU-only)
    gpu_bw_gbs: float = 0.0
    disk_free_gb: float = 0.0
    disk_read_mbs: float = 50.0    # sequential read, for mmap-streaming estimates
    net_mbps: float = 50.0         # real LAN goodput to the rest of the cluster
    rtt_ms: float = 5.0            # LAN round-trip
    os: str = ""
    runs_llama_cpp: bool = True    # can it run llama.cpp / rpc-server at all?
    os_reserve_gb: float = 0.0     # RAM to leave for the OS / appliance firmware (0 = auto)
    notes: str = ""
    extra: dict = field(default_factory=dict)

    def reserve_gb(self) -> float:
        """Never hand the model this much: OS, page cache, the appliance's own job."""
        if self.os_reserve_gb:
            return self.os_reserve_gb
        # 15 % of RAM, at least 0.25 GB on tiny boards, at most 4 GB on big machines.
        return min(4.0, max(0.25, 0.15 * self.ram_gb))

    def usable_gb(self) -> float:
        """What a model may actually occupy without risking an OOM / appliance crash."""
        base = self.avail_gb if self.avail_gb else self.ram_gb
        if self.avail_gb:
            # free memory already excludes the OS; keep a small extra margin for spikes
            return max(0.0, base - min(1.0, max(0.15, 0.05 * self.ram_gb)))
        return max(0.0, base - self.reserve_gb())

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Device":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        extra = {k: v for k, v in d.items() if k not in cls.__dataclass_fields__}
        dev = cls(**known)
        dev.extra.update(extra)
        return dev


# Wi-Fi: real single-client TCP goodput on a decent home network, and typical RTT.
WIFI = {
    "wifi4": {"net_mbps": 40, "rtt_ms": 6.0},     # 802.11n 2.4 GHz, 1-2 streams
    "wifi5": {"net_mbps": 300, "rtt_ms": 3.0},    # 802.11ac 5 GHz, 2x2
    "wifi6": {"net_mbps": 600, "rtt_ms": 2.0},    # 802.11ax 2x2
    "gige": {"net_mbps": 940, "rtt_ms": 0.2},     # wired gigabit
    "loopback": {"net_mbps": 20000, "rtt_ms": 0.05},
}

PRESETS = {
    "dishwasher-1g": Device(
        name="dishwasher-1g", description="The joke target: a 1 GB / 4-core board hidden in a dishwasher",
        ram_gb=1.0, cpu="4x Cortex-A53 class", cores=4, mem_bw_gbs=2.0, disk_free_gb=8,
        disk_read_mbs=40, net_mbps=40, rtt_ms=6, os="linux",
        notes="Simulated profile (Pi 3 / Zero 2 W class). Runs a <=0.5B model alone, or a few layers of a big one."),
    "smart-appliance-module": Device(
        name="smart-appliance-module", description="Stock Wi-Fi module inside a real washer/dishwasher",
        ram_gb=0.004, cpu="Cortex-M4F 192 MHz (MediaTek MT7686 class)", cores=1, mem_bw_gbs=0.1,
        disk_free_gb=0.004, disk_read_mbs=5, net_mbps=10, rtt_ms=10, os="rtos", runs_llama_cpp=False,
        notes="e.g. Samsung CWAP210M: ~4 MB RAM, 4 MB flash, 9600-baud UART to the mainboard, "
              "vendor firmware. Control via cloud APIs only; no LLM."),
    "esp32-s3": Device(
        name="esp32-s3", description="ESP32-S3 with 8 MB PSRAM (hobbyist 'appliance brain')",
        ram_gb=0.008, cpu="2x Xtensa LX7 240 MHz", cores=2, mem_bw_gbs=0.08, disk_free_gb=0.016,
        disk_read_mbs=10, net_mbps=20, rtt_ms=8, os="esp-idf", runs_llama_cpp=False,
        notes="llama2.c ports run a 260K model at ~19-30 tok/s, 3.3M int8 at ~12 tok/s. Not a llama.cpp node."),
    "pi-zero-2w": Device(
        name="pi-zero-2w", description="Raspberry Pi Zero 2 W (512 MB)",
        ram_gb=0.5, cpu="4x Cortex-A53 1 GHz", cores=4, mem_bw_gbs=1.5, disk_free_gb=16,
        disk_read_mbs=40, net_mbps=30, rtt_ms=6, os="linux",
        notes="2.4 GHz Wi-Fi 4 only. Tiny models (<=0.5B at Q2-Q4) or an RPC node with a couple of layers."),
    "pi-4-4gb": Device(
        name="pi-4-4gb", description="Raspberry Pi 4 Model B 4 GB",
        ram_gb=4.0, cpu="4x Cortex-A72 1.8 GHz", cores=4, mem_bw_gbs=4.5, disk_free_gb=32,
        disk_read_mbs=80, net_mbps=64, rtt_ms=4, os="linux"),
    "pi-5-8gb": Device(
        name="pi-5-8gb", description="Raspberry Pi 5 8 GB",
        ram_gb=8.0, cpu="4x Cortex-A76 2.4 GHz", cores=4, mem_bw_gbs=10.0, disk_free_gb=64,
        disk_read_mbs=400, net_mbps=217, rtt_ms=4, os="linux",
        notes="Add an NVMe HAT for fast mmap streaming."),
    "old-android-4gb": Device(
        name="old-android-4gb", description="An old 4 GB Android phone in Termux",
        ram_gb=4.0, cpu="8-core big.LITTLE", cores=8, mem_bw_gbs=12.0, disk_free_gb=16,
        disk_read_mbs=200, net_mbps=200, rtt_ms=5, os="android", os_reserve_gb=2.0,
        notes="Android's low-memory killer acts around half of RAM -- budget ~2 GB."),
    "rk3588-16gb": Device(
        name="rk3588-16gb", description="RK3588 board (Orange Pi 5 Plus / Rock 5B) 16 GB",
        ram_gb=16.0, cpu="4x A76 + 4x A55", cores=8, mem_bw_gbs=20.0, disk_free_gb=128,
        disk_read_mbs=800, net_mbps=400, rtt_ms=2.0, os="linux",
        notes="NPU via rkllm is a separate stack; ChorePilot uses the CPU path."),
    # ---- the "brain" tier: where the model that drives the appliance should live
    # (nameplate bandwidth from vendor spec pages; see docs/RESEARCH.md section 6)
    "jetson-orin-nano-super-8g": Device(
        name="jetson-orin-nano-super-8g", description="NVIDIA Jetson Orin Nano Super 8 GB",
        ram_gb=8.0, cpu="6x Cortex-A78AE", cores=6, mem_bw_gbs=60.0, gpu="cuda", vram_gb=8.0,
        gpu_bw_gbs=102.0, disk_free_gb=64, disk_read_mbs=1500, net_mbps=940, rtt_ms=0.5,
        os="linux", os_reserve_gb=1.5,
        notes="Unified 8 GB: 1.5-4B instruct models at ~30-45 tok/s. No room for 30B-A3B."),
    "jetson-agx-orin-64g": Device(
        name="jetson-agx-orin-64g", description="NVIDIA Jetson AGX Orin 64 GB",
        ram_gb=64.0, cpu="12x Cortex-A78AE", cores=12, mem_bw_gbs=120.0, gpu="cuda",
        vram_gb=64.0, gpu_bw_gbs=204.8, disk_free_gb=64, disk_read_mbs=2000, net_mbps=940,
        rtt_ms=0.5, os="linux"),
    "jetson-agx-thor-128g": Device(
        name="jetson-agx-thor-128g", description="NVIDIA Jetson AGX Thor (T5000) 128 GB",
        ram_gb=128.0, cpu="14x Neoverse-V3AE", cores=14, mem_bw_gbs=150.0, gpu="cuda",
        vram_gb=128.0, gpu_bw_gbs=273.0, disk_free_gb=900, disk_read_mbs=3000, net_mbps=940,
        rtt_ms=0.5, os="linux"),
    "dgx-spark-128g": Device(
        name="dgx-spark-128g", description="NVIDIA DGX Spark (GB10) 128 GB",
        ram_gb=128.0, cpu="10x Cortex-X925 + 10x Cortex-A725", cores=20, mem_bw_gbs=150.0,
        gpu="cuda", vram_gb=128.0, gpu_bw_gbs=273.0, disk_free_gb=1800, disk_read_mbs=5000,
        net_mbps=940, rtt_ms=0.5, os="linux",
        notes="30B-A3B MoE at Q4: ~60-80 tok/s measured; dense 27B Q4: ~11-14 (MTP ~2x)."),
    "strix-halo-128g": Device(
        name="strix-halo-128g", description="AMD Ryzen AI Max+ 395 (Strix Halo) 128 GB",
        ram_gb=128.0, cpu="16x Zen 5", cores=16, mem_bw_gbs=120.0, gpu="vulkan",
        gpu_bw_gbs=256.0, disk_free_gb=900, disk_read_mbs=5000, net_mbps=940, rtt_ms=0.5,
        os="linux", notes="llama.cpp Vulkan: 30B-A3B Q4 ~66-86 tok/s measured."),
    "mac-mini-m4-pro-64g": Device(
        name="mac-mini-m4-pro-64g", description="Mac mini M4 Pro 64 GB",
        ram_gb=64.0, cpu="10P+4E Apple M4 Pro", cores=10, mem_bw_gbs=120.0, gpu="metal",
        gpu_bw_gbs=273.0, disk_free_gb=500, disk_read_mbs=5000, net_mbps=940, rtt_ms=0.5,
        os="darwin"),
}


def _profile_dirs():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return [os.path.join(here, "profiles"), os.path.join(config_dir(), "profiles"),
            os.path.join(os.getcwd(), "profiles")]


def load_profile(name_or_path: str) -> Device:
    """A preset name, a JSON file path, or a name found in a profiles directory."""
    if os.path.isfile(name_or_path):
        with open(name_or_path) as f:
            return Device.from_dict(json.load(f))
    if name_or_path in PRESETS:
        d = PRESETS[name_or_path]
        return Device.from_dict(d.to_dict())
    for d in _profile_dirs():
        p = os.path.join(d, name_or_path + ".json")
        if os.path.isfile(p):
            with open(p) as f:
                return Device.from_dict(json.load(f))
    raise KeyError(f"unknown profile {name_or_path!r}; presets: {', '.join(PRESETS)}")


def apply_network(dev: Device, link: str) -> Device:
    if link not in WIFI:
        raise KeyError(f"unknown link {link!r}; choose from {', '.join(WIFI)}")
    dev.net_mbps = WIFI[link]["net_mbps"]
    dev.rtt_ms = WIFI[link]["rtt_ms"]
    return dev
