"""EV chargers (wallboxes).

Starting a charge and changing the current limit are tier 2 (large energy: an adult confirms;
a kid's request goes to a parent); stopping is tier 1. The current limit is always clamped to
the charger's configured range (house.json "min_current" / "max_current", default 6-32 A), so
a model can never ask for more than the circuit was installed for.

HA: the charging switch (switch.turn_on / turn_off; Matter EVSE maps EnableCharging / Disable
    to a switch) + optional entities: "current" (number.set_value), "power" (sensor, kW or W),
    "energy" (sensor, session kWh), "plug" (binary_sensor, car connected)
    Wallbox, Tesla Wall Connector, Ohme, Easee, go-e, Zaptec integrations; OCPP via the
    community lbbrhzn/ocpp integration.  https://www.home-assistant.io/integrations/wallbox/
Matter: EVSE 0x050C (1.3).
MQTT: <prefix>/charging/command ON|OFF, <prefix>/current/command <amps>;
      <prefix>/charging/state, <prefix>/current/state, <prefix>/plug/state, <prefix>/power/state.
"""
from __future__ import annotations

from . import register
from .base import Action, DeviceType, Param

AMPS = Param("current", "int", "charging current limit in amps", lo=6, hi=80)


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


@register
class EVCharger(DeviceType):
    name = "ev_charger"
    title = "EV charger"
    room = "Outside"
    tool = "ev_charger"
    tool_desc = "The EV charger: start_charging, stop_charging, or set_current (amps)."
    ha_domain = "switch"
    matter = ("EVSE",)
    capabilities = ("start_stop", "current_limit", "power", "session_energy")
    status_fields = ("state", "current", "power_kw", "session_kwh")
    keywords = ("charge", "charging", "charger", "ev", "car", "wallbox", "tesla", "amps")
    aliases = ("car charger", "wallbox", "charger", "ev")
    actions = {
        "start_charging": Action("start_charging", "Start charging the car", (AMPS,), tier=2,
                                 uses="a lot of electricity"),
        "stop_charging": Action("stop_charging", "Stop charging", (), tier=1),
        "set_current": Action("set_current", "Change the charging current limit", (AMPS,),
                              tier=2, uses="a lot of electricity"),
    }
    events_emitted = ("started", "finished")

    def action_aliases(self):
        return {"start": "start_charging", "charge": "start_charging", "on": "start_charging",
                "stop": "stop_charging", "off": "stop_charging", "pause": "stop_charging",
                "current": "set_current", "set_amps": "set_current", "limit": "set_current"}

    def param_aliases(self):
        return {"amps": "current", "amperage": "current", "current_limit": "current", "a": "current"}

    def default_action(self, args):
        return "set_current" if args.get("current") or args.get("amps") else "start_charging"

    def limits(self, param, cfg):
        if param == "current":
            return int((cfg or {}).get("min_current", 6)), int((cfg or {}).get("max_current", 32))
        return None, None

    def normalise(self, action, params, cfg):
        if action == "set_current" and "current" not in params:
            raise ValueError("set_current needs current (amps)")
        return params

    def interlock(self, action, params, status, cfg):
        why = super().interlock(action, params, status, cfg)
        if why:
            return why
        st = status.get("state")
        if action == "start_charging" and st == "charging":
            return "the car is already charging"
        if action == "start_charging" and st == "disconnected":
            return "no car is plugged in"
        if action == "stop_charging" and st != "charging":
            return "the car is not charging"
        if status.get("fault"):
            return f"the charger reports a fault ({status['fault']}); check it before charging"
        return None

    def confirmation(self, action, params, nick):
        amps = f" at {params['current']} A" if params.get("current") else ""
        if action == "start_charging":
            return f"Start charging the car on the {nick}{amps}? It uses a lot of electricity."
        if action == "set_current":
            return f"Set the {nick} to {params['current']} A?"
        return super().confirmation(action, params, nick)

    def events(self, prev, cur):
        p, c = prev.get("state"), cur.get("state")
        if p != "charging" and c == "charging":
            return ["started"]
        if p == "charging" and c in ("connected", "idle", "disconnected", "full"):
            return ["finished"]
        return []

    # simulator: a car is plugged in; 7.4 kW at 32 A, scaled by current; stops at target_kwh
    def sim_init(self, cfg):
        return {"state": "connected", "current": int((cfg or {}).get("max_current", 32)),
                "session_kwh": 0.0, "t": None,
                "target_kwh": float((cfg or {}).get("sim_target_kwh", 20))}

    def _kw(self, st):
        return round(230 * st.get("current", 16) / 1000, 2)

    def sim_apply(self, st, action, params, now, speed):
        self.sim_tick(st, now, speed)
        if params.get("current"):
            st["current"] = int(params["current"])
        if action == "start_charging":
            st.update(state="charging", session_kwh=0.0, t=now)
        elif action == "stop_charging":
            st.update(state="connected")

    def sim_tick(self, st, now, speed):
        if st.get("state") == "charging" and st.get("t") is not None:
            st["session_kwh"] = round(st["session_kwh"] + self._kw(st) * (now - st["t"]) * speed
                                      / 3600, 2)
            if st["session_kwh"] >= st.get("target_kwh", 20):
                st.update(state="full", session_kwh=st.get("target_kwh", 20))
        st["t"] = now

    def sim_status(self, st, now, speed):
        self.sim_tick(st, now, speed)
        return {"state": st["state"], "current": st.get("current"),
                "power_kw": self._kw(st) if st["state"] == "charging" else 0.0,
                "session_kwh": st.get("session_kwh")}

    # Home Assistant
    def ha_status(self, state, extra):
        if state is None or state.get("state") in ("unavailable", "unknown"):
            return {"state": "offline"}
        extra = extra or {}
        plug = extra.get("plug")
        on = state["state"] == "on"
        st = "charging" if on else ("disconnected" if plug and plug.get("state") == "off"
                                    else "connected")
        power = _num((extra.get("power") or {}).get("state"))
        unit = ((extra.get("power") or {}).get("attributes") or {}).get("unit_of_measurement")
        if power is not None and unit == "W":
            power = round(power / 1000, 2)
        cur = _num((extra.get("current") or {}).get("state"))
        return {"state": st, "current": int(cur) if cur is not None else None, "power_kw": power,
                "session_kwh": _num((extra.get("energy") or {}).get("state"))}

    def ha_ops(self, action, params, entity, cfg):
        cur_ent = ((cfg or {}).get("entities") or {}).get("current")
        ops = []
        if params.get("current"):
            if not cur_ent:
                if action == "set_current":
                    raise ValueError("this charger has no current-limit entity configured "
                                     "(house.json entities.current)")
            else:
                ops.append({"service": "number.set_value",
                            "data": {"entity_id": cur_ent, "value": params["current"]}})
        if action == "start_charging":
            ops.append({"service": "switch.turn_on", "data": {"entity_id": entity}})
        elif action == "stop_charging":
            ops.append({"service": "switch.turn_off", "data": {"entity_id": entity}})
        return ops

    # MQTT
    def mqtt_state_topics(self, prefix):
        return {"charging": f"{prefix}/charging/state", "current": f"{prefix}/current/state",
                "plug": f"{prefix}/plug/state", "power": f"{prefix}/power/state"}

    def mqtt_status(self, v):
        if not v.get("charging"):
            return {"state": "offline"}
        on = v["charging"].upper() in ("ON", "TRUE", "1")
        plug = (v.get("plug") or "").upper()
        cur = _num(v.get("current"))
        return {"state": "charging" if on else ("disconnected" if plug in ("OFF", "0") else
                                                 "connected"),
                "current": int(cur) if cur is not None else None,
                "power_kw": _num(v.get("power")), "session_kwh": None}

    def mqtt_ops(self, action, params, prefix):
        ops = []
        if params.get("current"):
            ops.append((f"{prefix}/current/command", str(params["current"])))
        if action == "start_charging":
            ops.append((f"{prefix}/charging/command", "ON"))
        elif action == "stop_charging":
            ops.append((f"{prefix}/charging/command", "OFF"))
        return ops

    def describe_status(self, st):
        bits = [st.get("state", "?")]
        if st.get("current"):
            bits.append(f"{st['current']} A")
        if st.get("state") == "charging" and st.get("power_kw"):
            bits.append(f"{st['power_kw']} kW")
        if st.get("session_kwh"):
            bits.append(f"{st['session_kwh']} kWh this session")
        return ", ".join(bits)

    # dashboard
    def ui_active(self, st):
        return st.get("state") == "charging"

    def ui_state(self, st):
        s = st.get("state")
        if s in ("offline", "unavailable"):
            return "Not responding"
        if s == "charging":
            kw = f", {st['power_kw']:g} kW" if st.get("power_kw") else ""
            return f"Charging{kw}"
        return {"connected": "Plugged in", "full": "Charged", "disconnected": "Unplugged"}.get(
            s, str(s or "unknown").replace("_", " ").capitalize())
