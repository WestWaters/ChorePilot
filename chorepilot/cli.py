"""ChorePilot command line (the `dishsoaker` and `wash` commands are aliases)."""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .fit import ModelSpec, plan_cluster
from .gguf import read_gguf
from .profiles import PRESETS, WIFI, Device, apply_network, load_profile


# ------------------------------------------------------------------ shared
def _device(args) -> Device:
    if getattr(args, "profile", None):
        dev = load_profile(args.profile)
    else:
        from .probe import probe
        dev = probe(measure=getattr(args, "measure", False))
    if getattr(args, "link", None):
        apply_network(dev, args.link)
    return dev


def _workers(specs, link=None):
    out = []
    for s in specs or []:
        d = load_profile(s)
        if link:
            apply_network(d, link)
        out.append(d)
    return out


def _scan_models(args):
    from .models import scan
    return scan(args.models_dir, include_defaults=not args.no_defaults,
                include_ollama=not args.no_defaults)


SYNTHETIC = {  # what-if geometries (from published configs; see docs/RESEARCH.md)
    "27b": dict(n_params=27_000_000_000, n_layers=64, n_embd=5120, n_vocab=248_320,
                kv_per_token=65536, n_ctx_train=262144, arch="qwen35-like"),
    "8b": dict(n_params=8_000_000_000, n_layers=32, n_embd=4096, n_vocab=128_256,
               kv_per_token=131072, n_ctx_train=131072, arch="llama3-like"),
    "1b": dict(n_params=1_000_000_000, n_layers=16, n_embd=2048, n_vocab=128_256,
               kv_per_token=32768, n_ctx_train=131072, arch="llama3.2-like"),
}


def _spec_from_arg(m: str) -> ModelSpec:
    """A GGUF path, or synthetic:<27b|8b|1b>@<bpw> for what-if planning."""
    if m.startswith("synthetic:"):
        name, _, bpw = m.split(":", 1)[1].partition("@")
        g = dict(SYNTHETIC[name])
        bpw = float(bpw or 4.0)
        fb = int(g["n_params"] * bpw / 8)
        emb = g["n_vocab"] * g["n_embd"] * 2 * bpw / 8
        return ModelSpec(name=f"synthetic-{name}@{bpw}bpw", file_bytes=fb, active_bytes=fb,
                         per_layer_bytes=(fb - emb) / g["n_layers"], nonlayer_bytes=int(emb),
                         quant=f"{bpw}bpw", source="synthetic", **g)
    return ModelSpec.from_gguf(read_gguf(m))


def _pick_model(args, dev):
    """--model PATH, --model substring of a scanned model, or the best local RUNS model."""
    from .recommend import recommend
    if args.model and os.path.isfile(args.model):
        return read_gguf(args.model)
    models = _scan_models(args)
    if args.model:
        hits = [m for m in models if args.model.lower() in (m.path + m.name).lower()]
        if not hits:
            raise SystemExit(f"no local model matches {args.model!r}")
        return hits[0]
    recs = [r for r in recommend(models, dev, args.ctx) if r.verdict in ("RUNS", "TIGHT")]
    if not recs:
        raise SystemExit("nothing on disk fits this device; run `chorepilot recommend --catalog`")
    path = recs[0].spec.source
    return next(m for m in models if m.path == path)


# ------------------------------------------------------------------ commands
def cmd_probe(args):
    dev = _device(args)
    if args.json:
        print(json.dumps(dev.to_dict(), indent=2))
        return
    print(f"{dev.name}  ({dev.description or dev.os})")
    print(f"  RAM      {dev.ram_gb:g} GB total, {dev.avail_gb or dev.ram_gb:g} GB available, "
          f"~{dev.usable_gb():.2f} GB usable by a model (guarded)")
    print(f"  CPU      {dev.cpu} -- {dev.cores} cores for decode, ~{dev.mem_bw_gbs:g} GB/s memory")
    if dev.gpu:
        print(f"  GPU      {dev.gpu} {dev.extra.get('gpu_name', '')} ~{dev.gpu_bw_gbs:g} GB/s"
              + (f", {dev.vram_gb:g} GB VRAM free" if dev.vram_gb else " (unified memory)"))
    print(f"  Disk     {dev.disk_free_gb:g} GB free")
    lan = dev.extra.get("lan_ip")
    print(f"  Network  ~{dev.net_mbps:g} Mbps, ~{dev.rtt_ms:g} ms RTT"
          + (f" ({dev.extra.get('link')}, {lan})" if lan else ""))
    if not dev.runs_llama_cpp:
        print("  !! cannot run llama.cpp -- control-only device")
    if dev.notes:
        print(f"  Notes    {dev.notes}")


def cmd_recommend(args):
    from .recommend import recommend, render
    dev = _device(args)
    models = _scan_models(args)
    cat = []
    if args.catalog:
        from .catalog import catalog_specs
        budget = max(dev.usable_gb(), sum(w.usable_gb() for w in _workers(args.worker)) +
                     dev.usable_gb()) * 1e9
        try:
            cat = catalog_specs(budget, verbose=args.verbose)
        except OSError as e:
            print(f"(catalog unavailable offline: {e})")
    recs = recommend(models, dev, args.ctx, args.kv, cat, _workers(args.worker, args.link))
    if args.json:
        print(json.dumps([{"model": r.spec.name, "source": r.spec.source, "verdict": r.verdict,
                           "quant": r.spec.quant, "file_gb": round(r.spec.file_bytes / 1e9, 3),
                           "need_gb": round(r.fit.total_gb, 3), "ctx": r.fit.ctx,
                           "tok_s": round(r.fit.tok_s, 2), "advice": r.advice} for r in recs],
                         indent=2))
        return
    print(render(recs, dev, args.limit, args.all))


def cmd_plan(args):
    host = _device(args)
    workers = _workers(args.worker, args.link)
    spec = _spec_from_arg(args.model)
    plan = plan_cluster(spec, host, workers, args.ctx or 4096, args.kv, args.strategy)
    print(f"{spec.name}: {spec.file_bytes / 1e9:.2f} GB, {spec.n_layers} layers, "
          f"ctx {plan.ctx}, strategy {args.strategy}")
    for n in plan.nodes:
        tag = "host" if n.is_host else "rpc "
        print(f"  {tag} {n.device.name:<24} layers {n.layers:>3}   {n.total_gb:6.2f} / "
              f"{n.usable_gb:6.2f} GB   {n.compute_s * 1000:7.1f} ms/token")
    if plan.fits:
        print(f"  => ~{plan.tok_s:.2f} tok/s  (compute {plan.compute_s * 1000:.0f} ms + network "
              f"{plan.net_s * 1000:.0f} ms per token); first load ships weights in "
              f"~{plan.load_s:.0f} s (cached after with --cache)")
    else:
        print(f"  => does not fit: {plan.note}")


def cmd_scan(args):
    from .serve import scan
    found = scan(args.targets or None, timeout=args.timeout)
    if not found:
        print("no ChorePilot workers answered (start one with `chorepilot serve --worker --lan`)")
    for ep, d in found.items():
        print(f"{ep:<22} {d.name:<20} {d.ram_gb:g} GB RAM, ~{d.usable_gb():.2f} GB lendable, "
              f"{d.cores} cores")


def _speed(args):
    from . import serve
    if not (getattr(args, "draft", None) or getattr(args, "spec", None)):
        return []
    srv = serve.llama_server_bin()
    if not srv:
        raise SystemExit("llama-server not found")
    return serve.speed_args(srv, args.draft, args.spec, args.draft_max)


def cmd_serve(args):
    from . import serve
    dev = _device(args)
    if args.worker:
        serve.serve_worker(dev, port=args.rpc_port, lan=args.lan, cache=args.cache,
                           cpu_only=args.cpu_only, beacon_port=args.beacon_port)
        return
    info = _pick_model(args, dev)
    if args.rpc or args.scan is not None:
        workers = {}
        if args.scan is not None:
            workers.update(serve.scan(args.scan or None))
        for ep in (args.rpc.split(",") if args.rpc else []):
            if ep not in workers:
                found = serve.scan([ep.split(":")[0]])
                workers[ep] = next(iter(found.values()), None) or load_profile(
                    args.worker_profile or "dishwasher-1g")
        if not workers:
            raise SystemExit("no workers found")
        serve.serve_host(info, dev, workers, ctx=args.ctx, port=args.port, host=args.host,
                         strategy=args.strategy, force=args.force, kv_type=args.kv,
                         dry_run=args.dry_run, extra_args=_speed(args))
        return
    serve.serve_local(info, dev, ctx=args.ctx, port=args.port, host=args.host, force=args.force,
                      kv_type=args.kv, extra_args=_speed(args))


def cmd_wash(args):
    from .wash import wash
    wash(args)


# ------------------------------------------------------------------ parser
def build_parser():
    p = argparse.ArgumentParser(prog="chorepilot", description="ChorePilot: your own local LLM "
                                "runs the house and keeps track of the chores. It started in a "
                                "dishwasher (Dishsoaker3000): it still runs models on anything with "
                                "RAM and Wi-Fi.")
    p.add_argument("--version", action="version", version=f"chorepilot {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def dev_args(sp):
        sp.add_argument("--profile", help=f"simulate a device: {', '.join(PRESETS)} or a JSON file")
        sp.add_argument("--link", choices=list(WIFI), help="override network link type")
        sp.add_argument("--measure", action="store_true", help="measure memory bandwidth")

    def model_args(sp):
        sp.add_argument("--models-dir", action="append", default=[], help="extra model dir/file")
        sp.add_argument("--no-defaults", action="store_true", help="only scan --models-dir")
        sp.add_argument("--ctx", type=int, help="context length you need")
        sp.add_argument("--kv", default="f16", choices=["f16", "q8_0", "q4_0"], help="KV cache type")

    sp = sub.add_parser("probe", help="what is this machine (or a simulated one)?")
    dev_args(sp)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_probe)

    sp = sub.add_parser("recommend", help="what can it run, from your models (+catalog)?")
    dev_args(sp)
    model_args(sp)
    sp.add_argument("--catalog", action="store_true", help="include PollardWeights rungs (online)")
    sp.add_argument("--worker", action="append", help="LAN worker profile(s) to consider clustering with")
    sp.add_argument("--all", action="store_true", help="also list models that don't fit")
    sp.add_argument("--limit", type=int, default=25)
    sp.add_argument("--json", action="store_true")
    sp.add_argument("-v", "--verbose", action="store_true")
    sp.set_defaults(func=cmd_recommend)

    sp = sub.add_parser("plan", help="what-if: split a model over host + LAN workers")
    dev_args(sp)
    sp.add_argument("--model", required=True, help="GGUF path or synthetic:27b@3.0")
    sp.add_argument("--worker", action="append", default=[], help="worker profile (repeatable)")
    sp.add_argument("--ctx", type=int, default=4096)
    sp.add_argument("--kv", default="f16", choices=["f16", "q8_0", "q4_0"])
    sp.add_argument("--strategy", default="fastest", choices=["fastest", "proportional"])
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("scan", help="find ChorePilot workers on the LAN")
    sp.add_argument("targets", nargs="*", help="hosts to ask directly (default: broadcast)")
    sp.add_argument("--timeout", type=float, default=1.5)
    sp.set_defaults(func=cmd_scan)

    for name, fn, hlp in (("serve", cmd_serve, "run a model locally, as an RPC worker, or as a cluster host"),
                          ("wash", cmd_wash, "start a wash cycle (= probe, pick, serve)")):
        sp = sub.add_parser(name, help=hlp)
        dev_args(sp)
        model_args(sp)
        sp.add_argument("--model", help="GGUF path or name substring (default: best that fits)")
        sp.add_argument("--port", type=int, default=8080)
        sp.add_argument("--host", default="127.0.0.1", help="HTTP bind address")
        sp.add_argument("--force", action="store_true", help="start even if the guard says no")
        sp.add_argument("--draft", help="speculative decoding: small draft GGUF (same tokenizer)")
        sp.add_argument("--spec", help="speculative type: draft-simple, draft-mtp, draft-eagle3, "
                        "draft-dflash, ngram-mod (no draft model needed) ...")
        sp.add_argument("--draft-max", type=int, help="tokens to draft per step")
        if name == "serve":
            sp.add_argument("--worker", action="store_true", help="lend this machine as an RPC worker")
            sp.add_argument("--lan", action="store_true", help="worker: listen on the LAN (trusted only!)")
            sp.add_argument("--cache", action="store_true", help="worker: cache received tensors on disk")
            sp.add_argument("--cpu-only", action="store_true", help="worker: expose only the CPU")
            sp.add_argument("--rpc-port", type=int, default=50052)
            sp.add_argument("--beacon-port", type=int, default=50053)
            sp.add_argument("--rpc", help="host: comma-separated worker endpoints ip:port")
            sp.add_argument("--scan", nargs="*", help="host: discover workers (optionally at these IPs)")
            sp.add_argument("--worker-profile", help="host: assume this profile for silent workers")
            sp.add_argument("--strategy", default="fastest", choices=["fastest", "proportional"])
            sp.add_argument("--dry-run", action="store_true", help="host: print the command only")
        else:
            sp.add_argument("--cycle", default="normal", choices=["quick", "normal", "heavy"],
                            help="quick=2k ctx, normal=4k, heavy=8k")
            sp.add_argument("--prompt", help="run one prompt then drain (exit)")
            sp.add_argument("request", nargs="?", help='a request for the appliance, e.g. '
                            '"wash my dishes": the model picks the tool, the bridge runs it')
            sp.add_argument("--url", help="use a model server already running (any LAN host) "
                            "instead of starting one")
            from .control import add_bridge_args
            add_bridge_args(sp)
        sp.set_defaults(func=fn)
    from .control import add_parser as add_control
    add_control(sub)
    from .commands import add_commands
    add_commands(sub, model_args, dev_args, None)
    return p


def private_files():
    """Everything ChorePilot writes (house.json, chores.json, the database, tokens, the audit
    log, caches, logs) is for this user only: the household's layout, names and habits are
    nobody else's business on a shared computer. Folders made by older versions are tightened."""
    from .env import cache_dir, config_dir
    os.umask(0o077)
    for d in (config_dir(), cache_dir()):
        try:
            st = os.stat(d)
            if st.st_uid == getattr(os, "getuid", lambda: st.st_uid)() and st.st_mode & 0o077:
                os.chmod(d, 0o700)
        except OSError:
            pass


def main(argv=None):
    try:
        sys.stdout.reconfigure(line_buffering=True)  # logs show up live when redirected
    except AttributeError:
        pass
    args = build_parser().parse_args(argv)
    private_files()
    try:
        args.func(args)
    except KeyError as e:
        sys.exit(str(e))


def wash_main():
    """The `wash` command: `wash "wash my dishes"` = `chorepilot wash "wash my dishes"`."""
    main(["wash", *sys.argv[1:]])


if __name__ == "__main__":
    main()
