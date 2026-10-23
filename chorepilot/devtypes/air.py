"""Air purifiers and (de)humidifiers: tier 1 (adults; kids if the household allows tier 1).

Air purifier
  HA: fan.turn_on / turn_off / set_percentage {percentage} / set_preset_mode {preset_mode};
      attributes percentage, preset_mode, preset_modes; optional extra entities "pm25" / "aqi"
      / "filter" (sensors) -- https://www.home-assistant.io/integrations/fan/
  Matter: Air Purifier 0x002D (1.2). Vendors: Levoit/VeSync, Dyson, Philips, Xiaomi, Coway,
      Winix, LG ThinQ -- through HA.
Humidifier / dehumidifier
  HA: humidifier.turn_on / turn_off / set_humidity {humidity} / set_mode {mode}; attributes
      humidity (target), current_humidity, mode, available_modes, device_class
      https://www.home-assistant.io/integrations/humidifier/
  Matter: Humidity Conditioner (newer spec). Target humidity is limited to 30-65 % by default
  (house.json "min_humidity" / "max_humidity"): above ~60 % mould grows, below ~30 % is dry.
MQTT: <prefix>/command ON|OFF, <prefix>/speed/command | <prefix>/humidity/command,
      <prefix>/mode/command; the same names /state.
"""
from __future__ import annotations

from . import register
from .base import Action, DeviceType, Param

SPEED = Param("speed", "int", "fan speed in percent (1-100)", lo=1, hi=100)
PMODE = Param("mode", "string", "a preset mode the purifier offers, e.g. auto, sleep, turbo")
HUM = Param("humidity", "int", "target relative humidity in percent", lo=20, hi=80)
HMODE = Param("mode", "string", "a mode the humidifier offers, e.g. normal, eco, sleep, auto")


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class _OnOff(DeviceType):
    def interlock(self, action, params, status, cfg):
        why = super().interlock(action, params, status, cfg)
        if why:
            return why
        modes = (cfg or {}).get("modes")
        if action == "set_mode" and modes and params.get("mode") not in modes:
            return f"the {self.title}'s modes are: {', '.join(modes)}"
        return None

    def events(self, prev, cur):
        if prev.get("state") != cur.get("state") and cur.get("state") in ("on", "off"):
            return [f"turned_{cur['state']}"]
        return []

    def mqtt_state_topics(self, prefix):
        return {"state": f"{prefix}/state", "level": f"{prefix}/{self._level}/state",
                "mode": f"{prefix}/mode/state"}

    def mqtt_ops(self, action, params, prefix):
        if action in ("turn_on", "turn_off"):
            ops = [(f"{prefix}/command", "ON" if action == "turn_on" else "OFF")]
        else:
            ops = []
        if params.get(self._level_param):
            ops.append((f"{prefix}/{self._level}/command", str(params[self._level_param])))
        if params.get("mode"):
            ops.append((f"{prefix}/mode/command", str(params["mode"])))
        return ops


@register
class AirPurifier(_OnOff):
    name = "air_purifier"
    title = "air purifier"
    room = "Living room"
    tool = "air_purifier"
    tool_desc = "The air purifier: turn_on / turn_off, set_speed (percent) or set_mode (auto, sleep ...)."
    ha_domain = "fan"
    matter = ("Air Purifier",)
    capabilities = ("on_off", "speed", "preset_mode", "air_quality")
    status_fields = ("state", "speed", "mode", "pm25", "filter")
    keywords = ("purifier", "air purifier", "air quality", "pm2.5", "allergies", "smoke smell")
    aliases = ("purifier", "air cleaner")
    actions = {
        "turn_on": Action("turn_on", "Turn the air purifier on", (SPEED, PMODE), tier=1),
        "turn_off": Action("turn_off", "Turn the air purifier off", (), tier=1),
        "set_speed": Action("set_speed", "Set the purifier's fan speed", (SPEED,), tier=1),
        "set_mode": Action("set_mode", "Set the purifier's mode", (PMODE,), tier=1),
    }
    _level, _level_param = "speed", "speed"

    def action_aliases(self):
        return {"on": "turn_on", "off": "turn_off", "speed": "set_speed", "mode": "set_mode",
                "preset": "set_mode", "set_preset_mode": "set_mode", "set_percentage": "set_speed"}

    def param_aliases(self):
        return {"percentage": "speed", "fan_speed": "speed", "preset_mode": "mode", "preset": "mode"}

    def default_action(self, args):
        if str(args.get("state", "")).lower() in ("off", "false", "0"):
            return "turn_off"
        return "turn_on"

    def normalise(self, action, params, cfg):
        if action == "set_speed" and "speed" not in params:
            raise ValueError("set_speed needs speed (percent)")
        if action == "set_mode" and not params.get("mode"):
            raise ValueError("set_mode needs mode")
        return params

    def sim_init(self, cfg):
        return {"state": "off", "speed": None, "mode": None, "pm25": 18, "filter": 72}

    def sim_apply(self, st, action, params, now, speed):
        if action == "turn_off":
            st.update(state="off", speed=None)
            return
        st["state"] = "on"
        st["speed"] = params.get("speed") or st.get("speed") or 50
        if params.get("mode"):
            st["mode"] = params["mode"]

    def ha_status(self, state, extra):
        if state is None or state.get("state") in ("unavailable", "unknown"):
            return {"state": "offline"}
        a = state.get("attributes") or {}
        extra = extra or {}
        pm = extra.get("pm25") or extra.get("aqi")
        return {"state": state["state"], "speed": a.get("percentage"), "mode": a.get("preset_mode"),
                "pm25": _num((pm or {}).get("state")),
                "filter": _num((extra.get("filter") or {}).get("state"))}

    def ha_ops(self, action, params, entity, cfg):
        e = {"entity_id": entity}
        if action == "turn_off":
            return [{"service": "fan.turn_off", "data": e}]
        if action == "set_speed":
            return [{"service": "fan.set_percentage", "data": {**e, "percentage": params["speed"]}}]
        if action == "set_mode":
            return [{"service": "fan.set_preset_mode", "data": {**e, "preset_mode": params["mode"]}}]
        data = dict(e)
        if params.get("speed"):
            data["percentage"] = params["speed"]
        if params.get("mode"):
            data["preset_mode"] = params["mode"]
        return [{"service": "fan.turn_on", "data": data}]

    def mqtt_status(self, v):
        s = (v.get("state") or "").lower()
        if not s:
            return {"state": "offline"}
        sp = _num(v.get("level"))
        return {"state": s, "speed": int(sp) if sp is not None else None, "mode": v.get("mode")}

    def describe_status(self, st):
        bits = [st.get("state", "?")]
        if st.get("speed"):
            bits.append(f"speed {st['speed']}%")
        if st.get("mode"):
            bits.append(f"mode {st['mode']}")
        if st.get("pm25") is not None:
            bits.append(f"PM2.5 {st['pm25']:g}")
        return ", ".join(bits)


@register
class Humidifier(_OnOff):
    name = "humidifier"
    title = "humidifier"
    room = "Bedroom"
    tool = "humidifier"
    tool_desc = ("The humidifier or dehumidifier: turn_on / turn_off, set_humidity (target %) "
                 "or set_mode.")
    ha_domain = "humidifier"
    matter = ("Humidity Conditioner",)
    capabilities = ("on_off", "target_humidity", "mode")
    status_fields = ("state", "target", "current", "mode")
    keywords = ("humidifier", "dehumidifier", "humidity", "humid", "damp", "dry air")
    aliases = ("dehumidifier",)
    actions = {
        "turn_on": Action("turn_on", "Turn the humidifier on", (HUM, HMODE), tier=1),
        "turn_off": Action("turn_off", "Turn the humidifier off", (), tier=1),
        "set_humidity": Action("set_humidity", "Set the target humidity", (HUM,), tier=1),
        "set_mode": Action("set_mode", "Set the humidifier's mode", (HMODE,), tier=1),
    }
    _level, _level_param = "humidity", "humidity"

    def action_aliases(self):
        return {"on": "turn_on", "off": "turn_off", "humidity": "set_humidity",
                "set": "set_humidity", "mode": "set_mode"}

    def param_aliases(self):
        return {"target": "humidity", "target_humidity": "humidity", "percent": "humidity",
                "level": "humidity"}

    def limits(self, param, cfg):
        if param == "humidity":
            return int((cfg or {}).get("min_humidity", 30)), int((cfg or {}).get("max_humidity", 65))
        return None, None

    def default_action(self, args):
        if str(args.get("state", "")).lower() in ("off", "false", "0"):
            return "turn_off"
        if args.get("humidity") not in (None, ""):
            return "set_humidity"
        return "turn_on"

    def normalise(self, action, params, cfg):
        if action == "set_humidity" and "humidity" not in params:
            raise ValueError("set_humidity needs humidity (percent)")
        if action == "set_mode" and not params.get("mode"):
            raise ValueError("set_mode needs mode")
        return params

    def sim_init(self, cfg):
        return {"state": "off", "target": 45, "current": 38, "mode": "normal"}

    def sim_apply(self, st, action, params, now, speed):
        if action == "turn_off":
            st["state"] = "off"
            return
        st["state"] = "on"
        if params.get("humidity"):
            st["target"] = params["humidity"]
        if params.get("mode"):
            st["mode"] = params["mode"]

    def ha_status(self, state, extra):
        if state is None or state.get("state") in ("unavailable", "unknown"):
            return {"state": "offline"}
        a = state.get("attributes") or {}
        return {"state": state["state"], "target": a.get("humidity"),
                "current": a.get("current_humidity"), "mode": a.get("mode")}

    def ha_ops(self, action, params, entity, cfg):
        e = {"entity_id": entity}
        if action == "turn_off":
            return [{"service": "humidifier.turn_off", "data": e}]
        ops = []
        if action == "turn_on":
            ops.append({"service": "humidifier.turn_on", "data": e})
        if params.get("humidity"):
            ops.append({"service": "humidifier.set_humidity",
                        "data": {**e, "humidity": params["humidity"]}})
        if params.get("mode"):
            ops.append({"service": "humidifier.set_mode", "data": {**e, "mode": params["mode"]}})
        return ops

    def mqtt_status(self, v):
        s = (v.get("state") or "").lower()
        if not s:
            return {"state": "offline"}
        t = _num(v.get("level"))
        return {"state": s, "target": int(t) if t is not None else None, "mode": v.get("mode")}

    def describe_status(self, st):
        bits = [st.get("state", "?")]
        if st.get("target") is not None:
            bits.append(f"target {st['target']}%")
        if st.get("current") is not None:
            bits.append(f"room {st['current']}%")
        return ", ".join(bits)
