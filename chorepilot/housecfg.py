"""house.json: every device in the house, one entry per nickname.

    {
      "unit": "F",                     # F or C, for thermostats / ovens / fridges
      "backend": "simulated",          # default adapter: simulated | ha | matter | mqtt | homeconnect
      "ha": {"url": "http://homeassistant.local:8123"},      # shared sections (token: $HA_TOKEN)
      "policy": {"kids_tier1": true},                        # see policy.py
      "devices": {
        "dishwasher": {"type": "dishwasher", "model": "SMV6ZCX42E"},
        "thermostat": {"type": "climate", "backend": "ha", "entity": "climate.hallway",
                       "aliases": ["ac"], "comfort": [68, 76]},
        "lawn_sprinkler": {"type": "valve", "backend": "ha", "entity": "valve.front_lawn",
                           "max_minutes": 30},
        "front_door": {"type": "lock", "backend": "matter", "entity": "lock.front_door"}
      }
    }

Program appliances (dishwasher / washer / dryer) go through the original bridge and keep every
control.json option (model, haId, program_keys, ha/mqtt sections). An existing control.json
with no house.json still works: its appliances become the house's devices.
"""
from __future__ import annotations

import copy
import json
import os

from .env import config_dir

PROGRAM_TYPES = ("dishwasher", "washer", "dryer")
SECTIONS = ("ha", "homeconnect", "mqtt", "simulated")

PRESETS = {
    # everything simulated: what `chorepilot demo` and the tests use
    "demo": {
        "household": "the demo house",
        "unit": "F",
        "backend": "simulated",
        "devices": {
            "dishwasher": {"type": "dishwasher", "model": "SMV6ZCX42E", "aliases": ["dishes"]},
            "washer": {"type": "washer", "model": "WAT286H9SG", "aliases": ["washing machine"]},
            "dryer": {"type": "dryer", "model": "WTW876H8SG", "aliases": ["tumble dryer"]},
            "living_room_light": {"type": "light", "aliases": ["lights", "living room"]},
            "thermostat": {"type": "climate", "aliases": ["ac", "air conditioner", "heating"],
                           "comfort": [68, 76]},
            "lawn_sprinkler": {"type": "valve", "aliases": ["sprinkler", "lawn"], "max_minutes": 30},
            "coffee_machine": {"type": "coffee_maker", "aliases": ["coffee", "espresso"]},
            "oven": {"type": "oven", "max_temp": 480},
            "fridge": {"type": "fridge", "aliases": ["freezer", "refrigerator"]},
            "vacuum": {"type": "vacuum", "aliases": ["roomba", "robot vacuum"]},
            "front_door": {"type": "lock", "aliases": ["door", "front door lock"]},
            "garage": {"type": "garage_door", "aliases": ["garage door"]},
            "bedroom_blinds": {"type": "cover", "aliases": ["blinds"]},
            "alarm": {"type": "alarm_panel", "aliases": ["security system"]},
            "car_charger": {"type": "ev_charger", "aliases": ["ev charger", "wallbox"],
                            "max_current": 32},
            "water_heater": {"type": "water_heater", "aliases": ["boiler", "hot water"]},
            "air_purifier": {"type": "air_purifier", "aliases": ["purifier"]},
            "humidifier": {"type": "humidifier"},
            "living_room_speaker": {"type": "media_player", "aliases": ["speaker", "music", "tv"],
                                    "max_volume": 80},
        },
    },
    # a Bosch dishwasher + washer + dryer, straight to Home Connect (example models)
    "bosch": {
        "household": "our home",
        "unit": "C",
        "backend": "homeconnect",
        "devices": {
            "dishwasher": {"type": "dishwasher", "model": "SMV6ZCX42E", "aliases": ["dishes", "dish washer"]},
            "washer": {"type": "washer", "model": "WAT286H9SG", "aliases": ["washing machine"]},
            "dryer": {"type": "dryer", "model": "WTW876H8SG", "aliases": ["tumble dryer", "drier"]},
        },
    },
}


def house_path():
    return os.path.join(config_dir(), "house.json")


def control_path():
    return os.path.join(config_dir(), "control.json")


def _read(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def from_control(ctl):
    """A Dishsoaker control.json -> a house config (program appliances only)."""
    h = {k: v for k, v in ctl.items() if k != "appliances"}
    apps = ctl.get("appliances")
    if not apps:
        apps = {"dishwasher": {"type": "dishwasher", **({"model": ctl["model"]} if ctl.get("model") else {})}}
    h["devices"] = {n: dict(e or {}, type=(e or {}).get("type") or n) for n, e in apps.items()}
    return h


def load_house(path=None):
    """house.json, else the appliances of an existing control.json, else the simulated demo
    house (with "_preset": "demo" set so callers can say so)."""
    h = _read(path or house_path())
    if h is not None:
        return h
    ctl = _read(control_path())
    if ctl is not None:
        out = from_control(ctl)
        out["_from"] = "control.json"
        return out
    out = copy.deepcopy(PRESETS["demo"])
    out["_preset"] = "demo"
    return out


def program_fleet_cfg(house):
    """The control.json-style config for the program appliances in a house config."""
    apps = {n: dict(d, type=d.get("type") or n) for n, d in (house.get("devices") or {}).items()
            if (d.get("type") or n) in PROGRAM_TYPES}
    if not apps:
        return None
    cfg = {k: house[k] for k in SECTIONS if k in house}
    cfg["backend"] = house.get("backend") or "simulated"
    if cfg["backend"] not in ("simulated", "ha", "mqtt", "homeconnect"):
        cfg["backend"] = "ha"                   # matter devices are reached through HA
    for e in apps.values():
        if e.get("backend") == "matter":
            e["backend"] = "ha"
    cfg["appliances"] = apps
    cfg["default"] = house.get("default") if house.get("default") in apps else next(iter(apps))
    return cfg


def write_house(cfg, path=None, force=False):
    path = path or house_path()
    if os.path.exists(path) and not force:
        raise SystemExit(f"{path} exists; pass --force to replace it")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
    return path
