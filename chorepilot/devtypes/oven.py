"""Ovens and ranges: status any time, preheat only with a STRONG confirmation.

Preheating is tier 2 with confirm="strong": the adult has to type the word PREHEAT (a y/N is not
enough, and --yes does not pre-confirm it). Every preheat also carries an auto-off duration
(default 60 min, cap "max_minutes", default 180) and a temperature cap ("max_temp", default
480 F / 250 C). The oven's own interlocks always win: door open, a program already running, or
remote start not enabled on the appliance -> refused. Cooktops/hobs are deliberately NOT a
controllable type: ChorePilot reads them, never switches a burner on (see docs/DEVICES.md).

Home Connect (https://api-docs.home-connect.com/programs-and-options/):
  program  Cooking.Oven.Program.HeatingMode.<PreHeating|HotAir|TopBottomHeating|PizzaSetting>
  options  Cooking.Oven.Option.SetpointTemperature (C), BSH.Common.Option.Duration (s),
           Cooking.Oven.Option.FastPreHeat (bool)
  status   BSH.Common.Status.DoorState, BSH.Common.Status.OperationState,
           BSH.Common.Status.RemoteControlStartAllowed, Cooking.Oven.Status.CurrentCavityTemperature
  event    Cooking.Oven.Event.PreheatFinished
  Before any start Home Connect wants: connected, RemoteControlActive, RemoteControlStartAllowed
  (set at the oven, typically expires after ~24 h), LocalControlActive false, state Ready.
Matter: Oven (1.4 appliance types: Oven, Cooktop, Cook Surface, Extractor Hood, Microwave Oven).
"""
from __future__ import annotations

from . import register
from .base import Action, DeviceType, Param, human_minutes

HM = "Cooking.Oven.Program.HeatingMode."
MODES = {"bake": HM + "TopBottomHeating", "convection": HM + "HotAir",
         "pizza": HM + "PizzaSetting", "preheat": HM + "PreHeating"}
TEMP = Param("temperature", "number", "oven temperature (Fahrenheit unless the house uses Celsius)",
             required=True)
MODE = Param("mode", "enum", "bake, convection or pizza", values=("bake", "convection", "pizza"),
             default="bake", aliases={"fan": "convection", "hot_air": "convection",
                                      "conventional": "bake", "top_bottom": "bake", "roast": "bake"})
DUR = Param("duration_minutes", "int", "turn the oven off automatically after this many minutes",
            lo=5, hi=180, default=60)


@register
class Oven(DeviceType):
    name = "oven"
    title = "oven"
    room = "Kitchen"
    tool = "oven"
    tool_desc = ("Preheat the oven (temperature, optional mode and duration_minutes) or turn it "
                 "off. Preheating needs the person to type PREHEAT to confirm.")
    ha_domain = "select"
    matter = ("Oven", "Cooktop (read-only here)", "Microwave Oven")
    capabilities = ("preheat", "setpoint", "auto_off", "door_state", "cavity_temperature")
    status_fields = ("state", "target", "current", "unit", "door", "remote_start_allowed", "remaining")
    keywords = ("oven", "preheat", "bake", "roast", "range", "stove")
    hc_type = "Oven"
    actions = {
        "preheat": Action("preheat", "Preheat the oven", (TEMP, MODE, DUR), tier=2, uses="heat",
                          confirm="strong", phrase="PREHEAT"),
        "turn_off": Action("turn_off", "Turn the oven off", (), tier=1),
    }
    events_emitted = ("started", "preheated", "finished")

    def unit(self, cfg):
        return str((cfg or {}).get("unit") or self.cfg.get("unit") or "F").upper()[:1]

    def action_aliases(self):
        return {"heat": "preheat", "start": "preheat", "on": "preheat", "bake": "preheat",
                "off": "turn_off", "stop": "turn_off", "cancel": "turn_off"}

    def param_aliases(self):
        return {"temp": "temperature", "degrees": "temperature", "duration": "duration_minutes",
                "minutes": "duration_minutes", "heating_mode": "mode", "program": "mode"}

    def default_action(self, args):
        return "preheat" if args.get("temperature") is not None else "turn_off"

    def limits(self, param, cfg):
        if param == "duration_minutes":
            return 5, int((cfg or {}).get("max_minutes", 180))
        return None, None

    def normalise(self, action, params, cfg):
        if action == "preheat":
            u = self.unit(cfg)
            cap = float((cfg or {}).get("max_temp") or (480 if u == "F" else 250))
            lo = 150 if u == "F" else 65
            t = params["temperature"]
            if u == "C" and t > 260:                     # "350" in a Celsius house = Fahrenheit
                params["temperature"], params["converted_from"] = round((t - 32) * 5 / 9), f"{t:g} F"
            if not lo <= params["temperature"] <= cap:
                raise ValueError(f"oven temperature must be {lo:g}-{cap:g} {u}")
            params["unit"] = u
        return params

    def interlock(self, action, params, status, cfg):
        why = super().interlock(action, params, status, cfg)
        if why or action != "preheat":
            return why
        if (cfg or {}).get("allow_remote_preheat") is False:
            return "remote preheating is switched off for this oven in house.json"
        if status.get("door") == "open":
            return "the oven door is open; close it first"
        if status.get("state") in ("heating", "preheated", "running"):
            return "the oven is already on; turn it off first"
        if status.get("remote_control") is False:
            return "remote control is switched off for the oven in the Home Connect app"
        if status.get("local_control"):
            return "someone is using the oven's own controls right now"
        if status.get("remote_start_allowed") is False:
            return ("remote start is not enabled on the oven; enable it at the oven (a deliberate "
                    "safety step we never bypass)")
        return None

    def confirmation(self, action, params, nick):
        if action == "preheat":
            return (f"Preheat the {nick} to {params['temperature']:g} {params['unit']} "
                    f"({params['mode']}), switching off by itself after "
                    f"{params['duration_minutes']} min? Make sure nothing is on or in it.")
        return super().confirmation(action, params, nick)

    def events(self, prev, cur):
        p, c = prev.get("state"), cur.get("state")
        out = []
        if p in (None, "off", "ready") and c in ("heating", "preheated"):
            out.append("started")
        if p == "heating" and c == "preheated":
            out.append("preheated")
        if p in ("heating", "preheated") and c in ("off", "ready"):
            out.append("finished")
        return out

    # simulator: ~12 simulated minutes to temperature, then holds until auto-off
    def sim_init(self, cfg):
        u = self.unit(cfg)
        room = 70 if u == "F" else 21
        return {"state": "off", "target": None, "current": room, "room": room, "unit": u,
                "door": "closed", "remote_start_allowed": True, "t0": None, "end": None}

    def sim_apply(self, st, action, params, now, speed):
        if action == "preheat":
            st.update(state="heating", target=params["temperature"], t0=now, mode=params["mode"],
                      end=now + params["duration_minutes"] * 60 / speed)
        else:
            st.update(state="off", target=None, end=None, t0=None, current=st["room"])

    def sim_tick(self, st, now, speed):
        if st["state"] in ("heating", "preheated"):
            if now >= st["end"]:
                st.update(state="off", target=None, end=None, current=st["room"])
                return
            mins = (now - st["t0"]) * speed / 60
            frac = min(1.0, mins / 12)
            st["current"] = round(st["room"] + (st["target"] - st["room"]) * frac)
            if frac >= 1:
                st["state"] = "preheated"

    def sim_status(self, st, now, speed):
        self.sim_tick(st, now, speed)
        rem = human_minutes((st["end"] - now) * speed / 60) if st.get("end") else None
        return {"state": st["state"], "target": st.get("target"), "current": st.get("current"),
                "unit": st["unit"], "door": st.get("door"),
                "remote_start_allowed": st.get("remote_start_allowed"), "remaining": rem}

    # Home Connect
    def hc_status(self, status, settings, active):
        op = str(status.get("BSH.Common.Status.OperationState", "")).rsplit(".", 1)[-1].lower()
        door = str(status.get("BSH.Common.Status.DoorState", "")).rsplit(".", 1)[-1].lower() or None
        cur = status.get("Cooking.Oven.Status.CurrentCavityTemperature")
        state = {"run": "heating", "ready": "off", "inactive": "off", "finished": "off",
                 "delayedstart": "heating"}.get(op, op or "offline")
        target = None
        for o in (active or {}).get("options", []):
            if o.get("key") == "Cooking.Oven.Option.SetpointTemperature":
                target = o.get("value")
        if state == "heating" and target and cur and cur >= target - 2:
            state = "preheated"
        return {"state": state, "door": door, "current": cur, "target": target, "unit": "C",
                "remote_start_allowed": status.get("BSH.Common.Status.RemoteControlStartAllowed"),
                "remote_control": status.get("BSH.Common.Status.RemoteControlActive"),
                "local_control": status.get("BSH.Common.Status.LocalControlActive")}

    def hc_ops(self, action, params, cfg):
        if action == "turn_off":
            return [{"method": "DELETE", "path": "/programs/active", "body": None}]
        t = params["temperature"]
        c = round((t - 32) * 5 / 9) if params["unit"] == "F" else round(t)
        opts = [{"key": "Cooking.Oven.Option.SetpointTemperature", "value": c, "unit": "°C"},
                {"key": "BSH.Common.Option.Duration", "value": params["duration_minutes"] * 60,
                 "unit": "seconds"}]
        if c >= 100:                     # FastPreHeat only works from 100 C / 212 F
            opts.append({"key": "Cooking.Oven.Option.FastPreHeat", "value": True})
        return [{"method": "PUT", "path": "/programs/active",
                 "body": {"data": {"key": MODES[params["mode"]], "options": opts}}}]

    # Home Assistant (home_connect integration)
    def ha_status(self, state, extra):
        if state is None or state.get("state") in ("unavailable", "unknown"):
            return {"state": "offline"}
        op = str(state["state"]).lower().replace("_", "")
        door = (extra or {}).get("door")
        rs = (extra or {}).get("remote_start")
        return {"state": {"run": "heating", "ready": "off", "inactive": "off",
                          "finished": "off"}.get(op, op),
                "door": {"on": "open", "off": "closed"}.get((door or {}).get("state")),
                "remote_start_allowed": {"on": True, "off": False}.get((rs or {}).get("state"))}

    def ha_ops(self, action, params, entity, cfg):
        from ..appliance import ha_key
        if action == "turn_off":
            return [{"service": "button.press",
                     "data": {"entity_id": (cfg or {}).get("stop_entity", "button.oven_stop_program")}}]
        dev = (cfg or {}).get("device_id")
        if not dev:
            raise ValueError("set device_id for the oven in house.json (HA > Settings > Devices)")
        t = params["temperature"]
        c = round((t - 32) * 5 / 9) if params["unit"] == "F" else round(t)
        return [{"service": "home_connect.set_program_and_options",
                 "data": {"device_id": dev, "affects_to": "active_program",
                          "program": ha_key(MODES[params["mode"]]),
                          ha_key("Cooking.Oven.Option.SetpointTemperature"): c,
                          ha_key("BSH.Common.Option.Duration"): params["duration_minutes"] * 60}}]

    def describe_status(self, st):
        if st.get("state") in ("heating", "preheated"):
            return (f"{st['state']} to {st.get('target')} {st.get('unit')}, now {st.get('current')}, "
                    f"off in {st.get('remaining')}")
        return st.get("state", "?")

    # dashboard
    def ui_active(self, st):
        return st.get("state") in ("heating", "preheated", "preheating")

    def ui_primary(self, st, cfg=None):
        if self.ui_active(st):
            return {"action": "turn_off", "label": "Turn the oven off", "params": {}, "tier": 1}
        return None

    def ui_state(self, st):
        s = st.get("state")
        if s in ("offline", "unavailable"):
            return "Not responding"
        if s == "heating":
            return f"Preheating to {st.get('target'):g}°, {st.get('current'):g}° now" \
                if st.get("target") is not None and st.get("current") is not None else "Heating"
        if s == "preheated":
            return f"Ready at {st['target']:g}°" if st.get("target") is not None else "Preheated"
        return "Off"
