"""Covers: blinds / shades / curtains / awnings (tier 1), and garage doors / gates.

Garage doors and gates are their own type because opening one is a SECURITY action:

  garage_door  open   tier 3: owner only + PIN or TOTP typed by the person, never from an
                      external agent (MCP), never on a schedule, never on a kid's request
               close  tier 1 (it makes the house safer)
               stop   tier 1
  cover        open / close / stop / set_position: tier 1 (blinds, shades, curtains)

The opener's own safety reversal (photo eyes, force sensing) is never bypassed: ChorePilot only
asks Home Assistant to open or close, exactly like the wall button.

HA: cover.open_cover / close_cover / stop_cover / set_cover_position {position}
    states open, opening, closed, closing; attribute current_position (0 closed .. 100 open)
    device classes garage, gate, blind, shade, curtain, awning, shutter
    https://www.home-assistant.io/integrations/cover/
Matter: Window Covering (1.0); Closure (1.5) for garage doors -- through HA.
ESPHome MQTT: <prefix>/command OPEN | CLOSE | STOP, <prefix>/position/command 0-100,
              <prefix>/state open|closed|opening|closing, <prefix>/position/state.
"""
from __future__ import annotations

from . import register
from .base import Action, DeviceType, Param

POSITION = Param("position", "int", "how far open, in percent (0 = closed, 100 = fully open)",
                 lo=0, hi=100)
_STATES = ("open", "opening", "closed", "closing")


class _CoverBase(DeviceType):
    ha_domain = "cover"
    status_fields = ("state", "position")
    events_emitted = ("opened", "closed")

    def action_aliases(self):
        return {"raise": "open", "up": "open", "lower": "close", "down": "close", "shut": "close",
                "halt": "stop", "position": "set_position", "set": "set_position"}

    def param_aliases(self):
        return {"percent": "position", "percentage": "position", "level": "position"}

    def interlock(self, action, params, status, cfg):
        why = super().interlock(action, params, status, cfg)
        if why:
            return why
        st = status.get("state")
        if action == "open" and st == "open":
            return f"the {self.title} is already open"
        if action == "close" and st == "closed":
            return f"the {self.title} is already closed"
        if status.get("obstructed"):
            return f"the {self.title} reports an obstruction; check it by hand"
        return None

    def events(self, prev, cur):
        p, c = prev.get("state"), cur.get("state")
        if p != c and c == "open":
            return ["opened"]
        if p != c and c == "closed":
            return ["closed"]
        return []

    # simulator: moves instantly (a real one passes through opening/closing)
    def sim_init(self, cfg):
        return {"state": "closed", "position": 0}

    def sim_apply(self, st, action, params, now, speed):
        if action == "open":
            st.update(state="open", position=100)
        elif action == "close":
            st.update(state="closed", position=0)
        elif action == "set_position":
            p = int(params["position"])
            st.update(state="closed" if p == 0 else "open", position=p)
        # stop: stays where it is

    # Home Assistant
    def ha_status(self, state, extra):
        if state is None or state.get("state") in ("unavailable", "unknown"):
            return {"state": "offline"}
        a = state.get("attributes") or {}
        return {"state": state["state"], "position": a.get("current_position")}

    def ha_ops(self, action, params, entity, cfg):
        if action == "set_position":
            return [{"service": "cover.set_cover_position",
                     "data": {"entity_id": entity, "position": params["position"]}}]
        svc = {"open": "cover.open_cover", "close": "cover.close_cover",
               "stop": "cover.stop_cover"}[action]
        return [{"service": svc, "data": {"entity_id": entity}}]

    # MQTT
    def mqtt_state_topics(self, prefix):
        return {"state": f"{prefix}/state", "position": f"{prefix}/position/state"}

    def mqtt_status(self, v):
        s = (v.get("state") or "").lower()
        if not s:
            return {"state": "offline"}
        pos = v.get("position")
        return {"state": s, "position": int(float(pos)) if pos not in (None, "") else None}

    def mqtt_ops(self, action, params, prefix):
        if action == "set_position":
            return [(f"{prefix}/position/command", str(params["position"]))]
        return [(f"{prefix}/command", action.upper())]

    def describe_status(self, st):
        if st.get("position") is not None and st.get("state") not in ("offline",):
            return f"{st.get('state')}, {st['position']}% open"
        return st.get("state", "?")


@register
class Cover(_CoverBase):
    name = "cover"
    title = "blind"
    room = "Bedroom"
    tool = "set_cover"
    tool_desc = "Open, close, stop or set the position of blinds, shades or curtains."
    matter = ("Window Covering",)
    capabilities = ("open_close", "position")
    keywords = ("blind", "blinds", "shade", "shades", "curtain", "curtains", "shutter",
                "shutters", "awning")
    aliases = ("blinds", "shades", "curtains")
    actions = {
        "open": Action("open", "Open the blinds", (), tier=1),
        "close": Action("close", "Close the blinds", (), tier=1),
        "stop": Action("stop", "Stop the blinds", (), tier=1),
        "set_position": Action("set_position", "Move the blinds to a position", (POSITION,), tier=1),
    }

    def default_action(self, args):
        return "set_position" if args.get("position") not in (None, "") else "open"

    def normalise(self, action, params, cfg):
        if action == "set_position" and "position" not in params:
            raise ValueError("set_position needs position (0-100)")
        return params


@register
class GarageDoor(_CoverBase):
    name = "garage_door"
    title = "garage door"
    room = "Entry"
    tool = "garage_door"
    tool_desc = ("Open or close the garage door or gate. Closing is simple; OPENING is owner-only "
                 "and the owner enters their PIN at the prompt: just call the tool, never ask for "
                 "or pass a code.")
    matter = ("Closure",)
    capabilities = ("open_close", "obstruction")
    status_fields = ("state", "position", "obstructed")
    keywords = ("garage", "gate", "garage door", "driveway gate")
    aliases = ("garage", "gate", "garage door")
    actions = {
        "close": Action("close", "Close the garage door", (), tier=1),
        "open": Action("open", "Open the garage door", (), tier=3),
        "stop": Action("stop", "Stop the garage door", (), tier=1),
    }
    events_emitted = ("opened", "closed", "obstructed")

    def action_aliases(self):
        return {**super().action_aliases(), "unlock": "open", "lock": "close"}

    def default_action(self, args):
        return "close"

    def confirmation(self, action, params, nick):
        return f"{'Open' if action == 'open' else action.capitalize()} the {nick}?"

    def events(self, prev, cur):
        out = super().events(prev, cur)
        if cur.get("obstructed") and not prev.get("obstructed"):
            out.append("obstructed")
        return out

    def sim_init(self, cfg):
        return {"state": "closed", "position": 0, "obstructed": False}

    def sim_apply(self, st, action, params, now, speed):
        if action == "stop":
            return
        super().sim_apply(st, action, params, now, speed)

    def ha_status(self, state, extra):
        st = super().ha_status(state, extra)
        ob = (extra or {}).get("obstruction")
        if ob is not None:
            st["obstructed"] = ob.get("state") == "on"
        return st
