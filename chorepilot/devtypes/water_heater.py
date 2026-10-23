"""Water heaters and boilers. Every write is tier 2 (heat): an adult confirms, a kid's request
goes to a parent.

Setpoints are limited twice: a hard range (95-160 F / 35-71 C) refused outright, and the
household's own ceiling, house.json "max_temp" (default 140 F / 60 C: hot enough to keep
Legionella down, while scald risk climbs fast above it). A number that only makes sense in the
other unit ("set the water heater to 50" in a Fahrenheit house) is read as that unit.

HA: water_heater.set_temperature {temperature}, set_operation_mode {operation_mode},
    set_away_mode {away_mode}; state = the operation mode; attributes current_temperature,
    temperature, operation_list, away_mode
    https://www.home-assistant.io/integrations/water_heater/
Matter: Water Heater 0x050F (1.4). Vendors: Rheem EcoNet, A. O. Smith, LG ThinQ, Viessmann --
through HA.
MQTT: <prefix>/target_temperature/command, <prefix>/mode/command, <prefix>/away/command;
      the same names /state.
"""
from __future__ import annotations

from . import register
from .base import Action, DeviceType, Param
from .climate import c_to_f, f_to_c

MODES = ("eco", "electric", "gas", "heat_pump", "high_demand", "performance", "off")
MODE_ALIASES = {"economy": "eco", "efficient": "eco", "boost": "high_demand",
                "high": "high_demand", "heatpump": "heat_pump", "hybrid": "heat_pump",
                "vacation": "off"}
TEMP = Param("temperature", "number", "target water temperature, in the house's unit")
MODE = Param("operation_mode", "enum", ", ".join(MODES), values=MODES, aliases=MODE_ALIASES)
AWAY = Param("away", "bool", "true = vacation/away mode on, false = off", default=True)
HARD = {"F": (95, 160), "C": (35, 71)}
CEILING = {"F": 140, "C": 60}


@register
class WaterHeater(DeviceType):
    name = "water_heater"
    title = "water heater"
    room = "Climate"
    tool = "water_heater"
    tool_desc = ("The water heater / boiler: set_temperature, set_mode (eco, heat_pump, "
                 "high_demand, off ...) or set_away.")
    ha_domain = "water_heater"
    matter = ("Water Heater",)
    capabilities = ("setpoint", "operation_mode", "away_mode")
    status_fields = ("state", "target", "current", "unit", "away")
    keywords = ("water heater", "hot water", "boiler", "tank", "geyser", "immersion")
    aliases = ("boiler", "hot water", "geyser")
    actions = {
        "set_temperature": Action("set_temperature", "Set the water temperature", (TEMP,),
                                  tier=2, uses="heat"),
        "set_mode": Action("set_mode", "Change the water heater mode", (MODE,), tier=2,
                           uses="heat"),
        "set_away": Action("set_away", "Switch away (vacation) mode", (AWAY,), tier=2),
    }

    def unit(self, cfg):
        return str((cfg or {}).get("unit") or self.cfg.get("unit") or "F").upper()[:1]

    def action_aliases(self):
        return {"set": "set_temperature", "temperature": "set_temperature",
                "mode": "set_mode", "set_operation_mode": "set_mode", "away": "set_away",
                "vacation": "set_away", "set_away_mode": "set_away"}

    def param_aliases(self):
        return {"temp": "temperature", "setpoint": "temperature", "target": "temperature",
                "mode": "operation_mode", "away_mode": "away"}

    def default_action(self, args):
        if args.get("operation_mode") or args.get("mode"):
            return "set_mode"
        if "away" in args or "away_mode" in args:
            return "set_away"
        return "set_temperature"

    def normalise(self, action, params, cfg):
        if action == "set_temperature":
            if "temperature" not in params:
                raise ValueError("set_temperature needs temperature")
            u, t = self.unit(cfg), params["temperature"]
            if u == "C" and t >= 95:
                params["temperature"], params["converted_from"] = f_to_c(t), f"{t:g} F"
            elif u == "F" and t <= 75:
                params["temperature"], params["converted_from"] = c_to_f(t), f"{t:g} C"
            lo, hi = HARD[u]
            ceiling = min(float((cfg or {}).get("max_temp", CEILING[u])), hi)
            t = params["temperature"]
            if not lo <= t <= hi:
                raise ValueError(f"{t:g} {u} is outside the safe range {lo}-{hi} {u}")
            if t > ceiling:
                raise ValueError(f"{t:g} {u} is above this household's limit of {ceiling:g} {u} "
                                 "(scald risk); the owner can change max_temp in house.json")
            params["unit"] = u
        if action == "set_mode" and not params.get("operation_mode"):
            raise ValueError("set_mode needs operation_mode")
        return params

    def interlock(self, action, params, status, cfg):
        why = super().interlock(action, params, status, cfg)
        if why:
            return why
        ops = (cfg or {}).get("operation_list")
        if action == "set_mode" and ops and params["operation_mode"] not in ops:
            return f"this water heater's modes are: {', '.join(ops)}"
        return None

    def confirmation(self, action, params, nick):
        if action == "set_temperature":
            return f"Set the {nick} to {params['temperature']:g} {params['unit']}? It uses heat."
        if action == "set_mode":
            return f"Switch the {nick} to {params['operation_mode']}?"
        return f"Turn away mode {'on' if params.get('away') else 'off'} on the {nick}?"

    def sim_init(self, cfg):
        u = self.unit(cfg)
        return {"state": "eco", "target": 120 if u == "F" else 50,
                "current": 118 if u == "F" else 48, "unit": u, "away": False}

    def sim_apply(self, st, action, params, now, speed):
        if action == "set_temperature":
            st["target"] = params["temperature"]
        elif action == "set_mode":
            st["state"] = params["operation_mode"]
        else:
            st["away"] = bool(params.get("away"))

    def ha_status(self, state, extra):
        if state is None or state.get("state") in ("unavailable", "unknown"):
            return {"state": "offline"}
        a = state.get("attributes") or {}
        return {"state": state["state"], "target": a.get("temperature"),
                "current": a.get("current_temperature"), "unit": self.unit(self.cfg),
                "away": a.get("away_mode") == "on" if a.get("away_mode") else None}

    def ha_ops(self, action, params, entity, cfg):
        if action == "set_temperature":
            return [{"service": "water_heater.set_temperature",
                     "data": {"entity_id": entity, "temperature": params["temperature"]}}]
        if action == "set_mode":
            return [{"service": "water_heater.set_operation_mode",
                     "data": {"entity_id": entity, "operation_mode": params["operation_mode"]}}]
        return [{"service": "water_heater.set_away_mode",
                 "data": {"entity_id": entity, "away_mode": bool(params.get("away"))}}]

    def mqtt_state_topics(self, prefix):
        return {"mode": f"{prefix}/mode/state", "target": f"{prefix}/target_temperature/state",
                "current": f"{prefix}/current_temperature/state", "away": f"{prefix}/away/state"}

    def mqtt_status(self, v):
        if not v.get("mode"):
            return {"state": "offline"}
        num = (lambda x: float(x) if x not in (None, "", "nan") else None)
        return {"state": v["mode"].lower(), "target": num(v.get("target")),
                "current": num(v.get("current")), "unit": self.unit(self.cfg),
                "away": (v.get("away") or "").upper() in ("ON", "TRUE", "1")}

    def mqtt_ops(self, action, params, prefix):
        if action == "set_temperature":
            return [(f"{prefix}/target_temperature/command", f"{params['temperature']:g}")]
        if action == "set_mode":
            return [(f"{prefix}/mode/command", params["operation_mode"])]
        return [(f"{prefix}/away/command", "ON" if params.get("away") else "OFF")]

    def describe_status(self, st):
        if st.get("state") == "offline":
            return "offline"
        return (f"{st.get('state')}, set to {st.get('target')} {st.get('unit') or ''}, tank "
                f"{st.get('current')}" + (", away mode" if st.get("away") else "")).strip()
