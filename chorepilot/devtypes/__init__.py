"""Device-type plugins: one small module per class of device.

    from chorepilot.devtypes import get_type, all_types
    get_type("climate")        # -> a DeviceType instance

Built in: dishwasher / washer / dryer (program appliances, driven by the original Dishsoaker
bridge), light, climate (thermostat / AC / heat pump), valve (sprinklers, irrigation), coffee_maker,
oven, fridge, vacuum, lock, cover (blinds / shades), garage_door (garage doors / gates),
alarm_panel, ev_charger, water_heater, air_purifier, humidifier, media_player. User plugins: any *.py in ~/.config/chorepilot/devtypes/ that calls
`register(MyType)`.
"""
from __future__ import annotations

import glob
import importlib
import importlib.util
import os
import sys
import threading

from .base import TIER_NAMES, Action, DeviceType, Param  # noqa: F401

_REGISTRY: dict = {}
_BUILTIN = ("program", "light", "climate", "valve", "coffee_maker", "oven", "fridge", "vacuum",
            "lock", "cover", "alarm", "ev_charger", "water_heater", "air", "media_player")
_loaded = False


def register(cls):
    """Add a DeviceType subclass to the registry (usable as a decorator)."""
    _REGISTRY[cls.name] = cls
    return cls


_LOCK = threading.RLock()


def _load():
    # thread-safe: the MCP server and the Telegram bot build Houses from several threads, and
    # a half-filled registry must never be visible ("unknown device type 'light'")
    global _loaded
    if _loaded:
        return
    with _LOCK:
        if _loaded:
            return
        for mod in _BUILTIN:
            importlib.import_module(f"{__name__}.{mod}")
        from ..env import config_dir
        for path in sorted(glob.glob(os.path.join(config_dir(), "devtypes", "*.py"))):
            spec = importlib.util.spec_from_file_location(
                f"chorepilot_user_devtype_{os.path.basename(path)[:-3]}", path)
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
            except Exception as e:  # a broken user plugin must not take the house down
                print(f"[devtypes] skipped {path}: {e}", file=sys.stderr)
        _loaded = True


TYPE_ALIASES = {
    "thermostat": "climate", "ac": "climate", "air_conditioner": "climate", "hvac": "climate",
    "heat_pump": "climate", "sprinkler": "valve", "irrigation": "valve", "water_valve": "valve",
    "coffee": "coffee_maker", "espresso": "coffee_maker", "coffee_machine": "coffee_maker",
    "range": "oven", "stove": "oven", "refrigerator": "fridge", "freezer": "fridge",
    "fridge_freezer": "fridge", "robot_vacuum": "vacuum", "door_lock": "lock",
    "washing_machine": "washer", "tumble_dryer": "dryer", "lamp": "light",
    "blind": "cover", "blinds": "cover", "shade": "cover", "shades": "cover", "curtain": "cover",
    "curtains": "cover", "shutter": "cover", "awning": "cover", "window_covering": "cover",
    "garage": "garage_door", "gate": "garage_door", "garage_opener": "garage_door",
    "alarm": "alarm_panel", "alarm_control_panel": "alarm_panel", "security_system": "alarm_panel",
    "ev": "ev_charger", "evse": "ev_charger", "wallbox": "ev_charger", "car_charger": "ev_charger",
    "boiler": "water_heater", "hot_water": "water_heater", "geyser": "water_heater",
    "purifier": "air_purifier", "air_cleaner": "air_purifier", "dehumidifier": "humidifier",
    "speaker": "media_player", "tv": "media_player", "television": "media_player",
    "media": "media_player",
}


def get_type(name, cfg=None):
    _load()
    key = str(name).strip().lower().replace(" ", "_").replace("-", "_")
    key = TYPE_ALIASES.get(key, key)
    if key not in _REGISTRY:
        raise KeyError(f"unknown device type {name!r}; known: {', '.join(sorted(_REGISTRY))}")
    return _REGISTRY[key](cfg)


def all_types():
    _load()
    return {k: v() for k, v in sorted(_REGISTRY.items())}


def is_program_type(name):
    """dishwasher / washer / dryer: these run through the program-appliance bridge."""
    return str(name).lower() in ("dishwasher", "washer", "dryer")
