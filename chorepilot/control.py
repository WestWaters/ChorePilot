"""`chorepilot control`: let a local model drive the dishwasher, washer and dryer by tool calls.

  LLM (llama-server --jinja, anywhere on the LAN)
     -> OpenAI-style tool call   (get_status / start_program / pause / resume / cancel /
                                  notify_when_done / start_when_done)
     -> Bridge: which appliance, schema check, safety interlocks, confirmation, rate limit,
                dry-run
     -> Backend for that appliance: simulated | ha | mqtt | homeconnect
     -> appliance

The model never talks to an appliance directly. It can only ask for the tools below, and the
bridge decides. Anything that opens a water valve or turns on a heater (start_program,
resume, start_when_done) needs a human "yes" unless you configure otherwise.

Every tool takes `appliance`: a nickname from ~/.config/chorepilot/control.json ("dishwasher",
"washer", "dryer"), an alias ("washing machine"), a type, or a model number. With a single
appliance it can be left out.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

from .appliance import (ACTIVE, OPTIONS, PRESETS, PROGRAMS, BackendError, Fleet, cache_dir,
                        config_path, human_time, make_fleet)
from .env import getenv

# ------------------------------------------------------------------ tool schemas
_GENERIC_TOOLS = [
    {"type": "function", "function": {
        "name": "get_status",
        "description": "Read the dishwasher: state (idle/ready/running/paused/finished/...), "
                       "program, remaining time, door, whether remote start is allowed.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "start_program",
        "description": "Start a wash program. Uses water and heat. Use 'auto' when the user "
                       "does not name a program, 'eco' to save energy, 'quick' when in a hurry, "
                       "'intensive' for pots and pans.",
        "parameters": {"type": "object", "properties": {
            "program": {"type": "string", "enum": list(PROGRAMS),
                        "description": "; ".join(f"{k}: {v}" for k, v in PROGRAMS.items())},
            "options": {"type": "object", "description": "optional extras",
                        "properties": {
                            "half_load": {"type": "boolean"},
                            "extra_dry": {"type": "boolean"},
                            "speed_boost": {"type": "boolean"},
                            "hygiene": {"type": "boolean"},
                            "delay_minutes": {"type": "integer", "minimum": 0,
                                              "maximum": 1440}},
                        "additionalProperties": False}},
            "required": ["program"]}}},
    {"type": "function", "function": {
        "name": "pause",
        "description": "Pause the running program.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "resume",
        "description": "Resume a paused program. Uses water and heat.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "cancel",
        "description": "Stop and cancel the current program.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "notify_when_done",
        "description": "Tell the user when the current program finishes.",
        "parameters": {"type": "object", "properties": {
            "message": {"type": "string",
                        "description": "optional short note for the user, e.g. 'unload the "
                                       "glasses'. Do not include times."}},
            "required": []}}},
]
TOOLS = _GENERIC_TOOLS                      # the original single-dishwasher tool set
TOOL_NAMES = [t["function"]["name"] for t in TOOLS] + ["start_when_done"]
HEAT_ACTIONS = {"start_program", "resume", "start_when_done"}   # water valve / heater
WRITE_ACTIONS = HEAT_ACTIONS | {"pause", "cancel"}


def _is_legacy(fleet):
    if fleet is None:
        return True
    if len(fleet) != 1:
        return False
    b = fleet.get()
    return b.machine.model is None and b.machine.kind == "dishwasher"


def build_tools(fleet=None):
    """The tool schemas for this fleet. One generic dishwasher -> the original six tools."""
    if _is_legacy(fleet):
        return _GENERIC_TOOLS
    names = fleet.names()
    multi = len(names) > 1
    app = {"type": "string", "enum": names,
           "description": "which appliance: " + ", ".join(
               f"{n} ({b.machine.describe()} {b.machine.kind})" for n, b in fleet)}
    progs, seen = [], set()
    for _, b in fleet:
        for p in b.machine.programs:
            if p not in seen:
                seen.add(p)
                progs.append(p)
    prog_desc = "; ".join(f"{n}: {'|'.join(b.machine.programs)}" for n, b in fleet)
    opt_props = {}
    for n, b in fleet:
        for o in b.machine.options.values():
            prop = opt_props.setdefault(o.name, {"_desc": o.desc, "_for": []})
            prop["_for"].append(n)
            if o.kind == "bool":
                prop["type"] = "boolean"
            elif o.kind == "minutes":
                prop.update(type="integer", minimum=o.lo if o.lo else 0, maximum=o.hi)
            else:
                prop["type"] = "string"
                prop.setdefault("enum", [])
                prop["enum"] += [v for v in o.values if v not in prop["enum"]]
    for name, prop in opt_props.items():
        who = prop.pop("_for")
        desc = prop.pop("_desc")
        prop["description"] = desc + ("" if not multi or len(who) == len(names)
                                      else f" ({'/'.join(who)} only)")
    target = {"appliance": app, "program": {"type": "string", "enum": progs,
                                            "description": prog_desc}, **opt_props}

    def tool(name, desc, props=None, required=()):
        return {"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props or {},
                           "required": list(required)}}}
    req = ["appliance", "program"] if multi else ["program"]
    tools = [
        tool("get_status", "Read an appliance (or all of them): state, program, remaining time, "
             "door, remote start, child lock.", {"appliance": app}),
        tool("start_program", "Start a program on one appliance now (or with finish_in_minutes / "
             "delay_minutes). Uses water and/or heat.", target, req),
        tool("pause", "Pause the running program.", {"appliance": app}, ["appliance"] if multi else []),
        tool("resume", "Resume a paused program. Uses water and heat.", {"appliance": app},
             ["appliance"] if multi else []),
        tool("cancel", "Stop and cancel the current program (or a pending start_when_done).",
             {"appliance": app}, ["appliance"] if multi else []),
        tool("notify_when_done", "Tell the user when the appliance's program finishes.",
             {"appliance": app, "message": {"type": "string", "description":
                                            "optional short note, no times"}}),
    ]
    if multi:
        chained = {k: v for k, v in target.items() if k != "appliance"}
        tools.append(tool(
            "start_when_done", "When one appliance finishes, start another, e.g. wait_for=washer "
            "then_start=dryer. The user must move the laundry, close the door and press Remote "
            "Start; it starts only then. Also tells the user when the first one is done.",
            {"wait_for": dict(app, description="the appliance running now (e.g. washer)"),
             "then_start": dict(app, description="the appliance to start afterwards (e.g. dryer)"),
             **chained}, ["wait_for", "then_start", "program"]))
    return tools


# ------------------------------------------------------------------ validation
_FLAT_SKIP = {"appliance", "program", "options", "message", "after", "wait_for", "then_start"}


def _pick_appliance(fleet, args, prefer_active=False):
    name = args.get("appliance")
    if name not in (None, ""):
        return fleet.resolve(name)
    if args.get("program") and len(fleet) > 1:
        ok = []
        for n, b in fleet:
            try:
                b.machine.resolve_program(args["program"])
                ok.append(n)
            except ValueError:
                pass
        if len(ok) == 1:
            return ok[0]
    if prefer_active and len(fleet) > 1:
        act = []
        for n, b in fleet:
            try:
                if b.status().get("state") in ACTIVE:
                    act.append(n)
            except BackendError:
                pass
        if len(act) == 1:
            return act[0]
    return fleet.resolve(None)


def validate_args(name, args, fleet=None):
    """Strict check of the model's arguments against the appliance's own profile.

    Returns cleaned args ({"appliance", "program", "options"} ...). Raises ValueError."""
    if name not in TOOL_NAMES:
        raise ValueError(f"unknown tool {name!r}")
    if not isinstance(args, dict):
        raise ValueError("arguments must be a JSON object")
    legacy = fleet is None
    fleet = fleet or make_fleet({}, backend="simulated")
    try:
        if name == "start_when_done":
            if len(fleet) < 2:
                raise ValueError("start_when_done needs two appliances (e.g. washer and dryer)")
            then = args.get("then_start") or args.get("appliance")
            if not then:
                raise ValueError("say which appliance to start afterwards (then_start)")
            nick = fleet.resolve(then)
        elif name == "get_status" and not args.get("appliance"):
            return {"appliance": None} if not legacy else {}
        else:
            nick = _pick_appliance(fleet, args, prefer_active=name in ("pause", "resume", "cancel",
                                                                       "notify_when_done"))
    except KeyError as e:
        raise ValueError(str(e).strip("'\"")) from None
    m = fleet.get(nick).machine
    out = {} if legacy else {"appliance": nick}
    if name in ("start_program", "start_when_done"):
        if not args.get("program"):
            raise ValueError(f"program is required; the {nick} has: {', '.join(m.programs)}")
        prog, parsed = m.resolve_program(args["program"])
        raw = dict(parsed)
        if isinstance(args.get("options"), dict):
            raw.update(args["options"])
        elif args.get("options") not in (None, ""):
            raise ValueError("options must be an object")
        raw.update({k: v for k, v in args.items() if k not in _FLAT_SKIP})
        out.update(program=prog, options=m.clean_options(raw, prog))
        if name == "start_when_done":
            wait_for = args.get("wait_for") or args.get("after")
            try:
                out["after"] = fleet.resolve(wait_for) if wait_for else None
            except KeyError as e:
                raise ValueError(str(e).strip("'\"")) from None
        return out
    if name == "notify_when_done":
        note = str(args.get("message") or "").strip()[:160]
        out["message"] = m.done_message + (f" {note}" if note else "")
    return out


# ------------------------------------------------------------------ policy
RATE_LIMITS = {
    # tier 1 (lights, blinds, speakers, vacuum, pause/cancel): generous, per device, so tapping
    # a light on the dashboard never gets blocked; it only stops a runaway loop
    "tier1": {"per_device": {"max": 30, "window_s": 60}},
    # tier 2 (water / heat) and tier 3 (security): strict and house-wide, plus a gap between
    # starts of the same device
    "tier2": {"house": {"max": 6, "window_s": 600}, "start_gap_s": 60},
    "tier3": {"house": {"max": 6, "window_s": 600}, "start_gap_s": 60},
    # MCP / external agents: stricter tier-1 limits than a person at the dashboard
    "agent": {"tier1": {"per_device": {"max": 6, "window_s": 60},
                        "house": {"max": 30, "window_s": 600}}},
}
_RL_LOCK = threading.Lock()


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _span(s):
    s = int(s)
    if s == 60:
        return "minute"
    if s % 60 == 0:
        return f"{s // 60} min"
    return f"{s} s"


class RateLimiter:
    """Write limits per safety tier, persisted to a small JSON file so separate CLI runs and
    dashboard requests share the budget.

    Each tier can have a `per_device` and a `house` limit ({"max": n, "window_s": s}, or null
    for none) and a `start_gap_s` between starts of the same device. Tier 1 is counted per kind
    of caller (people vs MCP / external agents, whose limits come from "agent"), so a runaway
    agent can never use up the dashboard's budget. Tiers 2 and 3 are counted across everyone.
    house.json "rate_limits" overrides any of it (see docs/CONTROL.md).

    max_actions / window_s / min_start_gap_s set the tier 2 and tier 3 limits (the 0.7 API)."""

    def __init__(self, max_actions=6, window_s=600, min_start_gap_s=60, path=None, limits=None):
        base = _merge(RATE_LIMITS, {t: {"house": {"max": max_actions, "window_s": window_s},
                                        "start_gap_s": min_start_gap_s} for t in ("tier2", "tier3")})
        self.limits = _merge(base, limits if isinstance(limits, dict) else None)
        self.path = path
        self._mem = {"w": [], "last_start": 0, "last_start_by": {}}

    @staticmethod
    def tier_of(action):
        return 2 if action in HEAT_ACTIONS else 1

    def rules(self, tier, agent=False):
        t = f"tier{min(max(int(tier), 1), 3)}"
        r = dict(self.limits.get(t) or {})
        if agent:
            r.update((self.limits.get("agent") or {}).get(t) or {})
        return r

    @staticmethod
    def _rule(r, key):
        v = r.get(key)
        if not isinstance(v, dict):
            return None
        try:
            n, w = int(v.get("max")), float(v.get("window_s"))
        except (TypeError, ValueError):
            return None
        return (n, w) if n >= 1 and w > 0 else None

    def _load(self):
        if not self.path:
            return self._mem
        try:
            with open(self.path) as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}
        if not isinstance(st, dict):
            st = {}
        st.setdefault("w", [])
        st.setdefault("last_start", 0)
        st.setdefault("last_start_by", {})
        return st

    def _save(self, st):
        if not self.path:
            self._mem = st
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f)
        os.replace(tmp, self.path)

    def _horizon(self):
        spans = [3600.0]
        for t in ("tier1", "tier2", "tier3"):
            for agent in (False, True):
                r = self.rules(int(t[-1]), agent)
                spans += [x[1] for x in (self._rule(r, "per_device"), self._rule(r, "house")) if x]
        return max(spans)

    def check(self, action, now=None, key=None, tier=None, agent=False):
        now = now or time.time()
        tier = self.tier_of(action) if tier is None else max(1, min(int(tier), 3))
        r = self.rules(tier, agent)
        with _RL_LOCK:
            st = self._load()
        mine = [w for w in st["w"] if w[1] == tier and (tier >= 2 or bool(w[3]) == bool(agent))]
        dev = self._rule(r, "per_device")
        if dev and key is not None:
            n, win = dev
            if sum(1 for w in mine if w[2] == key and now - w[0] < win) >= n:
                return f"rate limit: {n} actions per {_span(win)} on the {str(key).replace('_', ' ')}"
        house = self._rule(r, "house")
        if house:
            n, win = house
            if sum(1 for w in mine if now - w[0] < win) >= n:
                what = "actions" if tier == 1 else "water / heat actions" if tier == 2 \
                    else "security actions"
                return f"rate limit: {n} {what} per {_span(win)}"
        gap = float(r.get("start_gap_s") or 0)
        if tier >= 2 and gap > 0:
            last = st["last_start_by"].get(key, 0) if key else st.get("last_start", 0)
            if now - last < gap:
                return f"rate limit: wait {int(gap - (now - last)) or 1} s between starts"
        return None

    def record(self, action, now=None, key=None, tier=None, agent=False):
        now = now or time.time()
        tier = self.tier_of(action) if tier is None else max(1, min(int(tier), 3))
        with _RL_LOCK:
            st = self._load()
            keep = self._horizon()
            st["w"] = [w for w in st["w"] if isinstance(w, list) and len(w) == 4
                       and now - w[0] < keep] + [[now, tier, key, 1 if agent else 0]]
            if tier >= 2:
                st["last_start"] = now
                if key:
                    st["last_start_by"][key] = now
            st.pop("writes", None)                     # the 0.7 file format
            self._save(st)

    def for_caller(self, agent=False):
        """A view that passes `agent` on every call (for the Bridge, which doesn't know who
        is asking)."""
        return _CallerLimiter(self, agent)


class _CallerLimiter:
    def __init__(self, base, agent):
        self.base, self.agent = base, agent

    @property
    def path(self):
        return self.base.path

    def check(self, action, now=None, key=None, tier=None, agent=None):
        return self.base.check(action, now, key, tier, self.agent if agent is None else agent)

    def record(self, action, now=None, key=None, tier=None, agent=None):
        return self.base.record(action, now, key, tier, self.agent if agent is None else agent)

    def for_caller(self, agent=False):
        return self.base.for_caller(agent)


def tty_confirm(question):
    if not sys.stdin or not sys.stdin.isatty():
        return False
    try:
        return input(f"  ?? {question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


class ChainStore:
    """Pending "start B when A finishes" jobs, in ~/.cache/chorepilot/chains.json. Removing an
    entry (cancel) stops its watcher at the next poll."""

    MEMORY = ":memory:"

    def __init__(self, path=None):
        self.path = path or os.path.join(cache_dir(), "chains.json")
        self._mem = {}

    def _all(self):
        if self.path == self.MEMORY:
            return json.loads(json.dumps(self._mem))
        try:
            with open(self.path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _write(self, d):
        if self.path == self.MEMORY:
            self._mem = d
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, self.path)

    def add(self, chain):
        d = self._all()
        chain = dict(chain, id=chain.get("id") or uuid.uuid4().hex[:8], created=time.time())
        d[chain["id"]] = chain
        self._write(d)
        return chain

    def get(self, cid):
        return self._all().get(cid)

    def update(self, cid, **kw):
        d = self._all()
        if cid in d:
            d[cid].update(kw)
            self._write(d)

    def remove(self, cid):
        d = self._all()
        gone = d.pop(cid, None)
        self._write(d)
        return gone

    def pending(self, target=None):
        return [c for c in self._all().values() if target in (None, c["target"])]


def _fmt_opts(options):
    return ", ".join(f"{k}={v}" for k, v in (options or {}).items())


class Bridge:
    """Executes tool calls against the fleet, behind the safety policy.

    confirm: "heat" (default; start/resume/start_when_done need a yes), "all" (every write),
    "never". dry_run: reads run for real; writes return the exact requests instead.
    """

    def __init__(self, backend, confirm="heat", dry_run=False, ask=tty_confirm,
                 limiter=None, notifier=None, log=print, watch_spawner=None, chain_spawner=None,
                 chains=None):
        if confirm not in ("heat", "all", "never"):
            raise ValueError("confirm must be heat|all|never")
        self.fleet = backend if isinstance(backend, Fleet) else Fleet.single(backend)
        self.legacy = _is_legacy(self.fleet)
        self.confirm, self.dry_run = confirm, dry_run
        self.ask, self.log = ask, log
        self.limiter = limiter or RateLimiter()
        self.notifier = notifier or Notifier(self.fleet.get())
        self.watch_spawner = watch_spawner
        self.chain_spawner = chain_spawner
        self.chains = chains or ChainStore(os.path.join(
            os.path.dirname(self.limiter.path), "chains.json") if self.limiter.path
            else ChainStore.MEMORY)
        self.audit = []

    @property
    def backend(self):
        """The default appliance's backend (the only one, in a single-appliance setup)."""
        return self.fleet.get()

    def tools(self):
        return build_tools(self.fleet)

    def _needs_yes(self, name):
        return (self.confirm == "all" and name in WRITE_ACTIONS) or \
               (self.confirm == "heat" and name in HEAT_ACTIONS)

    def call(self, name, args=None):
        """Run one tool. Always returns a JSON-able dict; errors are reported, not raised,
        so the model can tell the user what happened."""
        try:
            args = validate_args(name, args or {}, self.fleet)
        except (ValueError, TypeError) as e:
            return self._done(name, args, {"ok": False, "error": f"invalid arguments: {e}"})
        nick = args.get("appliance") or self.fleet.default
        try:
            if name == "get_status":
                if args.get("appliance") is None and len(self.fleet) > 1:
                    return self._done(name, args, {"ok": True, "appliances": {
                        n: _brief(self._status_or_error(b)) for n, b in self.fleet}})
                be = self.fleet.get(nick)
                return self._done(name, args, {"ok": True, **self._tag(be.status(), nick)})
            if name == "notify_when_done":
                return self._done(name, args, self._notify(nick, args["message"]))
            if name == "start_when_done":
                return self._done(name, args, self._chain(args))
            return self._done(name, args, self._write(name, nick, args))
        except BackendError as e:
            return self._done(name, args, {"ok": False, "error": str(e)})

    def _status_or_error(self, be):
        try:
            return be.status()
        except BackendError as e:
            return {"state": "offline", "error": str(e)}

    def _tag(self, st, nick):
        if not self.legacy:
            st = dict(st, appliance=nick, model=self.fleet.get(nick).machine.model)
        return st

    def _interlocks(self, name, nick, st):
        """What the appliance itself would (or should) refuse; None if the write may go."""
        m = self.fleet.get(nick).machine
        if st["state"] == "offline":
            return f"the {nick} is offline or not reachable"
        if name in ("start_program", "start_when_done"):
            if st["state"] in ACTIVE:
                return (f"a program is already {st['state']} on the {nick} "
                        f"({st.get('program')}); cancel it first")
            if st.get("door") == "open":
                return f"the {nick} door is open; close it first"
            if st.get("remote_control") is False:
                return (f"remote control is switched off in the {nick}'s Home Connect "
                        "settings; turn it on once in the app")
            if st.get("local_control"):
                return f"someone is operating the {nick} at the machine right now; try again shortly"
            if name == "start_program" and st.get("remote_start_allowed") is False:
                return (f"remote start is not enabled on the {nick}; {m.remote_start} "
                        "(a deliberate safety step we never bypass)")
        if name == "resume" and st["state"] != "paused":
            return f"nothing to resume on the {nick} (state: {st['state']})"
        if name == "pause" and st["state"] != "running":
            return f"nothing running to pause on the {nick} (state: {st['state']})"
        if name == "cancel" and st["state"] not in ("running", "paused", "delayed",
                                                     "action_required"):
            return f"nothing to cancel on the {nick} (state: {st['state']})"
        return None

    def _write(self, name, nick, args):
        be = self.fleet.get(nick)
        action = {"start_program": "start"}.get(name, name)
        st = be.status()
        if name == "cancel" and st["state"] not in ("running", "paused", "delayed",
                                                     "action_required"):
            pend = self.chains.pending(nick)
            if pend:                         # cancel a scheduled "start when done" instead
                if self.dry_run:
                    return {"ok": True, "dry_run": True, "note": f"would cancel {len(pend)} "
                            f"scheduled start(s) of the {nick}"}
                for c in pend:
                    self.chains.remove(c["id"])
                return {"ok": True, "cancelled_scheduled": [f"{c['target']} {c['program']} after "
                                                            f"{c['after']}" for c in pend]}
        why = self._interlocks(name, nick, st)
        if why:
            return {"ok": False, "error": why}
        ops = be.plan(action, args.get("program"), args.get("options"))
        if self.dry_run:
            return {"ok": True, "dry_run": True, "would_send": ops,
                    "note": "dry run: nothing was sent to the appliance"}
        why = self.limiter.check(name, key=nick)
        if why:
            return {"ok": False, "error": why}
        if self._needs_yes(name):
            if name == "start_program":
                what = f"Start {args['program']}" + (f" with {args['options']}"
                                                     if args.get("options") else "")
            else:
                what = {"resume": "Resume the program"}.get(name, name.capitalize())
            if not self.ask(f"{what} on the {nick} ({be.machine.describe()}, {be.describe()})? "
                            f"It uses {be.machine.uses}."):
                return {"ok": False, "declined": True,
                        "error": "the user did not confirm; nothing was started"}
        be.apply(ops)
        self.limiter.record(name, key=nick)
        after = self._tag(be.status(), nick)
        return {"ok": True, "sent": ops, "status": after}

    def _notify(self, nick, message):
        st = self.fleet.get(nick).status()
        if st["state"] not in ACTIVE:
            return {"ok": False, "error": f"nothing is running on the {nick} (state: {st['state']})"}
        if self.dry_run:
            return {"ok": True, "dry_run": True, "note": "would watch and notify when done"}
        how = "registered"
        if self.watch_spawner:
            try:
                how = self.watch_spawner(message, nick)
            except TypeError:                # older single-argument hooks
                how = self.watch_spawner(message)
        res = {"ok": True, "watching": how, "remaining_s": st.get("remaining_s")}
        if not self.legacy:
            res.update(appliance=nick, remaining=st.get("remaining"))
        return res

    def _chain(self, args):
        target = args["appliance"]
        after = args.get("after")
        if after is None:                    # the one that is running now
            act = [n for n, b in self.fleet if n != target
                   and self._status_or_error(b).get("state") in ACTIVE]
            if len(act) != 1:
                return {"ok": False, "error": "say which appliance to wait for (wait_for)"}
            after = act[0]
        if after == target:
            return {"ok": False, "error": "wait_for and then_start must be different appliances"}
        sa = self.fleet.get(after).status()
        if sa["state"] not in ACTIVE:
            return {"ok": False, "error": f"the {after} isn't running (state: {sa['state']}); "
                    f"start the {target} directly when it's loaded"}
        tb = self.fleet.get(target)
        st = tb.status()
        why = self._interlocks("start_when_done", target, dict(st, door=None))
        if why:
            return {"ok": False, "error": why}
        ops = tb.plan("start", args["program"], args["options"])   # also checks the mapping
        human_step = (f"when the {after} is done: move the laundry into the {target}, close "
                      f"the door and press Remote Start on the {target}")
        if self.dry_run:
            return {"ok": True, "dry_run": True, "would_send_later": ops,
                    "note": f"dry run: nothing scheduled; {human_step}"}
        if self._needs_yes("start_when_done"):
            opts = f" ({_fmt_opts(args['options'])})" if args["options"] else ""
            if not self.ask(f"When the {after} finishes, start the {target} "
                            f"({tb.machine.describe()}) on {args['program']}{opts}? It starts "
                            f"only after you load it and press its Remote Start. It uses "
                            f"{tb.machine.uses}."):
                return {"ok": False, "declined": True,
                        "error": "the user did not confirm; nothing was scheduled"}
        ch = self.chains.add({"after": after, "target": target, "program": args["program"],
                              "options": args["options"],
                              # the yes (if one was needed) was given now, for the later start
                              "confirmed": True})
        how = self.chain_spawner(ch["id"]) if self.chain_spawner else "registered"
        return {"ok": True, "scheduled": f"{target} {args['program']} after the {after}",
                "after_remaining": sa.get("remaining"), "watching": how,
                "user_must": human_step}

    def _done(self, name, args, result):
        self.audit.append({"t": time.time(), "tool": name, "args": args, "result": result})
        return result


def _brief(st):
    keep = ("state", "program", "remaining", "door", "remote_start_allowed", "child_lock",
            "error")
    return {k: st.get(k) for k in keep if st.get(k) is not None}


# ------------------------------------------------------------------ notifications
class Notifier:
    """Desktop notification + optional webhook ($CHOREPILOT_NOTIFY_URL, e.g. an ntfy topic)
    + HA notify service when the backend is Home Assistant."""

    def __init__(self, backend=None):
        self.backend = backend

    def send(self, message, log=print):
        log(f"  [notify    ] {message}")
        url = getenv("NOTIFY_URL")
        if url:
            try:
                urllib.request.urlopen(urllib.request.Request(url, data=message.encode(),
                                                              method="POST"), timeout=10)
            except OSError as e:
                log(f"  [notify    ] webhook failed: {e}")
        if hasattr(self.backend, "notify"):
            try:
                self.backend.notify(message)
            except (BackendError, OSError) as e:
                log(f"  [notify    ] HA notify failed: {e}")
        if getenv("NOTIFY_DESKTOP", "1") == "0":
            return
        if sys.platform == "darwin" and shutil.which("osascript"):
            subprocess.run(["osascript", "-e", f"display notification {json.dumps(message)} "
                            "with title \"ChorePilot\""], capture_output=True)
        elif shutil.which("notify-send"):
            subprocess.run(["notify-send", "ChorePilot", message], capture_output=True)


def _status_light(backend):
    try:
        return backend.status(light=True)
    except TypeError:
        return backend.status()


def _wait(backend, poll_s=30, timeout_s=6 * 3600, sleep=time.sleep, cancelled=None):
    """Poll until the program leaves running/paused/delayed. Returns (final status, done?).

    Polls rarely while the end is far away (Home Connect allows 1000 requests a day), and
    every `poll_s` near the end."""
    t0, last = time.time(), None
    speed = _speed(backend)
    while time.time() - t0 < timeout_s:
        if cancelled and cancelled():
            return None, False
        st = _status_light(backend)
        if st["state"] not in ACTIVE:
            near_end = last is not None and (last.get("remaining_s") or 0) <= 2 * poll_s * speed
            return st, st["state"] == "finished" or (st["state"] in ("ready", "idle") and near_end)
        last = st
        rem = st.get("remaining_s")
        nap = poll_s if rem is None else min(max(poll_s, rem / speed / 2), 600, max(1, rem / speed))
        sleep(nap)
    return backend.status(), False


def wait_until_done(backend, message="The dishes are done.", poll_s=30, timeout_s=6 * 3600,
                    notifier=None, log=print, sleep=time.sleep):
    """Poll until the program ends, then notify. Returns the final state.

    "finished" (or idle shortly after the expected end) = done; anything else = it stopped
    early (cancelled, error, power cut), and the notification says so."""
    notifier = notifier or Notifier(backend)
    st, done = _wait(backend, poll_s, timeout_s, sleep)
    if st is not None and (done or st["state"] not in ACTIVE):
        if done:
            notifier.send(message, log)
        else:
            what = getattr(backend, "appliance", "dishwasher")
            what = what[:1].upper() + what[1:]
            notifier.send(f"{what} stopped before finishing (now: {st['state']}).", log)
    return st


def run_chain(fleet, cid, bridge, notifier=None, chains=None, poll_s=30, handoff_poll_s=5,
              handoff_timeout_s=3 * 3600, log=print, sleep=time.sleep):
    """The washer -> dryer hand-off, run by a detached `chorepilot control chain --id`.

      1. wait for `after` to finish (stopped early -> tell the user, start nothing);
      2. tell the user to move the laundry, close the door, press Remote Start;
      3. wait until the target's remote start is armed with the door closed, and armed
         *after* the hand-off began (loading means opening the door, which disarms it, so an
         earlier arm on an empty machine doesn't count);
      4. start it through the bridge (pre-confirmed when scheduled; interlocks and the rate
         limit still apply), then tell the user when it is done.
    Removing the chain (cancel) stops it at the next poll. Returns a short outcome string."""
    chains = chains or bridge.chains
    ch = chains.get(cid)
    if not ch:
        return "unknown chain"
    notifier = notifier or Notifier(fleet.get(ch["after"]))
    a, t = fleet.get(ch["after"]), fleet.get(ch["target"])
    what = f"{ch['program']}" + (f" ({_fmt_opts(ch['options'])})" if ch["options"] else "")
    gone = lambda: chains.get(cid) is None  # noqa: E731
    st, done = _wait(a, poll_s, sleep=sleep, cancelled=gone)
    if st is None:
        return "cancelled"
    if not done:
        chains.remove(cid)
        notifier.send(f"The {ch['after']} stopped before finishing (now: {st['state']}); the "
                      f"{ch['target']} will NOT be started.", log)
        return "after-stopped"
    notifier.send(f"{a.machine.done_message} Move it into the {ch['target']}, close the door "
                  f"and press Remote Start on the {ch['target']}: it will then start {what}.", log)
    chains.update(cid, phase="waiting-for-remote-start")
    t0 = time.time()
    first = _status_light(t)
    reset = not (first.get("remote_start_allowed") is True and first.get("door") == "closed")
    opened0 = first.get("door_opened")      # backends that count door openings (simulator)
    while True:
        if gone():
            return "cancelled"
        s = _status_light(t)
        if s["state"] in ACTIVE:
            chains.remove(cid)
            notifier.send(f"The {ch['target']} was started at the machine; nothing for me to do.", log)
            return "started-locally"
        armed = s.get("remote_start_allowed") is True and s.get("door") in ("closed", None)
        if opened0 is not None and (s.get("door_opened") or 0) > opened0:
            reset = True                     # loaded and re-armed between two polls
        if not armed:
            reset = True
        elif reset:
            break
        if time.time() - t0 > handoff_timeout_s:
            chains.remove(cid)
            notifier.send(f"Gave up: Remote Start on the {ch['target']} wasn't pressed within "
                          f"{human_time(handoff_timeout_s)}. Start it at the machine.", log)
            return "timeout"
        sleep(handoff_poll_s if time.time() - t0 < 600 else max(handoff_poll_s, 30))
    # pre-confirmed when scheduled (the detached runner has no terminal to ask on);
    # interlocks and the rate limit still apply
    saved = bridge.confirm
    if ch.get("confirmed") and bridge.confirm == "heat":
        bridge.confirm = "never"
    try:
        res = bridge.call("start_program", {"appliance": ch["target"], "program": ch["program"],
                                            **ch["options"]})
    finally:
        bridge.confirm = saved
    chains.remove(cid)
    if not res.get("ok"):
        notifier.send(f"The {ch['target']} did not start: {res.get('error')}", log)
        return "start-failed"
    rem = (res.get("status") or {}).get("remaining")
    notifier.send(f"The {ch['target']} started: {what}" + (f", about {rem}." if rem else "."), log)
    wait_until_done(t, t.machine.done_message, poll_s=poll_s, notifier=notifier, log=log, sleep=sleep)
    return "done"


def _speed(backend):
    return getattr(backend, "speed", 1.0) or 1.0


def _spawn(args):
    cmd = [sys.executable, "-u", "-m", "chorepilot", "control", *args]
    log = os.path.join(cache_dir(), "watch.log")
    os.makedirs(cache_dir(), exist_ok=True)
    with open(log, "a") as out:
        subprocess.Popen(cmd, stdout=out, stderr=out, stdin=subprocess.DEVNULL,
                         start_new_session=True,
                         cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return f"background watcher (log: {log})"


def spawn_watcher(appliance, message, backend=None):
    """Detach a `chorepilot control wait` so notify_when_done outlives this command."""
    return _spawn(["wait", "--appliance", appliance, "--message", message]
                  + (["--backend", backend] if backend else []))


def spawn_chain(cid, backend=None):
    return _spawn(["chain", "--id", cid] + (["--backend", backend] if backend else []))


# ------------------------------------------------------------------ the agent loop
SYSTEM = ("You are the voice of a dishwasher controller. You can only act through the tools. "
          "When the user asks to wash, clean or run the dishes, call start_program right away "
          "(program 'auto' unless they ask for something else: eco to save energy, quick when "
          "in a hurry, intensive for pots). Then, if they want to know when it is done, call "
          "notify_when_done. Never claim the machine started unless the tool result says "
          "ok: true. If a tool returns an error, explain it to the user in one sentence. "
          "Keep answers short.")


def system_prompt(bridge, statuses=None):
    """Short enough for a 1.5B model: one line per appliance, then the few rules that matter."""
    if bridge.legacy:
        st = statuses or bridge.call("get_status")
        return SYSTEM + " Current dishwasher status: " + json.dumps(
            {k: st.get(k) for k in ("state", "program", "remaining", "door",
                                    "remote_start_allowed")})
    statuses = statuses or {n: bridge._status_or_error(b) for n, b in bridge.fleet}
    lines = ["You control home appliances, only through the tools. Appliances:"]
    for n, b in bridge.fleet:
        s = _brief(statuses[n])
        now = s.get("state", "?") + (f" {s['program']}" if s.get("program") else "") + \
            (f", {s['remaining']} left" if s.get("remaining") else "")
        lines.append(f"- {n} ({b.machine.describe()}): now {now}. {b.machine.compact()}")
    kinds = {b.machine.kind: n for n, b in bridge.fleet}
    if "dishwasher" in kinds:
        d = kinds["dishwasher"]
        lines.append(f"'wash the dishes' -> start_program appliance={d} program=auto (eco to save "
                     "energy, quick when in a hurry, intensive for pots).")
    if "washer" in kinds:
        lines.append(f"'start the washer on cotton 40' -> start_program appliance={kinds['washer']} "
                     "program=cotton temperature=40.")
    if "washer" in kinds and "dryer" in kinds:
        lines.append(f"'dry it when the wash finishes' -> start_when_done wait_for={kinds['washer']} "
                     f"then_start={kinds['dryer']} program=cotton dryness=cupboard (iron = "
                     "damp for ironing; 'extra dry' = cupboard_plus). It also tells the user "
                     "when the wash is done.")
    lines.append("To be told when something finishes -> notify_when_done. Never say something "
                 "started unless the tool result has ok: true; if a tool fails, explain it in "
                 "one sentence. Keep answers short.")
    return "\n".join(lines)


def chat(url, messages, tools=TOOLS, model=None, timeout=300, temperature=0.0, max_tokens=256):
    body = {"messages": messages, "tools": tools, "tool_choice": "auto",
            "temperature": temperature, "max_tokens": max_tokens}
    if model:
        body["model"] = model
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


_TEXT_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def parse_tool_calls(msg):
    """Native tool_calls, or a Hermes-style <tool_call>{...}</tool_call> left in the text by a
    server running without --jinja / a model the parser didn't recognise."""
    calls = []
    for i, c in enumerate(msg.get("tool_calls") or []):
        fn = c.get("function", {})
        a = fn.get("arguments") or "{}"
        try:
            a = json.loads(a) if isinstance(a, str) else a
        except ValueError:
            a = {"_unparsed": a}
        calls.append({"id": c.get("id") or f"call_{i}", "name": fn.get("name"), "args": a})
    if calls:
        return calls
    for i, m in enumerate(_TEXT_CALL.finditer(msg.get("content") or "")):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        calls.append({"id": f"text_{i}", "name": d.get("name"),
                      "args": d.get("arguments") or d.get("parameters") or {}})
    return calls


def run_agent(request, bridge, url, model=None, max_steps=5, log=print, chat_fn=chat):
    """One user request -> model -> tool calls -> bridge -> ... -> final answer."""
    messages = [{"role": "system", "content": system_prompt(bridge)},
                {"role": "user", "content": request}]
    tools = bridge.tools()
    trace = []
    for _ in range(max_steps):
        t = time.time()
        if tools is TOOLS:
            out = chat_fn(url, messages, model=model)
        else:
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
            res = bridge.call(c["name"], c["args"])
            brief = {k: v for k, v in res.items() if k not in ("sent", "would_send", "status",
                                                                "detail", "would_send_later")}
            if "status" in res:
                brief["state"] = res["status"].get("state")
                brief["remaining"] = res["status"].get("remaining")
            log(f"  [bridge    ] {json.dumps(brief)}")
            for op in (res.get("would_send") or []) + (res.get("would_send_later") or []):
                log(f"  [dry-run   ] would send {json.dumps(op)}")
            # the model gets the outcome, not the raw requests (shorter, and no URLs/tokens)
            messages.append({"role": "tool", "tool_call_id": c["id"], "name": c["name"],
                             "content": json.dumps(brief)})
    return "(stopped after too many tool steps)", trace


# ------------------------------------------------------------------ CLI
def build_bridge(args, log=print):
    fleet = make_fleet(backend=getattr(args, "backend", None))
    confirm = "never" if getattr(args, "yes", False) else getattr(args, "confirm", "heat")
    limiter = RateLimiter(path=os.path.join(cache_dir(), "control-ratelimit.json"))
    be_override = getattr(args, "backend", None)
    spawner = chainer = None
    if not getattr(args, "no_watch", False):
        spawner = lambda msg, nick: spawn_watcher(nick, msg, be_override)  # noqa: E731
        chainer = lambda cid: spawn_chain(cid, be_override)  # noqa: E731
    return Bridge(fleet, confirm=confirm, dry_run=getattr(args, "dry_run", False),
                  limiter=limiter, log=log, watch_spawner=spawner, chain_spawner=chainer)


def _fmt_status(st):
    if st.get("error") and st.get("state") == "offline":
        return f"offline ({st['error']})"
    rem = st.get("remaining_s")
    rem_s = f"{rem // 3600}h{rem % 3600 // 60:02d}m left" if isinstance(rem, int) else ""
    bits = [st.get("state", "?")]
    if st.get("program"):
        bits.append(st["program"] + (f" ({_fmt_opts(st['options'])})" if st.get("options") else ""))
    if rem_s:
        bits.append(rem_s)
    if st.get("door"):
        bits.append(f"door {st['door']}")
    if st.get("remote_start_allowed") is not None:
        bits.append("remote start " + ("on" if st["remote_start_allowed"] else "OFF"))
    if st.get("child_lock") is not None:
        bits.append("child lock " + ("on" if st["child_lock"] else "off"))
    return ", ".join(bits)


def _print_programs(nick, be, available=None):
    m = be.machine
    print(f"{nick}: {m.describe()} -- {m.title}")
    for k, p in m.programs.items():
        mark = ""
        if available is not None:
            mark = "  [on device]" if p.hc in available else "  [NOT reported by the device]"
        mins = f"{p.minutes} min" + (" (est.)" if p.estimate else "")
        print(f"  {k:<13} {p.label:<44} {p.hc or '(local only)'}  {mins}{mark}")
        if p.hc_note:
            print(f"  {'':<13} key: {p.hc_note}")
    for o in m.options.values():
        vals = "|".join(o.values) if o.kind == "enum" else (
            f"{o.lo}-{o.hi} min" if o.kind == "minutes" else "on/off")
        print(f"  option {o.name:<18} {vals:<32} {o.hc or ''}")
    print(f"  remote start: {m.remote_start}")
    print(f"  disarmed by: {', '.join(m.remote_start_disarms)}")
    if available is not None:
        extra = [k for k in available if k not in {p.hc for p in m.programs.values()}]
        if extra:
            print("  the device also reports: " + ", ".join(extra))


def cmd_control(args):
    if args.action == "tools":
        print(json.dumps(build_tools(make_fleet(backend=args.backend)), indent=2))
        return
    if args.action == "login":
        from .appliance import HC_TOKEN_FILE, config_dir, device_flow_login, write_secret
        tok = device_flow_login(scope=args.scope)
        path = os.path.join(config_dir(), HC_TOKEN_FILE)
        write_secret(path, tok)
        print(f"saved token to {path} (mode 0600; outside the repo)")
        return
    if args.action == "init":
        preset = args.request[0] if args.request else "bosch"
        if preset not in PRESETS:
            raise SystemExit(f"unknown preset {preset!r}; known: {', '.join(PRESETS)}")
        cfg = json.loads(json.dumps(PRESETS[preset]))
        if args.backend:
            cfg["backend"] = args.backend
        path = config_path()
        if os.path.exists(path) and not args.force:
            raise SystemExit(f"{path} exists; pass --force to replace it")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(cfg, f, indent=2)
        print(f"wrote {path}: " + ", ".join(f"{n} = Bosch {e['model']}"
                                            for n, e in cfg["appliances"].items())
              + f" (backend {cfg['backend']})")
        return
    fleet = make_fleet(backend=args.backend)
    if args.action == "wait":
        be = fleet.get(args.appliance)
        msg = args.message or be.machine.done_message
        st = wait_until_done(be, msg, poll_s=args.poll)
        print(f"final: {_fmt_status(st)}")
        try:                     # ChorePilot: the finish verifies chores and opens follow-ups
            from .events import poll_once
            from .house import House
            poll_once(House(watch=False))
        except Exception as e:  # the notification already went out; chores are best-effort here
            print(f"(chore update skipped: {e})")
        return
    if args.action == "programs":
        for n, be in fleet:
            if args.appliance and fleet.resolve(args.appliance) != n:
                continue
            avail = None
            if hasattr(be, "available_programs"):
                try:
                    avail = be.available_programs()
                except BackendError as e:
                    print(f"  ({n}: could not ask the device: {e})")
            _print_programs(n, be, avail)
        return
    if args.action == "sim":
        be = fleet.get(args.appliance)
        if not hasattr(be, "set"):
            raise SystemExit("`control sim` only drives simulated appliances")
        kw = {}
        for item in args.request:
            if item == "load":       # what a person does: open, load, close, press Remote Start
                be.set(door="open")
                kw.update(door="closed", remote_start_allowed=True)
                continue
            k, _, v = item.partition("=")
            k = {"remote_start": "remote_start_allowed", "remote": "remote_start_allowed"}.get(k, k)
            kw[k] = {"on": True, "off": False, "true": True, "false": False}.get(v.lower(), v)
        if kw:
            be.set(**kw)
        print(f"{be.appliance} (simulated): {_fmt_status(be.status())}")
        return
    bridge = build_bridge(args)
    if args.action == "chain":
        out = run_chain(bridge.fleet, args.id, bridge, poll_s=args.poll)
        print(f"chain {args.id}: {out}")
        return
    if args.action == "chains":
        pend = bridge.chains.pending()
        for c in pend:
            print(f"{c['id']}: start {c['target']} {c['program']} {_fmt_opts(c['options'])} after "
                  f"the {c['after']} ({c.get('phase', 'waiting for the ' + c['after'])})")
        if not pend:
            print("no scheduled starts")
        return
    if args.action == "status":
        names = [fleet.resolve(args.appliance)] if args.appliance else fleet.names()
        for n in names:
            st = bridge.call("get_status", {"appliance": n} if not bridge.legacy else {})
            if args.json:
                print(json.dumps(st, indent=2))
            else:
                print(f"{n}: {_fmt_status(st)}")
        for c in bridge.chains.pending():
            print(f"scheduled: {c['target']} {c['program']} after the {c['after']}")
        return
    if args.action == "ask":
        if not args.request:
            raise SystemExit("usage: chorepilot control ask \"wash my dishes\" --url http://host:8080")
        answer, _ = run_agent(" ".join(args.request), bridge, args.url, model=args.llm)
        print("\n" + answer + "\n")
        return
    # direct tool call, no model: chorepilot control start cotton --appliance washer --option temperature=40
    name = {"start": "start_program"}.get(args.action, args.action)
    targs = {} if bridge.legacy else {"appliance": args.appliance}
    if name == "start_program":
        be = fleet.get(args.appliance)
        targs["program"] = args.request[0] if args.request else be.machine.default_program
        targs["options"] = {}
        for kv in args.option or []:
            k, _, v = kv.partition("=")
            targs["options"][k] = int(v) if v.isdigit() else (v.lower() != "false" if v else True)
    if name == "notify_when_done" and args.request:
        targs["message"] = " ".join(args.request)
    print(json.dumps(bridge.call(name, targs), indent=2))


def add_parser(sub):
    sp = sub.add_parser("control", help="drive the appliances: status/start/pause/resume/cancel, "
                        "or `ask` a model to do it")
    sp.add_argument("action", choices=["status", "start", "pause", "resume", "cancel",
                                       "notify_when_done", "wait", "ask", "tools", "login",
                                       "programs", "init", "sim", "chain", "chains"])
    sp.add_argument("request", nargs="*", help="start: program name; ask: the request; "
                    "init: preset (bosch); sim: load | door=open | remote_start=on ...")
    add_bridge_args(sp)
    sp.add_argument("--appliance", "-a", help="which appliance (nickname, alias, type or model); "
                    "default: the only/default one")
    sp.add_argument("--option", action="append", help="start: key=value (temperature=40, "
                    "spin=1200, dryness=cupboard_plus, finish_in_minutes=120, delay_minutes=30)")
    sp.add_argument("--url", default=getenv("LLM_URL", "http://127.0.0.1:8080"),
                    help="ask: the model server (chorepilot serve) -- any host on the LAN")
    sp.add_argument("--llm", help="ask: model name to send (multi-model servers)")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--message", help="wait: notification text (default: per appliance)")
    sp.add_argument("--poll", type=float, default=30, help="wait/chain: seconds between polls "
                    "near the end")
    sp.add_argument("--id", help="chain: the scheduled start to run (internal)")
    sp.add_argument("--force", action="store_true", help="init: overwrite control.json")
    sp.add_argument("--scope", default="IdentifyAppliance Monitor Control Settings",
                    help="login: Home Connect scopes")
    sp.set_defaults(func=cmd_control)
    return sp


def add_bridge_args(sp):
    sp.add_argument("--backend", choices=["simulated", "ha", "mqtt", "homeconnect"],
                    help="default: $CHOREPILOT_BACKEND, ~/.config/chorepilot/control.json, "
                         "else simulated")
    sp.add_argument("--confirm", default="heat", choices=["heat", "all", "never"],
                    help="which actions need a yes: heat (start/resume, default), all, never")
    sp.add_argument("--yes", action="store_true", help="pre-confirm (same as --confirm never)")
    sp.add_argument("--dry-run", action="store_true",
                    help="read for real, but only print the writes it would send")
    sp.add_argument("--no-watch", action="store_true",
                    help="notify_when_done: don't spawn a background watcher")
