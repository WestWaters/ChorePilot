"""Memory guard: the promise that ChorePilot never crashes the appliance (or your laptop).

Two layers:
  1. pre-flight -- refuse to start anything whose planned footprint exceeds usable RAM;
  2. watchdog  -- while llama-server / rpc-server runs, poll available RAM and stop the
     child cleanly (SIGTERM, then SIGKILL) if the machine drops below a floor, *before*
     the kernel's OOM killer picks a victim (which on an appliance could be the firmware).
"""
from __future__ import annotations

import signal
import subprocess
import threading
import time

from .probe import ram_available_gb


def floor_gb(ram_gb: float) -> float:
    """Minimum free RAM we always leave the machine: 5 % of RAM, 150 MB .. 1 GB."""
    return max(0.15, min(1.0, 0.05 * ram_gb))


class Watchdog(threading.Thread):
    def __init__(self, proc: subprocess.Popen, floor: float, interval: float = 1.0,
                 strikes: int = 2, log=print, sampler=ram_available_gb):
        super().__init__(daemon=True)
        self.proc, self.floor, self.interval, self.strikes = proc, floor, interval, strikes
        self.log, self.sampler = log, sampler
        self.tripped = False
        self.min_seen = float("inf")
        self._halt = threading.Event()

    def stop(self):
        self._halt.set()

    def run(self):
        bad = 0
        while not self._halt.is_set() and self.proc.poll() is None:
            avail = self.sampler()
            self.min_seen = min(self.min_seen, avail)
            bad = bad + 1 if avail and avail < self.floor else 0
            if bad >= self.strikes:
                self.tripped = True
                self.log(f"[guard] available RAM {avail:.2f} GB < floor {self.floor:.2f} GB -- "
                         "stopping the model before the OS has to. Pick a smaller quant/context.")
                terminate(self.proc)
                return
            self._halt.wait(self.interval)


def terminate(proc: subprocess.Popen, grace: float = 5.0):
    if proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.SIGTERM)
        t = time.time()
        while proc.poll() is None and time.time() - t < grace:
            time.sleep(0.1)
        if proc.poll() is None:
            proc.kill()
    except OSError:
        pass
