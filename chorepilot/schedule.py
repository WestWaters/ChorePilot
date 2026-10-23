"""Chore schedules: a small deterministic parser (no model needed) and next-due arithmetic.

Accepted forms (case-insensitive):

  daily | every day [at 8pm]          weekly | every week        monthly | every month
  every 3 days | every other day      every monday [and thursday] [at 18:00]
  every other tuesday                 weekdays | weekends
  3 days after done | every 3 days after completion      (Donetick-style adaptive)
  once | on demand | none | ""        (no schedule: instances are opened by hand or by events)
  RRULE subset: FREQ=DAILY|WEEKLY|MONTHLY;INTERVAL=n;BYDAY=MO,TH;BYHOUR=18;BYMINUTE=30

A parsed schedule is a dict: {"freq": "daily|weekly|monthly|after_done|none", "interval": n,
"days": [0..6] (Mon=0), "time": "HH:MM"}. All times are local.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
RR_DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
_DAYRX = r"(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)[a-z]*"


def _day(tok):
    tok = tok.lower()[:3]
    return [d[:3] for d in DAYS].index(tok)


def _time(text):
    m = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", text) or \
        re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text) or \
        re.search(r"\b(\d{1,2}):(\d{2})()\b", text)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= mi < 60):
        raise ValueError(f"bad time in {text!r}")
    return f"{h:02d}:{mi:02d}"


def parse(text):
    """Text or RRULE -> schedule dict. Raises ValueError for anything it doesn't understand."""
    if isinstance(text, dict):
        return text
    t = (text or "").strip().lower()
    if t in ("", "none", "once", "on demand", "on-demand", "manual", "never", "as needed"):
        return {"freq": "none", "interval": 1, "days": [], "time": None}
    if "freq=" in t:
        parts = dict(p.split("=", 1) for p in t.upper().replace("RRULE:", "").split(";") if "=" in p)
        freq = {"DAILY": "daily", "WEEKLY": "weekly", "MONTHLY": "monthly"}.get(parts.get("FREQ"))
        if not freq:
            raise ValueError(f"unsupported RRULE FREQ in {text!r}")
        days = [RR_DAYS.index(d[-2:]) for d in parts.get("BYDAY", "").split(",") if d]
        tm = None
        if "BYHOUR" in parts:
            tm = f"{int(parts['BYHOUR']):02d}:{int(parts.get('BYMINUTE', 0)):02d}"
        return {"freq": freq, "interval": int(parts.get("INTERVAL", 1)), "days": days, "time": tm}
    tm = _time(t)
    m = re.search(r"(?:every\s+)?(\d+)\s+days?\s+after\s+(?:done|completion|completed|it'?s done)", t)
    if m:
        return {"freq": "after_done", "interval": int(m.group(1)), "days": [], "time": tm}
    if re.search(r"\bweekdays?\b", t) and "every" not in t.replace("weekday", ""):
        return {"freq": "weekly", "interval": 1, "days": [0, 1, 2, 3, 4], "time": tm}
    if re.search(r"\bweekends?\b", t):
        return {"freq": "weekly", "interval": 1, "days": [5, 6], "time": tm}
    if re.search(r"\bweekdays?\b", t):
        return {"freq": "weekly", "interval": 1, "days": [0, 1, 2, 3, 4], "time": tm}
    days = [_day(d) for d in re.findall(r"\b" + _DAYRX + r"\b", t)]
    other = bool(re.search(r"\bevery\s+other\b", t))
    if days:
        return {"freq": "weekly", "interval": 2 if other else 1, "days": sorted(set(days)), "time": tm}
    m = re.search(r"\bevery\s+(\d+)\s+(day|week|month)s?\b", t)
    if m:
        unit = {"day": "daily", "week": "weekly", "month": "monthly"}[m.group(2)]
        return {"freq": unit, "interval": int(m.group(1)), "days": [], "time": tm}
    if re.search(r"\bevery\s+other\s+day\b", t):
        return {"freq": "daily", "interval": 2, "days": [], "time": tm}
    if re.search(r"\bevery\s+other\s+week\b|\bfortnightly\b|\bbiweekly\b", t):
        return {"freq": "weekly", "interval": 2, "days": [], "time": tm}
    if re.search(r"\b(daily|every\s+day|each\s+day|every\s+night|nightly)\b", t):
        return {"freq": "daily", "interval": 1, "days": [], "time": tm}
    if re.search(r"\b(weekly|every\s+week|each\s+week|once\s+a\s+week)\b", t):
        return {"freq": "weekly", "interval": 1, "days": [], "time": tm}
    if re.search(r"\b(monthly|every\s+month|once\s+a\s+month)\b", t):
        return {"freq": "monthly", "interval": 1, "days": [], "time": tm}
    raise ValueError(f"can't read the schedule {text!r}; try 'daily at 8pm', 'every monday', "
                     "'every 3 days', 'weekdays', '3 days after done' or an RRULE")


def describe(s):
    s = parse(s)
    f, n = s["freq"], s.get("interval", 1)
    at = f" at {s['time']}" if s.get("time") else ""
    if f == "none":
        return "on demand"
    if f == "after_done":
        return f"{n} day{'s' if n != 1 else ''} after it's done{at}"
    if f == "weekly" and s.get("days"):
        if s["days"] == [0, 1, 2, 3, 4]:
            return f"weekdays{at}"
        if s["days"] == [5, 6]:
            return f"weekends{at}"
        names = " and ".join(DAYS[d].capitalize() for d in s["days"])
        return f"every {'other ' if n == 2 else ''}{names}{at}"
    unit = {"daily": "day", "weekly": "week", "monthly": "month"}[f]
    if n == 1:
        return {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}[f] + at
    return f"every {n} {unit}s{at}"


def _at(dt, tm):
    if not tm:
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    h, m = map(int, tm.split(":"))
    return dt.replace(hour=h, minute=m, second=0, microsecond=0)


def next_due(s, after, anchor=None, last_done=None):
    """The first due time strictly after `after` (datetime). `anchor` = when the chore was
    created (for every-N and every-other-week), `last_done` for after_done schedules.
    None for on-demand chores."""
    s = parse(s)
    f, n = s["freq"], max(1, int(s.get("interval", 1)))
    anchor = anchor or after
    if f == "none":
        return None
    if f == "after_done":
        base = last_done or anchor
        return _at(base + timedelta(days=n), s.get("time")) if s.get("time") else base + timedelta(days=n)
    if f == "daily":
        d = _at(anchor, s.get("time"))
        while d <= after:
            d += timedelta(days=n)
        return d
    if f == "weekly":
        days = s.get("days") or [anchor.weekday()]
        a_week = (anchor - timedelta(days=anchor.weekday())).date()
        d = _at(after, s.get("time"))
        for _ in range(7 * n + 8):
            wk = ((d - timedelta(days=d.weekday())).date() - a_week).days // 7
            if d > after and d.weekday() in days and wk % n == 0:
                return d
            d = _at(d + timedelta(days=1), s.get("time"))
        return None
    if f == "monthly":
        d = _at(anchor, s.get("time"))
        while d <= after:
            y, mo = divmod(d.month - 1 + n, 12)
            day = min(anchor.day, 28)
            d = d.replace(year=d.year + y, month=mo + 1, day=day)
        return d
    return None


def week_start(dt):
    """Monday 00:00 of dt's week (local)."""
    return (dt - timedelta(days=dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def now():
    return datetime.now()
