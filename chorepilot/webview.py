"""What the dashboard shows, built from the House and the chore database.

Everything here is derived from the device-type registry (devtypes/*: rooms, tile actions,
status lines, controls) and from the household database. There is no per-device or per-house
code: a user plugin in ~/.config/chorepilot/devtypes/ gets a tile and a detail sheet for free.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta

from .appliance import BackendError
from .chores import MARKED, UNDO_WINDOW_S, when
from .devtypes import get_type
from .devtypes.program import TOOL_TIERS

ROOM_WORDS = ("living_room", "dining_room", "family_room", "kids_room", "guest_room", "bedroom",
              "master_bedroom", "kitchen", "bathroom", "office", "study", "hallway", "garage",
              "garden", "yard", "backyard", "basement", "attic", "laundry", "nursery", "patio",
              "porch", "den", "playroom", "utility")
ROOM_ORDER = ("Living room", "Kitchen", "Laundry", "Bedroom", "Climate", "Entry", "Garage",
              "Outside")
OFFLINE = ("offline", "unavailable")
PALETTE = 6                       # avatar colours (CSS classes c0..c5)


def pretty(s):
    s = str(s or "").replace("_", " ").strip()
    return s[:1].upper() + s[1:]


def room_of(nick, dtype, dcfg):
    """house.json "room", else a room word the nickname starts with, else the type's default."""
    if dcfg.get("room"):
        return pretty(dcfg["room"])
    n = nick.lower()
    for w in sorted(ROOM_WORDS, key=len, reverse=True):
        if n == w or n.startswith(w + "_"):
            return pretty(w)
    return dtype.room or "Other"


def short_name(nick, room, dtype, dcfg):
    if dcfg.get("name"):
        return str(dcfg["name"])
    n = nick.lower()
    r = room.lower().replace(" ", "_")
    if n == r:
        return pretty(dtype.title)
    if n.startswith(r + "_"):
        return pretty(n[len(r) + 1:])
    return pretty(n)


def _clean(st):
    return {k: v for k, v in st.items() if k not in ("detail", "options") and v not in (None, "", [])}


def program_controls(m):
    params = [{"name": "program", "type": "enum", "desc": "program", "values": list(m.programs),
               "labels": {k: p.label for k, p in m.programs.items()},
               "minutes": {k: p.minutes for k, p in m.programs.items()},
               "default": m.default_program, "required": True}]
    for o in m.options.values():
        t = {"bool": "bool", "enum": "enum"}.get(o.kind, "int")
        params.append({"name": o.name, "type": t, "desc": o.desc, "values": list(o.values),
                       "lo": o.lo if t == "int" else None, "hi": o.hi if t == "int" else None,
                       "default": None, "required": False, "programs": list(o.programs),
                       "advanced": True})
    return [
        {"name": "start_program", "label": "Start", "tier": TOOL_TIERS["start_program"],
         "uses": m.uses, "confirm": "normal", "params": params},
        {"name": "pause", "label": "Pause", "tier": 1, "uses": None, "confirm": "normal",
         "params": []},
        {"name": "resume", "label": "Resume", "tier": 2, "uses": m.uses, "confirm": "normal",
         "params": []},
        {"name": "cancel", "label": "Cancel the program", "tier": 1, "uses": None,
         "confirm": "normal", "params": []},
    ]


def _view(nick, dtype, st, dcfg, controls, progress=None, extra=None):
    room = room_of(nick, dtype, dcfg)
    state = str(st.get("state") or "unknown")
    offline = state in OFFLINE
    v = {"id": nick, "name": nick, "display": short_name(nick, room, dtype, dcfg),
         "full": pretty(dcfg.get("name") or nick), "room": room, "type": dtype.name,
         "title": dtype.title, "icon": dtype.icon or dtype.name,
         "state": state, "status": _clean(st), "text": dtype.ui_state(st),
         "active": bool(dtype.ui_active(st)) and not offline,
         "attention": bool(dtype.ui_attention(st)) and not offline, "offline": offline,
         "primary": None if offline else dtype.ui_primary(st, dcfg),
         "controls": controls, "progress": progress}
    if extra:
        v.update(extra)
    return v


def device_views(house, cfg, run_totals=None):
    """Every device as the dashboard draws it. run_totals: {nick: seconds} remembered between
    calls, so a progress ring survives a backend that only reports time left."""
    run_totals = run_totals if run_totals is not None else {}
    devcfg = cfg.get("devices") or {}
    out = []
    if house.bridge:
        for nick, be in house.bridge.fleet:
            st = house.bridge._status_or_error(be)
            m = be.machine
            dtype = get_type(m.kind)
            prog = None
            rem = st.get("remaining_s")
            if st.get("state") in ("running", "paused", "delayed") and rem is not None:
                est = 0
                if st.get("program") in m.programs:
                    try:
                        est = m.run_minutes(st["program"], st.get("options")) * 60
                    except (KeyError, TypeError, ValueError):
                        est = m.programs[st["program"]].minutes * 60
                key = f"{nick}:{st.get('program')}"
                total = max(est, rem, run_totals.get(key, 0))
                run_totals[key] = total
                prog = {"remaining_s": rem, "total_s": total,
                        "fraction": round(1 - rem / total, 3) if total else 0}
            else:
                for k in [k for k in run_totals if k.startswith(nick + ":")]:
                    run_totals.pop(k, None)
            out.append(_view(nick, dtype, st, devcfg.get(nick) or {}, program_controls(m), prog,
                             {"program_appliance": True, "model": m.describe()}))
    for nick, d in house.devices.items():
        st = house._dev_status(d)
        prog = None
        if st.get("remaining_s") is not None and st.get("duration_s"):
            prog = {"remaining_s": st["remaining_s"], "total_s": st["duration_s"],
                    "fraction": round(1 - st["remaining_s"] / max(1, st["duration_s"]), 3)}
        out.append(_view(nick, d.dtype, st, d.cfg, d.dtype.ui_controls(d.cfg), prog))
    order = {r: i for i, r in enumerate(ROOM_ORDER)}
    out.sort(key=lambda v: (order.get(v["room"], 50), v["room"], v["display"]))
    return out


# ------------------------------------------------------------------ "right now"
def right_now(devices, approvals=0, confirmations=0):
    """Short facts for the summary line, most important first."""
    facts = []
    for d in devices:
        if d.get("program_appliance") and d["state"] in ("running", "paused", "delayed"):
            facts.append({"kind": "running", "device": d["id"], "text": f"{d['full']}: {d['text']}",
                          "rank": 2})
        elif d["type"] in ("valve", "vacuum", "oven", "coffee_maker", "ev_charger") and d["active"]:
            facts.append({"kind": "running", "device": d["id"], "text": f"{d['full']}: {d['text']}",
                          "rank": 2})
    for d in devices:
        if d["attention"]:
            facts.append({"kind": "attention", "device": d["id"],
                          "text": f"{d['full']}: {d['text'].lower()}", "rank": 1})
        elif d["offline"]:
            facts.append({"kind": "offline", "device": d["id"],
                          "text": f"{d['full']} is not responding", "rank": 4})
    climate = [d for d in devices if d["type"] == "climate" and not d["offline"]]
    for d in climate[:1]:
        st = d["status"]
        if st.get("current") is not None:
            facts.append({"kind": "climate", "device": d["id"],
                          "text": f"{st['current']:g}° inside" + (
                              f", {st['hvac_action']}" if st.get("hvac_action") not in (None, "idle")
                              else ""), "rank": 3})
    n = approvals + confirmations
    if n:
        facts.insert(0, {"kind": "approvals", "text": f"{n} waiting for a decision", "rank": 0})
    facts.sort(key=lambda f: f["rank"])
    return facts


# ------------------------------------------------------------------ chores
def _week_start(now):
    d = datetime.fromtimestamp(now)
    return (d - timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def _day0(now):
    return datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)


def initials(name):
    parts = [p for p in re.split(r"[\s\-_]+", str(name)) if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][0].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def streak(db, pid, now):
    """Days in a row (ending today, or yesterday if nothing yet today) with a chore done."""
    since = now - 90 * 86400
    rows = db.db.execute("SELECT completed_at FROM instances WHERE completed_by=? AND state IN "
                         "('done','approved') AND completed_at>=?", (pid, since)).fetchall()
    days = {datetime.fromtimestamp(r[0]).date() for r in rows if r[0]}
    d = _day0(now).date()
    if d not in days:
        d -= timedelta(days=1)
    n = 0
    while d in days:
        n += 1
        d -= timedelta(days=1)
    return n


def board_full(db, me, now=None):
    now = now or time.time()
    db.materialise()
    chores = {c["id"]: c for c in db.chores()}
    people = db.people()
    pid = {p["name"]: p["id"] for p in people}
    week0 = _week_start(now).timestamp()
    today0 = _day0(now).timestamp()
    tomorrow0 = today0 + 86400
    open_ = db.instances(("open", "pending_approval"))
    week = db.instances(None, since=week0)
    pts7 = db.points(7)
    me_p = next((p for p in people if p["name"] == me), None)
    adult = bool(me_p and me_p["role"] in ("owner", "adult"))
    done_rows = [i for i in db.instances(("done", "approved"), since=now - 7 * 86400)
                 if i.get("completed_at")]
    marks = db.done_marks([i["id"] for i in done_rows + open_ if i["state"] in MARKED])

    def undo(i):
        """Who may undo this "done" and until when (None = no limit, for an adult)."""
        if i["state"] not in MARKED or not me_p:
            return False, None
        if adult:
            return True, None
        m = marks.get(i["id"])
        if not m or (m["by"] or "").lower() != me.lower():
            return False, None
        until = m["at"] + UNDO_WINDOW_S
        return until > now, until

    def verify_text(c):
        v = (c or {}).get("verify")
        if not v:
            return None
        dev, _, ev = v.partition(".")
        ev = {"finished": "finishes", "started": "starts", "door_opened": "door opens"}.get(
            ev, ev.replace("_", " "))
        return f"Checks itself off when the {pretty(dev).lower()} {ev}"

    def inst(i):
        c = chores.get(i["chore_id"]) or {}
        due = i.get("due")
        if (c.get("schedule") or {}).get("freq") == "none" and i["state"] == "open":
            due = None                     # on-demand: never "overdue", just to do
        anytime = due is None and i["state"] == "open"
        return {"id": i["id"], "chore": i["title"], "chore_id": i["chore_id"],
                "who": i["assignee"], "state": i["state"], "due": when(due), "due_ts": due,
                "points": i["points"], "room": c.get("room"),
                "overdue": bool(due and due < now and i["state"] == "open"),
                "today": bool(due and today0 <= due < tomorrow0) or anytime,
                "done_by": i.get("done_by"), "done": when(i.get("completed_at")),
                "done_ts": i.get("completed_at"),
                "verified": str(i.get("verified_by") or "").startswith("device:"),
                "verified_by": (i.get("verified_by") or "").replace("device:", "") or None,
                "auto": verify_text(c),
                "mine": i["assignee"] in (None, me),
                "marked_by": (marks.get(i["id"]) or {}).get("by"),
                "can_undo": undo(i)[0], "undo_until": undo(i)[1]}

    out_people = []
    for idx, p in enumerate(people):
        if p["role"] == "guest":
            continue
        mine_week = [i for i in week if i["assignee"] == p["name"] and i["state"] != "missed"]
        done_week = [i for i in week if i.get("done_by") == p["name"]
                     and i["state"] in ("done", "approved") and (i.get("completed_at") or 0) >= week0]
        total = len({i["id"] for i in mine_week} | {i["id"] for i in done_week})
        due_today = [i for i in open_ if i["assignee"] == p["name"] and i["state"] == "open"
                     and i["due"] and i["due"] < tomorrow0
                     and (chores.get(i["chore_id"]) or {}).get("schedule", {}).get("freq") != "none"]
        out_people.append({
            "name": p["name"], "role": p["role"], "initials": initials(p["name"]),
            "color": p["id"] % PALETTE, "points": pts7.get(p["name"], 0),
            "done_week": len(done_week), "total_week": total,
            "streak": streak(db, p["id"], now), "due_today": len(due_today),
            "overdue": sum(1 for i in due_today if i["due"] < now),
            "me": p["name"] == me})
    rotations = []
    for c in chores.values():
        try:
            w = db.who_has(c["id"], at=now)
        except Exception:  # noqa: BLE001 - one odd chore must not break the board
            continue
        rotations.append({"chore": c["title"], "chore_id": c["id"], "rotate": c["rotate"],
                          "this_week": w.get("this_week"), "next_week": w.get("next_week"),
                          "next_up": w.get("next_up"), "last_done": w.get("last_done"),
                          "now": ((w.get("open") or [{}])[0].get("assignee")
                                  if c["rotate"] != "week" else None),
                          "schedule": w.get("schedule"), "auto": verify_text(c),
                          "people": [db.person(x)["name"] for x in c["rotation"]
                                     if db.person(x, required=False)]})
    done = list(done_rows)
    done.sort(key=lambda i: -i["completed_at"])
    open_sorted = sorted(open_, key=lambda i: (i["state"] != "open", i["due"] or 9e12))
    return {"people": out_people, "open": [inst(i) for i in open_sorted],
            "done": [inst(i) for i in done[:30]], "rotations": rotations,
            "week_of": datetime.fromtimestamp(week0).strftime("%b %-d"),
            "members": [{"name": p["name"], "role": p["role"], "initials": initials(p["name"]),
                         "color": p["id"] % PALETTE} for p in people],
            "_pid": pid}


# ------------------------------------------------------------------ activity
SOURCES = {"web": "from the dashboard", "cli": "from the terminal", "telegram": "in Telegram",
           "mcp": "through an agent", "agent": "through an agent", "schedule": "on a schedule",
           "events": "automatically"}


PAST = {"turn_on": "turned on", "turn_off": "turned off", "lock": "locked", "unlock": "unlocked",
        "open": "opened", "close": "closed", "stop": "stopped", "start": "started",
        "water": "watered with", "dock": "sent home", "pause": "paused", "play": "played",
        "next": "skipped a track on", "previous": "went back a track on", "mute": "muted",
        "unmute": "unmuted", "brew": "made a drink with", "cancel": "cancelled", "preheat": "preheated",
        "arm_away": "armed", "arm_home": "armed (home)", "arm_night": "armed (night)",
        "disarm": "disarmed", "start_charging": "started charging at", "stop_charging":
        "stopped charging at", "set_temperature": "changed the temperature of",
        "set_mode": "changed the mode of", "set_volume": "changed the volume of",
        "set_position": "moved", "set_speed": "changed the speed of", "set_humidity":
        "changed the humidity of", "set_current": "changed the current of",
        "select_source": "switched the input of", "super_mode": "used super mode on",
        "set_away": "changed away mode on"}


def _arg_text(k, v):
    if k in ("brightness", "position", "volume", "speed", "humidity"):
        return f"{v}%"
    if k == "temperature":
        return f"{v}°"
    if k in ("minutes", "duration_minutes"):
        return f"{v} min"
    if k == "size_ml":
        return f"{v} ml"
    return f"{k.replace('_', ' ')} {str(v).replace('_', ' ')}"


def _sentence(r):
    a = r.get("args") or {}
    tool, who = r.get("tool") or "", r.get("who") or "Someone"
    dev = pretty(a.get("device") or a.get("appliance") or "").lower()
    act = str(a.get("action") or "").replace("_", " ")
    if tool in ("approve", "deny"):
        s = re.sub(r"(?<=[a-z])_(?=[a-z])", " ", str(a.get("summary", "")))
        return f"{who} {'approved' if tool == 'approve' else 'declined'}: {s}"
    if tool == "complete_chore":
        return f"{who} marked {a.get('chore', 'a chore')} done"
    if tool == "undo_chore":
        if not r.get("ok"):
            return f"{who} tried to undo a chore"
        c = str(a.get("chore") or "a chore")
        bits = [f"done by {a['person']}"] if a.get("person") else []
        if a.get("points"):
            bits.append(f"{a['points']} point{'' if a['points'] == 1 else 's'} taken back")
        return f"{who} undid {c[:1].lower() + c[1:]}" + (f" ({', '.join(bits)})" if bits else "")
    if tool == "assign_chore":
        return f"{who} gave {a.get('chore')} to {a.get('person')}"
    if tool == "get_status":
        return f"{who} checked {dev or 'the house'}"
    if tool in ("start_program", "start_when_done"):
        prog = pretty(a.get("program") or "").lower()
        return f"{who} started {prog + ' on ' if prog else ''}the {dev or 'appliance'}"
    if tool in ("pause", "resume", "cancel"):
        verb = {"pause": "paused", "resume": "resumed", "cancel": "cancelled"}[tool]
        return f"{who} {verb} the {dev or 'appliance'}"
    if tool == "set_climate" and a.get("temperature") is not None:
        return f"{who} set the {dev or 'thermostat'} to {a['temperature']}°"
    if tool == "water_zone" and act in ("water", ""):
        return f"{who} watered with the {dev or 'sprinkler'}" + (
            f" for {a['minutes']} min" if a.get("minutes") else "")
    if act:
        past = PAST.get(a.get("action"), act)
        rest = ", ".join(_arg_text(k, v) for k, v in a.items()
                         if k not in ("device", "action", "appliance", "unit"))
        return f"{who} {past} the {dev or 'device'}" + (f", {rest}" if rest else "")
    return f"{who} used {tool.replace('_', ' ')}"


def _asked(r):
    a = dict(r.get("args") or {})
    dev = pretty(a.pop("device", None) or a.pop("appliance", None) or "").lower()
    act = str(a.pop("action", "") or r.get("tool") or "").replace("_", " ")
    rest = ", ".join(_arg_text(k, v) for k, v in a.items() if k not in ("unit", "options"))
    return f"{r.get('who') or 'Someone'} asked to {act} the {dev or 'device'}" + (
        f", {rest}" if rest else "")


def activity(audit_rows, db, limit=80, now=None):
    """A readable, day-grouped timeline: device actions (audit log), device events, chores."""
    now = now or time.time()
    items = []
    for r in audit_rows:
        if r.get("tool") == "get_status" or (r.get("tool") == "complete_chore" and r.get("ok")):
            continue                      # finished chores have their own, richer entry
        outcome = "done" if r.get("ok") else (
            "queued" if r.get("outcome") == "approval" else
            "declined" if "confirm" in str(r.get("error") or "") or r.get("outcome") in (
                "confirm", "strong_confirm", "second_factor") and not r.get("ok") else
            "denied" if r.get("outcome") == "deny" else "failed")
        a = r.get("args") or {}
        text = _sentence(r)
        if outcome in ("queued", "declined"):
            text = _asked(r)
        elif outcome in ("failed", "denied") and r.get("tool") not in ("approve", "deny",
                                                                       "undo_chore"):
            text = _asked(r).replace(" asked to ", " tried to ", 1)
        items.append({"t": r.get("ts") or 0, "kind": "action", "text": text, "tool": r.get("tool"),
                      "device": a.get("device") or a.get("appliance"),
                      "via": SOURCES.get(r.get("source"), r.get("source") or ""),
                      "client": r.get("client"), "outcome": outcome,
                      "detail": None if r.get("ok") or outcome in ("queued", "declined") else r.get("error")})
    for e in db.events(limit):
        if e["event"] in ("second_factor_failed",):
            items.append({"t": e["at"], "kind": "security", "outcome": "failed",
                          "text": f"Wrong PIN or code entered for {e['device']}", "via": ""})
            continue
        if e["event"] not in ("started", "finished", "stopped_early", "door_opened",
                              "door_alarm", "locked", "unlocked", "jammed"):
            continue
        ev = {"started": "started", "finished": "finished", "stopped_early": "stopped early",
              "door_opened": "door was opened", "door_alarm": "door was left open",
              "locked": "locked", "unlocked": "unlocked", "jammed": "jammed"}[e["event"]]
        by = f" by {e['actor']}" if e.get("actor") and e["event"] == "started" else ""
        items.append({"t": e["at"], "kind": "device", "device": e["device"],
                      "echo": e["event"] in ("started", "locked", "unlocked"),
                      "text": f"{pretty(e['device'])} {ev}{by}", "via": "",
                      "outcome": "attention" if e["event"] in ("door_alarm", "jammed") else "info"})
    for i in db.instances(("done", "approved", "pending_approval"), since=now - 14 * 86400):
        if not i.get("completed_at"):
            continue
        dev = str(i.get("verified_by") or "")
        items.append({"t": i["completed_at"], "kind": "chore",
                      "text": f"{i.get('done_by') or 'Someone'} finished "
                              f"{i['title'][:1].lower() + i['title'][1:]}"
                              + (f", +{i['points']}" if i.get("points") else ""),
                      "via": f"verified by the {pretty(dev[7:].split('.')[0]).lower()}"
                             if dev.startswith("device:") else "",
                      "outcome": "waiting" if i["state"] == "pending_approval" else "done",
                      "verified": dev.startswith("device:")})
    acted = [(it["t"], it.get("device")) for it in items if it["kind"] == "action"]
    items = [it for it in items if not (it["kind"] == "device" and it.get("echo") and any(
        abs(t - it["t"]) < 15 and d == it["device"] for t, d in acted))]
    items.sort(key=lambda x: -x["t"])
    items = items[:limit]
    today = _day0(now)
    for it in items:
        d = datetime.fromtimestamp(it["t"])
        delta = (today.date() - d.date()).days
        it["day"] = "Today" if delta == 0 else "Yesterday" if delta == 1 else d.strftime("%A, %b %-d")
        it["time"] = d.strftime("%H:%M")
    return items


def audit_rows(path, limit=300):
    try:
        with open(path) as f:
            lines = f.readlines()[-limit:]
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        r["ts"] = r.get("t", 0)
        out.append(r)
    return out


def approval_view(a, db=None):
    who = a.get("person")
    why = {"chore": "Kids' check-offs on this chore are checked by a parent"}.get(a["kind"])
    summary = a["summary"]
    if a["kind"] == "device":
        tool = (a.get("payload") or {}).get("tool")
        args = dict((a.get("payload") or {}).get("args") or {})
        dev = args.pop("device", None) or args.pop("appliance", None)
        act = str(args.pop("action", "") or tool or "").replace("_", " ")
        if tool in ("start_program", "resume") and args.get("program"):
            act = f"start {pretty(args.pop('program')).lower()} on"
        opts = args.get("options") if isinstance(args.get("options"), dict) else {}
        rest = ", ".join([_arg_text(k, v) for k, v in args.items() if k not in ("unit", "options")]
                         + [_arg_text(k, v) for k, v in opts.items()])
        if dev:
            summary = f"{who or 'Someone'} wants to {act} the {pretty(dev).lower()}" + (
                f", {rest}" if rest else "")
        why = ("Uses water or heat, so a parent decides" if tool not in ("set_light", "vacuum")
               else "Needs a parent's OK in this household")
    return {"id": a["id"], "kind": a["kind"], "summary": summary, "who": who,
            "initials": initials(who or "?"), "why": why, "created": when(a["created"]),
            "created_ts": a["created"]}


def safe(fn, default):
    try:
        return fn()
    except (BackendError, OSError, ValueError, KeyError):
        return default
