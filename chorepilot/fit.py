"""The arithmetic: will it fit, at what context, how fast, and how to split it over a LAN.

Everything here is a pure function of (model geometry, device numbers) so it can be unit
tested and run against simulated devices ("would this 27B run on my dishwasher + laptop?").

Memory to RUN = weights + KV cache(ctx) + compute buffer + runtime overhead, and it must
fit inside Device.usable_gb() (which already leaves the OS / appliance its reserve).
Decode speed is memory-bandwidth bound: every token streams the active weights once,
so tok/s ~= efficiency * bandwidth / bytes_read_per_token.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .profiles import Device

GB = 1e9

# Effective bits per weight of a WHOLE llama.cpp model file (mixed tensors, embeddings and
# output head included), from the Llama-3.1-8B table in llama.cpp tools/quantize/README.md.
# Real files are always sized from their actual bytes; these are only for "what if" rungs.
QUANT_LADDER = [
    ("Q8_0", 8.50), ("Q6_K", 6.56), ("Q5_K_M", 5.70), ("Q4_K_M", 4.89), ("IQ4_XS", 4.46),
    ("Q3_K_M", 4.00), ("IQ3_XXS", 3.25), ("Q2_K", 3.16), ("IQ2_M", 2.93), ("IQ2_XXS", 2.38),
    ("IQ1_M", 2.01),
]

# Extra nominal whole-file sizes, only used to find where the *current* file sits on the ladder.
KNOWN_BPW = dict(QUANT_LADDER, F32=32.0, F16=16.0, BF16=16.0, Q5_K_S=5.57, Q4_K_S=4.67,
                 Q4_0=4.71, IQ4_NL=4.68, Q3_K_L=4.30, IQ3_M=3.76, IQ3_S=3.66, Q3_K_S=3.64,
                 IQ3_XS=3.50, IQ2_S=2.74, IQ2_XS=2.59, IQ1_S=1.88)

KV_TYPES = {"f16": 2.0, "q8_0": 34 / 32, "q4_0": 18 / 32}

CPU_EFF = 0.80      # of a preset's sustained (STREAM-like) bandwidth a llama.cpp decode reaches
GPU_EFF = 0.70
RUNTIME_GB = 0.06   # llama.cpp binary, threads, misc


@dataclass
class ModelSpec:
    """Geometry needed for the math; built from a GGUFInfo or a catalog entry."""
    name: str
    n_params: int
    file_bytes: int
    n_layers: int
    n_embd: int
    n_vocab: int
    kv_per_token: float            # bytes, f16 cache
    active_bytes: int              # bytes streamed per decoded token
    per_layer_bytes: float
    nonlayer_bytes: int
    n_ctx_train: int = 4096
    arch: str = ""
    quant: str = ""
    source: str = ""               # local path or hf repo/file
    moe: bool = False
    warnings: list = field(default_factory=list)

    @property
    def bpw(self) -> float:
        return self.file_bytes * 8 / self.n_params if self.n_params else 0.0

    @classmethod
    def from_gguf(cls, info, quant: str = "") -> "ModelSpec":
        warnings = []
        if info.nonstock_types:
            warnings.append("uses fork-only tensor types " + ",".join(info.nonstock_types)
                            + " (needs ik_llama.cpp; stock llama.cpp will refuse it)")
        q = quant or _guess_quant(info)
        label = info.meta.get("chorepilot.label")
        if not label and info.path and os.path.exists(info.path):
            label = os.path.basename(info.path).removesuffix(".gguf")
        return cls(name=label or info.name, n_params=info.n_params,
                   file_bytes=info.file_bytes, n_layers=info.n_layers or 1, n_embd=info.n_embd or 1,
                   n_vocab=info.n_vocab or 32000, kv_per_token=info.kv_bytes_per_token(),
                   active_bytes=info.active_bytes, per_layer_bytes=info.per_layer_bytes,
                   nonlayer_bytes=info.nonlayer_bytes, n_ctx_train=info.n_ctx_train or 4096,
                   arch=info.arch, quant=q, source=info.path,
                   moe=bool(info.expert_count), warnings=warnings)

    def at_bpw(self, bpw: float, quant: str) -> "ModelSpec":
        """The same model re-quantized to `bpw` (hypothetical rung for recommendations)."""
        s = bpw / self.bpw if self.bpw else 1.0
        return ModelSpec(name=self.name, n_params=self.n_params, file_bytes=int(self.file_bytes * s),
                         n_layers=self.n_layers, n_embd=self.n_embd, n_vocab=self.n_vocab,
                         kv_per_token=self.kv_per_token, active_bytes=int(self.active_bytes * s),
                         per_layer_bytes=self.per_layer_bytes * s,
                         nonlayer_bytes=int(self.nonlayer_bytes * s), n_ctx_train=self.n_ctx_train,
                         arch=self.arch, quant=quant, source=self.source, moe=self.moe)


_FTYPE = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K",
          11: "Q3_K_S", 12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S",
          17: "Q5_K_M", 18: "Q6_K", 19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S", 22: "IQ3_XS",
          23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL", 26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S",
          29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16", 36: "TQ1_0", 37: "TQ2_0", 38: "MXFP4"}


_QNAME = re.compile(r"(?i)(?<![A-Za-z0-9])(I?Q\d_[A-Z0-9]+(?:_[A-Z0-9]+)?|BF16|F16|F32|MXFP4|TQ\d_0)"
                    r"(?=[.\-]|$)")


def _guess_quant(info) -> str:
    hits = _QNAME.findall(os.path.basename(info.path or ""))
    if hits:
        return hits[-1].upper()
    ft = info.meta.get("general.file_type")
    if isinstance(ft, int) and ft in _FTYPE:
        return _FTYPE[ft]
    return f"~{info.bpw:.1f}bpw"


# ------------------------------------------------------------------ single device
def ubatch_for(dev: Device) -> int:
    """Tiny boards get a small micro-batch: the logits buffer is n_vocab * ubatch * 4 bytes,
    which is ~300 MB for a 152k vocabulary at the default 512 -- a third of a 1 GB board."""
    return 128 if dev.ram_gb <= 2 else 512


def compute_buffer_gb(m: ModelSpec, ctx: int, ubatch: int = 512) -> float:
    """llama.cpp compute (scratch) buffer: logits for a ubatch + activations + attention scores."""
    ub = min(ubatch, ctx)
    logits = m.n_vocab * ub * 4
    acts = m.n_embd * ub * 4 * 12
    attn = ub * min(ctx, 8192) * 4 * 2  # flash-attn keeps this small; stays conservative
    return (logits + acts + attn) / GB + 0.03


@dataclass
class Fit:
    model: ModelSpec
    device: Device
    ctx: int
    kv_type: str
    weights_gb: float
    kv_gb: float
    compute_gb: float
    total_gb: float
    usable_gb: float
    fits: bool
    mode: str            # "ram" | "gpu" | "stream" | "no"
    tok_s: float
    ngl: int
    note: str = ""

    @property
    def spare_gb(self) -> float:
        return self.usable_gb - self.total_gb


def decode_tok_s(m: ModelSpec, dev: Device, ctx: int, kv_type="f16", on_gpu=False) -> float:
    bw = (dev.gpu_bw_gbs if on_gpu and dev.gpu_bw_gbs else dev.mem_bw_gbs) * GB
    eff = GPU_EFF if on_gpu else CPU_EFF
    per_tok = m.active_bytes + m.kv_per_token * KV_TYPES[kv_type] / 2.0 * ctx / 2
    return eff * bw / per_tok if per_tok else 0.0


def fit_local(m: ModelSpec, dev: Device, ctx: int, kv_type: str = "f16",
              ubatch: int | None = None, allow_stream: bool = True) -> Fit:
    ubatch = ubatch or ubatch_for(dev)
    weights = m.file_bytes / GB
    kv = m.kv_per_token * KV_TYPES[kv_type] / 2.0 * ctx / GB
    comp = compute_buffer_gb(m, ctx, ubatch)
    total = weights + kv + comp + RUNTIME_GB
    usable = dev.usable_gb()
    on_gpu = dev.gpu in ("metal", "cuda", "vulkan")
    if dev.gpu == "metal":
        # Metal's recommended working set is ~2/3 of unified RAM on small Macs, ~3/4 on big ones
        gpu_cap = dev.ram_gb * (0.67 if dev.ram_gb <= 36 else 0.75)
        on_gpu = total <= min(usable, gpu_cap)
    elif dev.gpu == "cuda":
        on_gpu = total <= dev.vram_gb * 0.92
    if not dev.runs_llama_cpp:
        return Fit(m, dev, ctx, kv_type, weights, kv, comp, total, usable, False, "no", 0.0, 0,
                   "this device cannot run llama.cpp (MCU / locked firmware)")
    if total <= usable:
        tps = decode_tok_s(m, dev, ctx, kv_type, on_gpu)
        return Fit(m, dev, ctx, kv_type, weights, kv, comp, total, usable, True,
                   "gpu" if on_gpu else "ram", tps, 999 if on_gpu else 0)
    # Doesn't fit in RAM. mmap can still page weights from disk if KV+compute fit.
    resident = kv + comp + RUNTIME_GB
    if allow_stream and resident < usable * 0.8 and dev.disk_read_mbs > 0:
        cached = max(0.0, usable - resident) * GB
        miss = max(0.0, m.active_bytes - cached)
        t = miss / (dev.disk_read_mbs * 1e6) + m.active_bytes / (dev.mem_bw_gbs * GB * CPU_EFF)
        return Fit(m, dev, ctx, kv_type, weights, kv, comp, total, usable, False, "stream",
                   1.0 / t if t else 0.0, 0,
                   "weights exceed RAM; mmap would page them from disk every token (very slow)")
    return Fit(m, dev, ctx, kv_type, weights, kv, comp, total, usable, False, "no", 0.0, 0,
               f"short by {total - usable:.2f} GB")


CTX_STEPS = [512, 1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072]


def best_ctx(m: ModelSpec, dev: Device, kv_type="f16", want: int | None = None,
             cap: int = 32768, headroom: float = 0.08) -> Fit:
    """Largest context (<= want/cap/training ctx) that fits with `headroom` of usable RAM to
    spare (an explicit `want` only has to fit). Returns the smallest-ctx Fit if none do."""
    limit = min(want or cap, m.n_ctx_train or cap)
    steps = sorted(set([c for c in CTX_STEPS if c <= limit] + ([want] if want and want <= limit
                                                                else [])) or {min(512, limit)})
    if want:
        f = fit_local(m, dev, min(want, limit), kv_type)
        return f
    best = None
    for c in steps:
        f = fit_local(m, dev, c, kv_type)
        ok = f.fits and (f.spare_gb >= headroom * f.usable_gb or c == steps[0])
        if ok:
            best = f
        else:
            if best is None:
                best = f
            break
    return best


def best_rung(m: ModelSpec, dev: Device, ctx: int, kv_type="f16"):
    """Highest-quality quant from the ladder that fits this device at `ctx` (None if none)."""
    cur = KNOWN_BPW.get(m.quant.upper())
    for q, bpw in QUANT_LADDER:
        if cur is not None:
            if bpw >= cur - 0.05:      # only rungs strictly below the file we have
                continue
            target = m.bpw * bpw / cur  # scale this file's real size by the ladder ratio
        else:
            if bpw > m.bpw - 0.05:
                continue
            target = bpw
        f = fit_local(m.at_bpw(target, q), dev, ctx, kv_type, allow_stream=False)
        if f.fits:
            return f
    return None


# ------------------------------------------------------------------ LAN cluster
@dataclass
class NodePlan:
    device: Device
    layers: int
    weights_gb: float
    kv_gb: float
    total_gb: float
    usable_gb: float
    compute_s: float
    is_host: bool = False


@dataclass
class ClusterPlan:
    model: ModelSpec
    ctx: int
    nodes: list
    fits: bool
    tok_s: float
    net_s: float
    compute_s: float
    load_s: float
    note: str = ""

    def tensor_split(self):
        """--tensor-split proportions, host-first order (callers reorder to device order)."""
        return [n.layers for n in self.nodes]


def plan_cluster(m: ModelSpec, host: Device, workers: list, ctx: int, kv_type="f16",
                 strategy: str = "fastest", act_bytes: int = 4) -> ClusterPlan:
    """Split a model's layers over host + RPC workers so no node exceeds its usable memory.

    llama.cpp RPC is a pipeline: each node holds whole layers (weights + their KV cache);
    per token the host sends the activation (n_embd floats) to each node in turn. So the
    network carries KBs per token, and per-hop *latency* dominates, not bandwidth.
    """
    kv_layer = m.kv_per_token * KV_TYPES[kv_type] / 2.0 * ctx / m.n_layers
    per_layer = m.per_layer_bytes + kv_layer
    nodes = [(host, True)] + [(w, False) for w in workers]
    caps = []
    for d, is_host in nodes:
        if not d.runs_llama_cpp:
            caps.append(0)
            continue
        comp = compute_buffer_gb(m, ctx, ubatch_for(d)) * GB
        cap = d.usable_gb() * GB - comp - RUNTIME_GB * GB
        if is_host:
            cap -= m.nonlayer_bytes
        caps.append(max(0, int(cap // per_layer)))
    remaining = m.n_layers
    alloc = [0] * len(nodes)
    order = list(range(len(nodes)))
    if strategy == "fastest":
        order.sort(key=lambda i: -(nodes[i][0].gpu_bw_gbs or nodes[i][0].mem_bw_gbs))
        for i in order:
            take = min(caps[i], remaining)
            alloc[i] = take
            remaining -= take
    else:  # proportional to capacity -- everybody (including the dishwasher) gets some
        tot = sum(caps) or 1
        for i in order:
            alloc[i] = min(caps[i], int(m.n_layers * caps[i] / tot))
        remaining = m.n_layers - sum(alloc)
        for i in sorted(order, key=lambda i: -(caps[i] - alloc[i])):
            take = min(caps[i] - alloc[i], remaining)
            alloc[i] += take
            remaining -= take
    plans, comp_s = [], 0.0
    for (d, is_host), n in zip(nodes, alloc):
        w = n * m.per_layer_bytes + (m.nonlayer_bytes if is_host else 0)
        kv = n * kv_layer
        comp = compute_buffer_gb(m, ctx, ubatch_for(d)) * GB
        tot = w + kv + (comp if (n or is_host) else 0) + RUNTIME_GB * GB
        on_gpu = bool(d.gpu)
        bw = (d.gpu_bw_gbs if on_gpu and d.gpu_bw_gbs else d.mem_bw_gbs) * GB
        eff = GPU_EFF if on_gpu else CPU_EFF
        frac = m.active_bytes / m.file_bytes if m.file_bytes else 1.0
        t = (w * frac + kv / 2) / (bw * eff) if n or is_host else 0.0
        comp_s += t
        plans.append(NodePlan(d, n, w / GB, kv / GB, tot / GB, d.usable_gb(), t, is_host))
    used_workers = [p for p in plans if not p.is_host and p.layers]
    # per used worker per token: ~2 round trips (activation in + graph recompute, result out;
    # worker-to-worker traffic relays through the host) + 2 activation transfers
    net_s = sum(2 * p.device.rtt_ms / 1000 + 2 * m.n_embd * act_bytes * 8 /
                (p.device.net_mbps * 1e6) for p in used_workers)
    load_s = sum(p.weights_gb * GB * 8 / (p.device.net_mbps * 1e6) for p in used_workers)
    fits = remaining <= 0
    tps = 1.0 / (comp_s + net_s) if fits and (comp_s + net_s) > 0 else 0.0
    note = "" if fits else f"{remaining} of {m.n_layers} layers have nowhere to go"
    return ClusterPlan(m, ctx, plans, fits, tps, net_s, comp_s, load_s, note)


def quality_score(m: ModelSpec) -> float:
    """Ranking heuristic: bigger models win, but very low bpw is discounted."""
    q = min(1.0, max(0.15, (m.bpw - 1.5) / 3.0))
    return m.n_params / 1e9 * q
