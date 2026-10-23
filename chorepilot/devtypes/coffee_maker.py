"""Coffee makers and fully automatic espresso machines (Home Connect CoffeeMaker).

Brewing is tier 2 (hot water, steam): an adult confirms. The machine's own remote-start rule is
an interlock: Home Connect only accepts a program when the appliance reports
BSH.Common.Status.RemoteControlStartAllowed = true (set on the machine), and a cup has to be
under the spout -- which no API can check, so the confirmation says so.

Home Connect (https://api-docs.home-connect.com/programs-and-options/):
  program  ConsumerProducts.CoffeeMaker.Program.Beverage.<Espresso|Coffee|Cappuccino|...>
  options  ConsumerProducts.CoffeeMaker.Option.BeanAmount (enum ...EnumType.BeanAmount.<...>)
           ConsumerProducts.CoffeeMaker.Option.FillQuantity (ml)
HA: the home_connect integration (select active program / home_connect.set_program_and_options)
Matter: no coffee-maker device type yet; reached through HA.
"""
from __future__ import annotations

from . import register
from .base import Action, DeviceType, Param

P = "ConsumerProducts.CoffeeMaker.Program.Beverage."
BEVERAGES = {
    "espresso": P + "Espresso", "ristretto": P + "Ristretto", "double_espresso": P + "EspressoDoppio",
    "coffee": P + "Coffee", "xl_coffee": P + "XLCoffee", "caffe_grande": P + "CaffeGrande",
    "espresso_macchiato": P + "EspressoMacchiato", "cappuccino": P + "Cappuccino",
    "latte_macchiato": P + "LatteMacchiato", "caffe_latte": P + "CaffeLatte",
    "milk_froth": P + "MilkFroth", "warm_milk": P + "WarmMilk",
}
B = "ConsumerProducts.CoffeeMaker.EnumType.BeanAmount."
STRENGTH = {"very_mild": B + "VeryMild", "mild": B + "Mild", "normal": B + "Normal",
            "strong": B + "Strong", "very_strong": B + "VeryStrong",
            "double_shot": B + "DoubleShot"}
BEV = Param("beverage", "enum", "which drink", values=tuple(BEVERAGES), default="coffee",
            aliases={"americano": "coffee", "black_coffee": "coffee", "latte": "caffe_latte",
                     "flat_white": "cappuccino", "doppio": "double_espresso",
                     "macchiato": "latte_macchiato", "lungo": "xl_coffee", "long": "xl_coffee"})
STR = Param("strength", "enum", "bean amount", values=tuple(STRENGTH),
            aliases={"weak": "mild", "extra_strong": "very_strong", "regular": "normal"})
SIZE = Param("size_ml", "int", "cup size in ml", lo=20, hi=500)
MINUTES = {"espresso": 1, "ristretto": 1, "double_espresso": 2, "coffee": 2, "xl_coffee": 3,
           "caffe_grande": 3, "espresso_macchiato": 2, "cappuccino": 2, "latte_macchiato": 3,
           "caffe_latte": 3, "milk_froth": 1, "warm_milk": 1}


@register
class CoffeeMaker(DeviceType):
    name = "coffee_maker"
    title = "coffee machine"
    room = "Kitchen"
    tool = "brew_coffee"
    tool_desc = "Make a drink on the coffee machine (beverage, optional strength and size_ml)."
    ha_domain = "select"   # home_connect active program; status from its sensors
    capabilities = ("beverage_programs", "bean_amount", "fill_quantity")
    status_fields = ("state", "beverage", "remote_start_allowed", "water_tank_empty")
    keywords = ("coffee", "espresso", "cappuccino", "latte", "brew", "americano", "macchiato")
    aliases = ("coffee", "espresso machine", "coffee machine")
    hc_type = "CoffeeMaker"
    actions = {
        "brew": Action("brew", "Make a drink", (BEV, STR, SIZE), tier=2, uses="hot water and steam"),
        "cancel": Action("cancel", "Stop the drink being made", (), tier=1),
    }
    events_emitted = ("started", "finished")

    def action_aliases(self):
        return {"make": "brew", "start": "brew", "stop": "cancel"}

    def param_aliases(self):
        return {"drink": "beverage", "program": "beverage", "type": "beverage",
                "bean_amount": "strength", "fill_quantity": "size_ml", "size": "size_ml",
                "ml": "size_ml", "volume": "size_ml"}

    def interlock(self, action, params, status, cfg):
        why = super().interlock(action, params, status, cfg)
        if why or action != "brew":
            return why
        if status.get("state") == "brewing":
            return "the coffee machine is busy making a drink"
        if status.get("water_tank_empty"):
            return "the water tank is empty; fill it first"
        if status.get("remote_control") is False:
            return "remote control is switched off for the coffee machine in the Home Connect app"
        if status.get("local_control"):
            return "someone is using the coffee machine's own controls right now"
        if status.get("remote_start_allowed") is False:
            return ("remote start is off on the coffee machine; enable it on the machine "
                    "(a deliberate safety step we never bypass)")
        return None

    def confirmation(self, action, params, nick):
        if action == "brew":
            extra = "".join(f", {k} {params[k]}" for k in ("strength", "size_ml") if params.get(k))
            bev = params['beverage'].replace('_', ' ')
            return (f"Make {'an' if bev[:1] in 'aeiou' else 'a'} {bev} on the {nick}{extra}? Is a "
                    "cup under the spout? It uses hot water and steam.")
        return super().confirmation(action, params, nick)

    def events(self, prev, cur):
        p, c = prev.get("state"), cur.get("state")
        if p != "brewing" and c == "brewing":
            return ["started"]
        if p == "brewing" and c in ("ready", "finished"):
            return ["finished"]
        return []

    # simulator
    def sim_init(self, cfg):
        return {"state": "ready", "beverage": None, "end": None, "remote_start_allowed": True,
                "water_tank_empty": False, "cups": 0}

    def sim_apply(self, st, action, params, now, speed):
        if action == "brew":
            # real seconds, not sped up much: a drink is ~1-3 min; the sim runs it 10x faster
            st.update(state="brewing", beverage=params["beverage"],
                      end=now + MINUTES.get(params["beverage"], 2) * 6)
        else:
            st.update(state="ready", end=None)

    def sim_tick(self, st, now, speed):
        if st["state"] == "brewing" and now >= (st.get("end") or 0):
            st.update(state="ready", end=None, cups=st.get("cups", 0) + 1)

    def sim_status(self, st, now, speed):
        self.sim_tick(st, now, speed)
        return {k: st.get(k) for k in self.status_fields}

    # Home Connect
    def hc_status(self, status, settings, active):
        op = str(status.get("BSH.Common.Status.OperationState", "")).rsplit(".", 1)[-1].lower()
        state = {"run": "brewing", "ready": "ready", "inactive": "standby", "finished": "ready",
                 "actionrequired": "action_required", "error": "error"}.get(op, op or "offline")
        inv = {v: k for k, v in BEVERAGES.items()}
        return {"state": state, "beverage": inv.get((active or {}).get("key")),
                "remote_start_allowed": status.get("BSH.Common.Status.RemoteControlStartAllowed"),
                "remote_control": status.get("BSH.Common.Status.RemoteControlActive"),
                "local_control": status.get("BSH.Common.Status.LocalControlActive"),
                "water_tank_empty": None}

    def hc_ops(self, action, params, cfg):
        if action == "cancel":
            return [{"method": "DELETE", "path": "/programs/active", "body": None}]
        opts = []
        if params.get("strength"):
            opts.append({"key": "ConsumerProducts.CoffeeMaker.Option.BeanAmount",
                         "value": STRENGTH[params["strength"]]})
        if params.get("size_ml"):
            opts.append({"key": "ConsumerProducts.CoffeeMaker.Option.FillQuantity",
                         "value": params["size_ml"], "unit": "ml"})
        body = {"data": {"key": BEVERAGES[params["beverage"]], **({"options": opts} if opts else {})}}
        return [{"method": "PUT", "path": "/programs/active", "body": body}]

    # Home Assistant (home_connect integration)
    def ha_status(self, state, extra):
        if state is None or state.get("state") in ("unavailable", "unknown"):
            return {"state": "offline"}
        op = str(state["state"]).lower().replace("_", "")
        rs = (extra or {}).get("remote_start")
        return {"state": {"run": "brewing", "ready": "ready", "inactive": "standby",
                          "finished": "ready"}.get(op, op),
                "remote_start_allowed": {"on": True, "off": False}.get((rs or {}).get("state"))}

    def ha_ops(self, action, params, entity, cfg):
        from ..appliance import ha_key
        dev = (cfg or {}).get("device_id")
        if action == "cancel":
            return [{"service": "button.press",
                     "data": {"entity_id": (cfg or {}).get("stop_entity", "button.coffee_machine_stop_program")}}]
        if not dev:
            raise ValueError("set device_id for the coffee machine in house.json (HA > Settings > "
                             "Devices > the coffee machine; last part of the URL)")
        data = {"device_id": dev, "affects_to": "active_program",
                "program": ha_key(BEVERAGES[params["beverage"]])}
        if params.get("strength"):
            data[ha_key("ConsumerProducts.CoffeeMaker.Option.BeanAmount")] = ha_key(STRENGTH[params["strength"]])
        if params.get("size_ml"):
            data[ha_key("ConsumerProducts.CoffeeMaker.Option.FillQuantity")] = params["size_ml"]
        return [{"service": "home_connect.set_program_and_options", "data": data}]

    # dashboard
    def ui_active(self, st):
        return st.get("state") == "brewing"

    def ui_attention(self, st):
        return bool(st.get("water_tank_empty")) or bool(st.get("error"))

    def ui_state(self, st):
        s = st.get("state")
        if s in ("offline", "unavailable"):
            return "Not responding"
        if st.get("water_tank_empty"):
            return "Refill the water tank"
        if s == "brewing":
            return "Making " + str(st.get("beverage") or "a drink").replace("_", " ")
        return str(s or "unknown").capitalize()
