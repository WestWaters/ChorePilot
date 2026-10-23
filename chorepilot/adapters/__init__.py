"""Backend adapters for plugin device types: how one device is actually reached.

  simulated    a device in a JSON file (~/.cache/chorepilot/sim-<nick>.json). Default; safe.
  ha           Home Assistant REST (/api/states, /api/services) -- the universal adapter: any
               brand HA has an integration for (Hue, ecobee, Nest, Rachio, Roomba, Yale, ThinQ,
               SmartThings, Miele, Home Connect, ESPHome, Zigbee, Z-Wave ...). Token: $HA_TOKEN.
  matter       Matter devices, reached through Home Assistant's Matter server (same as `ha`,
               with Matter-flavoured defaults). ChorePilot does not run its own Matter controller.
  mqtt         ESPHome (or any) MQTT device; stdlib MQTT 3.1.1 client from appliance.py.
  homeconnect  the official Home Connect cloud API (coffee makers, ovens, fridges ...), sharing
               the program appliances' OAuth token.

Every adapter splits a write into plan() (the exact requests; --dry-run prints these) and
apply() (send them). A step with "delay_s" (close the sprinkler valve in 10 min) is handed to
`defer`, which by default stores it and starts a detached `chorepilot devices deferred` runner,
so the close is scheduled at the moment the valve opens.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import types
import uuid

from ..appliance import BackendError, HomeConnectBackend, MiniMQTT, http_json
from ..env import cache_dir, getenv, secret

ADAPTERS = ("simulated", "ha", "matter", "mqtt", "homeconnect")


def _slug(s):
    return "".join(c if c.isalnum() else "_" for c in str(s).lower()).strip("_")


class Adapter:
    name = "base"

    def __init__(self, nick, dtype, cfg, defer=None):
        self.nick, self.dtype, self.cfg = nick, dtype, dict(cfg or {})
        self.defer = defer or default_defer

    def status(self):
        raise NotImplementedError

    def plan(self, action, params):
        raise NotImplementedError

    def apply(self, ops):
        raise NotImplementedError

    def describe(self):
        return self.name

    def _split(self, ops):
        now_ops = [o for o in ops if not o.get("delay_s")]
        later = [o for o in ops if o.get("delay_s")]
        return now_ops, later


# ------------------------------------------------------------------ simulated
class SimAdapter(Adapter):
    name = "simulated"

    def __init__(self, nick, dtype, cfg, defer=None, path=None, speed=None):
        super().__init__(nick, dtype, cfg, defer)
        self.path = path or os.path.join(cache_dir(), f"sim-{_slug(nick)}.json")
        self.speed = float(speed or self.cfg.get("speed") or getenv("SIM_SPEED", 600))

    def _load(self):
        try:
            with open(self.path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return self.dtype.sim_init(self.cfg)

    def _save(self, st):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f)
        os.replace(tmp, self.path)

    def status(self):
        st = self._load()
        out = self.dtype.sim_status(st, time.time(), self.speed)
        self._save(st)
        return out

    def set(self, **kw):
        """Test/demo hook (`chorepilot devices sim <nick> key=value`)."""
        st = self._load()
        st.update(kw)
        self._save(st)

    def plan(self, action, params):
        return [{"op": action, "params": params, "target": f"file:{self.path}"}]

    def apply(self, ops):
        st = self._load()
        for op in ops:
            self.dtype.sim_apply(st, op["op"], op["params"], time.time(), self.speed)
        self._save(st)
        return {"ok": True}


# ------------------------------------------------------------------ Home Assistant
class HAAdapter(Adapter):
    name = "ha"

    def __init__(self, nick, dtype, cfg, defer=None, http=http_json, url=None, token=None):
        super().__init__(nick, dtype, cfg, defer)
        self.url = (url or self.cfg.get("url") or os.environ.get("HA_URL")
                    or "http://homeassistant.local:8123").rstrip("/")
        self.token = token or secret("ha_token", "HA_TOKEN")
        self.entity = self.cfg.get("entity") or f"{dtype.ha_domain or 'switch'}.{_slug(nick)}"
        self.extra = dict(self.cfg.get("entities") or {})
        self.http = http

    def _h(self):
        if not self.token:
            raise BackendError("set HA_TOKEN to a Home Assistant long-lived access token")
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def _state(self, entity):
        code, js = self.http("GET", f"{self.url}/api/states/{entity}", self._h())
        if code == 404:
            return None
        if code != 200:
            raise BackendError(f"HA GET {entity}: HTTP {code} {js}")
        return js

    def status(self):
        main = self._state(self.entity)
        extra = {k: self._state(v) for k, v in self.extra.items()
                 if isinstance(v, str) and "." in v and not k.endswith(("_setpoint", "_super"))}
        st = self.dtype.ha_status(main, extra)
        st.setdefault("detail", {"backend": self.name, "entity": self.entity})
        return st

    def plan(self, action, params):
        try:
            raw = self.dtype.ha_ops(action, params, self.entity, self.cfg)
        except ValueError as e:
            raise BackendError(str(e)) from None
        ops = []
        for o in raw:
            dom, _, svc = o["service"].partition(".")
            op = {"method": "POST", "url": f"{self.url}/api/services/{dom}/{svc}", "body": o["data"]}
            for k in ("delay_s", "note"):
                if o.get(k):
                    op[k] = o[k]
            ops.append(op)
        return ops

    def send(self, op):
        code, js = self.http(op["method"], op["url"], self._h(), op["body"])
        if code not in (200, 201):
            raise BackendError(f"HA {op['url']}: HTTP {code} {js}")

    def apply(self, ops):
        now_ops, later = self._split(ops)
        for op in now_ops:
            self.send(op)
        for op in later:
            self.defer(self, op)
        return {"ok": True, "scheduled": len(later)}


class MatterAdapter(HAAdapter):
    """Matter devices commissioned into Home Assistant's Matter server: they are ordinary HA
    entities (light.*, lock.*, climate.*, valve.*, vacuum.*), so this is the HA adapter."""
    name = "matter"


# ------------------------------------------------------------------ MQTT
class MQTTAdapter(Adapter):
    name = "mqtt"

    def __init__(self, nick, dtype, cfg, defer=None, client_factory=MiniMQTT):
        super().__init__(nick, dtype, cfg, defer)
        self.prefix = self.cfg.get("prefix") or _slug(nick)
        hp = self.cfg.get("host") or os.environ.get("MQTT_HOST") or "localhost"
        self.host, _, p = hp.partition(":")
        self.port = int(p or self.cfg.get("port") or os.environ.get("MQTT_PORT") or 1883)
        self.factory = client_factory

    def _client(self):
        return self.factory(self.host, self.port, secret("mqtt_user", "MQTT_USER"),
                            secret("mqtt_password", "MQTT_PASSWORD")).connect()

    def status(self):
        topics = self.dtype.mqtt_state_topics(self.prefix)
        cli = self._client()
        try:
            cli.subscribe(list(topics.values()))
            got = cli.collect(wait=float(getenv("MQTT_WAIT", 1.0)))
        finally:
            cli.close()
        return self.dtype.mqtt_status({k: got.get(t) for k, t in topics.items()})

    def plan(self, action, params):
        ops = []
        for t in self.dtype.mqtt_ops(action, params, self.prefix):
            op = {"topic": t[0], "payload": t[1]}
            if len(t) > 2 and t[2]:
                op["delay_s"] = t[2]
            ops.append(op)
        return ops

    def send(self, op):
        cli = self._client()
        try:
            cli.publish(op["topic"], op["payload"])
        finally:
            cli.close()

    def apply(self, ops):
        now_ops, later = self._split(ops)
        if now_ops:
            cli = self._client()
            try:
                for op in now_ops:
                    cli.publish(op["topic"], op["payload"])
            finally:
                cli.close()
        for op in later:
            self.defer(self, op)
        return {"ok": True, "scheduled": len(later)}


# ------------------------------------------------------------------ Home Connect
class HomeConnectAdapter(Adapter):
    name = "homeconnect"

    def __init__(self, nick, dtype, cfg, defer=None, http=http_json, token=None):
        super().__init__(nick, dtype, cfg, defer)
        if not dtype.hc_type:
            raise BackendError(f"a {dtype.title} is not a Home Connect appliance type")
        machine = types.SimpleNamespace(kind=dtype.name, hc_type=dtype.hc_type,
                                        model=self.cfg.get("model"), child_lock=None)
        self.be = HomeConnectBackend(base=self.cfg.get("url"), token=token,
                                     ha_id=self.cfg.get("haId"), http=http, machine=machine,
                                     appliance=nick)

    def status(self):
        be = self.be
        ha = be.appliance_id()
        code, js = be._get(f"/api/homeappliances/{ha}/status")
        if code == 409 or not be._connected(ha):
            return {"state": "offline", "detail": {"backend": "homeconnect", "haId": ha}}
        if code != 200:
            raise BackendError(f"Home Connect status: HTTP {code} {js}")
        st = {i["key"]: i.get("value") for i in js["data"]["status"]}
        settings = {}
        for key in getattr(self.dtype, "hc_settings", lambda: [])():
            c, s = be._get(f"/api/homeappliances/{ha}/settings/{key}")
            if c == 200:
                settings[key] = s["data"].get("value")
        active = None
        ac, ajs = be._get(f"/api/homeappliances/{ha}/programs/active")
        if ac == 200:
            active = ajs["data"]
        out = self.dtype.hc_status(st, settings, active)
        out["detail"] = {"backend": "homeconnect", "haId": ha}
        return out

    def plan(self, action, params):
        root = f"{self.be.base}/api/homeappliances/{self.be.ha_id or '{haId}'}"
        return [{"method": o["method"], "url": root + o["path"], "body": o["body"]}
                for o in self.dtype.hc_ops(action, params, self.cfg)]

    def apply(self, ops):
        return self.be.apply(ops)


# ------------------------------------------------------------------ deferred steps
class DeferredStore:
    """Steps to send later ({adapter config, op, due}), in ~/.cache/chorepilot/deferred.json."""

    def __init__(self, path=None):
        self.path = path or os.path.join(cache_dir(), "deferred.json")

    def all(self):
        try:
            with open(self.path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _write(self, d):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, self.path)

    def add(self, nick, op):
        d = self.all()
        jid = uuid.uuid4().hex[:8]
        d[jid] = {"nick": nick, "op": {k: v for k, v in op.items() if k != "delay_s"},
                  "due": time.time() + float(op["delay_s"])}
        self._write(d)
        return jid

    def pop(self, jid):
        d = self.all()
        job = d.pop(jid, None)
        self._write(d)
        return job


def default_defer(adapter, op):
    """Store the step and start a detached runner that sends it when due."""
    jid = DeferredStore().add(adapter.nick, op)
    log = os.path.join(cache_dir(), "watch.log")
    os.makedirs(cache_dir(), exist_ok=True)
    with open(log, "a") as out:
        subprocess.Popen([sys.executable, "-u", "-m", "chorepilot", "devices", "deferred", jid],
                         stdout=out, stderr=out, stdin=subprocess.DEVNULL, start_new_session=True,
                         cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    return jid


def run_deferred(jid, adapter_for, sleep=time.sleep, store=None):
    """Body of `chorepilot devices deferred <id>`: wait until due, then send the step."""
    store = store or DeferredStore()
    job = store.all().get(jid)
    if not job:
        return "unknown"
    while time.time() < job["due"]:
        sleep(min(30, max(0.2, job["due"] - time.time())))
        if store.all().get(jid) is None:
            return "cancelled"
    store.pop(jid)
    adapter_for(job["nick"]).send(job["op"])
    return "sent"


def make_adapter(nick, dtype, cfg, backend=None, defer=None, **kw):
    name = backend or cfg.get("backend") or "simulated"
    section = dict(cfg)
    if name == "simulated":
        return SimAdapter(nick, dtype, section, defer, **{k: v for k, v in kw.items()
                                                          if k in ("path", "speed")})
    if name == "ha":
        return HAAdapter(nick, dtype, section, defer, **{k: v for k, v in kw.items()
                                                         if k in ("http", "url", "token")})
    if name == "matter":
        return MatterAdapter(nick, dtype, section, defer, **{k: v for k, v in kw.items()
                                                             if k in ("http", "url", "token")})
    if name == "mqtt":
        return MQTTAdapter(nick, dtype, section, defer, **{k: v for k, v in kw.items()
                                                           if k == "client_factory"})
    if name == "homeconnect":
        return HomeConnectAdapter(nick, dtype, section, defer, **{k: v for k, v in kw.items()
                                                                  if k in ("http", "token")})
    raise KeyError(f"unknown adapter {name!r}; choose from {', '.join(ADAPTERS)}")
