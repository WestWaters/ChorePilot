"""The House: every device + the chore board + the policy engine, behind one tool surface.

    request -> tool router (which tool groups this request needs)
            -> LLM tool call
            -> House.call: who is asking (person, role, source)
                 -> device tool:  validate (type plugin) -> status + interlocks -> POLICY
                                  (allow / confirm / strong confirm / approval queue /
                                  second factor / deny) -> rate limit -> plan -> apply
                 -> program appliance tool (dishwasher/washer/dryer): POLICY in front of the
                                  original Dishsoaker Bridge (which keeps its own interlocks,
                                  confirmation, dry run and chains)
                 -> chore tool:   the SQLite chore board
            -> audit log (append-only JSONL, 0600)

Device writes also feed the event engine: starting the dishwasher records who started it, so
when it finishes the chore is credited to them and the follow-up goes to the next person.
"""
from __future__ import annotations

import getpass
import json
import os
import re
import sys
import time
from datetime import datetime

from . import policy
from .adapters import ADAPTERS, make_adapter
from .appliance import BackendError, make_fleet
from .chores import ChoreDB, ChoreError, when
from .control import (Bridge, Notifier, RateLimiter, _brief, spawn_chain, spawn_watcher,
                      system_prompt as bridge_prompt, tty_confirm)
from .devtypes import get_type, is_program_type
from .devtypes.program import TOOL_TIERS
from .env import cache_dir, config_dir, getenv
from .housecfg import load_house, program_fleet_cfg
from .machines import norm
from .schedule import describe as describe_schedule

PROGRAM_TOOLS = ("get_status", "start_program", "pause", "resume", "cancel", "notify_when_done",
                 "start_when_done")
CHORE_TOOLS = ("list_chores", "who_has", "assign_chore", "complete_chore", "remind", "create_chore")


# ------------------------------------------------------------------ prompts at the terminal
def tty_strong_confirm(question, phrase):
    if not sys.stdin or not sys.stdin.isatty():
        return False
    try:
        got = input(f"  !! {question}\n  !! Type {phrase} to confirm (anything else cancels): ")
    except EOFError:
        return False
    return got.strip() == phrase


def tty_secret(prompt):
    if not sys.stdin or not sys.stdin.isatty():
        return None
    try:
        return getpass.getpass(f"  ## {prompt} ")
    except (EOFError, KeyboardInterrupt):
        return None


class Snapshot:
    """Last-known status per device (for turning polls into started/finished events)."""

    def __init__(self, path=None):
        self.path = path or os.path.join(cache_dir(), "device-states.json")

    def all(self):
        try:
            with open(self.path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def get(self, nick):
        return self.all().get(nick)

    def set(self, nick, st):
        d = self.all()
        d[nick] = {k: v for k, v in st.items() if k != "detail"}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, self.path)


class Device:
    def __init__(self, nick, dtype, adapter, cfg):
        self.nick, self.dtype, self.adapter, self.cfg = nick, dtype, adapter, cfg
        self.aliases = [norm(a) for a in cfg.get("aliases", [])] + [norm(a) for a in dtype.aliases]


class House:
    def __init__(self, cfg=None, db=None, person=None, source="cli", yes=False, dry_run=False,
                 confirm=None, strong_confirm=None, ask_secret=None, notifier=None, log=print,
                 limiter=None, backend=None, watch=True, defer=None, adapter_kw=None,
                 snapshot=None, audit_path=None, client=None):
        self.cfg = cfg if cfg is not None else load_house()
        self.db = db or ChoreDB()
        self.source, self.yes, self.dry_run, self.log = source, yes, dry_run, log
        self.client = client      # which MCP client / chat front-end, for the audit log
        self.confirm_fn = confirm or tty_confirm
        self.strong_fn = strong_confirm or tty_strong_confirm
        self.secret_fn = ask_secret or tty_secret
        self.policy = self.cfg.get("policy") or {}
        self.limiter = limiter or RateLimiter(path=os.path.join(cache_dir(), "control-ratelimit.json"))
        self.snapshot = snapshot or Snapshot()
        self.audit_path = audit_path or os.path.join(config_dir(), "audit.log")
        self.person = self._who(person)
        unit = self.cfg.get("unit")
        self.devices = {}
        for nick, d in (self.cfg.get("devices") or {}).items():
            t = d.get("type") or nick
            if is_program_type(t):
                continue
            dcfg = {**({"unit": unit} if unit else {}), **d}
            override = backend or getenv("BACKEND")
            override = override if override in ADAPTERS else None
            name = override or d.get("backend") or self.cfg.get("backend") or "simulated"
            if name == "homeconnect" and not get_type(t).hc_type:
                name = "ha"
            shared = {**(self.cfg.get("ha", {}) if name in ("ha", "matter") else {}),
                      **(self.cfg.get("mqtt", {}) if name == "mqtt" else {}),
                      **(self.cfg.get("homeconnect", {}) if name == "homeconnect" else {})}
            dtype = get_type(t, dcfg)
            ad = make_adapter(nick, dtype, {**shared, **dcfg}, backend=name, defer=defer,
                              **(adapter_kw or {}).get(nick, {}))
            self.devices[nick] = Device(nick, dtype, ad, dcfg)
        fcfg = program_fleet_cfg(self.cfg)
        self.bridge = None
        if fcfg:
            fleet = make_fleet(fcfg, backend=backend if backend in (
                "simulated", "ha", "mqtt", "homeconnect") else None)
            sp = ch = None
            if watch:
                sp = lambda msg, nick: spawn_watcher(nick, msg, None)  # noqa: E731
                ch = lambda cid: spawn_chain(cid, None)  # noqa: E731
            self.bridge = Bridge(fleet, confirm="heat", dry_run=dry_run, ask=self._bridge_ask,
                                 limiter=self.limiter, log=log, watch_spawner=sp, chain_spawner=ch)
        self.notifier = notifier or Notifier(self.bridge.backend if self.bridge else None)
        self._last_status = {}

    # ---------------------------------------------------------------- identity
    def _who(self, ref):
        ref = ref or getenv("USER_NAME") or os.environ.get("CHOREPILOT_USER")
        if ref:
            return self.db.person(ref)
        local = self.cfg.get("local_person")      # whoever ran `chorepilot setup` on this box
        if local and self.source == "cli":
            p = self.db.person(local, required=False)
            if p:
                return p
        owners = [p for p in self.db.people() if p["role"] == "owner"]
        return owners[0] if len(owners) == 1 else None

    @property
    def me(self):
        return self.person["name"] if self.person else None

    # ---------------------------------------------------------------- confirmation
    def _bridge_ask(self, question):
        return self._confirm(question)

    def _confirm(self, question):
        if self.yes:
            self.log(f"  [confirm   ] {question} -> yes (pre-confirmed)")
            return True
        return bool(self.confirm_fn(question))

    def _strong(self, question, phrase):
        return bool(self.strong_fn(question, phrase))

    def _second_factor(self, question):
        p = self.person
        if not p or not self.db.has_second_factor(p["name"]):
            return False, (f"{self.me or 'the owner'} has no PIN or TOTP set; run `chorepilot people "
                           f"set-pin {self.me or '<owner>'}` first")
        recent = [e for e in self.db.events(50) if e["event"] == "second_factor_failed"
                  and e["device"] == p["name"] and time.time() - e["at"] < 600]
        if len(recent) >= 3:
            return False, "too many wrong codes; wait 10 minutes"
        code = self.secret_fn(f"{question} {p['name']}, enter your ChorePilot PIN or TOTP code:")
        if code and (self.db.check_pin(p["name"], code) or self.db.check_totp(p["name"], code)):
            return True, None
        self.db.record_event(p["name"], "second_factor_failed", "person")
        return False, "wrong or missing code; nothing was done"

    # ---------------------------------------------------------------- resolution
    def program_nicks(self):
        return self.bridge.fleet.names() if self.bridge else []

    def resolve_device(self, ref, tool=None):
        """Nickname / alias / type -> generic Device (None if it is a program appliance)."""
        cands = [d for d in self.devices.values() if tool is None or d.dtype.tool == tool]
        if ref in (None, ""):
            if len(cands) == 1:
                return cands[0]
            raise ValueError("which one? " + ", ".join(d.nick for d in cands))
        n = norm(ref)
        for d in cands:
            if n == norm(d.nick):
                return d
        for d in cands:
            if n in d.aliases or n == d.dtype.name or n.replace("the_", "") == norm(d.nick):
                return d
        hits = [d for d in cands if n in norm(d.nick) or norm(d.nick) in n]
        if len(hits) == 1:
            return hits[0]
        if tool is None:
            return None
        raise ValueError(f"no {cands[0].dtype.title if cands else 'device'} called {ref!r}; "
                         f"you have: {', '.join(d.nick for d in cands)}")

    # ---------------------------------------------------------------- tools
    def tool_groups(self, request=None):
        """The deterministic tool router. Returns the set of groups this request needs:
        'chores', 'program', and device type names. A question about the board ("who has the
        dishes this week?") gets the chore tools only, so a small model can't wander off and
        start the dishwasher."""
        all_groups = {"chores"} | ({"program"} if self.bridge else set()) | \
            {d.dtype.name for d in self.devices.values()}
        if not request:
            return all_groups
        t = " " + request.lower() + " "
        words = set(re.findall(r"[a-z/']+", t))

        def has(k):
            return (k in t) if " " in k or "/" in k else (k in words)
        chore_cue = any(has(k) for k in ("chore", "chores", "who", "whose", "turn", "rota", "rotation",
                                         "assign", "remind", "reminder", "points", "task", "tasks",
                                         "todo", "to do", "swap", "this week", "next week", "schedule"))
        did = re.search(r"\b(i|we|i've|i have|he|she|they|[a-z]+) (just )?(did|done|finished|emptied|"
                        r"cleaned|took out|fed|walked|folded|vacuumed|mowed|watered|unloaded|made)\b", t)
        mark = re.search(r"\b(mark|marked|completed?|done with)\b", t)
        question = bool(re.search(r"\b(who|whose|what|which|when|list|show|how many)\b|\?", t))
        groups = set()
        if self.bridge:
            kw = set()
            for _, be in self.bridge.fleet:
                kw |= set(get_type(be.machine.kind).keywords)
            kw |= {norm(n) for n in self.program_nicks()}
            if any(has(k) for k in kw):
                groups.add("program")
        for d in self.devices.values():
            keys = set(d.dtype.keywords) | {d.nick.replace("_", " ")} | set(d.cfg.get("aliases", []))
            if any(has(k.lower()) for k in keys):
                groups.add(d.dtype.name)
        if "garage_door" in groups and "lock" in groups and not re.search(
                r"\b(lock|unlock|deadbolt|front door|back door)\b", t):
            groups.discard("lock")                 # "close the garage door" is not the front door
        if "water_heater" in groups and "valve" in groups and not re.search(
                r"\b(lawn|garden|sprinkler|sprinklers|irrigat\w*|plants|yard)\b", t):
            groups.discard("valve")                # "hot water" is not the lawn
        if (chore_cue and question) or did or mark:
            return {"chores"}
        if chore_cue:
            return groups | {"chores"}
        return groups or all_groups

    def tools(self, request=None):
        groups = self.tool_groups(request)
        out = [self._status_tool()]
        if "program" in groups and self.bridge:
            out += [t for t in self.bridge.tools() if t["function"]["name"] != "get_status"]
        by_type = {}
        for d in self.devices.values():
            if d.dtype.name in groups and d.dtype.tool:
                by_type.setdefault(d.dtype.name, []).append(d)
        for name, ds in by_type.items():
            out.append(ds[0].dtype.tool_schema([d.nick for d in ds]))
        if "chores" in groups:
            out += chore_tools(self, request)
        return out

    def _status_tool(self):
        nicks = self.program_nicks() + list(self.devices)
        return {"type": "function", "function": {
            "name": "get_status",
            "description": "Read a device (or all of them): state, program, remaining time, "
                           "temperatures, door, alarms.",
            "parameters": {"type": "object", "properties": {
                "appliance": {"type": "string", "enum": nicks, "description": "which device"}},
                "required": []}}}

    # ---------------------------------------------------------------- the one entry point
    def call(self, name, args=None):
        args = dict(args or {}) if isinstance(args, dict) else {}
        try:
            if name in CHORE_TOOLS:
                res = self._chore_call(name, args)
            elif name == "get_status":
                res = self._status(args.get("appliance") or args.get("device"))
            elif name in PROGRAM_TOOLS:
                res = self._program_call(name, args)
            else:
                d = next((d for d in self.devices.values() if d.dtype.tool == name), None)
                res = self._device_call(d, args) if d else {"ok": False, "error": f"unknown tool {name!r}"}
        except (ChoreError, ValueError) as e:
            res = {"ok": False, "error": str(e)}
        except BackendError as e:
            res = {"ok": False, "error": str(e)}
        self._audit(name, args, res)
        return res

    def _audit(self, name, args, res):
        secret = ("pin", "code", "passcode", "password", "totp", "otp")
        args = {k: ("***" if str(k).lower() in secret else v) for k, v in args.items()}
        rec = {"t": round(time.time(), 3), "who": self.me, "source": self.source, "tool": name,
               "args": args, "ok": res.get("ok"), "error": res.get("error"),
               "outcome": res.get("policy")}
        if self.client:
            rec["client"] = self.client
        try:
            os.makedirs(os.path.dirname(self.audit_path), exist_ok=True)
            fd = os.open(self.audit_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except OSError:
            pass

    # ---------------------------------------------------------------- status
    def _status(self, ref):
        if ref in (None, "", "all"):
            out = {}
            if self.bridge:
                for n, be in self.bridge.fleet:
                    out[n] = _brief(self.bridge._status_or_error(be))
            for n, d in self.devices.items():
                out[n] = self._dev_status(d)
            return {"ok": True, "devices": out}
        if self.bridge:
            try:
                nick = self.bridge.fleet.resolve(ref)
                return self.bridge.call("get_status", {} if self.bridge.legacy else {"appliance": nick})
            except KeyError:
                pass
        d = self.resolve_device(ref)
        if d is None:
            raise ValueError(f"no device called {ref!r}; you have: "
                             f"{', '.join(self.program_nicks() + list(self.devices))}")
        return {"ok": True, "device": d.nick, **self._dev_status(d)}

    def _dev_status(self, d):
        try:
            st = d.adapter.status()
        except BackendError as e:
            return {"state": "offline", "error": str(e)}
        return {k: v for k, v in st.items() if k != "detail" and v not in (None, [], "")}

    # ---------------------------------------------------------------- notifications
    def notify(self, message, to="household", log=None):
        """Desktop / webhook / HA notify as before, plus the outbox the chat front-ends
        (Telegram) deliver from. `to`: household | parents | <person name>."""
        self.notifier.send(message, log=log or self.log)
        try:
            self.db.outbox_add(message, to)
        except Exception:  # noqa: BLE001 - a notification must never break an action
            pass

    # ---------------------------------------------------------------- policy helpers
    def _queue(self, tool, args, summary, tier):
        a = self.db.add_approval("device", self.person, {"tool": tool, "args": args}, summary)
        self.notify(f"Approval needed #{a['id']}: {summary}. "
                    f"`chorepilot approvals approve {a['id']}`", to="parents", log=lambda m: None)
        return {"ok": False, "queued_for_approval": a["id"], "policy": "approval", "tier": tier,
                "error": (f"not done yet: {self.me} needs a parent's OK for this, so I sent the "
                          f"request to the parents (#{a['id']}). It happens when a parent approves.")}

    # ---------------------------------------------------------------- program appliances
    def _program_call(self, name, args):
        if not self.bridge:
            return {"ok": False, "error": "no dishwasher, washer or dryer is configured"}
        tier = TOOL_TIERS.get(name, 2)
        dec = policy.decide(self.person, tier, self.source, self.policy)
        if dec.outcome == "deny":
            return {"ok": False, "error": dec.reason, "policy": "deny"}
        if dec.outcome == "approval":
            # a parent decides on exactly what will run: checked against the appliance's own
            # profile now, so the queued request (and the text the parent reads) can't carry
            # programs, options or appliance names that don't exist
            from .control import validate_args
            try:
                args = validate_args(name, self._only_own_options(name, args), self.bridge.fleet)
            except (ValueError, TypeError) as e:
                return {"ok": False, "error": f"invalid arguments: {e}", "tier": tier}
            what = name.replace("_", " ") + (f" {args.get('program')}" if args.get("program") else "")
            return self._queue(name, args, f"{self.me} wants to {what} on the "
                               f"{args.get('appliance') or args.get('then_start') or 'appliance'}", tier)
        args = self._only_own_options(name, args)
        res = self.bridge.call(name, args)
        res = dict(res, policy=dec.outcome, tier=tier)
        if res.get("ok") and not res.get("dry_run") and name in ("start_program", "resume"):
            nick = (res.get("status") or {}).get("appliance") or args.get("appliance") or \
                self.bridge.fleet.default
            nick = self.bridge.fleet.resolve(nick)
            kind = self.bridge.fleet.get(nick).machine.kind
            self.db.record_event(nick, "started", kind, actor=self.me)
            if res.get("status"):
                self.snapshot.set(nick, res["status"])
        return res

    def _only_own_options(self, name, args):
        """The multi-appliance start_program schema lists every appliance's options, and small
        models fill them all in with defaults ("temperature": "cold" for the dishwasher). Keep
        only the options the target appliance actually has, and drop empty/false fill-ins;
        the Bridge then validates what is left strictly, as before."""
        if name not in ("start_program", "start_when_done") or self.bridge.legacy:
            return args
        from .machines import OPTION_ALIASES
        target = args.get("then_start") if name == "start_when_done" else args.get("appliance")
        try:
            m = self.bridge.fleet.get(target).machine if target else None
        except KeyError:
            return args
        if m is None:
            return args
        keep = {"appliance", "program", "message", "wait_for", "then_start", "after"}
        known = set(m.options) | {k for k, v in OPTION_ALIASES.items() if v in m.options}
        empty = (None, "", False, 0, "0", "false", "none")

        def ok(k, v):
            return norm(k) in known and not (isinstance(v, (str, int, bool)) and v in empty)
        out = {k: v for k, v in args.items() if k in keep or (k != "options" and ok(k, v))}
        if isinstance(args.get("options"), dict):
            out["options"] = {k: v for k, v in args["options"].items() if ok(k, v)}
        return out

    # ---------------------------------------------------------------- generic devices
    def _device_call(self, d0, args):
        d = self.resolve_device(args.get("device") or args.get("appliance"), tool=d0.dtype.tool)
        dt = d.dtype
        action, params = dt.validate(args.get("action"), args, d.cfg)
        st = d.adapter.status()
        why = dt.interlock(action, params, st, d.cfg)
        if why:
            return {"ok": False, "error": why}
        tier = dt.tier_for(action, params, d.cfg)
        act = dt.actions[action]
        dec = policy.decide(self.person, tier, self.source, self.policy, strong=act.confirm == "strong")
        if dec.outcome == "deny":
            return {"ok": False, "error": dec.reason, "policy": "deny", "tier": tier}
        if dec.outcome == "approval":
            bits = ", ".join(f"{k} {v}" for k, v in params.items() if k != "unit")
            return self._queue(dt.tool, dict(args, device=d.nick, action=action),
                               f"{self.me} wants to {action.replace('_', ' ')} the {d.nick}"
                               + (f" ({bits})" if bits else ""), tier)
        ops = d.adapter.plan(action, params)
        if self.dry_run:
            return {"ok": True, "dry_run": True, "would_send": _redact(ops), "tier": tier,
                    "policy": dec.outcome, "note": "dry run: nothing was sent"}
        why = self.limiter.check("start_program" if tier >= 2 else "pause", key=d.nick)
        if why:
            return {"ok": False, "error": why, "tier": tier}
        q = dt.confirmation(action, params, d.nick)
        if dec.outcome == "confirm" and not self._confirm(q):
            return {"ok": False, "declined": True, "policy": "confirm", "tier": tier,
                    "error": "the user did not confirm; nothing was done"}
        if dec.outcome == "strong_confirm" and not self._strong(q, act.phrase or "YES"):
            return {"ok": False, "declined": True, "policy": "strong_confirm", "tier": tier,
                    "error": f"not confirmed (the person must type {act.phrase}); nothing was done"}
        if dec.outcome == "second_factor":
            ok, why = self._second_factor(q)
            if not ok:
                return {"ok": False, "declined": True, "policy": "second_factor", "tier": tier,
                        "error": why}
        d.adapter.apply(ops)
        self.limiter.record("start_program" if tier >= 2 else "pause", key=d.nick)
        after = d.adapter.status()
        for ev in dt.events(st, after):
            if ev == "started":
                self.db.record_event(d.nick, ev, dt.name, actor=self.me)
            else:
                for line in self.db.apply_event(d.nick, ev, dt.name, actor=self.me):
                    self.notify(line)
        self.snapshot.set(d.nick, after)
        out = {"ok": True, "device": d.nick, "action": action, "tier": tier, "policy": dec.outcome,
               "status": {k: v for k, v in after.items() if k != "detail" and v not in (None, [], "")}}
        if params.get("converted_from"):
            out["note"] = f"read {params['converted_from']} as {params['temperature']:g} {params.get('unit')}"
        return out

    # ---------------------------------------------------------------- chores
    def _chore_call(self, name, args):
        db = self.db
        role = (self.person or {}).get("role", "guest")

        def person_arg(key="person"):
            v = args.get(key)
            if v in (None, "", "me", "i", "myself", "Me", "I"):
                return self.person
            return db.person(v)
        if name == "list_chores":
            db.materialise()
            who = person_arg() if args.get("person") else None
            scope = (args.get("scope") or "open").lower()
            if scope == "week":
                since = time.time() - 7 * 86400
                rows = db.instances(person=who["name"] if who else None, since=since)
            else:
                rows = db.instances("open", person=who["name"] if who else None)
            items = [{"chore": r["title"], "who": r["assignee"] or "anyone", "due": when(r["due"]),
                      "state": r["state"]} for r in rows[:15]]
            weekly = [{"chore": c["title"], "this_week": (db.week_assignee(c) or {}).get("name")}
                      for c in db.chores() if c["rotate"] == "week"]
            return {"ok": True, "chores": items, "weekly_rotations": weekly}
        if name == "who_has":
            if not args.get("chore"):
                raise ValueError("which chore?")
            db.materialise()
            return {"ok": True, **db.who_has(args["chore"])}
        if name == "complete_chore":
            if not args.get("chore"):
                raise ValueError("which chore?")
            who = person_arg()
            if who is None:
                raise ValueError("I don't know who you are; run with --as <name>")
            if self.person and who["id"] != self.person["id"] and role not in ("owner", "adult"):
                return {"ok": False, "error": "you can only mark your own chores done",
                        "policy": "deny"}
            db.materialise()
            res = db.complete(args["chore"], by=who["name"])
            inst = res["instance"]
            out = {"ok": True, "chore": inst["title"], "done_by": inst["done_by"],
                   "state": inst["state"]}
            if res["approval"]:
                out["note"] = f"waiting for a parent to approve (#{res['approval']['id']})"
            if res["followups"]:
                out["opened"] = [{"chore": f["title"], "who": f["assignee"] or "anyone"}
                                 for f in res["followups"]]
            return out
        if name == "assign_chore":
            if role not in ("owner", "adult", "staff"):
                return {"ok": False, "error": "only an adult can assign chores", "policy": "deny"}
            if not args.get("chore") or not args.get("person"):
                raise ValueError("assign_chore needs chore and person")
            r = db.assign(args["chore"], person_arg()["name"], week=bool(args.get("whole_week")))
            return {"ok": True, "chore": r["title"], "assigned_to": r["assignee"],
                    **({"week": r["week"]} if r.get("week") else {})}
        if name == "remind":
            who = db.person(args["person"]) if args.get("person") not in (None, "", "everyone",
                                                                            "all") else None
            msgs = db.reminders(person=who["name"] if who else None, chore=args.get("chore") or None)
            sent, held = [], []
            for name_, msg, quiet in msgs:
                if quiet:
                    held.append(name_)
                    continue
                self.notify(msg, to=name_)
                sent.append(name_)
            if not msgs:
                return {"ok": True, "note": "nothing open to remind about"}
            return {"ok": True, "reminded": sorted(set(sent)),
                    **({"quiet_hours": sorted(set(held))} if held else {})}
        if name == "create_chore":
            if role not in ("owner", "adult", "staff"):
                return {"ok": False, "error": "only an adult can add chores", "policy": "deny"}
            c = db.add_chore(args["title"], args.get("schedule"), args.get("rotation") or [],
                             verify=args.get("verify"))
            return {"ok": True, "chore": c["title"], "schedule": describe_schedule(c["schedule"])}
        raise ValueError(f"unknown chore tool {name}")

    # ---------------------------------------------------------------- approvals
    def approve(self, aid, approve=True):
        """An adult decides a queued request. Device requests then run as the approver: the
        approval is their yes (strong confirmations and second factors still ask)."""
        a = self.db.approval(aid)
        if not a:
            raise ChoreError(f"no approval #{aid}")
        a = self.db.decide(aid, self.me, approve)
        self._audit("approve" if approve else "deny", {"approval": a["id"], "summary": a["summary"]},
                    {"ok": True})
        if a["kind"] != "device" or not approve:
            return {"ok": True, "approval": a["id"], "state": a["state"]}
        saved, self.yes = self.yes, True
        try:
            res = self.call(a["payload"]["tool"], a["payload"]["args"])
        finally:
            self.yes = saved
        self.db.set_result(a["id"], res)
        if not res.get("ok"):
            # it could not run (rate limit, interlock, offline): keep it pending to retry
            self.db._x("UPDATE approvals SET state='pending', decided_by=NULL, decided_at=NULL "
                       "WHERE id=?", (a["id"],))
            return {"ok": False, "approval": a["id"], "state": "pending",
                    "error": f"approved, but it could not run yet: {res.get('error')}", "result": res}
        if a.get("person"):
            self.notify(f"Approved: {a['summary']}.", to=a["person"])
        return {"ok": True, "approval": a["id"], "state": a["state"], "result": res}

    # ---------------------------------------------------------------- prompt
    def system_prompt(self, request=None):
        groups = self.tool_groups(request)
        p = self.person
        lines = [f"You are ChorePilot, the assistant of {self.cfg.get('household') or 'this house'}. "
                 "You act only through the tools.",
                 f"Talking to: {p['name']} ({p['role']})." if p else "Talking to: an unknown guest.",
                 f"Now: {datetime.now().strftime('%a %d %b %Y %H:%M')}."]
        if "program" in groups and self.bridge:
            if self.bridge.legacy:
                lines.append("Dishwasher: " + json.dumps(
                    _brief(self.bridge._status_or_error(self.bridge.backend))) + ". 'wash the dishes' "
                    "-> start_program program=auto.")
            else:
                lines.append("Appliances:")
                lines += bridge_prompt(self.bridge).split("\n")[1:-1]
            lines.append("To be told when an appliance finishes -> notify_when_done.")
        devs = [d for d in self.devices.values() if d.dtype.name in groups]
        if devs:
            lines.append("Devices:")
            for d in devs:
                st = self._dev_status(d)
                lines.append(f"- {d.nick} ({d.dtype.title}): {d.dtype.describe_status(st)}")
        hints = {"climate": "'set the AC to 72' -> set_climate temperature=72.",
                 "valve": "'water the lawn for 10 minutes' -> water_zone action=water minutes=10.",
                 "light": "'lights off' -> set_light action=turn_off.",
                 "oven": "'preheat the oven to 400' -> oven action=preheat temperature=400.",
                 "coffee_maker": "'make me an espresso' -> brew_coffee beverage=espresso.",
                 "vacuum": "'vacuum the floor' -> vacuum action=start.",
                 "lock": "'lock the front door' -> door_lock action=lock.",
                 "garage_door": "'close the garage' -> garage_door action=close.",
                 "alarm_panel": "'arm the alarm' -> alarm_panel action=arm_away.",
                 "ev_charger": "'charge the car' -> ev_charger action=start_charging.",
                 "water_heater": "'set the water heater to 120' -> water_heater "
                                 "action=set_temperature temperature=120.",
                 "air_purifier": "'turn on the purifier' -> air_purifier action=turn_on.",
                 "humidifier": "'set humidity to 45' -> humidifier action=set_humidity humidity=45.",
                 "media_player": "'turn the music down to 20' -> media_player action=set_volume "
                                 "volume=20.",
                 "cover": "'close the blinds' -> set_cover action=close."}
        for d in {d.dtype.name for d in devs}:
            if d in hints:
                lines.append(hints[d])
        if "chores" in groups:
            self.db.materialise()
            weekly = [f"{c['title']}: {(self.db.week_assignee(c) or {}).get('name', '?')} this week"
                      for c in self.db.chores() if c["rotate"] == "week"]
            open_ = [f"{i['title']} -> {i['assignee'] or 'anyone'} (due {when(i['due'])})"
                     for i in self.db.instances("open")[:8]]
            if weekly:
                lines.append("Weekly rotations: " + "; ".join(weekly) + ".")
            if open_:
                lines.append("Open chores: " + "; ".join(open_) + ".")
            lines.append("'who has the dishes this week?' -> who_has chore=dishes. 'I emptied the "
                         "dishwasher' -> complete_chore chore='empty the dishwasher'.")
        lines.append("Just call the tool: the app asks the person for any confirmation itself. "
                     "Never say something happened unless the tool result has ok: true; if it was "
                     "declined, queued for approval or failed, say that in one sentence. Answer in "
                     "one or two short sentences.")
        return "\n".join(lines)


def _redact(ops):
    """Dry-run output goes back to the model: never let a lock's or alarm panel's own code
    (from house.json / secrets.json) ride along."""
    out = []
    for op in ops:
        op = dict(op)
        if isinstance(op.get("body"), dict) and "code" in op["body"]:
            op["body"] = dict(op["body"], code="***")
        out.append(op)
    return out


def chore_tools(house, request=None):
    people = [p["name"] for p in house.db.people()]
    titles = [c["title"] for c in house.db.chores()]
    pdesc = "a person's name" + (f" ({', '.join(people)})" if people else "") + " or 'me'"
    cdesc = "the chore, e.g. " + (", ".join(titles[:8]) if titles else "dishes")

    def tool(name, desc, props, req=()):
        return {"type": "function", "function": {"name": name, "description": desc,
                "parameters": {"type": "object", "properties": props, "required": list(req)}}}
    out = [
        tool("who_has", "Who has a chore (this week / next up), and when it was last done.",
             {"chore": {"type": "string", "description": cdesc}}, ["chore"]),
        tool("list_chores", "List open chores, optionally for one person.",
             {"person": {"type": "string", "description": pdesc},
              "scope": {"type": "string", "enum": ["open", "week"]}}),
        tool("complete_chore", "Mark a chore done (by the person talking unless another is named).",
             {"chore": {"type": "string", "description": cdesc},
              "person": {"type": "string", "description": pdesc}}, ["chore"]),
        tool("assign_chore", "Give a chore to someone (whole_week for weekly chores).",
             {"chore": {"type": "string", "description": cdesc},
              "person": {"type": "string", "description": pdesc},
              "whole_week": {"type": "boolean"}}, ["chore", "person"]),
        tool("remind", "Send a reminder about open chores to a person (or everyone).",
             {"person": {"type": "string", "description": pdesc + " or 'everyone'"},
              "chore": {"type": "string", "description": cdesc}}),
    ]
    if request and re.search(r"\b(add|create|new|make)\b.*\bchore", request.lower()):
        out.append(tool("create_chore", "Add a new chore.",
                        {"title": {"type": "string"},
                         "schedule": {"type": "string", "description": "e.g. 'every monday', 'daily at 8pm'"},
                         "rotation": {"type": "array", "items": {"type": "string"}}}, ["title"]))
    return out
