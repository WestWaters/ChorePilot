"""Lights: the smallest complete device type (tier 1).

HA: light.turn_on {brightness_pct, color_temp_kelvin}, light.turn_off
    https://www.home-assistant.io/integrations/light/
Matter: On/Off Light, Dimmable Light, Color Temperature Light, Extended Color Light.
ESPHome MQTT: <prefix>/light/<id>/command with JSON {"state": "ON", "brightness": 0-255}.
"""
from __future__ import annotations

import json

from . import register
from .base import Action, DeviceType, Param

BRIGHT = Param("brightness", "int", "brightness in percent (1-100)", lo=1, hi=100)
KELVIN = Param("color_temp_kelvin", "int", "white colour temperature in kelvin (2000-6500)",
               lo=2000, hi=6500)


@register
class Light(DeviceType):
    name = "light"
    title = "light"
    room = "Living room"
    tool = "set_light"
    tool_desc = "Turn a light on or off, or dim it."
    ha_domain = "light"
    matter = ("On/Off Light", "Dimmable Light", "Color Temperature Light", "Extended Color Light")
    capabilities = ("on_off", "brightness", "color_temp")
    status_fields = ("state", "brightness")
    keywords = ("light", "lights", "lamp", "dim", "bright", "dark")
    actions = {
        "turn_on": Action("turn_on", "Turn the light on", (BRIGHT, KELVIN), tier=1),
        "turn_off": Action("turn_off", "Turn the light off", (), tier=1),
    }

    def action_aliases(self):
        return {"on": "turn_on", "off": "turn_off", "dim": "turn_on", "set": "turn_on",
                "switch_on": "turn_on", "switch_off": "turn_off"}

    def param_aliases(self):
        return {"brightness_pct": "brightness", "level": "brightness", "kelvin": "color_temp_kelvin"}

    def default_action(self, args):
        if str(args.get("state", "")).lower() in ("off", "false", "0"):
            return "turn_off"
        return "turn_on"

    def events(self, prev, cur):
        if prev.get("state") != cur.get("state") and cur.get("state") in ("on", "off"):
            return [f"turned_{cur['state']}"]
        return []

    # simulator
    def sim_init(self, cfg):
        return {"state": "off", "brightness": None}

    def sim_apply(self, st, action, params, now, speed):
        if action == "turn_on":
            st.update(state="on", brightness=params.get("brightness") or st.get("brightness") or 100)
        else:
            st.update(state="off", brightness=None)

    # Home Assistant
    def ha_status(self, state, extra):
        if state is None or state.get("state") in ("unavailable", "unknown"):
            return {"state": "offline"}
        b = (state.get("attributes") or {}).get("brightness")
        return {"state": state["state"], "brightness": round(b * 100 / 255) if b else None}

    def ha_ops(self, action, params, entity, cfg):
        if action == "turn_off":
            return [{"service": "light.turn_off", "data": {"entity_id": entity}}]
        data = {"entity_id": entity}
        if params.get("brightness"):
            data["brightness_pct"] = params["brightness"]
        if params.get("color_temp_kelvin"):
            data["color_temp_kelvin"] = params["color_temp_kelvin"]
        return [{"service": "light.turn_on", "data": data}]

    # MQTT (ESPHome JSON light schema)
    def mqtt_state_topics(self, prefix):
        return {"json": f"{prefix}/state"}

    def mqtt_status(self, vals):
        try:
            js = json.loads(vals.get("json") or "")
        except ValueError:
            return {"state": "offline"}
        b = js.get("brightness")
        return {"state": str(js.get("state", "")).lower() or "offline",
                "brightness": round(b * 100 / 255) if b else None}

    def mqtt_ops(self, action, params, prefix):
        body = {"state": "ON" if action == "turn_on" else "OFF"}
        if action == "turn_on" and params.get("brightness"):
            body["brightness"] = round(params["brightness"] * 255 / 100)
        return [(f"{prefix}/command", json.dumps(body))]
