"""Optional online catalog: PollardWeights GGUF ladders on Hugging Face.

Lists the org's repos, their GGUF rungs and exact file sizes, and reads each candidate's
GGUF *header only* with HTTP Range requests (a few MB, never the weights) so KV-cache
geometry is exact. Results are cached in ~/.cache/chorepilot/catalog.json. Offline is fine:
recommend just works on local models.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
import time
import urllib.parse
import urllib.request

from .fit import _QNAME, ModelSpec
from .gguf import read_gguf

HF = "https://huggingface.co"
DEFAULT_ORG = "PollardWeights"
CACHE = os.path.expanduser("~/.cache/chorepilot/catalog.json")
UA = {"User-Agent": "chorepilot/0.3"}


def _get_json(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


class RangeReader:
    """Seekless file-like over HTTP: read(n) fetches only the byte ranges it needs."""

    def __init__(self, url, chunk=4 << 20, max_bytes=64 << 20):
        self.url, self.chunk, self.max_bytes = url, chunk, max_bytes
        self.pos, self.buf_start, self.buf = 0, 0, b""

    def _fill(self, need_end):
        while self.buf_start + len(self.buf) < need_end:
            start = self.buf_start + len(self.buf)
            if start >= self.max_bytes:
                raise ValueError("GGUF header larger than the range budget")
            req = urllib.request.Request(self.url, headers={
                **UA, "Range": f"bytes={start}-{start + self.chunk - 1}"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            if not data:
                raise ValueError("short read")
            self.buf += data

    def read(self, n):
        self._fill(self.pos + n)
        off = self.pos - self.buf_start
        out = self.buf[off: off + n]
        self.pos += n
        return out


def _load_cache():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_cache(c):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w") as f:
        json.dump(c, f)


def list_rungs(org=DEFAULT_ORG, limit=100):
    """[(repo, filename, size_bytes)] for every main-model GGUF in the org (no mmproj/imatrix)."""
    models = _get_json(f"{HF}/api/models?author={urllib.parse.quote(org)}&limit={limit}")
    out, seen = [], set()
    for m in models:
        repo = m["id"]
        try:
            info = _get_json(f"{HF}/api/models/{repo}?blobs=true")
        except OSError:
            continue
        for s in info.get("siblings", []):
            fn = s.get("rfilename", "")
            low = fn.lower()
            if not low.endswith(".gguf") or "mmproj" in low or "imatrix" in low or "/" in fn:
                continue
            if "-of-" in fn and "-00001-of-" not in fn:
                continue
            size = int(s.get("size") or 0)
            if (fn, size) in seen:      # same file mirrored in two repos
                continue
            seen.add((fn, size))
            out.append((repo, fn, size))
    return out


def spec_for(repo, fn, size, cache=None):
    """ModelSpec for one remote rung; header read over HTTP ranges, cached."""
    cache = _load_cache() if cache is None else cache
    key = f"{repo}/{fn}@{size}"
    if key in cache:
        spec = ModelSpec(**cache[key])
        hits = _QNAME.findall(fn)
        spec.quant = hits[-1].upper() if hits else spec.quant
        return spec
    url = f"{HF}/{repo}/resolve/main/{urllib.parse.quote(fn)}"
    info = read_gguf(RangeReader(url), file_bytes=size, path=f"hf://{repo}/{fn}")
    spec = ModelSpec.from_gguf(info)
    spec.name = fn.removesuffix(".gguf")
    spec.source = f"hf://{repo}/{fn}"
    cache[key] = spec.__dict__.copy()
    return spec


def catalog_specs(max_bytes: float, org=DEFAULT_ORG, verbose=False, workers=8):
    """Specs for every rung whose *file* is <= max_bytes (others can't fit anyway)."""
    cache = _load_cache()
    t0 = time.time()
    todo = [(r, f, s) for r, f, s in list_rungs(org) if s and s <= max_bytes]

    def one(item):
        try:
            return spec_for(*item, cache)
        except (OSError, ValueError) as e:
            if verbose:
                print(f"  catalog: skip {item[0]}/{item[1]}: {e}")
            return None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        specs = [s for s in ex.map(one, todo) if s is not None]
    _save_cache(cache)
    if verbose:
        print(f"  catalog: {len(specs)} rungs considered in {time.time() - t0:.1f}s")
    return specs
