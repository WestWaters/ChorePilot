"""Alarm panels. ARMING is tier 1 (it makes the house safer); DISARMING is tier 3.

Tier 3 means: the owner only, plus a second factor typed by the person (ChorePilot PIN or TOTP),
never from an external agent (MCP), never on a schedule. The panel's OWN code, when HA needs one
to arm or disarm, lives in house.json ("alarm_code") or secrets.json and is never shown to the
model; the model can never pass a code.

HA: alarm_control_panel.alarm_arm_home / alarm_arm_away / alarm_arm_night / alarm_disarm
    {code}; states disarmed, armed_home, armed_away, armed_night, armed_vacation,
    armed_custom_bypass, pending, arming, disarming, triggered
    https://www.home-assistant.io/integrations/alarm_control_panel/
MQTT (HA's MQTT alarm convention): <prefix>/command ARM_HOME | ARM_AWAY | ARM_NIGHT | DISARM,
    <prefix>/state.
Vendors: Ring Alarm, SimpliSafe, Abode, Ajax, Alarm.com, Konnected, Envisalink -- through HA.
"""
from __future__ import annotations

from . import register
from .base import Action, DeviceType
from ..env import secret

ARMED = ("armed_home", "armed_away", "armed_night", "armed_vacation", "armed_custom_bypass")


@register
class AlarmPanel(DeviceType):
    name = "alarm_panel"
    title = "alarm"
    room = "Entry"
    tool = "alarm_panel"
    tool_desc = ("Arm the house alarm (home, away or night) or disarm it. Disarming is owner-only "
                 "and the owner enters their PIN at the prompt: never ask for or pass a code.")
    ha_domain = "alarm_control_panel"
    capabilities = ("arm_home", "arm_away", "arm_night", "disarm")
    status_fields = ("state", "changed_by")
    keywords = ("alarm", "arm", "disarm", "security system", "burglar")
    aliases = ("alarm", "security", "security system")
    actions = {
        "arm_away": Action("arm_away", "Arm the alarm (away)", (), tier=1),
        "arm_home": Action("arm_home", "Arm the alarm (home / stay)", (), tier=1),
        "arm_night": Action("arm_night", "Arm the alarm (night)", (), tier=1),
        "disarm": Action("disarm", "Disarm the alarm", (), tier=3),
    }
    events_emitted = ("armed", "disarmed", "triggered")

    def action_aliases(self):
        return {"arm": "arm_away", "away": "arm_away", "home": "arm_home", "stay": "arm_home",
                "night": "arm_night", "off": "disarm", "turn_off": "disarm", "on": "arm_away",
                "set": "arm_away"}

    def default_action(self, args):
        return "arm_away"

    def interlock(self, action, params, status, cfg):
        why = super().interlock(action, params, status, cfg)
        if why:
            return why
        st = status.get("state")
        if action == "disarm" and st == "disarmed":
            return "the alarm is already disarmed"
        if action.startswith("arm_") and st == "armed_" + action[4:]:
            return f"the alarm is already {st.replace('_', ' ')}"
        if action.startswith("arm_") and st == "triggered":
            return "the alarm is TRIGGERED; it has to be disarmed (owner + PIN) first"
        return None

    def confirmation(self, action, params, nick):
        return "Disarm the alarm?" if action == "disarm" else \
            f"Arm the {nick} ({action[4:]})?"

    def events(self, prev, cur):
        p, c = prev.get("state"), cur.get("state")
        if p == c:
            return []
        if c == "triggered":
            return ["triggered"]
        if c == "disarmed":
            return ["disarmed"]
        if c in ARMED and p not in ARMED:
            return ["armed"]
        return []

    def sim_init(self, cfg):
        return {"state": "disarmed", "changed_by": None}

    def sim_apply(self, st, action, params, now, speed):
        st["state"] = "disarmed" if action == "disarm" else "armed_" + action[4:]

    def _code(self, cfg):
        return (cfg or {}).get("alarm_code") or secret("alarm_code")

    def ha_status(self, state, extra):
        if state is None or state.get("state") in ("unavailable", "unknown"):
            return {"state": "offline"}
        return {"state": state["state"],
                "changed_by": (state.get("attributes") or {}).get("changed_by")}

    def ha_ops(self, action, params, entity, cfg):
        data = {"entity_id": entity}
        code = self._code(cfg)
        if code:
            data["code"] = str(code)
        svc = "alarm_disarm" if action == "disarm" else "alarm_" + action
        return [{"service": f"alarm_control_panel.{svc}", "data": data}]

    def mqtt_status(self, v):
        s = (v.get("state") or "").lower()
        return {"state": s or "offline"}

    def mqtt_ops(self, action, params, prefix):
        return [(f"{prefix}/command", action.upper())]

    def describe_status(self, st):
        return (st.get("state") or "?").replace("_", " ")

    # dashboard
    def ui_active(self, st):
        return str(st.get("state") or "").startswith("armed")

    def ui_state(self, st):
        s = str(st.get("state") or "unknown")
        if s in ("offline", "unavailable"):
            return "Not responding"
        return s.replace("armed_", "Armed, ").replace("_", " ").capitalize() if s != "disarmed" \
            else "Disarmed"
