"""The chore layer: people, chores, schedules, rotations, instances, device-event verification,
follow-ups, points, approvals, reminders. One SQLite file (stdlib sqlite3), mode 0600, in
~/.config/chorepilot/chorepilot.db. Nothing leaves the box.

The differentiator is `apply_event`: a device event ("dishwasher finished") completes the chore
it verifies ("Dishes"), credits whoever started the machine, and opens the follow-up chore
("Empty the dishwasher") for whoever is next in *that* chore's rotation.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import random
import re
import sqlite3
import struct
import time
from datetime import datetime, timedelta

from . import schedule as sch

ROLES = ("owner", "adult", "kid", "guest", "staff")
STRATEGIES = ("round_robin", "least_points", "fixed", "random", "claimable")
DONE = ("done", "approved")
MARKED = ("done", "approved", "pending_approval")
UNDO_WINDOW_S = 120          # whoever marked a chore done can undo it this long; adults any time

SCHEMA = """
CREATE TABLE IF NOT EXISTS people(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, role TEXT NOT NULL,
  handles TEXT NOT NULL DEFAULT '{}', quiet_hours TEXT, pin_hash TEXT, totp_secret TEXT,
  created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS chores(
  id INTEGER PRIMARY KEY, title TEXT NOT NULL UNIQUE COLLATE NOCASE, room TEXT,
  points INTEGER NOT NULL DEFAULT 1, schedule TEXT NOT NULL DEFAULT '{"freq": "none"}',
  strategy TEXT NOT NULL DEFAULT 'round_robin', rotate TEXT NOT NULL DEFAULT 'instance',
  rotation TEXT NOT NULL DEFAULT '[]', rot_idx INTEGER NOT NULL DEFAULT 0, verify TEXT,
  followups TEXT NOT NULL DEFAULT '[]', needs_approval INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL, last_done REAL);
CREATE TABLE IF NOT EXISTS instances(
  id INTEGER PRIMARY KEY, chore_id INTEGER NOT NULL REFERENCES chores(id),
  assignee_id INTEGER REFERENCES people(id), due REAL, state TEXT NOT NULL DEFAULT 'open',
  created REAL NOT NULL, completed_at REAL, completed_by INTEGER REFERENCES people(id),
  verified_by TEXT, note TEXT, reminded_at REAL);
CREATE TABLE IF NOT EXISTS week_overrides(
  chore_id INTEGER NOT NULL, week TEXT NOT NULL, person_id INTEGER NOT NULL,
  PRIMARY KEY (chore_id, week));
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, at REAL NOT NULL, device TEXT NOT NULL, dtype TEXT, event TEXT NOT NULL,
  actor_id INTEGER, data TEXT);
CREATE TABLE IF NOT EXISTS approvals(
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, person_id INTEGER, payload TEXT NOT NULL,
  summary TEXT, created REAL NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
  decided_by INTEGER, decided_at REAL, result TEXT);
CREATE TABLE IF NOT EXISTS confirmations(
  id INTEGER PRIMARY KEY, created REAL NOT NULL, expires REAL NOT NULL, person_id INTEGER,
  source TEXT, client TEXT, question TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'yes',
  phrase TEXT, state TEXT NOT NULL DEFAULT 'pending', answered_via TEXT, answered_at REAL,
  pushed TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS outbox(
  id INTEGER PRIMARY KEY, at REAL NOT NULL, audience TEXT NOT NULL, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS link_codes(
  code_hash TEXT PRIMARY KEY, person_id INTEGER NOT NULL, channel TEXT NOT NULL,
  expires REAL NOT NULL);
CREATE INDEX IF NOT EXISTS inst_chore ON instances(chore_id, state);
CREATE INDEX IF NOT EXISTS ev_dev ON events(device, at);
"""


def db_path():
    from .env import config_dir
    return os.path.join(config_dir(), "chorepilot.db")


def _stem(w):
    w = w.lower()
    for suf in ("ing", "es", "s"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


_STOP = {"the", "a", "an", "my", "our", "to", "of", "and", "this", "week", "who", "has", "is",
         "on", "for", "do", "does", "doing", "turn", "whose", "today", "chore"}


def _tokens(s):
    return [_stem(t) for t in re.findall(r"[a-z0-9]+", str(s).lower()) if t not in _STOP]


def when(ts):
    """'today 18:00', 'tomorrow 20:00', 'Mon 08:00', '2026-11-02' -- easy for small models."""
    if ts is None:
        return None
    d = datetime.fromtimestamp(ts)
    today = datetime.now().date()
    delta = (d.date() - today).days
    hm = d.strftime("%H:%M")
    if delta == 0:
        return f"today {hm}"
    if delta == 1:
        return f"tomorrow {hm}"
    if delta == -1:
        return f"yesterday {hm}"
    if -6 <= delta <= 6:
        return d.strftime("%a ") + hm
    return d.strftime("%Y-%m-%d")


class ChoreError(ValueError):
    pass


class ChoreDB:
    def __init__(self, path=None, clock=time.time):
        self.path = path or db_path()
        self.clock = clock
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(self.path) or ".", mode=0o700, exist_ok=True)
            fresh = not os.path.exists(self.path)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        if self.path != ":memory:" and fresh:
            os.chmod(self.path, 0o600)

    def close(self):
        self.db.close()

    def _x(self, sql, args=()):
        cur = self.db.execute(sql, args)
        self.db.commit()
        return cur

    def now(self):
        return self.clock()

    # ================================================================ people
    def add_person(self, name, role="adult", handles=None, quiet_hours=None):
        if role not in ROLES:
            raise ChoreError(f"role must be one of {', '.join(ROLES)}")
        try:
            self._x("INSERT INTO people(name, role, handles, quiet_hours, created) VALUES (?,?,?,?,?)",
                    (name.strip(), role, json.dumps(handles or {}), quiet_hours, self.now()))
        except sqlite3.IntegrityError:
            raise ChoreError(f"{name} is already in the household") from None
        return self.person(name)

    def people(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM people ORDER BY id")]

    def person(self, ref, required=True):
        """By name (case-insensitive, unique prefix ok), id, or chat handle value."""
        if ref is None:
            if required:
                raise ChoreError("who?")
            return None
        if isinstance(ref, dict):
            return ref
        if isinstance(ref, int) or str(ref).isdigit():
            r = self.db.execute("SELECT * FROM people WHERE id=?", (int(ref),)).fetchone()
            if r:
                return dict(r)
        s = str(ref).strip()
        r = self.db.execute("SELECT * FROM people WHERE name=?", (s,)).fetchone()
        if r:
            return dict(r)
        hits = [p for p in self.people() if p["name"].lower().startswith(s.lower())
                or s in json.loads(p["handles"] or "{}").values()]
        if len(hits) == 1:
            return hits[0]
        if required:
            names = ", ".join(p["name"] for p in self.people()) or "nobody yet"
            raise ChoreError(f"no one called {ref!r} in the household ({names})")
        return None

    def person_by_handle(self, kind, value):
        """Exact chat-handle lookup (e.g. telegram_id): no prefixes, no names. Identity for chat
        front-ends comes only from here."""
        if value in (None, ""):
            return None
        for p in self.people():
            if str(json.loads(p["handles"] or "{}").get(kind, "")) == str(value):
                return p
        return None

    def set_handle(self, name, kind, value):
        p = self.person(name)
        h = json.loads(p["handles"] or "{}")
        if value is None:
            h.pop(kind, None)
        else:
            other = self.person_by_handle(kind, value)
            if other and other["id"] != p["id"]:
                raise ChoreError(f"that {kind} is already linked to {other['name']}")
            h[kind] = str(value)
        self._x("UPDATE people SET handles=? WHERE id=?", (json.dumps(h), p["id"]))
        return self.person(p["id"])

    # one-time link codes (chat account -> household person) ------------------------------
    def new_link_code(self, name, channel="telegram", ttl=600):
        p = self.person(name)
        code = "".join(random.SystemRandom().choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
                       for _ in range(8))
        self._x("DELETE FROM link_codes WHERE expires<? OR (person_id=? AND channel=?)",
                (self.now(), p["id"], channel))
        self._x("INSERT INTO link_codes(code_hash, person_id, channel, expires) VALUES (?,?,?,?)",
                (hashlib.sha256(code.encode()).hexdigest(), p["id"], channel, self.now() + ttl))
        return code

    def use_link_code(self, code, channel="telegram"):
        """The person the code belongs to (and burn it), or None. Codes are single-use."""
        h = hashlib.sha256(str(code).strip().upper().encode()).hexdigest()
        r = self.db.execute("SELECT person_id, expires FROM link_codes WHERE code_hash=? AND "
                            "channel=?", (h, channel)).fetchone()
        if not r:
            return None
        self._x("DELETE FROM link_codes WHERE code_hash=?", (h,))
        return self.person(r[0]) if r[1] >= self.now() else None

    # confirmations a human answers on their own channel (MCP tier 2) -------------------------
    def add_confirmation(self, person, question, kind="yes", phrase=None, source=None,
                         client=None, ttl=120):
        p = self.person(person, required=False)
        cur = self._x("INSERT INTO confirmations(created, expires, person_id, source, client, "
                      "question, kind, phrase) VALUES (?,?,?,?,?,?,?,?)",
                      (self.now(), self.now() + ttl, p["id"] if p else None, source, client,
                       question, kind, phrase))
        return self.confirmation(cur.lastrowid)

    def confirmation(self, cid):
        r = self.db.execute("SELECT c.*, p.name AS person FROM confirmations c LEFT JOIN people "
                            "p ON p.id=c.person_id WHERE c.id=?", (int(cid),)).fetchone()
        if not r:
            return None
        c = dict(r)
        if c["state"] == "pending" and c["expires"] < self.now():
            self._x("UPDATE confirmations SET state='expired' WHERE id=? AND state='pending'",
                    (c["id"],))
            c["state"] = "expired"
        return c

    def pending_confirmations(self):
        ids = [r[0] for r in self.db.execute("SELECT id FROM confirmations WHERE state='pending' "
                                             "ORDER BY id")]
        return [c for c in (self.confirmation(i) for i in ids) if c["state"] == "pending"]

    def answer_confirmation(self, cid, yes, via, answer=None):
        """Record the human's answer. A phrase confirmation is only a yes when `answer` is the
        exact phrase. Returns the row (state yes/no), or None if it was not pending."""
        c = self.confirmation(cid)
        if not c or c["state"] != "pending":
            return None
        if yes and c["kind"] == "phrase" and (answer or "").strip() != c["phrase"]:
            yes = False
        cur = self._x("UPDATE confirmations SET state=?, answered_via=?, answered_at=? WHERE id=? "
                      "AND state='pending'", ("yes" if yes else "no", via, self.now(), c["id"]))
        return self.confirmation(cid) if cur.rowcount else None

    def mark_pushed(self, cid, channel):
        c = self.confirmation(cid)
        if c and channel not in c["pushed"].split(","):
            self._x("UPDATE confirmations SET pushed=? WHERE id=?",
                    (",".join(x for x in (c["pushed"], channel) if x), cid))

    # outbox: messages for chat front-ends (Telegram) -------------------------------------------
    def outbox_add(self, text, audience="household"):
        self._x("INSERT INTO outbox(at, audience, text) VALUES (?,?,?)", (self.now(), audience, text))
        self._x("DELETE FROM outbox WHERE at<?", (self.now() - 7 * 86400,))

    def outbox_since(self, last_id):
        return [dict(r) for r in self.db.execute("SELECT * FROM outbox WHERE id>? ORDER BY id",
                                                 (int(last_id),))]

    def outbox_last_id(self):
        return self.db.execute("SELECT COALESCE(MAX(id), 0) FROM outbox").fetchone()[0]

    def set_role(self, name, role):
        if role not in ROLES:
            raise ChoreError(f"role must be one of {', '.join(ROLES)}")
        p = self.person(name)
        if p["role"] == "owner" and role != "owner" and len(self.owners()) == 1:
            raise ChoreError(f"{p['name']} is the only owner; make someone else an owner first "
                             "(`chorepilot people transfer-owner`)")
        self._x("UPDATE people SET role=? WHERE id=?", (role, p["id"]))

    def owners(self):
        return [p for p in self.people() if p["role"] == "owner"]

    def transfer_owner(self, frm, to, keep=False):
        """`to` becomes an owner; `frm` stays one too with keep=True (several owners), else
        becomes an adult. Authentication is the caller's job (the CLI asks for frm's code)."""
        a, b = self.person(frm), self.person(to)
        if a["role"] != "owner":
            raise ChoreError(f"{a['name']} is not an owner")
        if a["id"] == b["id"]:
            raise ChoreError("that is the same person")
        self._x("UPDATE people SET role='owner' WHERE id=?", (b["id"],))
        if not keep:
            self._x("UPDATE people SET role='adult' WHERE id=?", (a["id"],))
        return self.person(b["id"])

    # second factor ---------------------------------------------------------------------
    def set_pin(self, name, pin):
        pin = str(pin)
        if not re.fullmatch(r"\d{4,12}", pin):
            raise ChoreError("a PIN is 4-12 digits")
        salt = os.urandom(16)
        h = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, 200_000)
        self._x("UPDATE people SET pin_hash=? WHERE id=?",
                ("pbkdf2$" + salt.hex() + "$" + h.hex(), self.person(name)["id"]))

    def check_pin(self, person, pin):
        p = self.person(person)
        if not p.get("pin_hash") or pin is None:
            return False
        _, salt, h = p["pin_hash"].split("$")
        got = hashlib.pbkdf2_hmac("sha256", str(pin).encode(), bytes.fromhex(salt), 200_000)
        return hmac.compare_digest(got.hex(), h)

    def set_totp(self, name):
        secret = base64.b32encode(os.urandom(20)).decode().rstrip("=")
        p = self.person(name)
        self._x("UPDATE people SET totp_secret=? WHERE id=?", (secret, p["id"]))
        return secret, f"otpauth://totp/ChorePilot:{p['name']}?secret={secret}&issuer=ChorePilot"

    def check_totp(self, person, code, at=None):
        p = self.person(person)
        if not p.get("totp_secret") or not code:
            return False
        return any(hmac.compare_digest(totp(p["totp_secret"], (at or time.time()) + d * 30),
                                       str(code).strip()) for d in (-1, 0, 1))

    def has_second_factor(self, person):
        p = self.person(person)
        return bool(p.get("pin_hash") or p.get("totp_secret"))

    # ================================================================ chores
    def add_chore(self, title, schedule=None, rotation=(), strategy="round_robin", rotate="instance",
                  points=1, verify=None, followups=(), needs_approval=False, room=None):
        if strategy not in STRATEGIES:
            raise ChoreError(f"strategy must be one of {', '.join(STRATEGIES)}")
        if rotate not in ("instance", "week"):
            raise ChoreError("rotate must be 'instance' (next person each time) or 'week'")
        try:
            spec = sch.parse(schedule)
        except ValueError as e:
            raise ChoreError(str(e)) from None
        ids = [self.person(p)["id"] for p in rotation]
        try:
            self._x("INSERT INTO chores(title, room, points, schedule, strategy, rotate, rotation, "
                    "verify, followups, needs_approval, created) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (title.strip(), room, int(points), json.dumps(spec), strategy, rotate,
                     json.dumps(ids), verify, json.dumps(list(followups)), int(bool(needs_approval)),
                     self.now()))
        except sqlite3.IntegrityError:
            raise ChoreError(f"there is already a chore called {title!r}") from None
        return self.chore(title)

    def chores(self, active=True):
        q = "SELECT * FROM chores" + (" WHERE active=1" if active else "") + " ORDER BY id"
        return [self._chore_row(r) for r in self.db.execute(q)]

    def _chore_row(self, r):
        c = dict(r)
        c["schedule"] = json.loads(c["schedule"])
        c["rotation"] = json.loads(c["rotation"])
        c["followups"] = json.loads(c["followups"])
        return c

    def chore(self, ref):
        """Fuzzy: exact title, then the best token match ('dishes' -> 'Dishes', 'empty
        dishwasher' -> 'Empty the dishwasher'). Raises ChoreError when unknown or ambiguous."""
        if isinstance(ref, dict):
            return ref
        if isinstance(ref, int) or str(ref).isdigit():
            r = self.db.execute("SELECT * FROM chores WHERE id=?", (int(ref),)).fetchone()
            if r:
                return self._chore_row(r)
        r = self.db.execute("SELECT * FROM chores WHERE title=?", (str(ref).strip(),)).fetchone()
        if r:
            return self._chore_row(r)
        want = _tokens(ref)
        if not want:
            raise ChoreError(f"which chore? ({self._titles()})")
        scored = []
        for c in self.chores():
            have = _tokens(c["title"]) + _tokens(c.get("room") or "")
            exact = sum(1 for w in want if w in have)
            part = sum(1 for w in want if w not in have and any(
                len(w) >= 4 and (h.startswith(w) or w.startswith(h)) for h in have))
            if exact or part:
                score = exact * 2 + part - 0.1 * max(0, len(have) - len(want))
                scored.append((score, c))
        if not scored:
            raise ChoreError(f"no chore matches {ref!r} ({self._titles()})")
        scored.sort(key=lambda x: -x[0])
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            raise ChoreError(f"{ref!r} could be {scored[0][1]['title']!r} or {scored[1][1]['title']!r}")
        return scored[0][1]

    def _titles(self):
        return ", ".join(c["title"] for c in self.chores()) or "no chores yet"

    def upsert_chore(self, title, **kw):
        """Add a chore, or update the one with this exact title (re-activating it if it was
        removed). Only the fields given change. -> (chore, created?)"""
        r = self.db.execute("SELECT id FROM chores WHERE title=?", (title.strip(),)).fetchone()
        if not r:
            return self.add_chore(title, **kw), True
        sets, vals = [], []
        for k, v in kw.items():
            if k == "schedule":
                try:
                    v = json.dumps(sch.parse(v))
                except ValueError as e:
                    raise ChoreError(str(e)) from None
            elif k == "rotation":
                v = json.dumps([self.person(x)["id"] for x in v])
            elif k == "followups":
                v = json.dumps(list(v))
            elif k == "needs_approval":
                v = int(bool(v))
            elif k == "points":
                v = int(v)
            elif k == "strategy" and v not in STRATEGIES:
                raise ChoreError(f"strategy must be one of {', '.join(STRATEGIES)}")
            elif k == "rotate" and v not in ("instance", "week"):
                raise ChoreError("rotate must be 'instance' or 'week'")
            elif k not in ("verify", "room", "strategy", "rotate"):
                raise ChoreError(f"unknown chore field {k!r}")
            sets.append(f"{k}=?")
            vals.append(v)
        self._x(f"UPDATE chores SET {', '.join(sets + ['active=1'])} WHERE id=?", (*vals, r[0]))
        return self.chore(r[0]), False

    def remove_chore(self, ref):
        self._x("UPDATE chores SET active=0 WHERE id=?", (self.chore(ref)["id"],))

    # ================================================================ rotation
    def _week_key(self, ts):
        return sch.week_start(datetime.fromtimestamp(ts)).strftime("%G-W%V")

    def _points_since(self, pid, since):
        r = self.db.execute(
            "SELECT COALESCE(SUM(c.points),0) FROM instances i JOIN chores c ON c.id=i.chore_id "
            "WHERE i.completed_by=? AND i.state IN ('done','approved') AND i.completed_at>=?",
            (pid, since)).fetchone()
        return r[0]

    def week_assignee(self, chore, at=None):
        """For rotate='week' chores: who has it in the week containing `at`."""
        c = self.chore(chore)
        at = at or self.now()
        o = self.db.execute("SELECT person_id FROM week_overrides WHERE chore_id=? AND week=?",
                            (c["id"], self._week_key(at))).fetchone()
        if o:
            return self.person(o[0])
        if not c["rotation"]:
            return None
        a = sch.week_start(datetime.fromtimestamp(c["created"]))
        w = sch.week_start(datetime.fromtimestamp(at))
        n = (w - a).days // 7
        return self.person(c["rotation"][(n + c["rot_idx"]) % len(c["rotation"])])

    def next_assignee(self, chore, at=None, advance=False):
        """Who gets the next instance, by the chore's strategy (None = claimable by anyone)."""
        c = self.chore(chore)
        rot = c["rotation"]
        if not rot or c["strategy"] == "claimable":
            return None
        if c["rotate"] == "week":
            return self.week_assignee(c, at)
        if c["strategy"] == "fixed":
            return self.person(rot[0])
        if c["strategy"] == "random":
            return self.person(random.choice(rot))
        if c["strategy"] == "least_points":
            since = self.now() - 7 * 86400
            return self.person(min(rot, key=lambda pid: (self._points_since(pid, since), rot.index(pid))))
        p = self.person(rot[c["rot_idx"] % len(rot)])
        if advance:
            self._x("UPDATE chores SET rot_idx=rot_idx+1 WHERE id=?", (c["id"],))
        return p

    # ================================================================ instances
    def open_instance(self, chore, assignee=None, due=None, note=None):
        c = self.chore(chore)
        if assignee is None:
            p = self.next_assignee(c, due or self.now(), advance=True)
        else:
            p = self.person(assignee)
        cur = self._x("INSERT INTO instances(chore_id, assignee_id, due, state, created, note) "
                      "VALUES (?,?,?,?,?,?)", (c["id"], p["id"] if p else None, due or self.now(),
                                               "open", self.now(), note))
        return self.instance(cur.lastrowid)

    def instance(self, iid):
        r = self.db.execute(
            "SELECT i.*, c.title, c.points, c.needs_approval, p.name AS assignee, q.name AS done_by "
            "FROM instances i JOIN chores c ON c.id=i.chore_id "
            "LEFT JOIN people p ON p.id=i.assignee_id LEFT JOIN people q ON q.id=i.completed_by "
            "WHERE i.id=?", (iid,)).fetchone()
        return dict(r) if r else None

    def instances(self, state=None, person=None, chore=None, since=None, limit=200):
        q = ("SELECT i.*, c.title, c.points, p.name AS assignee, q.name AS done_by "
             "FROM instances i JOIN chores c ON c.id=i.chore_id LEFT JOIN people p ON p.id=i.assignee_id "
             "LEFT JOIN people q ON q.id=i.completed_by WHERE c.active=1")
        args = []
        if state:
            states = (state,) if isinstance(state, str) else tuple(state)
            q += f" AND i.state IN ({','.join('?' * len(states))})"
            args += states
        if person is not None:
            q += " AND (i.assignee_id=? OR i.completed_by=?)"
            pid = self.person(person)["id"]
            args += [pid, pid]
        if chore is not None:
            q += " AND i.chore_id=?"
            args.append(self.chore(chore)["id"])
        if since is not None:
            q += " AND COALESCE(i.completed_at, i.due, i.created)>=?"
            args.append(since)
        q += " ORDER BY COALESCE(i.due, i.created), i.id LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.db.execute(q, args)]

    def materialise(self, at=None):
        """Open today's instances of scheduled chores. Older open ones become 'missed' when a
        newer one opens. Returns the instances it opened."""
        at = at or self.now()
        now_dt = datetime.fromtimestamp(at)
        horizon = now_dt.replace(hour=23, minute=59, second=59)
        opened = []
        for c in self.chores():
            s = c["schedule"]
            if s.get("freq") == "none":
                continue
            last = self.db.execute("SELECT MAX(due) FROM instances WHERE chore_id=?",
                                   (c["id"],)).fetchone()[0]
            created = datetime.fromtimestamp(c["created"])
            if s["freq"] == "after_done":
                if self.instances("open", chore=c["id"]):
                    continue
                base = datetime.fromtimestamp(c["last_done"]) if c["last_done"] else None
                due = sch.next_due(s, now_dt, anchor=created, last_done=base) if base else now_dt
                if due <= horizon:
                    opened.append(self.open_instance(c, due=due.timestamp()))
                continue
            after = datetime.fromtimestamp(last) if last else created - timedelta(seconds=1)
            due, pick = sch.next_due(s, after, anchor=created), None
            for _ in range(400):
                if due is None or due > horizon:
                    break
                pick = due
                due = sch.next_due(s, due, anchor=created)
            if pick is None:
                continue
            for old in self.instances("open", chore=c["id"]):
                if old["due"] and old["due"] < pick.timestamp() - 3600:
                    self._x("UPDATE instances SET state='missed' WHERE id=?", (old["id"],))
            opened.append(self.open_instance(c, due=pick.timestamp()))
        return opened

    def assign(self, chore, person, week=False):
        """Give the chore's open instance (or, with week=True / weekly chores, this week) to person."""
        c, p = self.chore(chore), self.person(person)
        if week or c["rotate"] == "week":
            self._x("INSERT OR REPLACE INTO week_overrides(chore_id, week, person_id) VALUES (?,?,?)",
                    (c["id"], self._week_key(self.now()), p["id"]))
        open_ = self.instances("open", chore=c["id"])
        if open_:
            for i in open_:
                self._x("UPDATE instances SET assignee_id=? WHERE id=?", (p["id"], i["id"]))
            return self.instance(open_[0]["id"])
        if c["rotate"] == "week":
            return {"title": c["title"], "assignee": p["name"], "week": self._week_key(self.now())}
        return self.open_instance(c, assignee=p)

    def swap(self, chore, a, b):
        """a and b trade: every open instance of the chore assigned to a goes to b, and vice versa."""
        c, pa, pb = self.chore(chore), self.person(a), self.person(b)
        n = 0
        for i in self.instances("open", chore=c["id"]):
            to = pb if i["assignee_id"] == pa["id"] else pa if i["assignee_id"] == pb["id"] else None
            if to:
                self._x("UPDATE instances SET assignee_id=? WHERE id=?", (to["id"], i["id"]))
                n += 1
        if c["rotate"] == "week":
            cur = self.week_assignee(c)
            if cur and cur["id"] in (pa["id"], pb["id"]):
                other = pb if cur["id"] == pa["id"] else pa
                self.assign(c, other, week=True)
                n += 1
        return n

    def complete(self, chore, by=None, verified_by="self_report", note=None, instance=None,
                 marked_by=None):
        """Mark the chore done. Prefers `by`'s own open instance; opens an ad-hoc one if there
        is none. A kid completing a needs_approval chore by self-report -> pending approval.
        `marked_by` is who pressed "done" (defaults to `by` for a self-report); it is kept,
        with what the completion opened, so `undo_complete` can put everything back."""
        c = self.chore(chore)
        who = self.person(by, required=False) if by is not None else None
        inst = self.instance(instance) if instance else None
        if inst is None:
            open_ = self.instances("open", chore=c["id"])
            mine = [i for i in open_ if who and i["assignee_id"] == who["id"]]
            inst = (mine or open_ or [None])[0]
        created = inst is None
        if inst is None:
            owner = self.week_assignee(c) if c["rotate"] == "week" else who
            inst = self.open_instance(c, assignee=owner or who, note="done without being scheduled")
        pending = (c["needs_approval"] and who and who["role"] == "kid"
                   and not verified_by.startswith("device"))
        state = "pending_approval" if pending else "done"
        credit = who["id"] if who else inst["assignee_id"]
        self._x("UPDATE instances SET state=?, completed_at=?, completed_by=?, verified_by=?, "
                "note=COALESCE(?, note) WHERE id=?",
                (state, self.now(), credit, verified_by, note, inst["id"]))
        self._x("UPDATE chores SET last_done=? WHERE id=?", (self.now(), c["id"]))
        out = {"instance": self.instance(inst["id"]), "followups": [], "approval": None}
        if marked_by is None and not verified_by.startswith("device") and who:
            marked_by = who["name"]
        track = []
        if pending:
            out["approval"] = self.add_approval(
                "chore", who, {"instance": inst["id"]},
                f"{who['name']} says '{c['title']}' is done")
        else:
            out["followups"] = self.open_followups(c, track=track)
        self.record_event(f"chore:{inst['id']}", "chore_done", "chore", actor=marked_by,
                          data={"created": created, "followups": track,
                                "verified_by": verified_by})
        return out

    def open_followups(self, chore, track=None):
        opened = []
        for title in self.chore(chore)["followups"]:
            try:
                f = self.chore(title)
            except ChoreError:
                continue
            if self.instances("open", chore=f["id"]):
                continue                         # don't stack "empty the dishwasher" twice
            inst = self.open_instance(f)
            opened.append(inst)
            if track is not None:
                track.append({"instance": inst["id"], "chore": f["id"], "rot_from": f["rot_idx"],
                              "rot_to": self.chore(f["id"])["rot_idx"]})
        return opened

    # ================================================================ undo a "done"
    def done_marks(self, iids):
        """{instance id: {"by": name or None, "at": ts}} for the latest "done" of each."""
        iids = [int(i) for i in iids]
        if not iids:
            return {}
        devs = [f"chore:{i}" for i in iids]
        rows = self.db.execute(
            "SELECT e.device, e.at, p.name AS by_name FROM events e LEFT JOIN people p ON "
            f"p.id=e.actor_id WHERE e.event='chore_done' AND e.device IN ({','.join('?' * len(devs))}) "
            "ORDER BY e.at, e.id", devs).fetchall()
        return {int(r["device"][6:]): {"by": r["by_name"], "at": r["at"]} for r in rows}

    def _done_events(self, iid, since):
        return [dict(r, data=json.loads(r["data"]) if r["data"] else {}) for r in self.db.execute(
            "SELECT * FROM events WHERE device=? AND event IN ('chore_done','chore_followups') "
            "AND at>=? ORDER BY at, id", (f"chore:{int(iid)}", since))]

    def can_undo(self, inst, person, now=None, window=UNDO_WINDOW_S):
        """(True, None) or (False, why). An owner or adult can undo any completion; anyone else
        only one they marked themselves, within `window` seconds."""
        if not inst or inst["state"] not in MARKED:
            return False, "that chore isn't marked done"
        p = self.person(person, required=False) if not isinstance(person, dict) else person
        if not p:
            return False, "who is asking?"
        if p["role"] in ("owner", "adult"):
            return True, None
        mark = self.done_marks([inst["id"]]).get(inst["id"])
        if not mark or (mark["by"] or "").lower() != p["name"].lower():
            return False, "only an adult can undo someone else's 'done'"
        if (now or self.now()) - mark["at"] > window:
            return False, (f"it's been more than {max(1, round(window / 60))} min; "
                           "ask an adult to undo it")
        return True, None

    def undo_complete(self, iid, by, now=None, window=UNDO_WINDOW_S):
        """Put a completed chore back the way it was: the instance reopens (an ad-hoc one made
        by the completion is removed), so its points and the streak it fed are gone; a pending
        parent approval is cancelled; follow-ups it opened that nobody has touched are removed
        and their rotation steps back. Raises ChoreError when `by` may not undo it."""
        inst = self.instance(iid)
        ok, why = self.can_undo(inst, by, now, window)
        if not ok:
            raise ChoreError(why)
        undoer = self.person(by) if not isinstance(by, dict) else by
        evs = self._done_events(iid, (inst["completed_at"] or 0) - 1)
        done_ev = next((e for e in reversed(evs) if e["event"] == "chore_done"), None)
        removed = []
        for e in evs:
            for f in e["data"].get("followups") or []:
                fi = self.instance(f["instance"])
                if not fi or fi["state"] != "open" or fi["completed_at"]:
                    continue                         # someone already did it: leave it
                self._x("DELETE FROM instances WHERE id=?", (fi["id"],))
                removed.append(fi["title"])
                if f.get("rot_to") != f.get("rot_from"):
                    self._x("UPDATE chores SET rot_idx=? WHERE id=? AND rot_idx=?",
                            (f["rot_from"], f["chore"], f["rot_to"]))
        for a in self.approvals("pending"):
            if a["kind"] == "chore" and a["payload"].get("instance") == inst["id"]:
                self._x("UPDATE approvals SET state='cancelled', decided_by=?, decided_at=? "
                        "WHERE id=?", (undoer["id"], self.now(), a["id"]))
        created = bool(done_ev and done_ev["data"].get("created"))
        if created:
            self._x("DELETE FROM instances WHERE id=?", (inst["id"],))
        else:
            self._x("UPDATE instances SET state='open', completed_at=NULL, completed_by=NULL, "
                    "verified_by=NULL WHERE id=?", (inst["id"],))
        self._x("UPDATE chores SET last_done=(SELECT MAX(completed_at) FROM instances WHERE "
                "chore_id=? AND state IN ('done','approved','pending_approval')) WHERE id=?",
                (inst["chore_id"], inst["chore_id"]))
        self.record_event(f"chore:{inst['id']}", "chore_undone", "chore", actor=undoer["name"],
                          data={"was": inst["state"], "credited": inst["done_by"]})
        return {"chore": inst["title"], "instance": inst["id"], "was": inst["state"],
                "credited": inst["done_by"], "points": inst["points"], "reopened": not created,
                "removed_followups": removed}

    def who_has(self, chore, at=None):
        c = self.chore(chore)
        at = at or self.now()
        out = {"chore": c["title"], "schedule": sch.describe(c["schedule"])}
        if c["rotate"] == "week":
            p = self.week_assignee(c, at)
            out["this_week"] = p["name"] if p else None
            nxt = self.week_assignee(c, at + 7 * 86400)
            out["next_week"] = nxt["name"] if nxt else None
        open_ = self.instances("open", chore=c["id"])
        if open_:
            out["open"] = [{"assignee": i["assignee"] or "anyone", "due": when(i["due"])} for i in open_]
        elif c["rotate"] != "week":
            p = self.next_assignee(c)
            out["next_up"] = p["name"] if p else "anyone"
        done = self.instances(DONE, chore=c["id"], since=at - 7 * 86400)
        if done:
            last = done[-1]
            out["last_done"] = f"{last['done_by'] or '?'} {when(last['completed_at'])}"
        return out

    # ================================================================ device events
    def record_event(self, device, event, dtype=None, actor=None, data=None, at=None):
        a = self.person(actor, required=False) if actor else None
        self._x("INSERT INTO events(at, device, dtype, event, actor_id, data) VALUES (?,?,?,?,?,?)",
                (at or self.now(), device, dtype, event, a["id"] if a else None,
                 json.dumps(data) if data else None))

    def last_starter(self, device, before=None):
        """Who started the device's current/last run (the most recent 'started' with an actor,
        after the previous 'finished')."""
        before = before or self.now() + 1
        fin = self.db.execute("SELECT MAX(at) FROM events WHERE device=? AND event IN "
                              "('finished','stopped_early') AND at<?", (device, before - 0.5)).fetchone()[0] or 0
        r = self.db.execute("SELECT actor_id FROM events WHERE device=? AND event='started' AND "
                            "actor_id IS NOT NULL AND at>? AND at<? ORDER BY at DESC LIMIT 1",
                            (device, fin, before)).fetchone()
        return self.person(r[0]) if r else None

    def apply_event(self, device, event, dtype=None, actor=None, data=None):
        """The rule engine. Records the event, then: every chore whose verify rule is
        '<device>.<event>' or '<type>.<event>' is completed (credited to whoever started the
        device, else the assignee) and its follow-ups open for the next person in rotation.
        Returns human-readable lines for a notification."""
        starter = self.last_starter(device) if event in ("finished", "stopped_early") else None
        self.record_event(device, event, dtype, actor, data)
        keys = {f"{device}.{event}".lower()}
        if dtype:
            keys.add(f"{dtype}.{event}".lower())
        lines = []
        for c in self.chores():
            if not c["verify"] or c["verify"].lower() not in keys:
                continue
            open_ = self.instances("open", chore=c["id"])
            credit = starter or (self.person(open_[0]["assignee_id"], required=False)
                                 if open_ and open_[0]["assignee_id"] else None)
            res = self.complete(c, by=credit["name"] if credit else None,
                                verified_by=f"device:{device}.{event}",
                                instance=open_[0]["id"] if open_ else None)
            inst = res["instance"]
            who = inst["done_by"] or "someone"
            cover = (f" (covering for {inst['assignee']})"
                     if inst["assignee"] and inst["done_by"] and inst["assignee"] != inst["done_by"] else "")
            lines.append(f"'{c['title']}' done by {who}{cover}, verified by the {device}.")
            for f in res["followups"]:
                lines.append(f"'{f['title']}' is now open for {f['assignee'] or 'anyone'}.")
        return lines

    def events(self, limit=20):
        """Device and security events, newest first (the bookkeeping that lets a chore's "done"
        be undone lives in the same table under "chore:<id>" and is left out)."""
        return [dict(r) for r in self.db.execute(
            "SELECT e.*, p.name AS actor FROM events e LEFT JOIN people p ON p.id=e.actor_id "
            "WHERE e.device NOT LIKE 'chore:%' ORDER BY e.at DESC, e.id DESC LIMIT ?", (limit,))]

    # ================================================================ approvals
    def add_approval(self, kind, person, payload, summary):
        p = self.person(person, required=False)
        cur = self._x("INSERT INTO approvals(kind, person_id, payload, summary, created) "
                      "VALUES (?,?,?,?,?)", (kind, p["id"] if p else None, json.dumps(payload),
                                             summary, self.now()))
        return self.approval(cur.lastrowid)

    def approval(self, aid):
        r = self.db.execute("SELECT a.*, p.name AS person FROM approvals a LEFT JOIN people p "
                            "ON p.id=a.person_id WHERE a.id=?", (int(aid),)).fetchone()
        if not r:
            return None
        a = dict(r)
        a["payload"] = json.loads(a["payload"])
        return a

    def approvals(self, state="pending"):
        return [self.approval(r[0]) for r in self.db.execute(
            "SELECT id FROM approvals WHERE state=? ORDER BY id", (state,))]

    def decide(self, aid, approver, approve=True, result=None):
        a = self.approval(aid)
        if not a or a["state"] != "pending":
            raise ChoreError(f"no pending approval #{aid}")
        p = self.person(approver)
        if p["role"] not in ("owner", "adult"):
            raise ChoreError("only an owner or adult can approve")
        self._x("UPDATE approvals SET state=?, decided_by=?, decided_at=?, result=? WHERE id=?",
                ("approved" if approve else "denied", p["id"], self.now(),
                 json.dumps(result) if result is not None else None, a["id"]))
        if a["kind"] == "chore":
            iid = a["payload"]["instance"]
            self._x("UPDATE instances SET state=? WHERE id=?",
                    ("approved" if approve else "open", iid))
            if approve:
                inst = self.instance(iid)
                track = []
                self.open_followups(inst["chore_id"], track=track)
                self.record_event(f"chore:{iid}", "chore_followups", "chore", actor=p["name"],
                                  data={"followups": track})
        return self.approval(aid)

    def set_result(self, aid, result):
        self._x("UPDATE approvals SET result=? WHERE id=?", (json.dumps(result), aid))

    # ================================================================ reminders + reports
    def in_quiet_hours(self, person, at=None):
        q = self.person(person).get("quiet_hours")
        if not q:
            return False
        m = re.fullmatch(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", q.strip())
        if not m:
            return False
        a, b = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
        d = datetime.fromtimestamp(at or self.now())
        t = d.hour * 60 + d.minute
        return a <= t < b if a < b else (t >= a or t < b)

    def reminders(self, person=None, chore=None):
        """[(name, message, quiet)] for open instances (optionally one person / one chore)."""
        out = []
        for i in self.instances("open", person=person, chore=chore):
            if not i["assignee"]:
                continue
            msg = f"Reminder for {i['assignee']}: '{i['title']}' (due {when(i['due'])})."
            out.append((i["assignee"], msg, self.in_quiet_hours(i["assignee"])))
            self._x("UPDATE instances SET reminded_at=? WHERE id=?", (self.now(), i["id"]))
        return out

    def points(self, days=7):
        since = self.now() - days * 86400
        return {p["name"]: self._points_since(p["id"], since) for p in self.people()}


def totp(secret, at=None, step=30, digits=6):
    """RFC 6238 TOTP (SHA-1), stdlib only."""
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    counter = struct.pack(">Q", int((at or time.time()) // step))
    h = hmac.new(key, counter, hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return f"{code:0{digits}d}"
