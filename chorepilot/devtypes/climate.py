"""Thermostats, air conditioners and heat pumps.

Tier: a setpoint inside the household's comfort band (house.json "comfort", default 68-76 F /
20-24.5 C) is tier 1; outside it, tier 2 (an adult confirms; a kid's request goes to a parent).
Hard limits (50-90 F / 10-32 C) are refused outright.

HA: climate.set_temperature {temperature, hvac_mode}, climate.set_hvac_mode, climate.turn_off
    state = the HVAC mode; attributes current_temperature, temperature, hvac_action
    https://www.home-assistant.io/integrations/climate/
Matter: Thermostat, Room Air Conditioner, Heat Pump. Vendors: ecobee, Google Nest (SDM API),
Daikin, Mitsubishi, LG/Samsung ACs -- all reachable through their HA integrations.
ESPHome MQTT: <prefix>/target_temperature/command, <prefix>/mode/command (+ .../state).
"""
from __future__ import annotations

from . import register
from .base import Action, DeviceType, Param

MODES = ("off", "cool", "heat", "heat_cool", "auto", "dry", "fan_only")
MODE_ALIASES = {"ac": "cool", "air_conditioning": "cool", "cooling": "cool", "heating": "heat",
                "warm": "heat", "fan": "fan_only", "dehumidify": "dry", "both": "heat_cool"}
TEMP = Param("temperature", "number", "target temperature, in the thermostat's unit "
             "(Fahrenheit unless the house uses Celsius)")
MODE = Param("hvac_mode", "enum", "cool, heat, heat_cool, auto, dry, fan_only or off",
             values=MODES, aliases=MODE_ALIASES)
HARD = {"F": (50, 90), "C": (10, 32)}
COMFORT = {"F": (68, 76), "C": (20, 24.5)}


def f_to_c(f):
    return round((f - 32) * 5 / 9 * 2) / 2


def c_to_f(c):
    return round(c * 9 / 5 + 32)


@register
class Climate(DeviceType):
    name = "climate"
    title = "thermostat"
    room = "Climate"
    tool = "set_climate"
    tool_desc = ("Set the thermostat / air conditioner: action set_temperature with temperature "
                 "(and optionally hvac_mode), set_mode, or turn_off.")
    ha_domain = "climate"
    matter = ("Thermostat", "Room Air Conditioner", "Heat Pump")
    capabilities = ("setpoint", "hvac_mode", "current_temperature")
    status_fields = ("state", "target", "current", "unit", "hvac_action")
    keywords = ("ac", "a/c", "air", "thermostat", "temperature", "heat", "heating", "cool",
                "cooling", "warm", "cold", "hvac", "degrees", "conditioner")
    aliases = ("ac", "air conditioner", "heating", "hvac")
    actions = {
        "set_temperature": Action("set_temperature", "Set the target temperature",
                                  (TEMP, MODE), tier=1),
        "set_mode": Action("set_mode", "Change the HVAC mode", (MODE,), tier=1),
        "turn_off": Action("turn_off", "Turn heating/cooling off", (), tier=1),
    }

    def unit(self, cfg):
        return str((cfg or {}).get("unit") or self.cfg.get("unit") or "F").upper()[:1]

    def action_aliases(self):
        return {"set": "set_temperature", "set_temp": "set_temperature", "temperature": "set_temperature",
                "mode": "set_mode", "off": "turn_off", "set_hvac_mode": "set_mode"}

    def param_aliases(self):
        return {"temp": "temperature", "setpoint": "temperature", "target": "temperature",
                "target_temperature": "temperature", "mode": "hvac_mode"}

    def default_action(self, args):
        if args.get("temperature") is not None or args.get("temp") is not None:
            return "set_temperature"
        if args.get("hvac_mode") or args.get("mode"):
            return "set_mode"
        return "set_temperature"

    def normalise(self, action, params, cfg):
        if action == "set_temperature":
            if "temperature" not in params:
                raise ValueError("set_temperature needs temperature")
            u, t = self.unit(cfg), params["temperature"]
            if u == "C" and 50 <= t <= 95:          # "set it to 72" in a Celsius house
                params["temperature"], params["converted_from"] = f_to_c(t), f"{t:g} F"
            elif u == "F" and 10 <= t <= 35:        # "set it to 22" in a Fahrenheit house
                params["temperature"], params["converted_from"] = c_to_f(t), f"{t:g} C"
            lo, hi = HARD[u]
            if not lo <= params["temperature"] <= hi:
                raise ValueError(f"{params['temperature']:g} {u} is outside the safe range "
                                 f"{lo}-{hi} {u}")
            params["unit"] = u
        if action == "set_mode" and not params.get("hvac_mode"):
            raise ValueError("set_mode needs hvac_mode")
        return params

    def comfort(self, cfg):
        u = self.unit(cfg)
        band = (cfg or {}).get("comfort") or COMFORT[u]
        return float(band[0]), float(band[1])

    def tier_for(self, action, params, cfg):
        if action == "set_temperature":
            lo, hi = self.comfort(cfg)
            return 1 if lo <= params["temperature"] <= hi else 2
        return 1

    def confirmation(self, action, params, nick):
        if action == "set_temperature":
            return (f"Set the {nick} to {params['temperature']:g} {params['unit']}? That is "
                    "outside the household's comfort band.")
        return super().confirmation(action, params, nick)

    # simulator: the room drifts 1 degree per simulated 10 minutes toward the target
    def sim_init(self, cfg):
        u = self.unit(cfg)
        return {"state": "cool", "target": 74 if u == "F" else 23.5,
                "current": 78 if u == "F" else 25.5, "unit": u, "t": None}

    def sim_apply(self, st, action, params, now, speed):
        self.sim_tick(st, now, speed)
        if action == "set_temperature":
            st["target"] = params["temperature"]
            if params.get("hvac_mode"):
                st["state"] = params["hvac_mode"]
            elif st["state"] == "off":
                st["state"] = "cool" if params["temperature"] < st["current"] else "heat"
        elif action == "set_mode":
            st["state"] = params["hvac_mode"]
        else:
            st["state"] = "off"
        st["t"] = now

    def sim_tick(self, st, now, speed):
        if st.get("t") is None:
            st["t"] = now
            return
        steps = (now - st["t"]) * speed / 600
        if st["state"] != "off" and steps > 0:
            d = st["target"] - st["current"]
            move = min(abs(d), steps)
            st["current"] = round(st["current"] + (move if d > 0 else -move), 1)
        st["t"] = now

    def sim_status(self, st, now, speed):
        self.sim_tick(st, now, speed)
        d = st["target"] - st["current"]
        act = "idle" if st["state"] == "off" or abs(d) < 0.5 else ("heating" if d > 0 else "cooling")
        return {"state": st["state"], "target": st["target"], "current": st["current"],
                "unit": st["unit"], "hvac_action": act}

    # Home Assistant
    def ha_status(self, state, extra):
        if state is None or state.get("state") in ("unavailable", "unknown"):
            return {"state": "offline"}
        a = state.get("attributes") or {}
        return {"state": state["state"], "target": a.get("temperature"),
                "current": a.get("current_temperature"), "hvac_action": a.get("hvac_action"),
                "unit": self.unit(self.cfg)}

    def ha_ops(self, action, params, entity, cfg):
        if action == "turn_off":
            return [{"service": "climate.turn_off", "data": {"entity_id": entity}}]
        if action == "set_mode":
            return [{"service": "climate.set_hvac_mode",
                     "data": {"entity_id": entity, "hvac_mode": params["hvac_mode"]}}]
        data = {"entity_id": entity, "temperature": params["temperature"]}
        if params.get("hvac_mode"):
            data["hvac_mode"] = params["hvac_mode"]
        return [{"service": "climate.set_temperature", "data": data}]

    # MQTT (ESPHome climate)
    def mqtt_state_topics(self, prefix):
        return {"mode": f"{prefix}/mode/state", "target": f"{prefix}/target_temperature/state",
                "current": f"{prefix}/current_temperature/state", "action": f"{prefix}/action/state"}

    def mqtt_status(self, v):
        if not v.get("mode"):
            return {"state": "offline"}
        num = (lambda x: float(x) if x not in (None, "", "nan") else None)
        return {"state": v["mode"].lower(), "target": num(v.get("target")),
                "current": num(v.get("current")), "hvac_action": (v.get("action") or "").lower() or None,
                "unit": self.unit(self.cfg)}

    def mqtt_ops(self, action, params, prefix):
        if action == "turn_off":
            return [(f"{prefix}/mode/command", "OFF")]
        if action == "set_mode":
            return [(f"{prefix}/mode/command", params["hvac_mode"].upper())]
        ops = [(f"{prefix}/target_temperature/command", f"{params['temperature']:g}")]
        if params.get("hvac_mode"):
            ops.insert(0, (f"{prefix}/mode/command", params["hvac_mode"].upper()))
        return ops

    def describe_status(self, st):
        if st.get("state") == "offline":
            return "offline"
        return (f"{st.get('state')}, set to {st.get('target')} {st.get('unit') or ''}, room "
                f"{st.get('current')}").strip()

    # dashboard
    def ui_active(self, st):
        return str(st.get("state") or "") not in ("off", "offline", "unavailable", "")

    def ui_state(self, st):
        s = st.get("state")
        if s in ("offline", "unavailable"):
            return "Not responding"
        now = f"{st['current']:g}° now" if st.get("current") is not None else ""
        if s == "off":
            return "Off" + (f", {now}" if now else "")
        act = st.get("hvac_action")
        tgt = f"{st['target']:g}°" if st.get("target") is not None else ""
        head = {"cooling": f"Cooling to {tgt}", "heating": f"Heating to {tgt}"}.get(
            act, f"{str(s).replace('_', ' ').capitalize()}, holding {tgt}")
        same = st.get("current") is not None and st.get("target") is not None and \
            abs(st["current"] - st["target"]) < 0.5
        return head + (f", {now}" if now and not same else "")
