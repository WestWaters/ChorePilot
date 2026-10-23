"""Push events: devices tell us when they change, instead of being polled.

    chorepilot events push        # HA websocket + Home Connect SSE + MQTT, polling as fallback

Sources (stdlib only; each reconnects with exponential backoff and jitter):
  Home Assistant  websocket /api/websocket: auth, subscribe_events state_changed. An event for
                  one of our entities (a plugin device's entity / extra entities, or a program
                  appliance's operation_state / door / remote_start ... entities) marks that
                  device changed.
  Home Connect    SSE GET /api/homeappliances/events (text/event-stream; STATUS / EVENT / NOTIFY
                  / CONNECTED / DISCONNECTED / KEEP-ALIVE). An item for one of our haIds marks it
                  changed (KEEP-ALIVE is ignored; only keys that can change a chore count, so the
                  1000-requests-a-day quota is not spent on progress ticks).
  MQTT            one persistent subscription to every device's state topics (PINGREQ keepalive).

A push only says "this device changed". The runner, in ONE thread (SQLite), then re-reads that
device and runs events.check_device: the same diff -> events -> chore rules as polling, so the
two can never disagree. Devices no push source covers are polled every --interval seconds; the
covered ones get a slow safety poll (--safety) in case a push was lost.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import queue
import random
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .appliance import (BackendError, HomeAssistantBackend, HomeConnectBackend, MiniMQTT,
                        MQTTBackend)
from .env import secret

HC_KEYS = ("OperationState", "DoorState", "ProgramFinished", "ProgramAborted",
           "RemoteControlStartAllowed", "LocalControlActive", "ActiveProgram",
           "PowerState", "DoorAlarm", "Alarm", "Temperature", "SuperMode")


# ------------------------------------------------------------------ backoff
class Backoff:
    """1, 2, 4 ... max seconds with +-20% jitter; reset once a connection has lasted."""

    def __init__(self, base=1.0, cap=300.0, rand=random.random):
        self.base, self.cap, self.rand, self.n = base, cap, rand, 0

    def next(self):
        d = min(self.cap, self.base * (2 ** self.n))
        self.n += 1
        return d * (0.8 + 0.4 * self.rand())

    def reset(self):
        self.n = 0


def _loop(name, connect_once, stop, log, sleep=time.sleep, backoff=None, stable_s=60):
    """Run connect_once() until stop() is true; reconnect with backoff on any failure."""
    bo = backoff or Backoff()
    while not stop():
        t0 = time.time()
        try:
            connect_once()
            why = "closed"
        except Exception as e:  # noqa: BLE001 - every failure means "reconnect later"
            why = f"{type(e).__name__}: {e}"
        if stop():
            return
        if time.time() - t0 > stable_s:
            bo.reset()
        d = bo.next()
        log(f"  [push      ] {name}: {why}; reconnecting in {d:.0f}s")
        end = time.time() + d
        while not stop() and time.time() < end:
            sleep(min(1.0, end - time.time()))


# ------------------------------------------------------------------ a tiny websocket client
class WSClosed(ConnectionError):
    pass


class WebSocket:
    """RFC 6455 client: text frames, masking, fragmentation, ping/pong, close. Enough for the
    HA websocket API. `sock` can be given (tests: a socketpair)."""

    def __init__(self, url, timeout=30, sock=None, headers=None):
        u = urlparse(url)
        self.host = u.hostname
        self.port = u.port or (443 if u.scheme == "wss" else 80)
        self.path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        if sock is None:
            sock = socket.create_connection((self.host, self.port), timeout=timeout)
            if u.scheme == "wss":
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
        self.sock = sock
        self.sock.settimeout(timeout)
        self.buf = b""
        self._handshake(headers or {})

    def _handshake(self, headers):
        key = base64.b64encode(os.urandom(16)).decode()
        lines = [f"GET {self.path} HTTP/1.1", f"Host: {self.host}:{self.port}",
                 "Upgrade: websocket", "Connection: Upgrade", f"Sec-WebSocket-Key: {key}",
                 "Sec-WebSocket-Version: 13"] + [f"{k}: {v}" for k, v in headers.items()]
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WSClosed("closed during handshake")
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status + b" ":
            raise WSClosed(f"handshake refused: {status.decode(errors='replace')}")
        want = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if want.lower() not in head.lower():
            raise WSClosed("bad Sec-WebSocket-Accept")

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WSClosed("connection closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _frame(self, opcode, data):
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack("!Q", n)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def send(self, obj):
        self._frame(0x1, (obj if isinstance(obj, str) else json.dumps(obj)).encode())

    def recv(self):
        """The next text message (str). Answers pings; raises WSClosed on close."""
        parts = []
        while True:
            b1, b2 = self._read(2)
            fin, op = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._read(8))[0]
            mask = self._read(4) if b2 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if op == 0x9:
                self._frame(0xA, data)
                continue
            if op == 0xA:
                continue
            if op == 0x8:
                try:
                    self._frame(0x8, data[:2])
                except OSError:
                    pass
                raise WSClosed("server closed the websocket")
            parts.append(data)
            if fin:
                return b"".join(parts).decode(errors="replace")

    def recv_json(self):
        return json.loads(self.recv())

    def close(self):
        try:
            self._frame(0x8, struct.pack("!H", 1000))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# ------------------------------------------------------------------ Home Assistant
def ha_ws_url(http_url):
    u = urlparse(http_url.rstrip("/"))
    if u.netloc == "supervisor" and u.path.rstrip("/") == "/core":
        return "ws://supervisor/core/websocket"      # inside a Home Assistant add-on
    return f"{'wss' if u.scheme == 'https' else 'ws'}://{u.netloc}{u.path}/api/websocket"


def ha_session(ws, token, entity_map, emit, stop=lambda: False):
    """One authenticated HA websocket session: auth, subscribe state_changed, emit(device,
    info) for our entities until the socket closes or stop()."""
    first = ws.recv_json()
    if first.get("type") != "auth_required":
        raise ConnectionError(f"unexpected HA hello: {first.get('type')}")
    ws.send({"type": "auth", "access_token": token})
    got = ws.recv_json()
    if got.get("type") != "auth_ok":
        raise PermissionError("Home Assistant refused the token (auth_invalid)")
    ws.send({"id": 1, "type": "subscribe_events", "event_type": "state_changed"})
    while not stop():
        msg = ws.recv_json()
        if msg.get("type") == "result" and msg.get("id") == 1 and not msg.get("success"):
            raise ConnectionError(f"subscribe_events failed: {msg.get('error')}")
        if msg.get("type") != "event":
            continue
        data = (msg.get("event") or {}).get("data") or {}
        ent = data.get("entity_id")
        if ent in entity_map:
            new = (data.get("new_state") or {}).get("state")
            old = (data.get("old_state") or {}).get("state")
            if new != old:
                emit(entity_map[ent], {"source": "ha", "entity": ent, "state": new})


# ------------------------------------------------------------------ Home Connect SSE
def parse_sse(lines):
    """SSE (text/event-stream) lines -> {"event", "data", "id"} dicts, per the WHATWG rules
    (comments ignored, multi-line data joined with \\n, a blank line dispatches)."""
    ev = {"event": "message", "data": [], "id": None}
    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = line.rstrip("\r\n")
        if line == "":
            if ev["data"] or ev["event"] != "message":
                yield {"event": ev["event"], "data": "\n".join(ev["data"]), "id": ev["id"]}
            ev = {"event": "message", "data": [], "id": None}
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            ev["event"] = value
        elif field == "data":
            ev["data"].append(value)
        elif field == "id":
            ev["id"] = value


def hc_items(ev):
    """A Home Connect SSE event -> (haId, [items]). KEEP-ALIVE -> (None, [])."""
    kind = ev["event"]
    if kind == "KEEP-ALIVE":
        return None, []
    try:
        data = json.loads(ev["data"]) if ev["data"] else {}
    except ValueError:
        data = {}
    ha = data.get("haId") or ev.get("id")
    if kind in ("CONNECTED", "DISCONNECTED", "PAIRED", "DEPAIRED"):
        return ha, [{"key": kind}]
    return ha, list(data.get("items") or [])


def hc_relevant(items):
    return any(it.get("key") in ("CONNECTED", "DISCONNECTED", "PAIRED", "DEPAIRED")
               or str(it.get("key", "")).rsplit(".", 1)[-1] in HC_KEYS for it in items)


def hc_session(lines, ha_map, emit, stop=lambda: False):
    for ev in parse_sse(lines):
        if stop():
            return
        ha, items = hc_items(ev)
        if ha in ha_map and hc_relevant(items):
            emit(ha_map[ha], {"source": "homeconnect", "event": ev["event"],
                              "keys": [it.get("key") for it in items]})


def hc_open(base, token, timeout=90):
    """Open the account-wide event stream. Home Connect sends KEEP-ALIVE every ~55 s, so a
    90 s read timeout means the stream is dead."""
    req = urllib.request.Request(f"{base.rstrip('/')}/api/homeappliances/events",
                                 headers={"Authorization": f"Bearer {token}",
                                          "Accept": "text/event-stream"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise PermissionError("Home Connect token rejected (401)") from None
        if e.code == 429:
            raise ConnectionError("Home Connect rate limit (429)") from None
        raise ConnectionError(f"Home Connect events: HTTP {e.code}") from None


# ------------------------------------------------------------------ MQTT
class MQTTStream(MiniMQTT):
    """MiniMQTT plus a blocking read loop with keepalive pings."""

    def ping(self):
        self._send(12, 0, b"")

    def messages(self, stop=lambda: False, keepalive=20):
        last = time.time()
        while not stop():
            self.sock.settimeout(1.0)
            try:
                t, flags, body = self._recv()
            except socket.timeout:
                t = None
            if time.time() - last > keepalive:
                self.ping()
                last = time.time()
            if t == 3:
                n = struct.unpack("!H", body[:2])[0]
                topic = body[2:2 + n].decode()
                off = 2 + n + (2 if (flags >> 1) & 3 else 0)
                yield topic, body[off:].decode(errors="replace")


def mqtt_session(cli, topic_map, emit, stop=lambda: False):
    cli.subscribe(list(topic_map))
    seen = {}
    for topic, payload in cli.messages(stop):
        if topic in topic_map and seen.get(topic) != payload:
            first = topic not in seen
            seen[topic] = payload
            if not first:               # the retained value on subscribe is not a change
                emit(topic_map[topic], {"source": "mqtt", "topic": topic, "payload": payload})


# ------------------------------------------------------------------ which device is where
def push_map(house):
    """{'ha': {entity: nick}, 'hc': {haId: nick}, 'mqtt': {(host, port): {topic: nick}},
    'hc_backend': a HomeConnectBackend for the token, 'ha_url', 'ha_token'}"""
    m = {"ha": {}, "hc": {}, "mqtt": {}, "hc_backend": None, "ha_url": None, "ha_token": None}

    def add_ha(url, token, ents, nick):
        m["ha_url"] = m["ha_url"] or url
        m["ha_token"] = m["ha_token"] or token
        for e in ents:
            if isinstance(e, str) and "." in e:
                m["ha"][e] = nick

    def add_mqtt(host, port, topics, nick):
        bucket = m["mqtt"].setdefault((host, int(port)), {})
        for t in topics:
            bucket[t] = nick
    if house.bridge:
        for nick, be in house.bridge.fleet:
            if isinstance(be, HomeAssistantBackend):
                add_ha(be.url, be.token, be.ent.values(), nick)
            elif isinstance(be, MQTTBackend):
                add_mqtt(be.host, be.port, be.state_topics.values(), nick)
            elif isinstance(be, HomeConnectBackend):
                try:
                    m["hc"][be.appliance_id()] = nick
                    m["hc_backend"] = m["hc_backend"] or be
                except BackendError:
                    pass
    for nick, d in house.devices.items():
        a = d.adapter
        if a.name in ("ha", "matter"):
            add_ha(a.url, a.token, [a.entity, *a.extra.values()], nick)
        elif a.name == "mqtt":
            add_mqtt(a.host, a.port, a.dtype.mqtt_state_topics(a.prefix).values(), nick)
        elif a.name == "homeconnect":
            try:
                m["hc"][a.be.appliance_id()] = nick
                m["hc_backend"] = m["hc_backend"] or a.be
            except BackendError:
                pass
    return m


# ------------------------------------------------------------------ the runner
class PushRunner:
    def __init__(self, house, log=print, interval=30, safety=900, debounce=2.0,
                 ws_factory=WebSocket, hc_opener=hc_open, mqtt_factory=MQTTStream):
        self.house, self.log = house, log
        self.interval, self.safety, self.debounce = interval, safety, debounce
        self.ws_factory, self.hc_opener, self.mqtt_factory = ws_factory, hc_opener, mqtt_factory
        self.q = queue.Queue()
        self.stopped = threading.Event()
        self.covered = set()
        self.threads = []

    def emit(self, nick, info):
        self.q.put((nick, info))

    def stop(self):
        self.stopped.set()

    def sources(self):
        m = push_map(self.house)
        stop = self.stopped.is_set
        out = []
        if m["ha"] and m["ha_token"]:
            url, tok, ents = m["ha_url"], m["ha_token"], dict(m["ha"])

            def ha_once():
                ws = self.ws_factory(ha_ws_url(url))
                try:
                    self.log(f"  [push      ] home assistant: connected ({len(ents)} entities)")
                    ha_session(ws, tok, ents, self.emit, stop)
                finally:
                    ws.close()
            out.append(("home assistant", ha_once, set(ents.values())))
        elif m["ha"]:
            self.log("  [push      ] home assistant: no HA_TOKEN, polling instead")
        if m["hc"] and m["hc_backend"] is not None:
            be, has = m["hc_backend"], dict(m["hc"])

            def hc_once():
                try:
                    tok = be.token()
                except BackendError as e:
                    raise PermissionError(str(e)) from None
                try:
                    resp = self.hc_opener(be.base, tok)
                except PermissionError:
                    be._token = None            # refresh from the token file next time
                    raise
                with resp:
                    self.log(f"  [push      ] home connect: event stream open ({len(has)} appliances)")
                    hc_session(resp, has, self.emit, stop)
            out.append(("home connect", hc_once, set(has.values())))
        for (host, port), topics in m["mqtt"].items():
            def mq_once(host=host, port=port, topics=dict(topics)):
                cli = self.mqtt_factory(host, port, secret("mqtt_user", "MQTT_USER"),
                                        secret("mqtt_password", "MQTT_PASSWORD")).connect()
                try:
                    self.log(f"  [push      ] mqtt {host}:{port}: subscribed ({len(topics)} topics)")
                    mqtt_session(cli, topics, self.emit, stop)
                finally:
                    cli.close()
            out.append((f"mqtt {host}:{port}", mq_once, set(topics.values())))
        return out

    def start(self):
        for name, once, nicks in self.sources():
            self.covered |= nicks
            t = threading.Thread(target=_loop, args=(name, once, self.stopped.is_set, self.log),
                                 daemon=True, name=f"push-{name}")
            t.start()
            self.threads.append(t)
        return self

    def run(self, rounds=None):
        """The single consumer: debounce pushes per device, check it, poll the rest."""
        from .events import _sources, check_device, poll_once
        self.start()
        everything = {n for n, _, _ in _sources(self.house)}
        uncovered = everything - self.covered
        self.log(f"  [push      ] push: {', '.join(sorted(self.covered)) or 'nothing'}; "
                 f"polled every {self.interval:g}s: {', '.join(sorted(uncovered)) or 'nothing'}")
        poll_once(self.house, self.log)          # baseline snapshot for every device
        last_poll = last_safety = time.time()
        n = 0
        while not self.stopped.is_set() and (rounds is None or n < rounds):
            n += 1
            due = {}
            try:
                nick, info = self.q.get(timeout=1.0)
                due[nick] = info
                end = time.time() + self.debounce
                while time.time() < end:
                    try:
                        nick, info = self.q.get(timeout=max(0.01, end - time.time()))
                        due[nick] = info
                    except queue.Empty:
                        break
            except queue.Empty:
                pass
            for nick, info in due.items():
                self.log(f"  [push      ] {nick} changed ({info.get('source')})")
                check_device(self.house, nick, self.log)
            now = time.time()
            if uncovered and now - last_poll >= self.interval:
                poll_once(self.house, self.log, only=uncovered)
                last_poll = now
            if self.covered and now - last_safety >= self.safety:
                poll_once(self.house, self.log, only=self.covered)
                last_safety = now
        self.stop()


def run_push(house, interval=30, safety=900, log=None):
    log = log or (lambda m: print(m, flush=True))
    r = PushRunner(house, log=log, interval=interval, safety=safety)
    try:
        r.run()
    except KeyboardInterrupt:
        r.stop()

