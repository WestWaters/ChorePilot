"""Appliance backends: the part that actually talks to the dishwasher, washer and dryer.

Every backend drives ONE appliance and turns the same small vocabulary (status, start, pause,
resume, cancel) into real requests for one control path:

  simulated     an appliance that lives in a JSON file (~/.cache/chorepilot/sim-<name>.json) so a
                separate `chorepilot control wait` can watch it. Default; safe.
  ha            Home Assistant REST API (/api/states, /api/services). Works with any
                integration HA has for your brand: Home Connect, SmartThings, LG ThinQ,
                Miele, Matter, or ESPHome. Token: $HA_TOKEN.
  mqtt          an ESPHome (or any) device on an MQTT broker, e.g. an ESP32 on a BSH
                appliance's internal bus. Minimal stdlib MQTT 3.1.1 client below.
  homeconnect   the official Home Connect cloud API (Bosch/Siemens/Neff/Gaggenau/Thermador).
                Token: $HOME_CONNECT_TOKEN or `chorepilot control login` (device flow).

What an appliance can do (programs, options, their Home Connect keys) comes from its *machine*
profile (machines.py): a generic dishwasher/washer/dryer, or an exact model such as the Bosch
SMV6ZCX42E / WAT286H9SG / WTW876H8SG. A *fleet* is the set of appliances in
~/.config/chorepilot/control.json, each with a nickname ("dishwasher", "washer", "dryer").

Writes are split into plan() and apply(): plan() returns the exact requests that would be
sent (that is what --dry-run prints), apply() sends them. Secrets only ever come from the
environment or from files under ~/.config/chorepilot (mode 0600), never from the repo.
"""
from __future__ import annotations

import json
import os
import re
import socket
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

from .machines import kind_from_name, machine_for, norm
from .env import cache_dir as _cache_dir, config_dir as _config_dir, getenv, secret as _secret

# ------------------------------------------------------------------ vocabulary
# The generic dishwasher's vocabulary (what a plain single-dishwasher setup exposes).
_GENERIC_DW = machine_for("dishwasher")
PROGRAMS = {k: p.label for k, p in _GENERIC_DW.programs.items()}
PROGRAMS.update({
    "auto": "sensor-driven program that picks temperature from soil level (good default)",
    "eco": "Eco 50 C: lowest energy and water, longest run",
    "quick": "short program for lightly soiled dishes",
    "intensive": "70 C for pots and heavily soiled dishes",
    "normal": "standard 65 C program",
    "glass": "gentle low-temperature program for glasses",
    "night": "quiet program for overnight runs",
    "prerinse": "cold rinse only, no detergent, no heat",
    "machine_care": "cleaning cycle for the empty machine",
})
OPTIONS = {k: o.desc for k, o in _GENERIC_DW.options.items()}
STATES = ("offline", "idle", "ready", "delayed", "running", "paused", "action_required",
          "finished", "error")
ACTIVE = ("running", "delayed", "paused")

USER_AGENT = "chorepilot"


class BackendError(RuntimeError):
    pass


def config_dir():
    return _config_dir()


def cache_dir():
    return _cache_dir()


def write_secret(path, data: dict):
    """Write a JSON secret with 0600 permissions (created that way, never world-readable)."""
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.chmod(path, 0o600)


def http_json(method, url, headers=None, body=None, timeout=15):
    """Tiny JSON-over-HTTP helper. Returns (status, parsed-or-text). Never raises on 4xx/5xx."""
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except OSError as e:
        raise BackendError(f"{method} {url}: {e}") from e
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, raw.decode(errors="replace")


def human_time(secs):
    if secs is None:
        return None
    h, m = divmod(int(secs) // 60, 60)
    return f"{h} h {m} min" if h else f"{m} min"


def _status(**kw):
    s = {"state": "offline", "program": None, "remaining_s": None, "door": None,
         "remote_start_allowed": None, "child_lock": None, "options": {}, "detail": {}}
    s.update(kw)
    s["remaining"] = human_time(s["remaining_s"])   # small models misread raw seconds
    return s


def ha_key(bsh_key):
    """Home Assistant's name for a Home Connect key (homeassistant/components/home_connect/
    utils.py bsh_key_to_translation_key): `Dishcare.Dishwasher.Program.Eco50` ->
    `dishcare_dishwasher_program_eco_50`, `...Temperature.GC40` -> `..._temperature_g_c_40`."""
    rx = re.compile(r"(?<!^)(?=[A-Z])|(?=\d)(?<=\D)")
    return "_".join(rx.sub("_", part) for part in bsh_key.split(".")).lower()


class Backend:
    name = "base"
    #: True when status() can say whether the appliance permits remote start
    reports_remote_start = False
    machine = None
    appliance = "dishwasher"

    def status(self) -> dict:
        raise NotImplementedError

    def programs(self):
        return list(self.machine.programs) if self.machine else list(PROGRAMS)

    def plan(self, action: str, program: str | None = None, options: dict | None = None):
        """The concrete operations for a write action, as a list of dicts (no side effects)."""
        raise NotImplementedError

    def apply(self, ops):
        raise NotImplementedError

    def describe(self):
        return self.name

    def _bind(self, machine, appliance):
        self.machine = machine or machine_for("dishwasher")
        self.appliance = appliance or self.machine.kind


# ------------------------------------------------------------------ simulated
SIM_MINUTES = {k: p.minutes for k, p in _GENERIC_DW.programs.items()}
SIM_MINUTES.update({"auto": 150, "eco": 225, "quick": 45, "intensive": 140, "normal": 120,
                    "glass": 110, "night": 260, "prerinse": 15, "machine_care": 120})


class SimulatedBackend(Backend):
    """An appliance that lives in a JSON file. `speed` compresses time (600 = 1 min per 10 h).

    It behaves like the real model where the manual says how: run times per program, laundry
    doors lock while running, and remote start disarms when the door opens and (laundry) when
    a program ends -- so a chained "dry it after the wash" really needs someone to load the
    dryer and press Remote Start again."""
    name = "simulated"
    reports_remote_start = True

    def __init__(self, path=None, speed=None, door="closed", remote_start_allowed=True,
                 machine=None, appliance=None, child_lock=False):
        self._bind(machine, appliance)
        default = "sim-dishwasher.json" if self.appliance == "dishwasher" else \
            f"sim-{norm(self.appliance)}.json"
        self.path = path or os.path.join(cache_dir(), default)
        self.speed = float(speed or getenv("SIM_SPEED", 600))
        self._init = {"state": "ready", "program": None, "options": {}, "door": door,
                      "remote_start_allowed": remote_start_allowed, "end": None,
                      "paused_left": None, "delay_until": None, "speed": self.speed,
                      "child_lock": child_lock if self.machine.child_lock else None}
        if machine is not None and machine.model:
            self._init["model"] = machine.model

    def _laundry(self):
        return self.machine.kind in ("washer", "dryer")

    def _load(self):
        try:
            with open(self.path) as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = dict(self._init)
        st["speed"] = self.speed
        now = time.time()
        if st["state"] == "delayed" and st.get("delay_until") and now >= st["delay_until"]:
            st["state"] = "running"
        if st["state"] == "running" and st.get("end") and now >= st["end"]:
            st.update(state="finished", end=None)
            if self._laundry():                  # manuals: "deactivated ... once the programme has ended"
                st["remote_start_allowed"] = False
        return st

    def _save(self, st):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f)
        os.replace(tmp, self.path)

    def status(self):
        st = self._load()
        rem = None
        if st["state"] in ("running", "delayed") and st.get("end"):
            rem = max(0, int((st["end"] - time.time()) * self.speed))
        elif st["state"] == "paused":
            rem = int(st.get("paused_left") or 0)
        door = st.get("door")
        if self._laundry() and st["state"] in ("running", "delayed") and door == "closed":
            door = "locked"
        return _status(state=st["state"], program=st.get("program"), remaining_s=rem,
                       door=door, remote_start_allowed=st.get("remote_start_allowed"),
                       child_lock=st.get("child_lock"), options=st.get("options", {}),
                       door_opened=st.get("door_opened", 0),
                       detail={"backend": "simulated", "time_speedup": self.speed,
                               "model": self.machine.model})

    def set(self, **kw):
        """Test/demo hook (`chorepilot control sim`): open the door, arm remote start, ...

        Opening the door disarms remote start, like on the real machines."""
        st = self._load()
        if kw.get("door") == "open":
            if self._laundry() and st["state"] in ("running", "delayed"):
                raise BackendError("the door is locked while the program runs")
            kw.setdefault("remote_start_allowed", False)
            kw["door_opened"] = st.get("door_opened", 0) + 1
            if st["state"] == "finished":
                kw.setdefault("state", "ready")
                kw.setdefault("program", None)
        st.update(kw)
        self._save(st)

    def plan(self, action, program=None, options=None):
        return [{"op": action, "program": program, "options": options or {},
                 "target": f"file:{self.path}"}]

    def apply(self, ops):
        for op in ops:
            st = self._load()
            a, now = op["op"], time.time()
            if a == "start":
                o = op["options"] or {}
                secs = self.machine.run_minutes(op["program"], o) * 60
                delay = int(o.get("delay_minutes") or 0) * 60
                if o.get("finish_in_minutes"):            # laundry: "ready in" = end time
                    delay = max(0, int(o["finish_in_minutes"]) * 60 - secs)
                st.update(state="delayed" if delay else "running", program=op["program"],
                          options=o, delay_until=now + delay / self.speed,
                          end=now + (delay + secs) / self.speed, paused_left=None)
            elif a == "pause":
                st.update(state="paused", paused_left=max(0, (st.get("end") or now) - now)
                          * self.speed, end=None)
            elif a == "resume":
                st.update(state="running", end=now + (st.get("paused_left") or 0) / self.speed,
                          paused_left=None)
            elif a == "cancel":
                st.update(state="ready", program=None, end=None, paused_left=None, options={})
            self._save(st)
        return {"ok": True}


# ------------------------------------------------------------------ Home Assistant
# Defaults follow the entity names the HA Home Connect integration creates for a device
# named "Dishwasher" / "Washer" / "Dryer". Every name is overridable in control.json
# ({"ha": {"device": ..., "entities": {...}, "programs": {...}}}, or per appliance) for
# SmartThings / ThinQ / Miele / Matter / ESPHome devices, whose entities are named differently.
HA_ENTITIES = {
    "operation_state": "sensor.{device}_operation_state",
    "door": "binary_sensor.{device}_door",
    "remote_start": "binary_sensor.{device}_remote_start",
    "finish_time": "sensor.{device}_program_finish_time",
    "active_program": "select.{device}_active_program",
    "child_lock": "switch.{device}_child_lock",
    "pause": "button.{device}_pause_program",
    "resume": "button.{device}_resume_program",
    "stop": "button.{device}_stop_program",
}
HA_DEFAULT = {
    "device": "dishwasher",
    "entities": dict(HA_ENTITIES),
    # HA select options for the Home Connect active_program entity (= ha_key(program key))
    "programs": {k: ha_key(p.hc) for k, p in _GENERIC_DW.programs.items()},
}

_HA_STATE = {  # HA operation_state values (Home Connect enum, snake_cased) -> ours
    "inactive": "idle", "ready": "ready", "delayedstart": "delayed", "delayed_start": "delayed",
    "run": "running", "running": "running", "pause": "paused", "paused": "paused",
    "actionrequired": "action_required", "action_required": "action_required",
    "finished": "finished", "error": "error", "aborting": "running",
    "unavailable": "offline", "unknown": "offline",
}


def _norm_state(v):
    if v is None:
        return "offline"
    k = str(v).rsplit(".", 1)[-1].replace(" ", "_").lower()
    return _HA_STATE.get(k, _HA_STATE.get(k.replace("_", ""), k))


class HomeAssistantBackend(Backend):
    name = "ha"
    reports_remote_start = True

    def __init__(self, url=None, token=None, cfg=None, http=http_json, machine=None,
                 appliance=None):
        self._bind(machine, appliance)
        cfg = cfg or {}
        self.url = (url or cfg.get("url") or os.environ.get("HA_URL")
                    or "http://homeassistant.local:8123").rstrip("/")
        self.token = token or _secret("ha_token", "HA_TOKEN")
        dev = cfg.get("device") or norm(self.appliance)
        ents = {**HA_ENTITIES, **cfg.get("entities", {})}
        self.ent = {k: v.format(device=dev) for k, v in ents.items()}
        self.prog = {k: ha_key(p.hc) for k, p in self.machine.programs.items() if p.hc}
        self.prog.update(cfg.get("programs", {}))
        # set_program_and_options needs HA's device id (Settings > Devices > the URL's last part)
        self.device_id = cfg.get("device_id")
        self.http = http

    def _h(self):
        if not self.token:
            raise BackendError("set HA_TOKEN to a Home Assistant long-lived access token "
                               "(Profile > Security > Long-lived access tokens)")
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def _state(self, entity):
        code, js = self.http("GET", f"{self.url}/api/states/{entity}", self._h())
        if code == 404:
            return None
        if code != 200:
            raise BackendError(f"HA GET {entity}: HTTP {code} {js}")
        return js

    def status(self):
        op = self._state(self.ent["operation_state"])
        door = self._state(self.ent["door"])
        rs = self._state(self.ent["remote_start"])
        ft = self._state(self.ent["finish_time"])
        ap = self._state(self.ent["active_program"])
        cl = self._state(self.ent["child_lock"]) if self.machine.child_lock else None
        rem = None
        if ft and ft.get("state") not in (None, "unknown", "unavailable"):
            rem = _seconds_until(ft["state"])
        inv = {v: k for k, v in self.prog.items()}
        prog = ap.get("state") if ap else None
        dstate = door.get("state") if door else None
        return _status(
            state=_norm_state(op.get("state")) if op else "offline",
            program=inv.get(prog, prog if prog not in ("unknown", "unavailable") else None),
            remaining_s=rem,
            door={"on": "open", "off": "closed", "open": "open", "closed": "closed",
                  "locked": "locked"}.get(dstate),
            remote_start_allowed={"on": True, "off": False}.get(rs.get("state")) if rs else None,
            child_lock={"on": True, "off": False}.get(cl.get("state")) if cl else None,
            detail={"backend": "ha", "url": self.url, "model": self.machine.model})

    def _option_fields(self, options):
        out = {}
        for k, v in (options or {}).items():
            o = self.machine.options.get(k)
            if not o or not o.hc:
                continue
            field = ha_key(o.hc)
            if o.kind == "enum":
                out[field] = ha_key(o.hc_values[v])
            elif o.kind == "minutes":
                out[field] = int(v) * 60
            elif o.name in ("speed_perfect", "eco_perfect") and self.machine.kind != "dishwasher":
                out[field] = ha_key("LaundryCare.Common.EnumType.VarioPerfect."
                                    + ("SpeedPerfect" if o.name == "speed_perfect" else "EcoPerfect"))
            else:
                out[field] = bool(v)
        return out

    def plan(self, action, program=None, options=None):
        if action == "start":
            if program not in self.prog:
                raise BackendError(f"no HA mapping for program {program!r}")
            if options and self.device_id:
                body = {"device_id": self.device_id, "affects_to": "active_program",
                        "program": self.prog[program], **self._option_fields(options)}
                return [{"method": "POST",
                         "url": f"{self.url}/api/services/home_connect/set_program_and_options",
                         "body": body}]
            ops = [{"method": "POST", "url": f"{self.url}/api/services/select/select_option",
                    "body": {"entity_id": self.ent["active_program"],
                             "option": self.prog[program]}}]
            if options:
                ops[0]["note"] = ("options need the HA device_id in control.json (then "
                                  "home_connect.set_program_and_options is used); ignored here: "
                                  + ", ".join(options))
            return ops
        button = {"pause": "pause", "resume": "resume", "cancel": "stop"}[action]
        return [{"method": "POST", "url": f"{self.url}/api/services/button/press",
                 "body": {"entity_id": self.ent[button]}}]

    def apply(self, ops):
        for op in ops:
            code, js = self.http(op["method"], op["url"], self._h(), op["body"])
            if code not in (200, 201):
                raise BackendError(f"HA {op['url']}: HTTP {code} {js}")
        return {"ok": True}

    def notify(self, message, service="notify.notify"):
        dom, _, svc = service.partition(".")
        self.http("POST", f"{self.url}/api/services/{dom}/{svc}", self._h(),
                  {"message": message, "title": "ChorePilot"})


def _seconds_until(iso):
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(0, int((t - datetime.now(timezone.utc)).total_seconds()))


# ------------------------------------------------------------------ MQTT (stdlib client)
def _varint(n):
    out = bytearray()
    while True:
        b, n = n % 128, n // 128
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _mstr(s):
    b = s.encode() if isinstance(s, str) else s
    return struct.pack("!H", len(b)) + b


class MiniMQTT:
    """Just enough MQTT 3.1.1 for a CLI: connect, subscribe (collect retained), publish QoS 0."""

    def __init__(self, host, port=1883, user=None, password=None, client_id=None, timeout=5.0):
        self.host, self.port, self.timeout = host, int(port), timeout
        self.user, self.password = user, password
        self.client_id = client_id or f"chorepilot-{os.getpid()}"
        self.sock = None
        self._pid = 0

    def _send(self, ptype, flags, body):
        self.sock.sendall(bytes([ptype << 4 | flags]) + _varint(len(body)) + body)

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise BackendError("MQTT connection closed")
            buf += chunk
        return buf

    def _recv(self):
        h = self._recv_exact(1)[0]
        mult, length = 1, 0
        while True:
            b = self._recv_exact(1)[0]
            length += (b & 0x7F) * mult
            mult *= 128
            if not b & 0x80:
                break
        return h >> 4, h & 0x0F, self._recv_exact(length) if length else b""

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        flags = 0x02  # clean session
        payload = _mstr(self.client_id)
        if self.user:
            flags |= 0x80
            payload += _mstr(self.user)
            if self.password:
                flags |= 0x40
                payload += _mstr(self.password)
        self._send(1, 0, _mstr("MQTT") + bytes([4, flags]) + struct.pack("!H", 30) + payload)
        t, _, body = self._recv()
        if t != 2 or len(body) < 2 or body[1] != 0:
            raise BackendError(f"MQTT CONNACK refused (code {body[1] if len(body) > 1 else '?'})")
        return self

    def subscribe(self, topics):
        self._pid += 1
        body = struct.pack("!H", self._pid) + b"".join(_mstr(t) + b"\x00" for t in topics)
        self._send(8, 0x02, body)

    def collect(self, wait=1.0):
        """Read PUBLISH packets for `wait` seconds; return {topic: payload-str}."""
        got, end = {}, time.time() + wait
        while time.time() < end:
            self.sock.settimeout(max(0.05, end - time.time()))
            try:
                t, flags, body = self._recv()
            except socket.timeout:
                break
            if t == 3:
                n = struct.unpack("!H", body[:2])[0]
                topic = body[2:2 + n].decode()
                off = 2 + n + (2 if (flags >> 1) & 3 else 0)
                got[topic] = body[off:].decode(errors="replace")
        return got

    def publish(self, topic, payload, retain=False):
        self._send(3, 0x01 if retain else 0, _mstr(topic) + str(payload).encode())

    def close(self):
        if self.sock:
            try:
                self._send(14, 0, b"")
            except OSError:
                pass
            self.sock.close()
            self.sock = None


# ESPHome publishes <prefix>/<component>/<object_id>/state and listens on .../command.
MQTT_DEFAULT = {
    "prefix": "dishwasher",
    "state_topics": {
        "availability": "{prefix}/status",   # ESPHome: retained online / offline (last will)
        "operation_state": "{prefix}/sensor/operation_state/state",
        "program": "{prefix}/text_sensor/program/state",
        "remaining_s": "{prefix}/sensor/remaining_time/state",
        "door": "{prefix}/binary_sensor/door/state",
        "remote_start": "{prefix}/binary_sensor/remote_start/state",
    },
    "commands": {
        # a list of (topic, payload) per action; {program} is replaced with the mapped value,
        # and {<option>} with the option's value (e.g. {temperature}, {spin}, {dryness})
        "start": [["{prefix}/select/program/command", "{program}"],
                  ["{prefix}/button/start/command", "PRESS"]],
        "pause": [["{prefix}/button/pause/command", "PRESS"]],
        "resume": [["{prefix}/button/resume/command", "PRESS"]],
        "cancel": [["{prefix}/button/cancel/command", "PRESS"]],
    },
    "programs": {},  # canonical -> payload your firmware expects (identity by default)
    "remaining_unit": "s",  # or "min"
}


class MQTTBackend(Backend):
    name = "mqtt"

    def __init__(self, host=None, port=None, cfg=None, client_factory=MiniMQTT, machine=None,
                 appliance=None):
        self._bind(machine, appliance)
        cfg = cfg or {}
        c = {**MQTT_DEFAULT, **cfg}
        self.prefix = cfg.get("prefix") or norm(self.appliance)
        self.state_topics = {k: v.format(prefix=self.prefix)
                             for k, v in {**MQTT_DEFAULT["state_topics"],
                                          **cfg.get("state_topics", {})}.items()}
        self.commands = {**MQTT_DEFAULT["commands"], **cfg.get("commands", {})}
        self.prog = c.get("programs") or {}
        self.unit = c.get("remaining_unit", "s")
        hp = host or os.environ.get("MQTT_HOST") or c.get("host") or "localhost"
        self.host, _, p = hp.partition(":")
        self.port = int(port or p or os.environ.get("MQTT_PORT") or c.get("port") or 1883)
        self.user = _secret("mqtt_user", "MQTT_USER")
        self.password = _secret("mqtt_password", "MQTT_PASSWORD")
        self.reports_remote_start = "remote_start" in self.state_topics
        self.factory = client_factory

    def _client(self):
        return self.factory(self.host, self.port, self.user, self.password).connect()

    def status(self):
        cli = self._client()
        try:
            cli.subscribe(list(self.state_topics.values()))
            got = cli.collect(wait=float(getenv("MQTT_WAIT", 1.0)))
        finally:
            cli.close()
        val = {k: got.get(t) for k, t in self.state_topics.items()}
        if val.get("availability") == "offline":
            return _status(detail={"backend": "mqtt", "broker": f"{self.host}:{self.port}",
                                   "note": "device reports offline (ESPHome last will)"})
        val.pop("availability", None)
        if not any(v is not None for v in val.values()):
            return _status(detail={"backend": "mqtt", "broker": f"{self.host}:{self.port}",
                                   "note": "no retained state on the status topics"})
        rem = None
        if val.get("remaining_s") not in (None, "", "nan", "unknown"):
            try:
                rem = int(float(val["remaining_s"]) * (60 if self.unit == "min" else 1))
            except ValueError:
                pass
        inv = {v: k for k, v in self.prog.items()}
        door = val.get("door")
        rs = val.get("remote_start")
        return _status(
            state=_norm_state(val.get("operation_state")), program=inv.get(val.get("program"),
                                                                         val.get("program")),
            remaining_s=rem,
            door={"ON": "open", "OFF": "closed"}.get(str(door).upper()) if door else None,
            remote_start_allowed={"ON": True, "OFF": False}.get(str(rs).upper()) if rs else None,
            detail={"backend": "mqtt", "broker": f"{self.host}:{self.port}", "raw": val})

    def plan(self, action, program=None, options=None):
        if action not in self.commands or not self.commands[action]:
            raise BackendError(f"no MQTT command configured for {action!r}")
        pv = self.prog.get(program, program) if program else ""
        fields = {k: v for k, v in (options or {}).items()}

        class _Keep(dict):
            def __missing__(self, k):
                return "{" + k + "}"
        return [{"topic": t.format(prefix=self.prefix),
                 "payload": p.format_map(_Keep(program=pv, **fields))}
                for t, p in self.commands[action]]

    def apply(self, ops):
        cli = self._client()
        try:
            for op in ops:
                cli.publish(op["topic"], op["payload"])
        finally:
            cli.close()
        return {"ok": True}


# ------------------------------------------------------------------ Home Connect API
HC_PROGRAMS = {k: p.hc for k, p in _GENERIC_DW.programs.items()}
HC_OPTIONS = {k: o.hc for k, o in _GENERIC_DW.options.items()}
HC_TYPE = "application/vnd.bsh.sdk.v1+json"
HC_TOKEN_FILE = "homeconnect-token.json"
_HC_LIST_CACHE = {}   # base -> (time, appliances): one listing serves the whole fleet


class HomeConnectBackend(Backend):
    """One Home Connect appliance. Which one: `haId` from control.json, else the first
    appliance of the right type whose model ("vib") matches the profile, else the first of
    the right type."""
    name = "homeconnect"
    reports_remote_start = True

    def __init__(self, base=None, token=None, ha_id=None, http=http_json, token_path=None,
                 machine=None, appliance=None):
        self._bind(machine, appliance)
        self.base = (base or os.environ.get("HOME_CONNECT_URL")
                     or "https://api.home-connect.com").rstrip("/")
        self.token_path = token_path or os.path.join(config_dir(), HC_TOKEN_FILE)
        self._token = token or os.environ.get("HOME_CONNECT_TOKEN")
        self.ha_id = ha_id or (os.environ.get("HOME_CONNECT_HAID")
                               if self.machine.kind == "dishwasher" else None)
        self.http = http

    # -- auth
    def token(self):
        if self._token:
            return self._token
        try:
            with open(self.token_path) as f:
                tok = json.load(f)
        except (OSError, ValueError):
            raise BackendError("no Home Connect token: set HOME_CONNECT_TOKEN or run "
                               "`chorepilot control login --backend homeconnect`") from None
        if tok.get("expires_at", 0) < time.time() + 60 and tok.get("refresh_token"):
            tok = refresh_token(self.base, tok, self.http)
            write_secret(self.token_path, tok)
        self._token = tok["access_token"]
        return self._token

    def _h(self, write=False):
        h = {"Authorization": f"Bearer {self.token()}", "Accept": HC_TYPE,
             "Accept-Language": "en-GB"}
        if write:
            h["Content-Type"] = HC_TYPE
        return h

    def _get(self, path):
        code, js = self.http("GET", f"{self.base}{path}", self._h())
        if code == 429:
            raise BackendError("Home Connect rate limit hit (HTTP 429: 1000/day, 50/min, "
                               "5 starts/min); back off and retry later")
        return code, js

    def appliances(self):
        hit = _HC_LIST_CACHE.get((self.base, id(self.http)))
        if hit and time.time() - hit[0] < 60:
            return hit[1]
        code, js = self._get("/api/homeappliances")
        if code != 200:
            raise BackendError(f"Home Connect list appliances: HTTP {code} {js}")
        lst = js["data"]["homeappliances"]
        _HC_LIST_CACHE[(self.base, id(self.http))] = (time.time(), lst)
        return lst

    def appliance_id(self):
        if self.ha_id:
            return self.ha_id
        want = self.machine.hc_type
        same = [a for a in self.appliances() if a.get("type") == want]
        if not same:
            raise BackendError(f"no {want.lower()} on this Home Connect account (pair it in the "
                               "Home Connect app first)")
        model = (self.machine.model or "").upper()
        exact = [a for a in same if model and model in str(a.get("vib", "")).upper()]
        self.ha_id = (exact or same)[0]["haId"]
        return self.ha_id

    appliance_ha_id = appliance_id

    def _connected(self, ha):
        for a in self.appliances():
            if a.get("haId") == ha:
                return a.get("connected", True)
        return True

    def status(self, light=False):
        ha = self.appliance_id()
        code, js = self._get(f"/api/homeappliances/{ha}/status")
        if code == 409 or not self._connected(ha):   # appliance offline / not connected
            return _status(detail={"backend": "homeconnect", "haId": ha, "error": js})
        if code != 200:
            raise BackendError(f"Home Connect status: HTTP {code} {js}")
        st = {i["key"]: i.get("value") for i in js["data"]["status"]}
        prog, rem, opts = None, None, {}
        acode, ajs = self._get(f"/api/homeappliances/{ha}/programs/active")
        if acode == 200:
            inv = {p.hc: k for k, p in self.machine.programs.items() if p.hc}
            key = ajs["data"].get("key")
            prog = inv.get(key, key)
            for o in ajs["data"].get("options", []):
                if o["key"] == "BSH.Common.Option.RemainingProgramTime":
                    rem = int(o["value"])
                for name, spec in self.machine.options.items():
                    if spec.hc == o["key"] and spec.kind == "enum":
                        back = {v: k for k, v in spec.hc_values.items()}
                        if o.get("value") in back:
                            opts[name] = back[o["value"]]
        child = None
        if self.machine.child_lock and not light:
            ccode, cjs = self._get(f"/api/homeappliances/{ha}/settings/BSH.Common.Setting.ChildLock")
            if ccode == 200:
                child = cjs["data"].get("value")
        door = st.get("BSH.Common.Status.DoorState")
        return _status(
            state=_norm_state(st.get("BSH.Common.Status.OperationState")), program=prog,
            remaining_s=rem, options=opts, child_lock=child,
            door=door.rsplit(".", 1)[-1].lower() if isinstance(door, str) else None,
            remote_start_allowed=st.get("BSH.Common.Status.RemoteControlStartAllowed"),
            local_control=st.get("BSH.Common.Status.LocalControlActive"),
            remote_control=st.get("BSH.Common.Status.RemoteControlActive"),
            detail={"backend": "homeconnect", "haId": ha, "model": self.machine.model})

    def available_programs(self):
        """What the appliance itself says it can run right now (GET /programs/available)."""
        ha = self.appliance_id()
        code, js = self._get(f"/api/homeappliances/{ha}/programs/available")
        if code != 200:
            raise BackendError(f"Home Connect available programs: HTTP {code} {js} (the "
                               "appliance must be on and connected)")
        return [p["key"] for p in js["data"].get("programs", [])]

    def _hc_option(self, name, value):
        o = self.machine.options.get(name)
        if not o or not o.hc or value in (None, False):
            return None
        if o.kind == "enum":
            return {"key": o.hc, "value": o.hc_values[value]}
        if o.kind == "minutes":
            return {"key": o.hc, "value": int(value) * 60, "unit": "seconds"} if value else None
        if name in ("speed_perfect", "eco_perfect") and self.machine.kind != "dishwasher":
            return {"key": o.hc, "value": "LaundryCare.Common.EnumType.VarioPerfect."
                    + ("SpeedPerfect" if name == "speed_perfect" else "EcoPerfect")}
        return {"key": o.hc, "value": bool(value)}

    def plan(self, action, program=None, options=None):
        ha = self.ha_id or "{haId}"
        root = f"{self.base}/api/homeappliances/{ha}"
        if action == "start":
            p = self.machine.programs.get(program)
            if p is None or not p.hc:
                raise BackendError(f"{program!r} can't be started over Home Connect on this "
                                   "model; start it at the machine")
            opts = [x for x in (self._hc_option(k, v) for k, v in (options or {}).items()) if x]
            if self.machine.model is None and self.machine.kind == "dishwasher":
                for x in opts:      # generic profile keeps the original, unit-less body
                    x.pop("unit", None)
            body = {"data": {"key": p.hc, **({"options": opts} if opts else {})}}
            return [{"method": "PUT", "url": f"{root}/programs/active", "body": body}]
        if action == "cancel":
            return [{"method": "DELETE", "url": f"{root}/programs/active", "body": None}]
        cmd = {"pause": "BSH.Common.Command.PauseProgram",
               "resume": "BSH.Common.Command.ResumeProgram"}[action]
        return [{"method": "PUT", "url": f"{root}/commands/{cmd}",
                 "body": {"data": {"key": cmd, "value": True}}}]

    def apply(self, ops):
        ha = self.appliance_id()
        for op in ops:
            url = op["url"].replace("{haId}", ha)
            code, js = self.http(op["method"], url, self._h(write=op["body"] is not None),
                                 op["body"])
            if code == 429:
                raise BackendError("Home Connect rate limit hit (HTTP 429)")
            if code == 409:
                raise BackendError(f"the {self.appliance} refused (HTTP 409): {_hc_err(js)}")
            if code not in (200, 202, 204):
                raise BackendError(f"Home Connect {op['method']} {url}: HTTP {code} {js}")
        return {"ok": True}


def _hc_err(js):
    try:
        e = js["error"]
        return f"{e.get('key')}: {e.get('description', '')}".strip()
    except (TypeError, KeyError):
        return str(js)


def device_flow_login(base=None, client_id=None, client_secret=None, scope=None,
                      http=http_json, log=print, sleep=time.sleep):
    """OAuth 2.0 device authorization grant against Home Connect. Returns the token dict."""
    base = (base or os.environ.get("HOME_CONNECT_URL") or "https://api.home-connect.com").rstrip("/")
    client_id = client_id or _secret("home_connect_client_id", "HOME_CONNECT_CLIENT_ID")
    client_secret = client_secret or _secret("home_connect_client_secret",
                                             "HOME_CONNECT_CLIENT_SECRET")
    if not client_id:
        raise BackendError("set HOME_CONNECT_CLIENT_ID (register an app at "
                           "developer.home-connect.com, OAuth flow: Device Flow)")
    scope = scope or "IdentifyAppliance Monitor Control Settings"
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    code, js = http("POST", f"{base}/security/oauth/device_authorization", form,
                    urllib.parse.urlencode({"client_id": client_id, "scope": scope}).encode())
    if code != 200:
        raise BackendError(f"device authorization failed: HTTP {code} {js}")
    log(f"Open {js.get('verification_uri_complete') or js['verification_uri']} and enter "
        f"code {js['user_code']} (expires in {js.get('expires_in', '?')} s)")
    interval = int(js.get("interval", 5))
    deadline = time.time() + int(js.get("expires_in", 600))
    body = {"grant_type": "device_code", "device_code": js["device_code"], "client_id": client_id}
    if client_secret:
        body["client_secret"] = client_secret
    while time.time() < deadline:
        sleep(interval)
        code, tok = http("POST", f"{base}/security/oauth/token", form,
                         urllib.parse.urlencode(body).encode())
        if code == 200 and isinstance(tok, dict) and "access_token" in tok:
            tok["expires_at"] = time.time() + int(tok.get("expires_in", 86400))
            tok["client_id"] = client_id
            return tok
        err = tok.get("error") if isinstance(tok, dict) else None
        if err == "slow_down":
            interval += 5
        elif err not in ("authorization_pending", None):
            raise BackendError(f"device flow failed: {tok}")
    raise BackendError("device flow timed out")


def refresh_token(base, tok, http=http_json):
    body = {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]}
    secret = _secret("home_connect_client_secret", "HOME_CONNECT_CLIENT_SECRET")
    if secret:
        body["client_secret"] = secret
    code, new = http("POST", f"{base}/security/oauth/token",
                     {"Content-Type": "application/x-www-form-urlencoded"},
                     urllib.parse.urlencode(body).encode())
    if code != 200:
        raise BackendError(f"token refresh failed: HTTP {code} {new}")
    new["expires_at"] = time.time() + int(new.get("expires_in", 86400))
    new.setdefault("refresh_token", tok["refresh_token"])
    return new


# ------------------------------------------------------------------ factory + fleet
BACKENDS = {"simulated": SimulatedBackend, "ha": HomeAssistantBackend, "mqtt": MQTTBackend,
            "homeconnect": HomeConnectBackend}


def config_path():
    return os.path.join(config_dir(), "control.json")


def load_config(path=None):
    """control.json; without one, the program appliances from ChorePilot's house.json."""
    default = path is None
    path = path or config_path()
    try:
        with open(path) as f:
            return json.load(f)
    except OSError:
        if default:
            from .housecfg import _read, house_path, program_fleet_cfg
            house = _read(house_path())
            if house:
                return program_fleet_cfg(house) or {}
        return {}


def _backend_for(name, cfg, machine, appliance, entry=None):
    entry = entry or {}

    def section(key):
        return {**cfg.get(key, {}), **entry.get(key, {})}
    if name == "simulated":
        return SimulatedBackend(machine=machine, appliance=appliance, **section("simulated"))
    if name == "ha":
        return HomeAssistantBackend(cfg=section("ha") or None, machine=machine, appliance=appliance)
    if name == "mqtt":
        return MQTTBackend(cfg=section("mqtt") or None, machine=machine, appliance=appliance)
    if name == "homeconnect":
        hc = section("homeconnect")
        return HomeConnectBackend(base=hc.get("url"), ha_id=entry.get("haId") or hc.get("haId"),
                                  machine=machine, appliance=appliance)
    raise KeyError(f"unknown backend {name!r}; choose from {', '.join(BACKENDS)}")


def make_backend(name=None, cfg=None, appliance=None):
    """The backend for one appliance (default: the fleet's default appliance)."""
    return make_fleet(cfg, backend=name).get(appliance)


class Fleet:
    """The appliances you can control, by nickname: {"dishwasher": backend, "washer": ...}."""

    def __init__(self, members, default=None, aliases=None):
        if not members:
            raise KeyError("no appliances configured")
        self.members = dict(members)
        self.default = default if default in self.members else next(iter(self.members))
        self.aliases = {norm(k): v for k, v in (aliases or {}).items()}

    @classmethod
    def single(cls, backend):
        return cls({backend.appliance: backend})

    def names(self):
        return list(self.members)

    def __len__(self):
        return len(self.members)

    def __iter__(self):
        return iter(self.members.items())

    def resolve(self, name=None, kind_hint=None):
        """Nickname, alias, device type or model -> nickname. Raises KeyError."""
        if name in (None, "", "default"):
            if len(self.members) == 1:
                return self.default
            if kind_hint:
                same = [n for n, b in self.members.items() if b.machine.kind == kind_hint]
                if len(same) == 1:
                    return same[0]
            return self.default
        n = norm(name)
        if n in self.members:
            return n
        for k in self.members:
            if norm(k) == n:
                return k
        if n in self.aliases:
            return self.aliases[n]
        kind = kind_from_name(n)
        for k, b in self.members.items():
            m = b.machine
            if (kind and m.kind == kind) or (m.model and m.model.lower() == n):
                return k
        raise KeyError(f"no appliance called {name!r}; you have: {', '.join(self.members)}")

    def get(self, name=None):
        return self.members[self.resolve(name)]


def make_fleet(cfg=None, backend=None):
    """Build the fleet from control.json. A config without "appliances" is the original
    single-dishwasher setup (backend from "backend", default simulated)."""
    cfg = cfg if cfg is not None else load_config()
    default_backend = backend or getenv("BACKEND") or cfg.get("backend") \
        or "simulated"
    entries = cfg.get("appliances")
    if not entries:
        m = machine_for("dishwasher", cfg.get("model"))
        return Fleet({"dishwasher": _backend_for(default_backend, cfg, m, "dishwasher")})
    members, aliases = {}, {}
    for nick, e in entries.items():
        e = e or {}
        m = machine_for(e.get("type") or kind_from_name(nick), e.get("model"))
        for prog, key in (e.get("program_keys") or {}).items():
            # fix a Home Connect key that `chorepilot control programs` showed is wrong
            if prog not in m.programs:
                raise KeyError(f"{nick}: program_keys names unknown program {prog!r}")
            m.programs[prog].hc, m.programs[prog].hc_note = key or None, "set in control.json"
        be_name = backend or getenv("BACKEND") or e.get("backend") \
            or default_backend
        members[nick] = _backend_for(be_name, cfg, m, nick, e)
        for a in e.get("aliases", []):
            aliases[a] = nick
    return Fleet(members, cfg.get("default"), aliases)


#: control.json for a Bosch Home Connect dishwasher + washer + dryer (example models)
PRESETS = {
    "bosch": {
        "backend": "homeconnect",
        "default": "dishwasher",
        "appliances": {
            "dishwasher": {"model": "SMV6ZCX42E", "aliases": ["dishes", "dish washer"]},
            "washer": {"model": "WAT286H9SG", "aliases": ["washing machine"]},
            "dryer": {"model": "WTW876H8SG", "aliases": ["tumble dryer", "drier"]},
        },
    },
}
