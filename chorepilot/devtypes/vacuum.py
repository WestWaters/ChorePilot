"""Robot vacuums (tier 1). "Cleaning ended back on the dock" is the `finished` event that
verifies a chore like "vacuum the living room".

HA: vacuum.start / pause / stop / return_to_base; states cleaning, docked, returning, paused,
    idle, error -- https://www.home-assistant.io/integrations/vacuum/
    iRobot Roomba: local through HA's roomba integration (no official public cloud API);
    Roborock, Ecovacs, Dreame, Neato, SmartThings: their HA integrations.
Matter: Robotic Vacuum Cleaner (1.2+).
"""
from __future__ import annotations

from . import register
from .base import Action, DeviceType, Param, human_minutes

MIN = Param("minutes", "int", "simulator only: how long the run takes", lo=5, hi=240)


@register
class Vacuum(DeviceType):
    name = "vacuum"
    title = "robot vacuum"
    room = "Living room"
    tool = "vacuum"
    tool_desc = "The robot vacuum: start cleaning, pause, or send it back to the dock."
    ha_domain = "vacuum"
    matter = ("Robotic Vacuum Cleaner",)
    capabilities = ("start", "pause", "dock", "battery")
    status_fields = ("state", "battery", "remaining")
    keywords = ("vacuum", "hoover", "roomba", "clean the floor", "floors", "robot")
    aliases = ("roomba", "robot vacuum", "hoover")
    actions = {
        "start": Action("start", "Start cleaning", (), tier=1),
        "pause": Action("pause", "Pause cleaning", (), tier=1),
        "dock": Action("dock", "Send it back to the dock", (), tier=1),
    }
    events_emitted = ("started", "finished")

    def action_aliases(self):
        return {"clean": "start", "go": "start", "return": "dock", "return_to_base": "dock",
                "home": "dock", "stop": "dock"}

    def interlock(self, action, params, status, cfg):
        why = super().interlock(action, params, status, cfg)
        if why:
            return why
        if action == "start" and status.get("state") == "cleaning":
            return "the vacuum is already cleaning"
        if action == "start" and (status.get("battery") or 100) < 15:
            return f"the vacuum's battery is at {status.get('battery')}%; let it charge"
        return None

    def events(self, prev, cur):
        p, c = prev.get("state"), cur.get("state")
        if p != "cleaning" and c == "cleaning":
            return ["started"]
        if p in ("cleaning", "returning") and c == "docked":
            return ["finished"]
        return []

    def sim_init(self, cfg):
        return {"state": "docked", "battery": 100, "end": None,
                "run_minutes": int((cfg or {}).get("run_minutes", 45))}

    def sim_apply(self, st, action, params, now, speed):
        if action == "start":
            st.update(state="cleaning", end=now + st.get("run_minutes", 45) * 60 / speed)
        elif action == "pause":
            st.update(state="paused", end=None)
        else:
            st.update(state="docked", end=None)

    def sim_tick(self, st, now, speed):
        if st["state"] == "cleaning" and now >= (st.get("end") or 0):
            st.update(state="docked", end=None, battery=max(20, st.get("battery", 100) - 35))

    def sim_status(self, st, now, speed):
        self.sim_tick(st, now, speed)
        rem = human_minutes((st["end"] - now) * speed / 60) if st.get("end") else None
        return {"state": st["state"], "battery": st.get("battery"), "remaining": rem}

    def ha_status(self, state, extra):
        if state is None or state.get("state") in ("unavailable", "unknown"):
            return {"state": "offline"}
        a = state.get("attributes") or {}
        return {"state": state["state"], "battery": a.get("battery_level"), "remaining": None}

    def ha_ops(self, action, params, entity, cfg):
        svc = {"start": "vacuum.start", "pause": "vacuum.pause", "dock": "vacuum.return_to_base"}[action]
        return [{"service": svc, "data": {"entity_id": entity}}]

    def mqtt_state_topics(self, prefix):
        return {"state": f"{prefix}/state", "battery": f"{prefix}/battery/state"}

    def mqtt_status(self, v):
        if not v.get("state"):
            return {"state": "offline"}
        return {"state": v["state"].lower(), "battery": int(float(v["battery"])) if v.get("battery") else None}

    def mqtt_ops(self, action, params, prefix):
        return [(f"{prefix}/command", {"start": "start", "pause": "pause", "dock": "return_to_base"}[action])]
