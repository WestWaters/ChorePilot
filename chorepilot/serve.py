"""`chorepilot serve`: run a model, three ways, all behind the memory guard.

  local   llama-server on this machine with a context sized to fit.
  worker  this machine (the dishwasher) runs llama.cpp's RPC server and lends its RAM;
          it answers a tiny UDP beacon with its probe so hosts can size its share.
  host    llama-server here + --rpc to the workers, with --tensor-split computed so no
          node is asked for more than its usable memory.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

from . import __version__
from .fit import ModelSpec, best_ctx, plan_cluster
from .guard import Watchdog, floor_gb, terminate
from .probe import probe, ram_available_gb
from .profiles import Device
from .env import getenv

BEACON_PORT = 50053
BEACON_ASK = b"DISHSOAKER?"   # wire name kept: old and new workers interoperate
RPC_PORT = 50052
_NOT_GPU = ("CPU", "BLAS", "AMX", "ACCEL")


# ------------------------------------------------------------------ binaries
def find_bin(*names):
    d = getenv("LLAMA_DIR")
    for n in names:
        if d and os.path.isfile(os.path.join(d, n)):
            return os.path.join(d, n)
        p = shutil.which(n)
        if p:
            return p
    return None


def llama_server_bin():
    return find_bin("llama-server")


def rpc_server_bin():
    return find_bin("ggml-rpc-server", "rpc-server")


def supports_rpc(server_bin) -> bool:
    try:
        out = subprocess.run([server_bin, "--help"], capture_output=True, text=True, timeout=20)
        return "--rpc" in (out.stdout + out.stderr)
    except (OSError, subprocess.SubprocessError):
        return False


def list_devices(server_bin, rpc: str | None = None):
    """[(name, description)] in llama.cpp's device order (what --tensor-split indexes)."""
    # --rpc must come first: --list-devices prints and exits as soon as it is parsed
    cmd = [server_bin] + (["--rpc", rpc] if rpc else []) + ["--list-devices"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    devs = []
    for line in (out.stdout + out.stderr).splitlines():
        m = re.match(r"\s+(\S+):\s+(.*)$", line)
        if m and not line.strip().startswith(("load", "ggml", "register", "Available")):
            devs.append((m.group(1), m.group(2)))
    return devs


# ------------------------------------------------------------------ helpers
def _wait_ready(port, proc, timeout=300):
    import urllib.request
    t = time.time()
    while time.time() - t < timeout and proc.poll() is None:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(0.5)
    return False


def port_free(port, host="127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) != 0


class _Stop(Exception):
    pass


def _raise_stop(signum, frame):
    raise _Stop()


def _run_guarded(cmd, dev: Device, log=print, ready_port=None, on_ready=None, detach_after=None,
                 check_port=None, output=None):
    floor = floor_gb(dev.ram_gb)
    port = check_port if check_port is not None else ready_port
    if port is not None and not port_free(port):
        raise SystemExit(f"[serve] port {port} is already in use (another server still running?)")
    log(f"[serve] {' '.join(cmd)}")
    log(f"[guard] floor {floor:.2f} GB free; will stop the model if RAM drops below it")
    if output:   # send llama.cpp's own logs to a file instead of the terminal
        os.makedirs(os.path.dirname(output), exist_ok=True)
        with open(output, "ab") as out:
            proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT)
    else:
        proc = subprocess.Popen(cmd)
    dog = Watchdog(proc, floor, log=log)
    dog.start()
    if detach_after is None and threading.current_thread() is threading.main_thread():
        # SIGTERM/SIGHUP to us must take the model down too -- never leave an orphan holding RAM
        for sig in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, _raise_stop)
    try:
        if ready_port is not None:
            if _wait_ready(ready_port, proc):
                log(f"[serve] ready: http://127.0.0.1:{ready_port}  (OpenAI API at /v1)")
                if on_ready:
                    on_ready()
            else:
                log("[serve] server exited before becoming ready")
        if detach_after is not None:
            time.sleep(detach_after)
            return proc, dog
        proc.wait()
    except (KeyboardInterrupt, _Stop):
        log("\n[serve] stopping")
    finally:
        if detach_after is None:
            dog.stop()
            terminate(proc)
    return proc, dog


# ------------------------------------------------------------------ local
_HELP_CACHE = {}


def server_help(server_bin) -> str:
    if server_bin not in _HELP_CACHE:
        try:
            out = subprocess.run([server_bin, "--help"], capture_output=True, text=True, timeout=20)
            _HELP_CACHE[server_bin] = out.stdout + out.stderr
        except (OSError, subprocess.SubprocessError):
            _HELP_CACHE[server_bin] = ""
    return _HELP_CACHE[server_bin]


def speed_args(server_bin, draft=None, spec=None, draft_max=None):
    """Speculative decoding flags, matched to what this llama-server build understands.

    draft: a small same-tokenizer GGUF (e.g. Qwen2.5-0.5B for a Qwen2.5-7B).
    spec:  a --spec-type value: draft-simple, draft-mtp (models shipping MTP heads),
           draft-eagle3, draft-dflash, ngram-mod (no draft model; good for repetitive text
           such as tool calls and code). Older builds only know -md / --draft-max.
    """
    if not draft and not spec:
        return []
    h = server_help(server_bin)
    new = "--spec-type" in h
    out = []
    if draft:
        out += ["-md", draft]
    if spec or (draft and new):
        if not new:
            raise SystemExit("this llama-server has no --spec-type; update llama.cpp or use --draft")
        out += ["--spec-type", spec or "draft-simple"]
    if draft_max:
        out += ["--spec-draft-n-max" if new else "--draft-max", str(draft_max)]
    return out


def local_cmd(model_path: str, fit, port=8080, host="127.0.0.1", threads=None,
              extra=None):
    srv = llama_server_bin()
    if not srv:
        raise SystemExit("llama-server not found (install llama.cpp or set CHOREPILOT_LLAMA_DIR)")
    cmd = [srv, "-m", model_path, "-c", str(fit.ctx), "--host", host, "--port", str(port),
           "-ngl", str(fit.ngl), "-t", str(threads or max(1, fit.device.cores))]
    if "--jinja" in server_help(srv):
        cmd.append("--jinja")   # OpenAI-style tool calls need the model's own chat template
    if fit.device.ram_gb <= 2:
        cmd += ["-b", "128", "-ub", "128"]   # smaller compute buffer on tiny boards
    return cmd + list(extra or [])


def serve_local(info, dev: Device, ctx=None, port=8080, host="127.0.0.1", force=False,
                kv_type="f16", log=print, extra_args=None, **kw):
    spec = ModelSpec.from_gguf(info)
    for w in spec.warnings:
        log(f"[warn] {w}")
    f = best_ctx(spec, dev, kv_type, want=ctx)
    log(f"[plan] {spec.name}: {f.total_gb:.2f} GB at ctx {f.ctx} (usable {f.usable_gb:.2f} GB) "
        f"-> {f.mode}, ~{f.tok_s:.1f} tok/s")
    if not f.fits and not force:
        raise SystemExit(f"[guard] refusing to start: {f.note or 'does not fit'}. "
                         "Try `chorepilot recommend` for a smaller rung, or host a cluster.")
    return _run_guarded(local_cmd(info.path, f, port, host, extra=extra_args), dev, log,
                        ready_port=port, **kw)


# ------------------------------------------------------------------ worker + beacon
class Beacon(threading.Thread):
    """Answers DISHSOAKER? on UDP with this node's live probe + RPC endpoint."""

    def __init__(self, dev: Device, rpc_host: str, rpc_port: int, bind="0.0.0.0",
                 port=BEACON_PORT):
        super().__init__(daemon=True)
        self.dev, self.rpc_host, self.rpc_port = dev, rpc_host, rpc_port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((bind, port))

    def run(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(256)
            except OSError:
                return
            if data.strip() != BEACON_ASK:
                continue
            if self.dev.extra.get("probed"):  # live numbers; a simulated profile keeps its own
                self.dev.avail_gb = round(ram_available_gb(), 2) or self.dev.avail_gb
            msg = {"chorepilot": __version__, "dishsoaker": __version__, "rpc": f"{self.rpc_host}:{self.rpc_port}",
                   "device": self.dev.to_dict()}
            self.sock.sendto(json.dumps(msg).encode(), addr)


def scan(targets=None, timeout=1.5, port=BEACON_PORT):
    """Broadcast (or unicast to `targets`) and collect worker beacons."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.2)
    for t in (targets or ["255.255.255.255"]):
        host, _, p = t.partition(":")
        try:
            s.sendto(BEACON_ASK, (host, int(p) if p else port))
        except OSError:
            pass
    found, t0 = {}, time.time()
    while time.time() - t0 < timeout:
        try:
            data, addr = s.recvfrom(65536)
        except socket.timeout:
            continue
        try:
            msg = json.loads(data)
        except ValueError:
            continue
        rpc = msg["rpc"]
        if rpc.startswith(("0.0.0.0", "127.0.0.1")) and addr[0] != "127.0.0.1":
            rpc = f"{addr[0]}:{rpc.split(':')[1]}"
        found[rpc] = Device.from_dict(msg["device"])
    s.close()
    return found


def serve_worker(dev: Device, port=RPC_PORT, lan=False, cache=False, cpu_only=False,
                 threads=None, log=print, beacon_port=BEACON_PORT, **kw):
    rpc = rpc_server_bin()
    if not rpc:
        raise SystemExit("ggml-rpc-server not found: build llama.cpp with -DGGML_RPC=ON "
                         "(see docs/DESIGN.md) or set CHOREPILOT_LLAMA_DIR")
    bind = "0.0.0.0" if lan else "127.0.0.1"
    if lan:
        log("[warn] the llama.cpp RPC server is unauthenticated and unencrypted. Only use "
            "--lan on a home network you trust; never port-forward it.")
    cmd = [rpc, "-H", bind, "-p", str(port), "-t", str(threads or max(1, dev.cores))]
    if cache:
        cmd.append("-c")
    if cpu_only:
        cmd += ["-d", "CPU"]
    adv_host = (dev.extra.get("lan_ip") or "127.0.0.1") if lan else "127.0.0.1"
    Beacon(dev, adv_host, port, bind=bind, port=beacon_port).start()
    log(f"[worker] lending ~{dev.usable_gb():.2f} GB on {adv_host}:{port} "
        f"(beacon udp/{beacon_port})")
    return _run_guarded(cmd, dev, log, check_port=port, **kw)


# ------------------------------------------------------------------ host
def serve_host(info, dev: Device, workers: dict, ctx=None, port=8080, host="127.0.0.1",
               strategy="fastest", force=False, kv_type="f16", log=print, dry_run=False,
               extra_args=None, **kw):
    """workers: {"ip:port": Device} (from scan() or --rpc + profiles)."""
    srv = llama_server_bin()
    if not srv or not supports_rpc(srv):
        raise SystemExit("this llama-server has no --rpc: build llama.cpp with -DGGML_RPC=ON "
                         "and point CHOREPILOT_LLAMA_DIR at it")
    spec = ModelSpec.from_gguf(info)
    endpoints = list(workers)
    plan = plan_cluster(spec, dev, [workers[e] for e in endpoints], ctx or 4096, kv_type, strategy)
    for n in plan.nodes:
        log(f"[plan] {n.device.name:<22} layers {n.layers:>3}  {n.total_gb:6.2f} GB of "
            f"{n.usable_gb:6.2f} usable{'  (host)' if n.is_host else ''}")
    log(f"[plan] est. ~{plan.tok_s:.2f} tok/s (compute {plan.compute_s * 1000:.0f} ms + network "
        f"{plan.net_s * 1000:.0f} ms per token); first load ships weights in ~{plan.load_s:.0f}s")
    if not plan.fits and not force:
        raise SystemExit(f"[guard] refusing: {plan.note}. Add a node or pick a smaller rung.")
    rpc_arg = ",".join(endpoints)
    devs = list_devices(srv, rpc_arg)
    # Pin the device order ourselves with --device, so --tensor-split lines up no matter how
    # llama.cpp orders its list (it moves RPC devices to the front of model->devices).
    # Order: RPC workers (endpoint order), then the host's first local GPU, if any. Layers
    # that are not offloaded (host without GPU) stay on the host CPU.
    devs = [(n, d) for n, d in devs if not n.upper().startswith(_NOT_GPU)]
    order, split = [], []
    for ep, node in zip(endpoints, plan.nodes[1:]):
        name = next((n for n, d in devs if n.upper().startswith("RPC") and (ep in d or ep in n)), None)
        if name is None:
            raise SystemExit(f"llama-server does not list worker {ep}; is it running and reachable?")
        if node.layers:
            order.append(name)
            split.append(node.layers)
    local_gpu = next((n for n, _ in devs if not n.upper().startswith("RPC")), None)
    if local_gpu and plan.nodes[0].layers:
        order.append(local_gpu)
        split.append(plan.nodes[0].layers)
    ngl = sum(split)
    if not order:
        order, split = ["none"], [1]   # everything stays on the host CPU
    cmd = [srv, "-m", info.path, "-c", str(plan.ctx), "--host", host, "--port", str(port),
           "--rpc", rpc_arg, "--device", ",".join(order), "-ngl", str(ngl),
           "--tensor-split", ",".join(map(str, split)),
           "-t", str(max(1, dev.cores))]
    if "--jinja" in server_help(srv):
        cmd.append("--jinja")
    cmd += list(extra_args or [])
    log(f"[plan] --device {','.join(order)} --tensor-split {','.join(map(str, split))} -ngl {ngl}")
    if dry_run:
        print(" ".join(cmd))
        return None, None
    return _run_guarded(cmd, dev, log, ready_port=port, **kw)


def this_device(profile: Device | None) -> Device:
    return profile if profile is not None else probe()


if __name__ == "__main__":  # pragma: no cover
    sys.exit("use `chorepilot serve`")
