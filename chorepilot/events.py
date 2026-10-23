"""Device events -> chore rules.

Ingestion is push where the device can push (push.py: Home Assistant websocket state_changed,
Home Connect SSE, MQTT subscriptions; `chorepilot events push`), and polling everywhere else: read every device, compare with the last-known status
(~/.cache/chorepilot/device-states.json), turn the difference into events with the device type's
own rules (dishwasher running -> finished = "finished"; vacuum cleaning -> docked = "finished";
fridge door open too long = "door_alarm"), and hand each event to the chore engine
(ChoreDB.apply_event), which completes the verified chore and opens its follow-ups.

    chorepilot events poll            # once (cron / launchd / systemd timer)
    chorepilot events watch -i 30     # loop
    chorepilot events emit dishwasher finished   # inject one by hand

The House records "started" (with WHO started it) the moment a write succeeds, so the credit is
right even though the finish is only seen by a later poll or push. A push only says "this
device changed"; `check_device` then reads it and runs the same diff -> rules path, so push and
poll can never disagree about what an event means.
"""
from __future__ import annotations

import time

from .appliance import BackendError
from .devtypes import get_type


def _sources(house):
    if house.bridge:
        for nick, be in house.bridge.fleet:
            yield nick, get_type(be.machine.kind), be.status
    for nick, d in house.devices.items():
        yield nick, d.dtype, d.adapter.status


def _check(house, nick, dtype, read, log, notify, out):
    try:
        cur = read()
    except BackendError as e:
        log(f"  [events    ] {nick}: {e}")
        return
    prev = house.snapshot.get(nick)
    if prev is not None:
        for ev in dtype.events(prev, cur):
            lines = house.db.apply_event(nick, ev, dtype.name)
            out.append((nick, ev, lines))
            log(f"  [event     ] {nick}: {ev}")
            for ln in lines:
                log(f"  [chores    ] {ln}")
                if notify:
                    house.notify(ln, log=lambda m: None)
    house.snapshot.set(nick, cur)


def poll_once(house, log=print, notify=True, only=None, skip=()):
    """One pass over every device (or just `only`). Returns [(device, event, [chore lines])]."""
    out = []
    for nick, dtype, read in _sources(house):
        if (only is not None and nick not in only) or nick in skip:
            continue
        _check(house, nick, dtype, read, log, notify, out)
    return out


def check_device(house, nick, log=print, notify=True):
    """Re-read one device now (a push said it changed) and apply its events."""
    return poll_once(house, log, notify, only={nick})


def watch(house, interval=30, log=print, sleep=time.sleep, rounds=None):
    n = 0
    while rounds is None or n < rounds:
        poll_once(house, log)
        n += 1
        if rounds is None or n < rounds:
            sleep(interval)
