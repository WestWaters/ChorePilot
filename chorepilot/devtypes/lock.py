"""Door locks. Locking is tier 1 (it makes the house safer); UNLOCKING is tier 3.

Tier 3 means: the owner only, plus a second factor the person types at the prompt (their
ChorePilot PIN or a TOTP code -- never a code the model supplies, and never the lock's own
keypad code), never from an external agent (MCP) alone, never by schedule. The same rules apply
to garage doors and gates (cover with device_class garage/gate) and alarm panels (disarm).

HA: lock.lock / lock.unlock / lock.open (optional `code` = the LOCK's code, kept in house.json,
    never shown to the model); states locked, unlocked, locking, unlocking, jammed, open
    https://www.home-assistant.io/integrations/lock/
Matter: Door Lock. Vendors: August/Yale, Schlage, Nuki, Aqara -- through HA.
"""
from __future__ import annotations

from . import register
from .base import Action, DeviceType


@register
class Lock(DeviceType):
    name = "lock"
    title = "door lock"
    room = "Entry"
    tool = "door_lock"
    tool_desc = ("Lock or unlock a door. Unlocking is owner-only and the owner must enter their "
                 "PIN at the prompt; just call the tool, never ask for or pass a code.")
    ha_domain = "lock"
    matter = ("Door Lock",)
    capabilities = ("lock", "unlock", "jam_detection")
    status_fields = ("state", "battery")
    keywords = ("lock", "unlock", "door", "front door", "back door", "deadbolt")
    actions = {
        "lock": Action("lock", "Lock the door", (), tier=1),
        "unlock": Action("unlock", "Unlock the door", (), tier=3),
    }
    events_emitted = ("locked", "unlocked", "jammed")

    def action_aliases(self):
        return {"close": "lock", "secure": "lock", "open": "unlock"}

    def interlock(self, action, params, status, cfg):
        why = super().interlock(action, params, status, cfg)
        if why:
            return why
        if status.get("state") == "jammed":
            return "the lock reports JAMMED; check the door by hand"
        if status.get("state") == ("locked" if action == "lock" else "unlocked"):
            return f"the door is already {status['state']}"
        return None

    def confirmation(self, action, params, nick):
        return f"{'Unlock' if action == 'unlock' else 'Lock'} the {nick}?"

    def events(self, prev, cur):
        c = cur.get("state")
        if prev.get("state") != c and c in ("locked", "unlocked", "jammed"):
            return [c]
        return []

    def sim_init(self, cfg):
        return {"state": "locked", "battery": 87}

    def sim_apply(self, st, action, params, now, speed):
        st["state"] = "locked" if action == "lock" else "unlocked"

    def ha_status(self, state, extra):
        if state is None or state.get("state") in ("unavailable", "unknown"):
            return {"state": "offline"}
        return {"state": state["state"],
                "battery": (state.get("attributes") or {}).get("battery_level")}

    def ha_ops(self, action, params, entity, cfg):
        data = {"entity_id": entity}
        if (cfg or {}).get("lock_code"):          # the lock's own code, from house.json only
            data["code"] = cfg["lock_code"]
        return [{"service": f"lock.{action}", "data": data}]

    def mqtt_state_topics(self, prefix):
        return {"state": f"{prefix}/state"}

    def mqtt_status(self, v):
        s = (v.get("state") or "").lower()
        return {"state": s or "offline"}

    def mqtt_ops(self, action, params, prefix):
        return [(f"{prefix}/command", action.upper())]

    # dashboard
    def ui_active(self, st):
        return st.get("state") == "unlocked"

    def ui_state(self, st):
        s = st.get("state")
        if s in ("offline", "unavailable"):
            return "Not responding"
        low = st.get("battery") is not None and st["battery"] < 20
        return str(s or "unknown").capitalize() + (f", battery {st['battery']}%" if low else "")
