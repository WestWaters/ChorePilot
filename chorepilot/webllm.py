"""The dashboard's "Local AI" panel: which model answers, how fast, on what hardware.

Everything is read from what the model server already exposes, and anything it doesn't expose is
simply left out (the panel shows a dash):

  llama-server  GET /props (context size, model file, slots), GET /metrics when started with
                --metrics (prompt and generation tokens/s, KV-cache use, requests in flight)
  Ollama        GET /api/ps (loaded model, its size and how much of it sits in GPU memory,
                context length)
  vLLM          GET /metrics (KV-cache use, throughput)
  any server    GET /v1/models (the model id)

plus what ChorePilot measured itself on its last requests (tokens/s, time to first token), and
the host from `chorepilot probe` (GPU / unified memory). Every call has a short timeout; a slow
or missing endpoint never slows the dashboard down.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request

_TIMEOUT = 0.8


def _get(url, timeout=_TIMEOUT):
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as r:
            if r.status != 200:
                return None
            return r.read(512 * 1024).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - not every server has every endpoint
        return None


def _json(url):
    t = _get(url)
    if t is None:
        return None
    try:
        return json.loads(t)
    except ValueError:
        return None


def prom(text):
    """Prometheus text -> {metric_name: value} (labels dropped, last sample wins)."""
    out = {}
    for ln in (text or "").splitlines():
        if not ln or ln.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_:][\w:]*)(\{[^}]*\})?\s+([-+0-9.eE]+|NaN)", ln)
        if m:
            try:
                out[m.group(1)] = float(m.group(3))
            except ValueError:
                pass
    return out


def server_metrics(url):
    """What this model server exposes. Never raises."""
    url = url.rstrip("/")
    out = {"server": None, "model": None, "ctx": None, "gen_tok_s": None, "prompt_tok_s": None,
           "kv_used": None, "busy": None, "model_gb": None, "gpu_gb": None, "slots": None}
    props = _json(url + "/props")
    if isinstance(props, dict) and ("default_generation_settings" in props or "model_path" in props):
        out["server"] = "llama.cpp"
        g = props.get("default_generation_settings") or {}
        out["ctx"] = g.get("n_ctx") or props.get("n_ctx")
        mp = props.get("model_path") or ""
        out["model"] = mp.rsplit("/", 1)[-1] or None
        out["slots"] = props.get("total_slots")
    ps = _json(url + "/api/ps") if out["server"] is None else None
    if isinstance(ps, dict) and isinstance(ps.get("models"), list):
        out["server"] = "Ollama"
        if ps["models"]:
            m = ps["models"][0]
            out["model"] = m.get("name") or m.get("model")
            out["model_gb"] = round((m.get("size") or 0) / 1e9, 1) or None
            out["gpu_gb"] = round((m.get("size_vram") or 0) / 1e9, 1) or None
            out["ctx"] = m.get("context_length")
    met = prom(_get(url + "/metrics"))
    if met:
        if any(k.startswith("llamacpp:") for k in met):
            out["server"] = out["server"] or "llama.cpp"
            out["gen_tok_s"] = met.get("llamacpp:predicted_tokens_seconds") or None
            out["prompt_tok_s"] = met.get("llamacpp:prompt_tokens_seconds") or None
            kv = met.get("llamacpp:kv_cache_usage_ratio")
            out["kv_used"] = round(kv, 3) if kv is not None else None
            out["busy"] = met.get("llamacpp:requests_processing")
        elif any(k.startswith("vllm:") for k in met):
            out["server"] = "vLLM"
            kv = met.get("vllm:gpu_cache_usage_perc") or met.get("vllm:kv_cache_usage_perc")
            out["kv_used"] = round(kv, 3) if kv is not None else None
            out["busy"] = met.get("vllm:num_requests_running")
            out["gen_tok_s"] = met.get("vllm:avg_generation_throughput_toks_per_s") or None
    models = _json(url + "/v1/models")              # the served name (an --alias) beats the file name
    if isinstance(models, dict):
        ids = [m.get("id") for m in models.get("data") or [] if isinstance(m, dict)]
        if ids:
            out["model"] = str(ids[0]).rsplit("/", 1)[-1]
    if out["model"]:
        out["model"] = re.sub(r"\.gguf$", "", out["model"], flags=re.I)
    return out


class Stats:
    """What ChorePilot measured on its own recent model calls."""

    def __init__(self):
        self.lock = threading.Lock()
        self.calls = []

    def wrap(self, chat_fn):
        def chat(url, messages, *a, **k):
            t = time.time()
            out = chat_fn(url, messages, *a, **k)
            took = time.time() - t
            tm = (out or {}).get("timings") or {}
            u = (out or {}).get("usage") or {}
            gen = tm.get("predicted_per_second")
            if gen is None and u.get("completion_tokens") and took > 0:
                gen = u["completion_tokens"] / took
            ttft = tm.get("prompt_ms") / 1000 if tm.get("prompt_ms") is not None else None
            with self.lock:
                self.calls = (self.calls + [{"at": time.time(), "s": round(took, 3),
                                             "tok_s": gen, "ttft_s": ttft,
                                             "prompt_n": tm.get("prompt_n") or u.get("prompt_tokens")}])[-20:]
            return out
        return chat

    def view(self):
        with self.lock:
            c = list(self.calls)
        if not c:
            return {"calls": 0}
        last = c[-1]
        toks = [x["tok_s"] for x in c if x.get("tok_s")]
        ttfts = [x["ttft_s"] for x in c if x.get("ttft_s") is not None]
        return {"calls": len(c), "last_s": last["s"], "tok_s": round(toks[-1], 1) if toks else None,
                "avg_tok_s": round(sum(toks) / len(toks), 1) if toks else None,
                "ttft_s": round(ttfts[-1], 3) if ttfts else None,
                "history": [round(x["tok_s"], 1) for x in c if x.get("tok_s")][-12:],
                "ago_s": round(time.time() - last["at"])}


_HOST = {"t": 0, "v": None}
_HOST_LOCK = threading.Lock()


def host():
    """This machine, from `chorepilot probe` (cached; the first call can take a second)."""
    with _HOST_LOCK:
        if _HOST["v"] is not None and time.time() - _HOST["t"] < 600:
            return _HOST["v"]
        try:
            from .probe import probe
            d = probe()
            gname = (d.extra or {}).get("gpu_name") or (d.gpu if d.gpu not in (None, "none") else None)
            unified = d.gpu == "metal" or (d.vram_gb or 0) == 0 and bool(gname)
            mem = (f"{d.ram_gb:.0f} GB unified" if unified else
                   f"{d.vram_gb:.0f} GB GPU" if d.vram_gb else f"{d.ram_gb:.0f} GB RAM")
            v = {"name": d.name, "cpu": d.cpu, "gpu": gname, "memory": mem,
                 "ram_gb": round(d.ram_gb, 1), "free_gb": round(d.avail_gb, 1),
                 "vram_gb": round(d.vram_gb or 0, 1), "unified": unified,
                 "mem_bw_gbs": d.mem_bw_gbs, "os": d.os,
                 "label": " · ".join(x for x in (gname or d.cpu, mem) if x)}
        except Exception as e:  # noqa: BLE001
            v = {"label": None, "error": str(e)[:120]}
        _HOST.update(t=time.time(), v=v)
        return v
