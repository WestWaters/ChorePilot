"""`chorepilot recommend`: of the models you have (and optionally the PollardWeights
catalog), what can this device run, at what quant and context, how fast -- and what to do
when nothing fits (smaller rung, mmap streaming, or a LAN cluster)."""
from __future__ import annotations

from dataclasses import dataclass

from .fit import Fit, ModelSpec, best_ctx, best_rung, fit_local, plan_cluster, quality_score
from .profiles import Device


@dataclass
class Rec:
    spec: ModelSpec
    fit: Fit
    verdict: str          # RUNS | TIGHT | REQUANT | STREAM | CLUSTER | NO
    advice: str
    origin: str           # "local" | "catalog"
    cluster: object = None

    @property
    def rank(self):
        order = {"RUNS": 0, "TIGHT": 1, "REQUANT": 2, "CLUSTER": 3, "STREAM": 4, "NO": 5}
        fork_only = 1 if self.spec.warnings and self.verdict in ("RUNS", "TIGHT") else 0
        return (order[self.verdict] + 1.5 * fork_only, -quality_score(self.spec))


def assess(spec: ModelSpec, dev: Device, ctx: int | None, kv_type="f16", origin="local",
           workers=None, min_ctx=2048, min_tok_s=0.5) -> Rec:
    need = ctx or min(min_ctx, spec.n_ctx_train or min_ctx)
    f = fit_local(spec, dev, need, kv_type)
    if f.fits:
        top = f if ctx else best_ctx(spec, dev, kv_type)
        tight = f.spare_gb < max(0.1, 0.1 * f.usable_gb)
        slow = f.tok_s < min_tok_s
        v = "TIGHT" if tight else "RUNS"
        adv = (f"ctx {need}" + (f" (up to {top.ctx})" if top.fits and top.ctx > need else "")
               + f", ~{f.tok_s:.1f} tok/s" + (" (slow -- a smaller model will feel better)"
                                              if slow else ""))
        return Rec(spec, f, v, adv, origin)
    rung = best_rung(spec, dev, need, kv_type)
    if rung and origin == "local":
        return Rec(spec, rung, "REQUANT",
                   f"re-quantize to {rung.model.quant} (~{rung.weights_gb:.2f} GB) -> ctx {need}, "
                   f"~{rung.tok_s:.1f} tok/s", origin)
    if workers:
        plan = plan_cluster(spec, dev, workers, need, kv_type)
        if plan.fits:
            return Rec(spec, f, "CLUSTER",
                       f"split over {sum(1 for n in plan.nodes if n.layers)} nodes, "
                       f"~{plan.tok_s:.2f} tok/s", origin, plan)
    if f.mode == "stream" and f.tok_s > 0:
        return Rec(spec, f, "STREAM", f"mmap-streams from disk at ~{f.tok_s:.3f} tok/s "
                   "(technically runs; practically, add a node)", origin)
    return Rec(spec, f, "NO", f.note or "does not fit", origin)


def recommend(models, dev: Device, ctx=None, kv_type="f16", catalog=None, workers=None):
    recs = [assess(ModelSpec.from_gguf(m), dev, ctx, kv_type, "local", workers) for m in models]
    for spec in catalog or []:
        recs.append(assess(spec, dev, ctx, kv_type, "catalog", workers))
    recs.sort(key=lambda r: r.rank)
    return recs


def _gb(x):
    return f"{x:5.2f}" if x < 100 else f"{x:5.0f}"


def render(recs, dev: Device, limit=25, show_no=False) -> str:
    lines = [f"Target: {dev.name} -- {dev.ram_gb:g} GB RAM, ~{dev.usable_gb():.2f} GB usable, "
             f"{dev.cores} cores, ~{dev.mem_bw_gbs:g} GB/s" + (f", {dev.gpu}" if dev.gpu else ""),
             f"{'verdict':<8} {'model':<46} {'quant':<9} {'size':>5} {'need':>5}  advice"]
    shown = 0
    for r in recs:
        if r.verdict == "NO" and not show_no:
            continue
        if shown >= limit:
            break
        name = r.spec.name[:44] + (" *" if r.origin == "catalog" else "")
        lines.append(f"{r.verdict:<8} {name:<46} {r.spec.quant[:9]:<9} "
                     f"{_gb(r.spec.file_bytes / 1e9)} {_gb(r.fit.total_gb)}  {r.advice}")
        for w in r.spec.warnings:
            lines.append(f"{'':<8} ! {w}")
        shown += 1
    hidden = sum(1 for r in recs if r.verdict == "NO")
    if hidden and not show_no:
        lines.append(f"({hidden} more don't fit at all; --all to list them)")
    if any(r.origin == "catalog" for r in recs):
        lines.append("* = PollardWeights catalog rung on Hugging Face (download with `hf download`)")
    if not recs:
        lines.append("No GGUF models found. Point --models-dir at yours, or add --catalog.")
    return "\n".join(lines)
