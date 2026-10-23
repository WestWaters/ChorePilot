"""Program appliances: dishwasher, washer, dryer -- the original Dishsoaker3000 machines.

These types describe the appliance (tiers, status fields, events, routing words) like every
other plugin, but their programs, options and exact model profiles live in machines.py and
their backends (simulated / ha / homeconnect / mqtt) in appliance.py. The House sends their
tool calls through the proven program-appliance Bridge (control.py), which keeps doing what it
always did: strict per-model validation, the vendor remote-start interlock, two-phase writes,
dry runs and the washer -> dryer chain. The policy engine sits in front of it.

Matter 1.2+: Dishwasher, Laundry Washer, Laundry Dryer. Vendors: Home Connect (Bosch, Siemens,
Neff, Gaggenau, Thermador), LG ThinQ, Samsung SmartThings, Miele -- see docs/DEVICES.md.
"""
from __future__ import annotations

from . import register
from .base import Action, DeviceType

ACTIVE = ("running", "delayed", "paused")
#: tool name -> safety tier (start/resume use water and heat)
TOOL_TIERS = {"get_status": 0, "notify_when_done": 0, "start_program": 2, "resume": 2,
              "start_when_done": 2, "pause": 1, "cancel": 1}


class ProgramAppliance(DeviceType):
    tool = None                       # tools come from the Bridge (start_program, pause, ...)
    status_fields = ("state", "program", "remaining", "door", "remote_start_allowed")
    capabilities = ("programs", "options", "remote_start_interlock", "delayed_start")
    events_emitted = ("started", "finished", "stopped_early", "door_opened")
    actions = {
        "start_program": Action("start_program", "Start a program", (), tier=2, uses="water and heat"),
        "resume": Action("resume", "Resume the program", (), tier=2, uses="water and heat"),
        "pause": Action("pause", "Pause the program", (), tier=1),
        "cancel": Action("cancel", "Cancel the program", (), tier=1),
    }

    def events(self, prev, cur):
        p, c = prev.get("state"), cur.get("state")
        out = []
        if p not in ACTIVE and c in ("running", "delayed"):
            out.append("started")
        if p in ACTIVE and c == "finished":
            out.append("finished")
        elif p in ACTIVE and c in ("ready", "idle"):
            # the simulator and some backends go straight back to ready at the end
            out.append("finished" if (prev.get("remaining_s") or 0) <= 1200 else "stopped_early")
        if (cur.get("door_opened") or 0) > (prev.get("door_opened") or 0) or \
                (prev.get("door") == "closed" and c != p and cur.get("door") == "open"):
            out.append("door_opened")
        return out

    # dashboard
    def ui_active(self, st):
        return st.get("state") in ACTIVE

    def ui_attention(self, st):
        return bool(st.get("error")) or st.get("state") == "action_required"

    def ui_primary(self, st, cfg=None):
        s = st.get("state")
        if s == "running":
            return {"action": "pause", "label": "Pause the program", "params": {}, "tier": 1}
        if s == "paused":
            return {"action": "resume", "label": "Resume the program", "params": {}, "tier": 2}
        return None

    def ui_state(self, st):
        s = st.get("state")
        prog = str(st.get("program") or "").replace("_", " ").capitalize()
        left = st.get("remaining")
        if s in ("offline", "unavailable"):
            return "Not responding"
        if s == "running":
            return f"{prog or 'Running'}, {left} left" if left else (prog or "Running")
        if s == "delayed":
            return f"{prog} starts later" + (f", done in {left}" if left else "")
        if s == "paused":
            return "Paused" + (f", {left} left" if left else "")
        if s == "finished":
            return "Finished"
        door = ", door open" if st.get("door") == "open" else ""
        return ("Ready" if s in ("ready", "idle") else str(s).replace("_", " ").capitalize()) + door


@register
class Dishwasher(ProgramAppliance):
    name = "dishwasher"
    title = "dishwasher"
    room = "Kitchen"
    matter = ("Dishwasher",)
    hc_type = "Dishwasher"
    keywords = ("dish", "dishes", "dishwasher", "plates", "glasses", "pots", "cutlery")


@register
class Washer(ProgramAppliance):
    name = "washer"
    title = "washing machine"
    room = "Laundry"
    matter = ("Laundry Washer",)
    hc_type = "Washer"
    keywords = ("washer", "washing", "laundry", "clothes", "wash", "cotton", "spin")


@register
class Dryer(ProgramAppliance):
    name = "dryer"
    title = "tumble dryer"
    room = "Laundry"
    matter = ("Laundry Dryer",)
    hc_type = "Dryer"
    keywords = ("dryer", "dry", "drying", "laundry", "tumble")
