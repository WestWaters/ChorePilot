"""`chorepilot wash` (also `dishsoaker wash`, or plain `wash`): the fun front door. Probe -> pick -> serve, narrated as a wash cycle.

  dishsoaker wash                       # best model that fits, serve until Ctrl-C
  dishsoaker wash --prompt "hi"         # one load, one answer, drain
  dishsoaker wash --profile dishwasher-1g --cycle quick
  dishsoaker wash "wash my dishes"      # the model calls the appliance tools (simulated
                                        #   backend unless --backend ha|mqtt|homeconnect)
  dishsoaker wash "eco wash, tell me when done" --url http://192.168.1.20:8080
  dishsoaker wash "start the washer on cotton 40"   # with washer/dryer in control.json
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

from .fit import ModelSpec, best_ctx

CYCLES = {"quick": 2048, "normal": 4096, "heavy": 8192}


def _say(step, msg):
    print(f"  [{step:<10}] {msg}")


def ask(port, prompt, max_tokens=64):
    body = json.dumps({"messages": [{"role": "user", "content": prompt}],
                       "max_tokens": max_tokens, "temperature": 0.2}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        out = json.load(r)
    dt = time.time() - t
    text = out["choices"][0]["message"].get("content", "")
    timings = out.get("timings", {})
    return text, dt, timings


_CHAT = ("instruct", "-it", "chat", "hermes", "functionary")


def _pick_tool_model(args, dev):
    """For tool calling, the fastest instruct model that fits and is big enough (>= 1B) to
    follow a tool schema. 0.5B models mostly can't; 30B-A3B MoE is the sweet spot if it fits."""
    from .cli import _pick_model, _scan_models
    from .recommend import recommend
    models = _scan_models(args)
    recs = [r for r in recommend(models, dev, args.ctx) if r.verdict in ("RUNS", "TIGHT")
            and any(k in (r.spec.name + r.spec.source).lower() for k in _CHAT)
            and "-vl-" not in (r.spec.name + r.spec.source).lower()]
    if not recs:
        return _pick_model(args, dev)
    # favour >= 1B (0.5B models often miss the schema), then speed
    best = max(recs, key=lambda r: r.fit.tok_s * min(1.0, (r.spec.n_params or 0) / 3e9))
    return next(m for m in models if m.path == best.spec.source)


def control_wash(args):
    """The real thing: a request in, a tool call out, the appliance (or simulator) runs it."""
    from . import serve
    from .cli import _device, _pick_model, _speed
    from .appliance import cache_dir
    from .control import build_bridge, run_agent
    print("ChorePilot (Dishsoaker3000 mode) -- wash cycle starting (appliance control)")
    bridge = build_bridge(args)
    for nick, be in bridge.fleet:
        st = bridge._status_or_error(be)
        model = f" {be.machine.model}" if be.machine.model else ""
        _say("pre-rinse", f"backend {be.describe()}: {nick}{model} {st.get('state')}"
             + (f", door {st['door']}" if st.get("door") else "")
             + (" [DRY RUN]" if bridge.dry_run else ""))
    proc = dog = None
    url = args.url
    server_log = os.path.join(cache_dir(), "llama-server.log")
    try:
        if not url:
            dev = _device(args)
            # one appliance: the six tools fit in 2k; three appliances' tools need ~3k more
            args.ctx = args.ctx or (CYCLES["quick"] if bridge.legacy else CYCLES["heavy"])
            info = _pick_model(args, dev) if args.model else _pick_tool_model(args, dev)
            spec = ModelSpec.from_gguf(info)
            f = best_ctx(spec, dev, args.kv, want=args.ctx)
            _say("detergent", f"{spec.name} {spec.quant} ({spec.file_bytes / 1e9:.2f} GB), "
                 f"ctx {f.ctx}, expected ~{f.tok_s:.1f} tok/s")
            proc, dog = serve.serve_local(info, dev, ctx=f.ctx, port=args.port, host=args.host,
                                          force=args.force, kv_type=args.kv, detach_after=0,
                                          extra_args=_speed(args), log=lambda m: None,
                                          output=server_log)
            if proc.poll() is not None:
                _say("error E21", f"model server did not start (log: {server_log})")
                return
            url = f"http://127.0.0.1:{args.port}"
        else:
            _say("detergent", f"model server {url}")
        _say("wash", f"request: {args.request!r}")
        t = time.time()
        answer, trace = run_agent(args.request, bridge, url, log=print)
        print("\n" + answer + "\n")
        speeds = [s["tok_s"] for s in trace if s.get("tok_s")]
        _say("spin", f"{time.time() - t:.1f}s, {len(trace)} model turn(s)"
             + (f", {max(speeds):.1f} tok/s generation" if speeds else ""))
        for nick, be in bridge.fleet:
            st = bridge._status_or_error(be)
            _say("drain", f"{nick} now: {st.get('state')}"
                 + (f" ({st.get('program')}, ~{st['remaining_s'] // 60} min left)"
                    if st.get("remaining_s") else ""))
        for c in bridge.chains.pending():
            _say("drain", f"scheduled: {c['target']} {c['program']} after the {c['after']}")
    finally:
        if dog:
            dog.stop()
        if proc:
            serve.terminate(proc)


def wash(args):
    from . import serve
    from .cli import _device, _pick_model
    if getattr(args, "request", None):
        return control_wash(args)
    print("ChorePilot (Dishsoaker3000 mode) -- wash cycle starting")
    dev = _device(args)
    _say("pre-rinse", f"{dev.name}: {dev.ram_gb:g} GB RAM, ~{dev.usable_gb():.2f} GB usable, "
         f"{dev.cores} cores")
    if not dev.runs_llama_cpp:
        _say("error E24", "this appliance's own module can't run a model (drain blocked). "
             "Put a Pi in it, or make it a client of a model elsewhere on the LAN.")
        return
    args.ctx = args.ctx or CYCLES[args.cycle]
    info = _pick_model(args, dev)
    spec = ModelSpec.from_gguf(info)
    f = best_ctx(spec, dev, args.kv, want=args.ctx)
    _say("detergent", f"{spec.name} {spec.quant} ({spec.file_bytes / 1e9:.2f} GB) "
         f"-> needs {f.total_gb:.2f} GB at ctx {f.ctx}")
    if not f.fits and not args.force:
        _say("error E15", f"load too heavy: {f.note}. Try `chorepilot recommend`.")
        return
    _say("fill", f"loading ({'GPU' if f.ngl else 'CPU'}), expected ~{f.tok_s:.1f} tok/s")
    if args.prompt:
        proc, dog = serve.serve_local(info, dev, ctx=f.ctx, port=args.port, host=args.host,
                                      force=args.force, kv_type=args.kv, detach_after=0)
        try:
            if proc.poll() is not None:
                _say("error E21", "server did not start")
                return
            _say("wash", f"prompt: {args.prompt!r}")
            text, dt, tm = ask(args.port, args.prompt)
            print("\n" + text.strip() + "\n")
            gen = tm.get("predicted_per_second")
            _say("spin", f"{dt:.1f}s total" + (f", {gen:.1f} tok/s generation" if gen else "")
                 + (f", guard low-water {dog.min_seen:.2f} GB free" if dog.min_seen != float('inf')
                    else ""))
        finally:
            dog.stop()
            serve.terminate(proc)
        _say("drain", "done. Dishes clean, model unloaded." if not dog.tripped
             else "guard stopped the model to protect the machine.")
        return
    serve.serve_local(info, dev, ctx=f.ctx, port=args.port, host=args.host, force=args.force,
                      kv_type=args.kv)
