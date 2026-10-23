"""Refrigerators and freezers: temperatures and door alarms (tier 0); setpoint changes, only in a
food-safe range, and super-cool / super-freeze are tier 2 (food safety and energy: an adult
confirms, see docs/DEVICES.md section 5).

Food-safe ranges (refused outside them): fridge 1-5 C (34-41 F), freezer -24 to -15 C
(-11 to 5 F). A door left open emits "door_alarm", which ChorePilot turns into a notification
and, if configured, a chore ("close the freezer").

Home Connect (https://api-docs.home-connect.com/settings/ and /events/):
  Refrigeration.FridgeFreezer.Setting.SetpointTemperatureRefrigerator / ...Freezer
  Refrigeration.FridgeFreezer.Setting.SuperModeRefrigerator / SuperModeFreezer
  Refrigeration.Common.Status.Door.Refrigerator / .Freezer
  Refrigeration.FridgeFreezer.Event.DoorAlarmRefrigerator / DoorAlarmFreezer / TemperatureAlarmFreezer
HA: sensor (temperatures), binary_sensor (door), number / switch for setpoints and super modes.
Matter: Refrigerator, Temperature Controlled Cabinet (1.2+ appliance types).
Small business: the same type watches a walk-in cooler's temperature sensor (read-only), but
ChorePilot is not a food-safety compliance log.
"""
from __future__ import annotations

from . import register
from .base import Action, DeviceType, Param

COMP = Param("compartment", "enum", "fridge or freezer", values=("fridge", "freezer"),
             default="fridge", aliases={"refrigerator": "fridge", "freezer_compartment": "freezer"})
TEMP = Param("temperature", "number", "target temperature for that compartment", required=True)
ON = Param("on", "bool", "switch super mode on (true) or off (false)", default=True)
SAFE = {"C": {"fridge": (1, 5), "freezer": (-24, -15)},
        "F": {"fridge": (34, 41), "freezer": (-11, 5)}}
HC = "Refrigeration.FridgeFreezer."


@register
class Fridge(DeviceType):
    name = "fridge"
    title = "fridge"
    room = "Kitchen"
    tool = "fridge"
    tool_desc = ("The fridge/freezer: set_temperature (compartment, temperature), super_mode "
                 "(compartment, on). Read temperatures and door alarms with get_status.")
    ha_domain = "sensor"
    matter = ("Refrigerator", "Temperature Controlled Cabinet")
    capabilities = ("temperatures", "door_alarms", "setpoints", "super_mode")
    status_fields = ("state", "fridge", "freezer", "unit", "fridge_door", "freezer_door", "alarms")
    keywords = ("fridge", "freezer", "refrigerator", "cold", "ice", "frozen")
    hc_type = "FridgeFreezer"
    actions = {
        "set_temperature": Action("set_temperature", "Change a compartment's temperature",
                                  (COMP, TEMP), tier=2, uses="the food's safe temperature"),
        "super_mode": Action("super_mode", "Super-cool / super-freeze for a few hours", (COMP, ON),
                             tier=2, uses="extra energy"),
    }
    events_emitted = ("door_alarm", "temperature_alarm", "door_closed")

    def unit(self, cfg):
        return str((cfg or {}).get("unit") or self.cfg.get("unit") or "F").upper()[:1]

    def param_aliases(self):
        return {"temp": "temperature", "zone": "compartment", "enabled": "on"}

    def normalise(self, action, params, cfg):
        if action == "set_temperature":
            u = self.unit(cfg)
            lo, hi = SAFE[u][params["compartment"]]
            if not lo <= params["temperature"] <= hi:
                raise ValueError(f"the {params['compartment']} must stay {lo}..{hi} {u} to keep "
                                 "food safe")
            params["unit"] = u
        return params

    def events(self, prev, cur):
        out = []
        pa, ca = set(prev.get("alarms") or ()), set(cur.get("alarms") or ())
        for a in sorted(ca - pa):
            out.append("door_alarm" if "door" in a else "temperature_alarm")
        for comp in ("fridge", "freezer"):
            if prev.get(f"{comp}_door") == "open" and cur.get(f"{comp}_door") == "closed":
                out.append("door_closed")
        return out

    # simulator: `chorepilot devices sim fridge freezer_door=open` (and wait) raises the alarm
    def sim_init(self, cfg):
        u = self.unit(cfg)
        return {"state": "ok", "fridge": 37 if u == "F" else 3, "freezer": 0 if u == "F" else -18,
                "unit": u, "fridge_door": "closed", "freezer_door": "closed", "opened_at": {},
                "super": {}}

    def sim_apply(self, st, action, params, now, speed):
        if action == "set_temperature":
            st[params["compartment"]] = params["temperature"]
        else:
            st.setdefault("super", {})[params["compartment"]] = bool(params["on"])

    def sim_status(self, st, now, speed):
        alarms = []
        for comp in ("fridge", "freezer"):
            if st.get(f"{comp}_door") == "open":
                t0 = st.setdefault("opened_at", {}).setdefault(comp, now)
                if (now - t0) * speed >= 120:           # open > 2 simulated minutes
                    alarms.append(f"{comp} door open")
            else:
                st.setdefault("opened_at", {}).pop(comp, None)
        return {"state": "alarm" if alarms else "ok", "fridge": st["fridge"],
                "freezer": st["freezer"], "unit": st["unit"], "fridge_door": st["fridge_door"],
                "freezer_door": st["freezer_door"], "alarms": alarms}

    # Home Connect
    def hc_status(self, status, settings, active):
        door = lambda k: str(status.get(f"Refrigeration.Common.Status.Door.{k}", "")).rsplit(".", 1)[-1].lower() or None  # noqa: E731
        alarms = [a for a, k in (("fridge door open", "Refrigerator"), ("freezer door open", "Freezer"))
                  if door(k) == "open"]
        return {"state": "alarm" if alarms else "ok",
                "fridge": settings.get(HC + "Setting.SetpointTemperatureRefrigerator"),
                "freezer": settings.get(HC + "Setting.SetpointTemperatureFreezer"), "unit": "C",
                "fridge_door": door("Refrigerator"), "freezer_door": door("Freezer"), "alarms": alarms}

    def hc_settings(self):
        return [HC + "Setting.SetpointTemperatureRefrigerator", HC + "Setting.SetpointTemperatureFreezer"]

    def hc_ops(self, action, params, cfg):
        comp = "Refrigerator" if params["compartment"] == "fridge" else "Freezer"
        if action == "set_temperature":
            key = HC + f"Setting.SetpointTemperature{comp}"
            t = params["temperature"]
            c = round((t - 32) * 5 / 9) if params["unit"] == "F" else t
            return [{"method": "PUT", "path": f"/settings/{key}",
                     "body": {"data": {"key": key, "value": c, "unit": "°C"}}}]
        key = HC + f"Setting.SuperMode{comp}"
        return [{"method": "PUT", "path": f"/settings/{key}",
                 "body": {"data": {"key": key, "value": bool(params["on"])}}}]

    # Home Assistant: main entity = fridge temperature sensor; extras by name
    def ha_status(self, state, extra):
        if state is None:
            return {"state": "offline"}
        e = extra or {}
        val = lambda k: (e.get(k) or {}).get("state")  # noqa: E731
        door = lambda k: {"on": "open", "off": "closed"}.get(val(k))  # noqa: E731
        alarms = [f"{c} door open" for c in ("fridge", "freezer") if door(f"{c}_door") == "open"]
        num = lambda x: float(x) if x not in (None, "unknown", "unavailable") else None  # noqa: E731
        return {"state": "alarm" if alarms else "ok", "fridge": num(state.get("state")),
                "freezer": num(val("freezer")), "unit": self.unit(self.cfg),
                "fridge_door": door("fridge_door"), "freezer_door": door("freezer_door"),
                "alarms": alarms}

    def ha_ops(self, action, params, entity, cfg):
        ents = (cfg or {}).get("entities", {})
        comp = params["compartment"]
        if action == "set_temperature":
            ent = ents.get(f"{comp}_setpoint")
            if not ent:
                raise ValueError(f"set entities.{comp}_setpoint (a number entity) in house.json")
            return [{"service": "number.set_value", "data": {"entity_id": ent,
                                                             "value": params["temperature"]}}]
        ent = ents.get(f"{comp}_super")
        if not ent:
            raise ValueError(f"set entities.{comp}_super (a switch entity) in house.json")
        return [{"service": "switch.turn_on" if params["on"] else "switch.turn_off",
                 "data": {"entity_id": ent}}]

    def describe_status(self, st):
        if st.get("state") == "offline":
            return "offline"
        s = f"fridge {st.get('fridge')}, freezer {st.get('freezer')} {st.get('unit')}"
        return s + (f", ALARM: {'; '.join(st['alarms'])}" if st.get("alarms") else "")

    # dashboard
    def ui_active(self, st):
        return False

    def ui_attention(self, st):
        return bool(st.get("alarms")) or "open" in (st.get("fridge_door"), st.get("freezer_door"))

    def ui_state(self, st):
        if st.get("state") in ("offline", "unavailable"):
            return "Not responding"
        if st.get("alarms"):
            return "Alarm: " + ", ".join(str(a).replace("_", " ") for a in st["alarms"])
        bits = []
        if st.get("fridge") is not None:
            bits.append(f"Fridge {st['fridge']:g}°")
        if st.get("freezer") is not None:
            bits.append(f"freezer {st['freezer']:g}°")
        if "open" in (st.get("fridge_door"), st.get("freezer_door")):
            bits.append("door open")
        return ", ".join(bits) or "OK"
