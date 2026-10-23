"""ChorePilot's house and chore commands: init, people, chores, approvals, events, devices,
ask, demo. (probe / recommend / plan / scan / serve / wash / control are the original
Dishsoaker3000 commands, in cli.py and control.py.)"""
from __future__ import annotations

import copy
import getpass
import json
import os
import sys
import time

from .chores import ChoreDB, ChoreError, when
from .env import cache_dir
from .housecfg import PRESETS, house_path, load_house, write_house
from .machines import norm
from .schedule import describe


def _db():
    return ChoreDB()


def _house(args, **kw):
    from .house import House
    cfg = load_house()
    quiet = kw.pop("quiet", False)
    if cfg.get("_preset") == "demo" and not quiet:
        print(f"  (no {house_path()} yet: using the simulated demo house; `chorepilot init` "
              "writes one)")
    return House(cfg, person=getattr(args, "as_person", None), yes=getattr(args, "yes", False),
                 dry_run=getattr(args, "dry_run", False), backend=getattr(args, "backend", None),
                 watch=not getattr(args, "no_watch", False), **kw)


def _fail(e):
    raise SystemExit(f"chorepilot: {e}")


# ------------------------------------------------------------------ init
def cmd_init(args):
    preset = args.preset or "demo"
    if preset not in PRESETS:
        _fail(f"unknown preset {preset!r}; known: {', '.join(PRESETS)}")
    cfg = copy.deepcopy(PRESETS[preset])
    if args.backend:
        cfg["backend"] = args.backend
    path = write_house(cfg, force=args.force)
    print(f"wrote {path}: {len(cfg['devices'])} devices ({', '.join(cfg['devices'])}), "
          f"backend {cfg['backend']}")


# ------------------------------------------------------------------ people
def cmd_people(args):
    db = _db()
    try:
        if args.action == "add":
            handles = {k: v for k, v in (("telegram", args.telegram), ("discord", args.discord),
                                         ("phone", args.phone)) if v}
            p = db.add_person(args.name, args.role, handles, args.quiet)
            print(f"added {p['name']} ({p['role']})")
        elif args.action == "list":
            for p in db.people():
                f2 = "PIN" if p["pin_hash"] else ""
                f2 += ("+" if f2 and p["totp_secret"] else "") + ("TOTP" if p["totp_secret"] else "")
                print(f"{p['name']:<12} {p['role']:<6} {f2 or '-':<9} "
                      f"{p['quiet_hours'] or ''}")
            if not db.people():
                print("nobody yet: run `chorepilot setup` (you become the owner)")
        elif args.action == "set-pin":
            pin = args.pin or (getpass.getpass(f"new PIN for {args.name}: ") if sys.stdin.isatty() else None)
            if not pin:
                _fail("give --pin or run it in a terminal")
            db.set_pin(args.name, pin)
            print(f"PIN set for {args.name} (stored as a PBKDF2 hash)")
        elif args.action == "set-totp":
            from . import qr
            secret, uri = db.set_totp(args.name)
            print(qr.terminal(qr.encode(uri), ansi=sys.stdout.isatty()))
            print(f"TOTP secret for {args.name}: {secret}\nauthenticator URI: {uri}")
        elif args.action == "set-role":
            p = db.person(args.name)
            if (args.role == "owner") != (p["role"] == "owner") and db.owners():
                _owner_code(db, args, f"change who owns the household ({p['name']})")
            db.set_role(p["name"], args.role)
            print(f"{p['name']} is now {args.role}")
        elif args.action == "transfer-owner":
            frm = db.person(args.from_owner) if args.from_owner else (
                db.owners()[0] if len(db.owners()) == 1 else None)
            if not frm or frm["role"] != "owner":
                _fail("say which owner hands over: --from <owner>")
            if not args.name:
                _fail("usage: chorepilot people transfer-owner <new owner> [--from <owner>] [--keep]")
            _owner_code(db, args, f"hand the household over to {args.name}", only=frm)
            to = db.transfer_owner(frm["name"], args.name, keep=args.keep)
            from .onboard import read_house, write_house_raw
            cfg = read_house()
            if cfg and cfg.get("local_person") == frm["name"] and not args.keep:
                cfg["local_person"] = to["name"]
                write_house_raw(cfg)
            print(f"{to['name']} is now an owner" + ("" if args.keep else
                  f"; {frm['name']} is an adult") + ". " + ("" if db.has_second_factor(to["name"])
                  else f"Set a PIN: chorepilot people set-pin {to['name']}"))
    except ChoreError as e:
        _fail(e)


def _owner_code(db, args, why, only=None):
    """Ownership changes need an owner's PIN or TOTP code (prompted, or --code)."""
    owners = [only] if only else db.owners()
    code = getattr(args, "code", None)
    if not code and sys.stdin.isatty():
        code = getpass.getpass(f"PIN or TOTP code of {' or '.join(o['name'] for o in owners)} "
                               f"(to {why}): ")
    if not code or not any(db.check_pin(o["name"], code) or db.check_totp(o["name"], code)
                           for o in owners):
        _fail(f"an owner's PIN or TOTP code is needed to {why} (--code)")


# ------------------------------------------------------------------ chores
def _fmt_inst(i):
    by = f" by {i['done_by']}" if i.get("done_by") and i["state"] != "open" else ""
    return (f"#{i['id']:<4} {i['title']:<26} {i['assignee'] or 'anyone':<10} {i['state']:<16} "
            f"due {when(i['due'])}{by}")


def cmd_chores(args):
    db = _db()
    try:
        a = args.action
        if a == "add":
            c = db.add_chore(args.title, args.schedule, [x for x in (args.rotation or "").split(",") if x],
                             args.strategy, args.rotate, args.points, args.verify, args.followup or [],
                             args.needs_approval, args.room)
            print(f"added '{c['title']}': {describe(c['schedule'])}, {c['strategy']}"
                  + (f" ({c['rotate']})" if c["rotate"] == "week" else "")
                  + (f", verified by {c['verify']}" if c["verify"] else "")
                  + (f", then {', '.join(c['followups'])}" if c["followups"] else ""))
        elif a == "list":
            db.materialise()
            if args.defs:
                for c in db.chores():
                    rot = ", ".join(db.person(p)["name"] for p in c["rotation"]) or "anyone"
                    print(f"{c['title']:<26} {describe(c['schedule']):<28} {c['strategy']}/{c['rotate']}"
                          f"  [{rot}]" + (f"  verify {c['verify']}" if c["verify"] else "")
                          + (f"  -> {', '.join(c['followups'])}" if c["followups"] else ""))
                return
            rows = db.instances(None if args.all else ("open", "pending_approval"),
                                person=args.person, since=time.time() - 7 * 86400 if args.all else None)
            for i in rows:
                print(_fmt_inst(i))
            for c in db.chores():
                if c["rotate"] == "week":
                    p = db.week_assignee(c)
                    print(f"this week: {c['title']} -> {p['name'] if p else '?'}")
            if not rows:
                print("nothing open")
        elif a == "done":
            by = args.as_person or args.person
            res = db.complete(args.chore, by=by, note=args.note)
            i = res["instance"]
            print(f"done: '{i['title']}' by {i['done_by'] or '?'} ({i['state']})")
            if res["approval"]:
                print(f"  waiting for a parent: approval #{res['approval']['id']}")
            for f in res["followups"]:
                print(f"  opened '{f['title']}' for {f['assignee'] or 'anyone'}")
        elif a == "assign":
            r = db.assign(args.chore, args.person, week=args.week)
            print(f"'{r['title']}' -> {r['assignee']}" + (f" for week {r['week']}" if r.get("week") else ""))
        elif a == "swap":
            n = db.swap(args.chore, args.person, args.other)
            print(f"swapped {n} assignment(s)")
        elif a == "who":
            print(json.dumps(db.who_has(args.chore), indent=2))
        elif a == "remind":
            for name, msg, quiet in db.reminders(person=args.person):
                print(("(quiet hours, held) " if quiet else "") + msg)
        elif a == "points":
            for name, pts in sorted(db.points(args.days).items(), key=lambda x: -x[1]):
                print(f"{name:<12} {pts}")
        elif a == "rm":
            db.remove_chore(args.chore)
            print("removed")
    except ChoreError as e:
        _fail(e)


# ------------------------------------------------------------------ approvals
def cmd_approvals(args):
    if args.action in (None, "list"):
        rows = _db().approvals()
        for a in rows:
            print(f"#{a['id']:<4} {a['kind']:<6} {a['summary']}  ({when(a['created'])})")
        if not rows:
            print("no pending approvals")
        return
    h = _house(args, quiet=True)
    if not h.person or h.person["role"] not in ("owner", "adult"):
        _fail("approvals are decided by an owner or adult: add --as <name>")
    try:
        res = h.approve(int(args.id), approve=args.action == "approve")
    except ChoreError as e:
        _fail(e)
    print(json.dumps(res, indent=2, default=str))


# ------------------------------------------------------------------ events
def cmd_events(args):
    from . import events
    if args.action == "list":
        for e in _db().events(args.limit):
            print(f"{time.strftime('%m-%d %H:%M:%S', time.localtime(e['at']))}  {e['device']:<16} "
                  f"{e['event']:<14} {e['actor'] or ''}")
        return
    h = _house(args, quiet=True)
    if args.action == "emit":
        dtype = None
        if h.bridge:
            try:
                dtype = h.bridge.fleet.get(args.device).machine.kind
            except KeyError:
                pass
        if dtype is None and args.device in h.devices:
            dtype = h.devices[args.device].dtype.name
        for ln in h.db.apply_event(args.device, args.event, dtype, actor=args.as_person):
            print(ln)
            h.notify(ln, log=lambda m: None)
        return
    if args.action == "poll":
        res = events.poll_once(h)
        if not res:
            print("no new device events")
        return
    if args.action == "push":
        from .push import run_push
        run_push(h, interval=args.interval, safety=args.safety)
        return
    events.watch(h, args.interval)


# ------------------------------------------------------------------ confirmations (MCP tier 2)
def cmd_confirm(args):
    from .confirm import channels, terminal_answer
    db = _db()
    if not args.id:
        rows = db.pending_confirmations()
        for c in rows:
            print(f"#{c['id']:<4} for {c['person'] or '?':<8} from {c['client'] or c['source']}: "
                  f"{c['question']}" + (f"  (type {c['phrase']})" if c["kind"] == "phrase" else "")
                  + f"  [{max(0, int(c['expires'] - time.time()))}s left]")
        if not rows:
            print("nothing is waiting for a confirmation")
        return
    if not args.deny and "terminal" not in channels(load_house()):
        _fail("house.json mcp.confirm_channels does not allow answering from the terminal; "
              "answer in Telegram")
    try:
        c = terminal_answer(db, int(args.id), deny=args.deny)
    except ValueError as e:
        _fail(e)
    print(f"#{args.id}: {c['state'] if c else 'no longer pending'}")


# ------------------------------------------------------------------ devices
def _kv(items):
    out = {}
    for item in items or []:
        k, _, v = item.partition("=")
        out[k] = {"true": True, "false": False, "on": True, "off": False}.get(v.lower(), v) if v else True
    return out


def cmd_devices(args):
    from .devtypes import TIER_NAMES, all_types
    if args.action == "types":
        for name, t in all_types().items():
            acts = ", ".join(f"{a.name}(t{a.tier})" for a in t.actions.values())
            print(f"{name:<13} {t.title:<17} tool={t.tool or '(bridge)':<14} {acts}")
        print("tiers: " + ", ".join(f"{k}={v}" for k, v in TIER_NAMES.items()))
        return
    if args.action == "deferred":
        from .adapters import run_deferred
        h = _house(args, quiet=True)
        print(f"deferred {args.nick}: {run_deferred(args.nick, lambda n: h.devices[n].adapter)}")
        return
    h = _house(args)
    if args.action == "list":
        if h.bridge:
            for n, be in h.bridge.fleet:
                print(f"{n:<18} {be.machine.kind:<13} {be.describe():<11} {be.machine.describe()}")
        for n, d in h.devices.items():
            print(f"{n:<18} {d.dtype.name:<13} {d.adapter.describe():<11} {d.dtype.title}")
        return
    if args.action == "status":
        print(json.dumps(h.call("get_status", {"appliance": args.nick} if args.nick else {}),
                         indent=2, default=str))
        return
    if args.action == "sim":
        d = h.devices.get(args.nick)
        if d is None or not hasattr(d.adapter, "set"):
            _fail("`devices sim` drives simulated plugin devices (use `control sim` for the "
                  "dishwasher/washer/dryer)")
        d.adapter.set(**_kv(args.params))
        print(json.dumps(d.adapter.status(), default=str))
        return
    if args.action == "do":
        if not args.nick or not args.params:
            _fail("usage: chorepilot devices do <device> <action> [key=value ...] --as <name>")
        act, rest = args.params[0], _kv(args.params[1:])
        if h.bridge and args.nick in [norm(n) for n in h.program_nicks()] + h.program_nicks():
            # dishwasher / washer / dryer: through the House (policy + who started it)
            name = {"start": "start_program"}.get(act, act)
            targs = {} if h.bridge.legacy else {"appliance": args.nick}
            if name == "start_program":
                prog = next((k for k, v in rest.items() if v is True), None)
                targs["program"] = prog or h.bridge.fleet.get(args.nick).machine.default_program
                targs["options"] = {k: v for k, v in rest.items() if v is not True}
            print(json.dumps(h.call(name, targs), indent=2, default=str))
            return
        d = h.resolve_device(args.nick)
        print(json.dumps(h.call(d.dtype.tool, {"device": d.nick, "action": act, **rest}),
                         indent=2, default=str))


# ------------------------------------------------------------------ ask (the chat front door)
def cmd_ask(args):
    from . import agent
    request = " ".join(args.request)
    h = _house(args)
    llm = h.cfg.get("llm") or {}
    if not args.url and llm.get("url"):
        args.url = llm["url"]
        args.llm = args.llm or llm.get("model")
    print(f"ChorePilot -- {h.me or 'guest'} asks: {request!r}")
    proc = dog = None
    url = args.url
    try:
        if not url:
            from . import serve
            from .cli import _device, _pick_model
            from .fit import ModelSpec, best_ctx
            dev = _device(args)
            args.ctx = args.ctx or 8192
            info = _pick_model(args, dev)
            spec = ModelSpec.from_gguf(info)
            f = best_ctx(spec, dev, args.kv, want=args.ctx)
            print(f"  [model     ] {spec.name} ({spec.file_bytes / 1e9:.2f} GB), ctx {f.ctx}")
            log = os.path.join(cache_dir(), "llama-server.log")
            proc, dog = serve.serve_local(info, dev, ctx=f.ctx, port=args.port, host="127.0.0.1",
                                          force=args.force, kv_type=args.kv, detach_after=0,
                                          log=lambda m: None, output=log)
            url = f"http://127.0.0.1:{args.port}"
        t = time.time()
        answer, trace = agent.run(request, h, url, model=args.llm)
        print("\n" + answer + "\n")
        speeds = [s["tok_s"] for s in trace if s.get("tok_s")]
        print(f"  [done      ] {time.time() - t:.1f}s, {len(trace)} model turn(s)"
              + (f", {max(speeds):.1f} tok/s" if speeds else ""))
    finally:
        if dog:
            dog.stop()
        if proc:
            from . import serve
            serve.terminate(proc)


# ------------------------------------------------------------------ demo household
DEMO_PEOPLE = [("Alex", "owner", None), ("Sam", "adult", None), ("Jordan", "kid", "21:00-07:00"),
               ("Riley", "kid", "20:30-07:00")]
DEMO_CHORES = [
    dict(title="Dishes", schedule="daily at 19:30", rotation=["Jordan", "Sam", "Riley", "Alex"],
         rotate="week", verify="dishwasher.finished", followups=["Empty the dishwasher"], points=2),
    dict(title="Empty the dishwasher", schedule="on demand", rotation=["Riley", "Jordan", "Sam"],
         points=1, needs_approval=True),
    dict(title="Laundry", schedule="every monday and thursday at 18:00", rotation=["Sam", "Alex"],
         verify="washer.finished", followups=["Fold the laundry"], points=2),
    dict(title="Fold the laundry", schedule="on demand", rotation=["Jordan", "Riley"], points=1,
         needs_approval=True),
    dict(title="Vacuum the living room", schedule="every 3 days", rotation=["Riley", "Jordan"],
         verify="vacuum.finished", points=1),
    dict(title="Water the lawn", schedule="weekends at 07:00", rotation=["Alex", "Sam"],
         verify="lawn_sprinkler.finished", points=1),
    dict(title="Take out the bins", schedule="every tuesday at 20:00", rotation=["Jordan", "Riley"],
         points=1, needs_approval=True),
]


def cmd_demo(args):
    db = _db()
    if db.people() and not args.force:
        _fail("the household already has people; pass --force to add the demo household anyway")
    for name, role, quiet in DEMO_PEOPLE:
        try:
            db.add_person(name, role, quiet_hours=quiet)
        except ChoreError:
            pass
    for c in DEMO_CHORES:
        try:
            db.add_chore(**c)
        except ChoreError:
            pass
    if args.pin:
        db.set_pin("Alex", args.pin)
    db.materialise()
    print("demo household: " + ", ".join(f"{n} ({r})" for n, r, _ in DEMO_PEOPLE))
    for c in db.chores():
        print(f"  {c['title']:<24} {describe(c['schedule'])}"
              + (f", verified by {c['verify']}" if c["verify"] else ""))


# ------------------------------------------------------------------ parser
def add_commands(sub, model_args, dev_args, bridge_args):
    def as_arg(sp):
        sp.add_argument("--as", dest="as_person", help="who is asking (a person's name); "
                        "default $CHOREPILOT_USER or the only owner")

    sp = sub.add_parser("init", help="write ~/.config/chorepilot/house.json from a preset")
    sp.add_argument("preset", nargs="?", help=f"{', '.join(PRESETS)} (default demo)")
    sp.add_argument("--backend", choices=["simulated", "ha", "matter", "mqtt", "homeconnect"])
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("people", help="the household: add, list, set-pin, set-totp, set-role, "
                        "transfer-owner")
    sp.add_argument("action", choices=["add", "list", "set-pin", "set-totp", "set-role",
                                       "transfer-owner"])
    sp.add_argument("name", nargs="?")
    sp.add_argument("--role", default="adult", choices=["owner", "adult", "kid", "guest", "staff"])
    sp.add_argument("--quiet", help="quiet hours, e.g. 21:00-07:00")
    sp.add_argument("--telegram")
    sp.add_argument("--discord")
    sp.add_argument("--phone")
    sp.add_argument("--pin", help="set-pin: non-interactive (prefer the prompt)")
    sp.add_argument("--from", dest="from_owner", help="transfer-owner: the owner handing over")
    sp.add_argument("--keep", action="store_true", help="transfer-owner: stay an owner too")
    sp.add_argument("--code", help="an owner's PIN/TOTP for ownership changes (prefer the prompt)")
    sp.set_defaults(func=cmd_people)

    sp = sub.add_parser("chores", help="the chore board: add, list, done, assign, swap, who, "
                        "remind, points, rm")
    sp.add_argument("action", choices=["add", "list", "done", "assign", "swap", "who", "remind",
                                       "points", "rm"])
    sp.add_argument("chore", nargs="?", help="chore title (fuzzy: 'dishes')")
    sp.add_argument("person", nargs="?")
    sp.add_argument("other", nargs="?", help="swap: the other person")
    sp.add_argument("--title", help="add: the chore's title (or give it positionally)")
    sp.add_argument("--schedule", help="daily at 8pm | every monday | every 3 days | weekdays | "
                    "3 days after done | on demand | RRULE")
    sp.add_argument("--rotation", help="comma-separated names, in order")
    sp.add_argument("--strategy", default="round_robin",
                    choices=["round_robin", "least_points", "fixed", "random", "claimable"])
    sp.add_argument("--rotate", default="instance", choices=["instance", "week"])
    sp.add_argument("--points", type=int, default=1)
    sp.add_argument("--verify", help="device event that proves it done, e.g. dishwasher.finished")
    sp.add_argument("--followup", action="append", help="chore to open when this one is done")
    sp.add_argument("--needs-approval", action="store_true", help="kids' self-reports need a parent")
    sp.add_argument("--room")
    sp.add_argument("--week", action="store_true", help="assign: the whole week")
    sp.add_argument("--all", action="store_true", help="list: include done/missed (7 days)")
    sp.add_argument("--defs", action="store_true", help="list: the chore definitions")
    sp.add_argument("--days", type=int, default=7)
    sp.add_argument("--note")
    as_arg(sp)
    sp.set_defaults(func=lambda a: cmd_chores(_fix_add(a)))

    sp = sub.add_parser("approvals", help="kids' requests waiting for a parent: list, approve, deny")
    sp.add_argument("action", nargs="?", choices=["list", "approve", "deny"], default="list")
    sp.add_argument("id", nargs="?")
    as_arg(sp)
    sp.add_argument("--backend", choices=["simulated", "ha", "matter", "mqtt", "homeconnect"])
    sp.set_defaults(func=cmd_approvals)

    sp = sub.add_parser("events", help="device events -> chores: poll, watch, push (HA ws, Home Connect SSE, MQTT), emit, list")
    sp.add_argument("action", choices=["poll", "watch", "push", "emit", "list"])
    sp.add_argument("device", nargs="?")
    sp.add_argument("event", nargs="?")
    sp.add_argument("-i", "--interval", type=float, default=30,
                    help="watch: poll interval; push: poll interval for devices with no push source")
    sp.add_argument("--safety", type=float, default=900,
                    help="push: safety poll for push-covered devices (seconds)")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--backend", choices=["simulated", "ha", "matter", "mqtt", "homeconnect"])
    as_arg(sp)
    sp.set_defaults(func=cmd_events)

    sp = sub.add_parser("devices", help="every device: list, types, status, do, sim")
    sp.add_argument("action", choices=["list", "types", "status", "do", "sim", "deferred"])
    sp.add_argument("nick", nargs="?")
    sp.add_argument("params", nargs="*", help="do: <action> key=value ...; sim: key=value ...")
    sp.add_argument("--backend", choices=["simulated", "ha", "matter", "mqtt", "homeconnect"])
    sp.add_argument("--yes", action="store_true", help="pre-confirm normal (y/N) confirmations")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--no-watch", action="store_true", help="don't spawn background watchers")
    as_arg(sp)
    sp.set_defaults(func=cmd_devices)

    sp = sub.add_parser("ask", help='ask the house anything: "set the AC to 72", "who has the '
                        'dishes this week?"')
    sp.add_argument("request", nargs="+")
    sp.add_argument("--url", default=os.environ.get("CHOREPILOT_LLM_URL") or
                    os.environ.get("DISHSOAKER_LLM_URL"),
                    help="a running model server (default: start the best local model)")
    sp.add_argument("--llm", help="model name to send (multi-model servers)")
    sp.add_argument("--model", help="GGUF to start when there is no --url")
    sp.add_argument("--port", type=int, default=8080)
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--yes", action="store_true", help="pre-confirm normal (y/N) confirmations; "
                    "never strong confirmations, approvals or PINs")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--no-watch", action="store_true")
    sp.add_argument("--backend", choices=["simulated", "ha", "matter", "mqtt", "homeconnect"])
    dev_args(sp)
    model_args(sp)
    as_arg(sp)
    sp.set_defaults(func=cmd_ask)

    sp = sub.add_parser("confirm", help="answer a confirmation an outside agent is waiting for "
                        "(interactive terminal only; or --deny)")
    sp.add_argument("id", nargs="?", help="(nothing) = list what is waiting")
    sp.add_argument("--deny", action="store_true", help="say no (works without a terminal)")
    sp.set_defaults(func=cmd_confirm)

    from .mcp import add_parser as add_mcp
    from .onboard import add_parser as add_setup
    from .runner import add_parser as add_run
    from .telegram import add_parser as add_telegram
    from .web import add_parser as add_web
    add_mcp(sub, as_arg)
    add_telegram(sub, as_arg)
    add_setup(sub)
    add_run(sub)
    add_web(sub)

    sp = sub.add_parser("demo", help="seed the demo household (people + chores)")
    sp.add_argument("action", nargs="?", default="seed", choices=["seed"])
    sp.add_argument("--pin", help="also set Alex's PIN (for the lock demo)")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_demo)


def _fix_add(a):
    if a.action == "add" and not a.title:
        a.title = a.chore
    return a
