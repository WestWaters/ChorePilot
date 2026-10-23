"""Minimal, stdlib-only GGUF header reader.

Reads the metadata key/values and tensor table of a GGUF file (local path, or any
seekable file-like object such as an HTTP range reader) without touching the weights.
That is all ChorePilot needs to size a model: architecture, layer count, KV-head
geometry, context length, parameter count, and how many bytes are expert (MoE) tensors.
"""
from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass, field

GGUF_MAGIC = b"GGUF"

# GGUF metadata value types
_U8, _I8, _U16, _I16, _U32, _I32, _F32, _BOOL, _STR, _ARR, _U64, _I64, _F64 = range(13)
_SCALAR = {
    _U8: "<B", _I8: "<b", _U16: "<H", _I16: "<h", _U32: "<I", _I32: "<i",
    _F32: "<f", _BOOL: "<?", _U64: "<Q", _I64: "<q", _F64: "<d",
}

# ggml tensor types -> (block size in elements, bytes per block). Used only to size
# tensors when the file size alone is not enough (e.g. splitting expert vs dense bytes).
GGML_TYPES = {
    0: ("F32", 1, 4), 1: ("F16", 1, 2), 2: ("Q4_0", 32, 18), 3: ("Q4_1", 32, 20),
    6: ("Q5_0", 32, 22), 7: ("Q5_1", 32, 24), 8: ("Q8_0", 32, 34), 9: ("Q8_1", 32, 36),
    10: ("Q2_K", 256, 84), 11: ("Q3_K", 256, 110), 12: ("Q4_K", 256, 144),
    13: ("Q5_K", 256, 176), 14: ("Q6_K", 256, 210), 15: ("Q8_K", 256, 292),
    16: ("IQ2_XXS", 256, 66), 17: ("IQ2_XS", 256, 74), 18: ("IQ3_XXS", 256, 98),
    19: ("IQ1_S", 256, 50), 20: ("IQ4_NL", 32, 18), 21: ("IQ3_S", 256, 110),
    22: ("IQ2_S", 256, 82), 23: ("IQ4_XS", 256, 136), 24: ("I8", 1, 1),
    25: ("I16", 1, 2), 26: ("I32", 1, 4), 27: ("I64", 1, 8), 28: ("F64", 1, 8),
    29: ("IQ1_M", 256, 56), 30: ("BF16", 1, 2), 34: ("TQ1_0", 256, 54),
    35: ("TQ2_0", 256, 66), 39: ("MXFP4", 32, 17),
}

# Types stock llama.cpp knows. Anything above this is a fork-only type (e.g. ik_llama's
# IQ4_K) and stock llama-server will refuse the file -- worth warning about up front.
STOCK_MAX_TYPE = 42

_EXPERT_RE = re.compile(r"_exps\b|\.ffn_(gate|up|down)_exps")


@dataclass
class GGUFInfo:
    path: str
    version: int
    meta: dict
    n_params: int
    file_bytes: int
    expert_bytes: int = 0          # bytes in routed-expert tensors (MoE)
    layer_bytes: int = 0           # bytes in repeating blk.N.* tensors (what RPC nodes hold)
    n_vocab: int = 0
    tensor_types: dict = field(default_factory=dict)  # type name -> count
    nonstock_types: list = field(default_factory=list)

    # ---- convenience accessors -------------------------------------------------
    @property
    def arch(self) -> str:
        return str(self.meta.get("general.architecture", "unknown"))

    def a(self, suffix, default=None):
        """Architecture-scoped key, e.g. a('block_count') -> llama.block_count."""
        return self.meta.get(f"{self.arch}.{suffix}", default)

    @property
    def name(self) -> str:
        return str(self.meta.get("general.name") or os.path.basename(self.path))

    @property
    def n_layers(self) -> int:
        return int(self.a("block_count", 0) or 0)

    @property
    def n_embd(self) -> int:
        return int(self.a("embedding_length", 0) or 0)

    @property
    def n_ctx_train(self) -> int:
        return int(self.a("context_length", 0) or 0)

    @property
    def n_head(self) -> int:
        v = self.a("attention.head_count", 0)
        return int(max(v) if isinstance(v, list) else v or 0)

    @property
    def n_head_kv(self):
        """Per-layer list of KV-head counts (0 = layer has no KV cache, e.g. SSM/linear)."""
        v = self.a("attention.head_count_kv", None)
        if v is None:
            v = self.n_head
        if isinstance(v, list):
            return [int(x) for x in v]
        return [int(v)] * self.n_layers

    @property
    def head_dim_k(self) -> int:
        v = self.a("attention.key_length")
        if v:
            return int(v)
        return self.n_embd // self.n_head if self.n_head else 0

    @property
    def head_dim_v(self) -> int:
        v = self.a("attention.value_length")
        return int(v) if v else self.head_dim_k

    @property
    def kv_lora_rank(self) -> int:
        return int(self.a("attention.kv_lora_rank", 0) or 0)

    @property
    def sliding_window(self) -> int:
        return int(self.a("attention.sliding_window", 0) or 0)

    @property
    def expert_count(self) -> int:
        return int(self.a("expert_count", 0) or 0)

    @property
    def expert_used(self) -> int:
        return int(self.a("expert_used_count", 0) or 0)

    @property
    def bpw(self) -> float:
        return self.file_bytes * 8 / self.n_params if self.n_params else 0.0

    @property
    def active_bytes(self) -> int:
        """Bytes read per decoded token: all dense weights + only the routed experts used."""
        if self.expert_count and self.expert_used and self.expert_bytes:
            dense = self.file_bytes - self.expert_bytes
            return int(dense + self.expert_bytes * self.expert_used / self.expert_count)
        return self.file_bytes

    @property
    def per_layer_bytes(self) -> float:
        lb = self.layer_bytes or self.file_bytes * 0.9
        return lb / self.n_layers if self.n_layers else lb

    @property
    def nonlayer_bytes(self) -> int:
        """Embeddings + output head: stays on the host in a cluster split."""
        return max(0, self.file_bytes - (self.layer_bytes or int(self.file_bytes * 0.9)))

    def kv_bytes_per_token(self, kv_elem_bytes: float = 2.0) -> float:
        """K+V cache bytes per token of context (f16 cache by default)."""
        if self.kv_lora_rank:  # MLA: one compressed latent per layer
            rope = int(self.a("rope.dimension_count", 64) or 64)
            return self.n_layers * (self.kv_lora_rank + rope) * kv_elem_bytes
        heads = self.n_head_kv
        interval = int(self.a("full_attention_interval", 0) or 0)
        if interval > 1 and len(set(heads)) == 1:
            # hybrid linear-attention models (Qwen3.5/3-Next style): only every Nth layer has KV
            heads = heads[interval - 1::interval]
        return sum(h for h in heads) * (self.head_dim_k + self.head_dim_v) * kv_elem_bytes


class _Reader:
    def __init__(self, f):
        self.f = f

    def read(self, n):
        b = self.f.read(n)
        if len(b) != n:
            raise ValueError("truncated GGUF header")
        return b

    def u32(self):
        return struct.unpack("<I", self.read(4))[0]

    def u64(self):
        return struct.unpack("<Q", self.read(8))[0]

    def string(self):
        return self.read(self.u64()).decode("utf-8", "replace")

    def value(self, t, keep_arrays=True):
        if t in _SCALAR:
            fmt = _SCALAR[t]
            return struct.unpack(fmt, self.read(struct.calcsize(fmt)))[0]
        if t == _STR:
            return self.string()
        if t == _ARR:
            et, n = self.u32(), self.u64()
            if et in _SCALAR and not keep_arrays:
                self.read(struct.calcsize(_SCALAR[et]) * n)
                return None
            if et in _SCALAR:
                fmt = _SCALAR[et]
                sz = struct.calcsize(fmt)
                raw = self.read(sz * n)
                return list(struct.unpack(f"<{n}{fmt[1]}", raw))
            out = [self.value(et, keep_arrays) for _ in range(n)]
            return out if keep_arrays else None
        raise ValueError(f"unknown GGUF value type {t}")


# Large arrays we never need (tokenizer vocab) -- skip them instead of materialising.
_SKIP_ARRAYS = ("tokenizer.ggml.tokens", "tokenizer.ggml.scores", "tokenizer.ggml.token_type",
                "tokenizer.ggml.merges")


def _tensor_bytes(ttype, n_elems):
    info = GGML_TYPES.get(ttype)
    if not info:
        return None
    _, blk, bpb = info
    return n_elems // blk * bpb


def read_gguf(src, file_bytes: int | None = None, path: str | None = None) -> GGUFInfo:
    """Parse a GGUF header. `src` is a path or a binary file-like object."""
    close = False
    if isinstance(src, (str, os.PathLike)):
        path = str(src)
        f = open(src, "rb")
        close = True
        if file_bytes is None:
            file_bytes = sum(os.path.getsize(p) for p in shard_paths(path))
    else:
        f = src
    try:
        r = _Reader(f)
        if r.read(4) != GGUF_MAGIC:
            raise ValueError("not a GGUF file")
        version = r.u32()
        n_tensors, n_kv = r.u64(), r.u64()
        meta = {}
        for _ in range(n_kv):
            key = r.string()
            t = r.u32()
            meta[key] = r.value(t, keep_arrays=key not in _SKIP_ARRAYS)
        n_params = 0
        expert_bytes = 0
        layer_bytes = 0
        n_vocab = 0
        types: dict = {}
        nonstock = set()
        for _ in range(n_tensors):
            name = r.string()
            nd = r.u32()
            dims = [r.u64() for _ in range(nd)]
            ttype = r.u32()
            r.u64()  # offset
            ne = 1
            for d in dims:
                ne *= d
            n_params += ne
            tname = GGML_TYPES.get(ttype, (f"type{ttype}",))[0]
            types[tname] = types.get(tname, 0) + 1
            if ttype > STOCK_MAX_TYPE:
                nonstock.add(tname)
            tb = _tensor_bytes(ttype, ne) or 0
            if _EXPERT_RE.search(name):
                expert_bytes += tb
            if name.startswith("blk."):
                layer_bytes += tb
            if name == "token_embd.weight" and len(dims) >= 2:
                n_vocab = int(dims[1])
        info = GGUFInfo(path=path or "<stream>", version=version, meta=meta,
                        n_params=n_params, file_bytes=int(file_bytes or 0),
                        expert_bytes=expert_bytes, layer_bytes=layer_bytes,
                        n_vocab=n_vocab or len(meta.get("tokenizer.ggml.tokens") or []),
                        tensor_types=types,
                        nonstock_types=sorted(nonstock))
        # A split GGUF only lists its own shard's tensors; scale params by bytes if so.
        split_count = int(meta.get("split.count", 1) or 1)
        if split_count > 1 and path:
            first = os.path.getsize(path)
            if first and info.file_bytes > first:
                info.n_params = int(n_params * info.file_bytes / first)
                info.expert_bytes = int(expert_bytes * info.file_bytes / first)
                info.layer_bytes = int(layer_bytes * info.file_bytes / first)
        return info
    finally:
        if close:
            f.close()


_SPLIT_RE = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$")


def shard_paths(path: str):
    """All shards of a split GGUF (name-00001-of-00003.gguf), or just [path]."""
    m = _SPLIT_RE.search(path)
    if not m:
        return [path]
    total = int(m.group(2))
    base = path[: m.start()]
    out = [f"{base}-{i:05d}-of-{total:05d}.gguf" for i in range(1, total + 1)]
    return [p for p in out if os.path.exists(p)] or [path]


def is_gguf(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == GGUF_MAGIC
    except OSError:
        return False
