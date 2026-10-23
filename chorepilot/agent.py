"""The chat loop: one request -> local model (any OpenAI-compatible server) -> tool calls ->
House (policy, devices, chores) -> ... -> one short answer.

The model only ever sees the tools the router picked for this request, a short system prompt
(who is talking, the relevant devices and chores) and tool OUTCOMES -- never request URLs,
tokens, PINs or lock codes.
"""
from __future__ import annotations

import json
import time

from .control import chat, parse_tool_calls

_HIDE = ("sent", "would_send", "would_send_later", "detail", "policy", "tier")


def brief(res):
    out = {k: v for k, v in res.items() if k not in _HIDE and k != "status"}
    st = res.get("status")
    if isinstance(st, dict):
        for k in ("state", "remaining", "target", "current", "unit", "program"):
            if st.get(k) is not None:
                out.setdefault(k, st[k])
    return out


def run(request, house, url, model=None, max_steps=5, log=print, chat_fn=chat):
    tools = house.tools(request)
    messages = [{"role": "system", "content": house.system_prompt(request)},
                {"role": "user", "content": request}]
    log(f"  [router    ] tools: {', '.join(t['function']['name'] for t in tools)}")
    trace = []
    for _ in range(max_steps):
        t = time.time()
        out = chat_fn(url, messages, tools=tools, model=model)
        msg = out["choices"][0]["message"]
        tm = out.get("timings", {})
        calls = parse_tool_calls(msg)
        trace.append({"latency_s": round(time.time() - t, 2),
                      "tok_s": tm.get("predicted_per_second"), "calls": calls})
        if not calls:
            return (msg.get("content") or "").strip(), trace
        messages.append({"role": "assistant", "content": msg.get("content") or "",
                         "tool_calls": [{"id": c["id"], "type": "function",
                                         "function": {"name": c["name"],
                                                      "arguments": json.dumps(c["args"])}}
                                        for c in calls]})
        for c in calls:
            log(f"  [tool call ] {c['name']}({json.dumps(c['args'])})")
            res = house.call(c["name"], c["args"])
            b = brief(res)
            log(f"  [house     ] {json.dumps(b)}"
                + (f"  (policy: {res['policy']}, tier {res.get('tier', '-')})" if res.get("policy") else ""))
            for op in res.get("would_send") or []:
                log(f"  [dry-run   ] would send {json.dumps(op)}")
            messages.append({"role": "tool", "tool_call_id": c["id"], "name": c["name"],
                             "content": json.dumps(b)})
    return "(stopped after too many tool steps)", trace
