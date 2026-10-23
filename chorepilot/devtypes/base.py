"""The device-type plugin contract.

A device type is one small module that says, for one class of device (a light, a thermostat,
a sprinkler valve, an oven...):

  - what it can do: `actions`, each with typed parameters and a SAFETY TIER;
  - what it reports: `status_fields`;
  - how to reach it: `ha_*` (Home Assistant, the universal adapter, and Matter through HA),
    `mqtt_*` (ESPHome conventions), `hc_*` (Home Connect), `sim_*` (the built-in simulator);
  - its own interlocks (`interlock`) and which device events it emits (`events`), which the
    chore engine uses to verify chores ("vacuum finished" => "vacuum the living room" done).

Safety tiers (docs/DEVICES.md has the per-class table):

  0 read            status, alarms                          any role
  1 low             lights, vacuum, fans, locking a door    adults; kids if the owner allows
  2 water / heat    start a wash, preheat the oven, brew,   an adult says yes; a kid's request
                    irrigate, setpoints outside comfort     goes to a parent's approval queue
  3 security        unlock, open the garage, disarm         owner only + second factor (PIN or
                                                            TOTP), never from an external agent

Adding a type: subclass DeviceType, fill in the attributes, implement the adapter hooks you
need, and call `register()` (see light.py for the smallest complete example). A user plugin can
live in ~/.config/chorepilot/devtypes/<name>.py; it is loaded at start-up.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

TIER_NAMES = {0: "read", 1: "low", 2: "water/heat", 3: "security"}


@dataclass
class Param:
    name: str
    type: str                      # "int" | "number" | "bool" | "enum" | "string"
    desc: str
    lo: float | None = None
    hi: float | None = None
    values: tuple = ()
    aliases: dict = field(default_factory=dict)
    required: bool = False
    default: object = None

    def schema(self):
        t = {"int": "integer", "number": "number", "bool": "boolean", "enum": "string",
             "string": "string"}[self.type]
        s = {"type": t, "description": self.desc}
        if self.type == "enum":
            s["enum"] = list(self.values)
        if self.lo is not None and self.type in ("int", "number"):
            s["minimum"] = self.lo
        if self.hi is not None and self.type in ("int", "number"):
            s["maximum"] = self.hi
        return s

    def clean(self, v, lo=None, hi=None):
        lo = self.lo if lo is None else lo
        hi = self.hi if hi is None else hi
        if self.type == "bool":
            if isinstance(v, str):
                return v.strip().lower() in ("true", "1", "yes", "on", "y")
            return bool(v)
        if self.type in ("int", "number"):
            if isinstance(v, str):
                m = re.match(r"^\s*(-?\d+(?:\.\d+)?)", v)
                if not m:
                    raise ValueError(f"{self.name} must be a number (got {v!r})")
                v = m.group(1)
            v = float(v)
            if self.type == "int":
                v = int(round(v))
            if lo is not None and v < lo or hi is not None and v > hi:
                raise ValueError(f"{self.name} must be {lo:g}..{hi:g} (got {v:g})")
            return v
        if self.type == "enum":
            s = re.sub(r"[\s\-]+", "_", str(v).strip().lower())
            s = self.aliases.get(s, s)
            if s not in self.values:
                raise ValueError(f"{self.name} must be one of {', '.join(self.values)} (got {v!r})")
            return s
        return str(v)[:200]


@dataclass
class Action:
    name: str
    desc: str
    params: tuple = ()
    tier: int = 1
    uses: str | None = None        # "water", "heat", "water and heat" -> said in the confirmation
    confirm: str = "normal"        # "normal" (y/N) | "strong" (type a phrase)
    phrase: str | None = None      # strong confirmation: what the person has to type
    write: bool = True             # False for read-only actions (status, alarms)


class DeviceType:
    """Base class. Class attributes describe the type; methods are the adapter hooks."""

    name = "device"                # registry key, also the `type` in house.json
    title = "device"
    tool = "device"                # the LLM tool name for this type
    tool_desc = ""
    ha_domain = None               # Home Assistant entity domain
    matter = ()                    # Matter device type names
    capabilities = ()
    status_fields = ("state",)
    actions: dict = {}
    keywords = ()                  # words that route a request to this type's tool
    aliases = ()                   # what people call it ("ac", "sprinkler")
    hc_type = None                 # Home Connect appliance type, when it has one
    events_emitted = ()

    def __init__(self, cfg=None):
        self.cfg = dict(cfg or {})

    # ---------------------------------------------------------------- validation + policy
    def default_action(self, args):
        """The action to use when the model left it out (e.g. temperature given -> set it)."""
        return next(iter(self.actions))

    def limits(self, param, cfg):
        """Per-device limits from house.json (e.g. max_minutes, max_temp). (lo, hi) or (None, None)."""
        return None, None

    def validate(self, action, args, cfg=None):
        """Strict check of the model's arguments. Returns (action, params). Raises ValueError."""
        cfg = cfg if cfg is not None else self.cfg
        if action in (None, ""):
            action = self.default_action(args)
        a = re.sub(r"[\s\-]+", "_", str(action).strip().lower())
        a = self.action_aliases().get(a, a)
        if a not in self.actions:
            raise ValueError(f"a {self.title} can't {action!r}; it can: {', '.join(self.actions)}")
        act = self.actions[a]
        known = {p.name: p for p in act.params}
        out = {}
        for k, v in (args or {}).items():
            if k in ("device", "action", "appliance") or v is None or v == "":
                continue
            if k in ("pin", "code", "otp", "totp", "password", "passcode"):
                raise ValueError("codes and PINs are never accepted from the model; the person "
                                 "enters them at the confirmation prompt")
            if k not in known:
                if self.param_aliases().get(k) in known:
                    k = self.param_aliases()[k]
                else:
                    continue                        # small models add junk; ignore, don't fail
            lo, hi = self.limits(k, cfg)
            out[k] = known[k].clean(v, lo, hi)
        for p in act.params:
            if p.name not in out:
                if p.default is not None:
                    out[p.name] = p.default
                elif p.required:
                    raise ValueError(f"{a} needs {p.name}: {p.desc}")
        return a, self.normalise(a, out, cfg)

    def normalise(self, action, params, cfg):
        return params

    def action_aliases(self):
        return {}

    def param_aliases(self):
        return {}

    def tier_for(self, action, params, cfg):
        return self.actions[action].tier

    def interlock(self, action, params, status, cfg):
        """What the device itself would refuse, or should. None if the write may go."""
        if status.get("state") in ("offline", "unavailable"):
            return f"the {self.title} is offline or not reachable"
        return None

    def confirmation(self, action, params, nick):
        act = self.actions[action]
        bits = ", ".join(f"{k}={v}" for k, v in params.items())
        q = f"{act.desc} on the {nick}" + (f" ({bits})" if bits else "") + "?"
        if act.uses:
            q += f" It uses {act.uses}."
        return q

    def events(self, prev, cur):
        """Device events from a status change, e.g. ["started"] or ["finished"]."""
        return []

    # ---------------------------------------------------------------- simulator hooks
    def sim_init(self, cfg):
        return {"state": "idle"}

    def sim_apply(self, st, action, params, now, speed):
        st["state"] = action

    def sim_tick(self, st, now, speed):
        """Advance timed things (a valve closing, a vacuum docking)."""

    def sim_status(self, st, now, speed):
        return {k: st.get(k) for k in self.status_fields}

    # ---------------------------------------------------------------- Home Assistant hooks
    def ha_status(self, state, extra):
        """HA /api/states object for the main entity (+ extra entities) -> our status dict."""
        if state is None:
            return {"state": "offline"}
        return {"state": state.get("state")}

    def ha_ops(self, action, params, entity, cfg):
        """-> [{"service": "domain.service", "data": {...}}, optionally "delay_s": N]"""
        raise NotImplementedError(f"{self.title}: no Home Assistant mapping for {action}")

    # ---------------------------------------------------------------- MQTT (ESPHome) hooks
    def mqtt_state_topics(self, prefix):
        return {"state": f"{prefix}/state"}

    def mqtt_status(self, vals):
        return {"state": vals.get("state") or "offline"}

    def mqtt_ops(self, action, params, prefix):
        raise NotImplementedError(f"{self.title}: no MQTT mapping for {action}")

    # ---------------------------------------------------------------- Home Connect hooks
    def hc_status(self, status, settings, active):
        raise NotImplementedError(f"{self.title}: not available over Home Connect")

    def hc_ops(self, action, params, cfg):
        raise NotImplementedError(f"{self.title}: not available over Home Connect")

    # ---------------------------------------------------------------- LLM tool schema
    def tool_schema(self, nicks):
        props = {"device": {"type": "string", "enum": list(nicks),
                            "description": f"which {self.title}"}}
        writes = [a for a in self.actions.values()]
        props["action"] = {"type": "string", "enum": [a.name for a in writes],
                           "description": "; ".join(f"{a.name}: {a.desc}" for a in writes)}
        for a in writes:
            for p in a.params:
                props.setdefault(p.name, p.schema())
        return {"type": "function", "function": {
            "name": self.tool, "description": self.tool_desc or f"Control a {self.title}.",
            "parameters": {"type": "object", "properties": props,
                           "required": [] if len(nicks) == 1 else ["device"]}}}

    def describe_status(self, st):
        """One short phrase for a system prompt line."""
        return ", ".join(f"{k} {st[k]}" for k in self.status_fields if st.get(k) not in (None, ""))

    # ---------------------------------------------------------------- dashboard hooks
    # The web dashboard draws every device from these, so a user plugin gets a working tile and
    # detail sheet without any web code. Override them only to say something better.
    room = None                    # default room on the dashboard ("Kitchen"); house.json "room" wins
    icon = None                    # dashboard icon key (defaults to the type name)
    #: (when active, when not) one-tap tile actions, first pair whose actions exist wins
    TOGGLES = (("turn_off", "turn_on"), ("unlock", "lock"), ("close", "open"), ("stop", "water"),
               ("dock", "start"), ("pause", "play"), ("stop_charging", "start_charging"))
    ACTIVE_STATES = ("on", "running", "watering", "cleaning", "playing", "charging", "brewing",
                     "preheating", "heating", "open", "opening", "unlocked", "armed_away",
                     "armed_home", "armed_night", "delayed", "paused", "returning")
    ATTENTION_STATES = ("unlocked", "jammed", "error", "obstructed", "triggered",
                        "action_required")

    def ui_active(self, st):
        s = str(st.get("state") or "").lower()
        return s in self.ACTIVE_STATES

    def ui_attention(self, st):
        """Something a person should look at: unlocked, jammed, an alarm, an error."""
        s = str(st.get("state") or "").lower()
        if st.get("error") or st.get("alarms") or st.get("obstructed"):
            return True
        return s in self.ATTENTION_STATES and s not in ("open", "offline", "unavailable")

    def ui_primary(self, st, cfg=None):
        """The one-tap tile action: {"action", "label", "params"} or None (tap opens the sheet)."""
        s = str(st.get("state") or "").lower()
        if s in ("offline", "unavailable"):
            return None
        active = self.ui_active(st)
        for on, off in self.TOGGLES:
            if on in self.actions and off in self.actions:
                name = on if active else off
                a = self.actions[name]
                params = {p.name: p.default for p in a.params if p.default is not None}
                return {"action": name, "label": a.desc, "params": params, "tier": a.tier}
        return None

    def ui_state(self, st):
        """A short human status line for a tile ("Cooling to 72°", "On, 80%", "Locked")."""
        s = str(st.get("state") or "").lower()
        if s in ("offline", "unavailable"):
            return "Not responding"
        words = s.replace("_", " ").capitalize() if s else "Unknown"
        bits = []
        unit = "°" if st.get("unit") in ("F", "C") else ""
        if st.get("target") is not None and unit:
            bits.append(f"set to {st['target']:g}{unit}")
        if st.get("brightness") and s == "on":
            bits.append(f"{st['brightness']}%")
        if st.get("position") not in (None, 0, 100) and s not in ("closed",):
            bits.append(f"{st['position']}% open")
        if st.get("remaining"):
            bits.append(f"{st['remaining']} left")
        if st.get("volume") is not None and s == "playing":
            bits.append(f"volume {st['volume']}")
        return ", ".join([words] + bits)

    def ui_controls(self, cfg=None):
        """Every action with its parameters (limits from house.json applied), for the sheet."""
        cfg = cfg if cfg is not None else self.cfg
        out = []
        for a in self.actions.values():
            if not a.write:
                continue
            params = []
            for p in a.params:
                lo, hi = self.limits(p.name, cfg)
                params.append({"name": p.name, "type": p.type, "desc": p.desc,
                               "lo": lo if lo is not None else p.lo,
                               "hi": hi if hi is not None else p.hi,
                               "values": list(p.values), "default": p.default,
                               "required": p.required})
            out.append({"name": a.name, "label": a.desc, "tier": a.tier, "uses": a.uses,
                        "confirm": a.confirm, "params": params})
        return out


def human_minutes(m):
    if m is None:
        return None
    m = int(round(m))
    h, mm = divmod(m, 60)
    return f"{h} h {mm} min" if h else f"{mm} min"


def now():
    return time.time()
