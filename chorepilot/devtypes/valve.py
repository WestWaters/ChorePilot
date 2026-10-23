"""Sprinklers, irrigation zones and water valves.

Watering is tier 2 (water): an adult confirms, kids ask a parent. Every run has a duration, capped
by house.json "max_minutes" (default 60), and the close is always scheduled when the valve opens,
so a crash in the middle never leaves the lawn flooding. Stopping is tier 1.

HA: valve.open_valve / valve.close_valve (states open, closed, opening, closing)
      https://www.home-assistant.io/integrations/valve/
    or switch.turn_on / turn_off for zone switches (OpenSprinkler, ESPHome sprinkler),
    or a controller's own timed service, configured as "duration_service", e.g.
    {"service": "rachio.start_watering", "field": "duration", "unit": "min"}
Matter: Water Valve (1.3+). Vendors: Rachio public API (zone start with duration), Rain Bird,
OpenSprinkler, Orbit B-hyve -- through HA.
ESPHome MQTT: <prefix>/command ON/OFF, <prefix>/state.
A water-main shutoff is the same device class at tier 3 (see DEVICES.md): re-opening a main
after a leak alarm is a security-tier action; configure it as type "valve" with "tier_open": 3.
"""
from __future__ import annotations

from . import register
from .base import Action, DeviceType, Param, human_minutes

MINUTES = Param("minutes", "int", "how long to water, in minutes", lo=1, hi=240, default=10)


@register
class Valve(DeviceType):
    name = "valve"
    title = "sprinkler"
    room = "Outside"
    tool = "water_zone"
    tool_desc = "Water the lawn or garden: action water with minutes, or stop."
    ha_domain = "valve"
    matter = ("Water Valve",)
    capabilities = ("open_close", "timed_run")
    status_fields = ("state", "remaining")
    keywords = ("water", "watering", "sprinkler", "sprinklers", "lawn", "garden", "irrigate",
                "irrigation", "plants", "yard", "valve")
    aliases = ("sprinkler", "sprinklers", "lawn", "irrigation", "garden")
    actions = {
        "water": Action("water", "Open the valve and water", (MINUTES,), tier=2, uses="water"),
        "stop": Action("stop", "Close the valve / stop watering", (), tier=1),
    }
    events_emitted = ("started", "finished")

    def action_aliases(self):
        return {"open": "water", "start": "water", "run": "water", "on": "water",
                "close": "stop", "off": "stop", "cancel": "stop"}

    def param_aliases(self):
        return {"duration": "minutes", "duration_minutes": "minutes", "mins": "minutes",
                "time": "minutes"}

    def limits(self, param, cfg):
        if param == "minutes":
            return 1, int((cfg or {}).get("max_minutes", 60))
        return None, None

    def tier_for(self, action, params, cfg):
        if action == "water":
            return int((cfg or {}).get("tier_open", 2))
        return 1

    def interlock(self, action, params, status, cfg):
        why = super().interlock(action, params, status, cfg)
        if why:
            return why
        if action == "water" and status.get("state") == "watering":
            return (f"the {self.title} is already watering ({status.get('remaining')} left); "
                    "stop it first")
        if action == "stop" and status.get("state") != "watering":
            return "nothing is watering right now"
        if action == "water" and status.get("rain_delay"):
            return "the controller has a rain delay on; watering skipped"
        return None

    def confirmation(self, action, params, nick):
        if action == "water":
            return f"Water with the {nick} for {params['minutes']} minutes? It uses water."
        return super().confirmation(action, params, nick)

    def events(self, prev, cur):
        p, c = prev.get("state"), cur.get("state")
        if p != "watering" and c == "watering":
            return ["started"]
        if p == "watering" and c == "idle":
            return ["finished"]
        return []

    # simulator
    def sim_init(self, cfg):
        return {"state": "idle", "end": None}

    def sim_apply(self, st, action, params, now, speed):
        if action == "water":
            st.update(state="watering", end=now + params["minutes"] * 60 / speed,
                      minutes=params["minutes"])
        else:
            st.update(state="idle", end=None)

    def sim_tick(self, st, now, speed):
        if st.get("state") == "watering" and st.get("end") and now >= st["end"]:
            st.update(state="idle", end=None)

    def sim_status(self, st, now, speed):
        self.sim_tick(st, now, speed)
        rem = secs = None
        if st["state"] == "watering":
            secs = max(0, int((st["end"] - now) * speed))
            rem = human_minutes(secs / 60)
        return {"state": st["state"], "remaining": rem, "remaining_s": secs,
                "duration_s": int(st.get("minutes") or 0) * 60 if secs is not None else None}

    # Home Assistant
    def _domain(self, entity):
        return entity.split(".", 1)[0]

    def ha_status(self, state, extra):
        if state is None or state.get("state") in ("unavailable", "unknown"):
            return {"state": "offline"}
        s = state["state"]
        return {"state": "watering" if s in ("open", "opening", "on") else "idle",
                "remaining": None}

    def ha_ops(self, action, params, entity, cfg):
        dom = self._domain(entity)
        on, off = (("switch.turn_on", "switch.turn_off") if dom == "switch"
                   else ("valve.open_valve", "valve.close_valve"))
        if action == "stop":
            return [{"service": off, "data": {"entity_id": entity}}]
        ds = (cfg or {}).get("duration_service")
        if ds:
            mins = params["minutes"]
            val = mins * 60 if ds.get("unit") == "s" else mins
            return [{"service": ds["service"], "data": {"entity_id": entity,
                                                        ds.get("field", "duration"): val}}]
        return [{"service": on, "data": {"entity_id": entity}},
                {"service": off, "data": {"entity_id": entity}, "delay_s": params["minutes"] * 60,
                 "note": "the close is scheduled now, when the valve opens"}]

    # MQTT
    def mqtt_state_topics(self, prefix):
        return {"state": f"{prefix}/state"}

    def mqtt_status(self, v):
        s = (v.get("state") or "").upper()
        if not s:
            return {"state": "offline"}
        return {"state": "watering" if s in ("ON", "OPEN") else "idle", "remaining": None}

    def mqtt_ops(self, action, params, prefix):
        if action == "stop":
            return [(f"{prefix}/command", "OFF")]
        return [(f"{prefix}/command", "ON"),
                (f"{prefix}/command", "OFF", params["minutes"] * 60)]

    def describe_status(self, st):
        return st.get("state", "?") + (f", {st['remaining']} left" if st.get("remaining") else "")

    # dashboard
    def ui_active(self, st):
        return st.get("state") == "watering"

    def ui_state(self, st):
        if st.get("state") in ("offline", "unavailable"):
            return "Not responding"
        if st.get("state") == "watering":
            return "Watering" + (f", {st['remaining']} left" if st.get("remaining") else "")
        return "Off"
