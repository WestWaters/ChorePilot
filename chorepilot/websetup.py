"""Onboarding and settings in the browser, on top of the same code as `chorepilot setup`.

Every step calls onboard.Setup with the flags the terminal wizard would get, so the browser and
the terminal can never disagree about what "add a member" or "link Home Assistant" means, and
`chorepilot setup` keeps working exactly as before.

Who may do what:
  * creating the FIRST owner: only while the household has no owner, and only from this machine
    (a loopback client, a loopback Host header, no proxy forwarding headers). A dashboard that is
    reachable through Tailscale or a tunnel can never be claimed from outside.
  * everything else: the signed-in owner (members, devices, model, Telegram, agents, remote
    access). Adults can read the checklist.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse

from . import onboard, qr
from .appliance import BackendError
from .chores import ROLES, ChoreError

FORWARD_HEADERS = ("X-Forwarded-For", "X-Forwarded-Host", "Forwarded", "X-Real-IP",
                   "CF-Connecting-IP", "CF-Ray", "Tailscale-User-Login", "Tailscale-Funnel-Request",
                   "X-Forwarded-Proto", "X-Forwarded-Port", "X-Forwarded-Server", "Via")
LOOPBACK = ("127.0.0.1", "::1", "localhost")


class WebSetupError(ValueError):
    pass


def is_local(handler):
    """A request made on this machine, not through a proxy or tunnel."""
    ip = handler.client_address[0]
    host = (handler.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
    if ip not in ("127.0.0.1", "::1", "::ffff:127.0.0.1") or host not in LOOPBACK:
        return False
    return not any(handler.headers.get(h) for h in FORWARD_HEADERS)


def _args(*flags):
    from .cli import build_parser
    return build_parser().parse_args(["setup", "--non-interactive", "--no-probe", *flags])


def _setup(db, *flags, http=None, **kw):
    lines = []
    io = onboard.IO(False, out=lambda m="": lines.append(str(m)))
    st = onboard.Setup(io, _args(*flags), db=db, **({"http": http} if http else {}), **kw)
    return st, lines


def _clean(lines):
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("==")]


def _pin_ok(pin):
    if not re.fullmatch(r"\d{4,12}", str(pin or "")):
        raise WebSetupError("a PIN is 4 to 12 digits")
    return str(pin)


# ------------------------------------------------------------------ status
def checklist(db, cfg):
    from .mcp import Tokens
    from .telegram import TelegramError, load_token
    try:
        load_token()
        tg = True
    except TelegramError:
        tg = False
    owners = db.owners()
    people = db.people()
    linked = sum(1 for p in people if json.loads(p["handles"] or "{}").get("telegram_id"))
    llm = cfg.get("llm") or {}
    rows = [
        {"key": "owner", "title": "Owner and PIN", "done": bool(owners) and all(
            db.has_second_factor(o["name"]) for o in owners),
         "note": ", ".join(o["name"] for o in owners) or "No owner yet"},
        {"key": "members", "title": "Household members", "done": len(people) > len(owners),
         "note": f"{len(people)} {'person' if len(people) == 1 else 'people'}"},
        {"key": "devices", "title": "Devices", "done": bool(cfg.get("devices")) and
         cfg.get("_preset") != "demo",
         "note": f"{len(cfg.get('devices') or {})} connected" if cfg.get("_preset") != "demo"
         else "Using the demo house"},
        {"key": "model", "title": "Assistant model", "done": bool(llm.get("url")),
         "note": llm.get("model") or llm.get("url") or "Not set"},
        {"key": "chores", "title": "Chores", "done": bool(db.chores()),
         "note": f"{len(db.chores())} chores"},
        {"key": "phone", "title": "Phone and agents", "done": tg or bool(Tokens().list()),
         "note": (f"Telegram, {linked} linked" if tg else "Telegram not set up")
         + (f"; {len(Tokens().list())} agent token(s)" if Tokens().list() else "")},
    ]
    return rows


# ------------------------------------------------------------------ owner + members
def create_owner(db, body):
    if db.owners():
        raise WebSetupError("this household already has an owner; sign in instead")
    name = str(body.get("name") or "").strip()[:40]
    if not name:
        raise WebSetupError("your name, please")
    pin = _pin_ok(body.get("pin"))
    flags = ["--owner", name, "--owner-pin", pin,
             "--household", str(body.get("household") or "Our home").strip()[:60] or "Our home",
             "--unit", "F" if str(body.get("unit") or "").upper() == "F" else "C"]
    st, lines = _setup(db, *flags)
    try:
        p = st.owner()
    except (onboard.SetupError, ChoreError) as e:
        raise WebSetupError(str(e)) from None
    return p, _clean(lines)


def add_member(db, body):
    name = str(body.get("name") or "").strip()[:40]
    role = str(body.get("role") or "adult").lower()
    if not name:
        raise WebSetupError("a name, please")
    if role not in ROLES or role == "owner":
        raise WebSetupError("role must be adult, kid, guest or staff")
    pin = str(body.get("pin") or "").strip()
    spec = f"{name}:{role}" + (f":{_pin_ok(pin)}" if pin else "")
    st, lines = _setup(db, "--member", spec)
    try:
        st.members()
    except (onboard.SetupError, ChoreError) as e:
        raise WebSetupError(str(e)) from None
    return db.person(name), _clean(lines)


def set_pin(db, body):
    name = str(body.get("name") or "")
    p = db.person(name, required=False)
    if not p:
        raise WebSetupError(f"no member called {name!r}")
    db.set_pin(p["name"], _pin_ok(body.get("pin")))
    return {"ok": True, "name": p["name"]}


def members(db):
    from .webview import PALETTE, initials
    out = []
    for p in db.people():
        h = json.loads(p["handles"] or "{}")
        out.append({"name": p["name"], "role": p["role"], "initials": initials(p["name"]),
                    "color": p["id"] % PALETTE, "pin": bool(p["pin_hash"]),
                    "totp": bool(p["totp_secret"]), "telegram": bool(h.get("telegram_id")),
                    "quiet_hours": p["quiet_hours"]})
    return out


# ------------------------------------------------------------------ model
def model_detect(http=None):
    found = onboard.detect_llms(**({"http": http} if http else {}))
    return [{"kind": r["kind"], "url": r["url"], "models": r["models"][:12]} for r in found]


def model_use(db, body, http=None):
    url = str(body.get("url") or "").strip()
    if url and not re.match(r"^https?://", url):
        raise WebSetupError("the model URL starts with http:// or https://")
    flags = ["--model-url", url or "none"]
    if body.get("model"):
        flags += ["--model-name", str(body["model"])[:120]]
    st, lines = _setup(db, *flags, http=http)
    llm = st.model()
    return {"llm": llm, "log": _clean(lines)}


def model_test(url, model=None, chat_fn=None):
    from .control import chat
    fn = chat_fn or chat
    t = time.time()
    try:
        out = fn(url, [{"role": "user", "content": "Reply with the single word: ready"}],
                 tools=None, model=model, timeout=60)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"no answer from {url}: {str(e)[:160]}"}
    msg = ((out.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return {"ok": True, "reply": msg.strip()[:80], "latency_s": round(time.time() - t, 2),
            "tok_s": (out.get("timings") or {}).get("predicted_per_second")}


# ------------------------------------------------------------------ devices
def simulated(db):
    st, lines = _setup(db, "--simulated")
    st.devices()
    return _clean(lines)


def ha_discover(url, token, http=None):
    try:
        msg, found = onboard.ha_discover(url, token, **({"http": http} if http else {}))
    except onboard.SetupError as e:
        raise WebSetupError(str(e)) from None
    return {"message": msg, "entities": found}


def ha_import(db, body, http=None):
    url, token = str(body.get("url") or "").strip(), str(body.get("token") or "").strip()
    if not url or not token:
        raise WebSetupError("the Home Assistant URL and a long-lived access token")
    flags = ["--ha-url", url, "--ha-token", token]
    keep = body.get("entities")
    st, lines = _setup(db, *flags, http=http)
    if keep is not None:
        st.a.ha_entities = [str(e) for e in keep]
    try:
        st._ha(url)
    except onboard.SetupError as e:
        raise WebSetupError(str(e)) from None
    st.save()
    return _clean(lines)


def mqtt(db, body):
    host = str(body.get("host") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9.\-]+(:\d{1,5})?", host):
        raise WebSetupError("the broker host, e.g. 192.168.1.20 or broker.local:1883")
    flags = ["--mqtt-host", host]
    if body.get("user"):
        flags += ["--mqtt-user", str(body["user"])]
        if body.get("password"):
            flags += ["--mqtt-password", str(body["password"])]
    st, lines = _setup(db, *flags)
    st._mqtt(host)
    st.save()
    return _clean(lines)


def hc_prompt(line):
    """The device-flow line -> (url, code). Only an https:// link is shown as a link: the page
    puts it in an <a href>, and a javascript: or data: URL there would run on our origin."""
    g = re.search(r"Open (https://\S+) and enter code (\S+)", str(line))
    return (g.group(1), g.group(2)) if g else None


class HCLogin:
    """Home Connect's device flow runs for minutes (the person signs in on their phone); it
    runs in a thread and the page polls it."""

    def __init__(self):
        self.state, self.lines, self.error, self.url, self.code = "idle", [], None, None, None
        self.lock = threading.Lock()

    def start(self, db_path, client_id, login=None):
        with self.lock:
            if self.state == "running":
                return
            self.state, self.lines, self.error, self.url, self.code = "running", [], None, None, None

        def out(m=""):
            m = str(m)
            self.lines.append(m)
            g = hc_prompt(m)
            if g:
                self.url, self.code = g

        def run():
            from .chores import ChoreDB
            db = ChoreDB(db_path)
            try:
                io = onboard.IO(False, out=out)
                st = onboard.Setup(io, _args("--homeconnect", "--hc-client-id", client_id), db=db,
                                   **({"hc_login": login} if login else {}))
                st._homeconnect()
                st.save()
                self.state = "done"
            except (onboard.SetupError, BackendError, OSError, ValueError) as e:
                self.error, self.state = str(e), "error"
            finally:
                db.close()
        threading.Thread(target=run, daemon=True, name="hc-login").start()

    def view(self):
        return {"state": self.state, "lines": _clean(self.lines)[-8:], "error": self.error,
                "url": self.url, "code": self.code,
                "qr": qr.svg(qr.encode(self.url), scale=4, border=2) if self.url else None}


# ------------------------------------------------------------------ chores
def default_chores(db):
    st, lines = _setup(db, "--chores", "default")
    rows = st.chores()
    return {"added": [r["title"] for r in rows], "log": _clean(lines)}


# ------------------------------------------------------------------ phone + agents
def telegram_save(db, token, api=None):
    st, lines = _setup(db, "--telegram-token", token.strip(), **({"tg_api": api} if api else {}))
    try:
        st.telegram()
    except onboard.SetupError as e:
        raise WebSetupError(str(e)) from None
    return {"bot": (st.cfg.get("telegram") or {}).get("bot"), "log": _clean(lines)}


def telegram_info(cfg):
    from .telegram import TelegramError, load_token
    try:
        load_token()
        have = True
    except TelegramError:
        have = False
    return {"configured": have, "bot": (cfg.get("telegram") or {}).get("bot")}


def telegram_link(db, cfg, name):
    p = db.person(name, required=False)
    if not p:
        raise WebSetupError(f"no member called {name!r}")
    code = db.new_link_code(p["name"])
    bot = (cfg.get("telegram") or {}).get("bot")
    link = f"https://t.me/{bot}?start={code}" if bot else None
    return {"name": p["name"], "code": code, "link": link, "expires_s": 600,
            "qr": qr.svg(qr.encode(link), scale=4, border=2) if link else None}


def mcp_token(db, body, base_url):
    from .mcp import Tokens
    client = re.sub(r"[^A-Za-z0-9_.\-]", "-", str(body.get("client") or "").strip())[:40]
    if not client:
        raise WebSetupError("a name for the agent, e.g. claude or grok-bot")
    who = db.person(str(body.get("person") or ""), required=False)
    if not who:
        raise WebSetupError("which person the agent acts as")
    tok = Tokens().add(who["name"], client)
    endpoint = "http://127.0.0.1:8770/mcp"
    cfgjson = json.dumps({"mcpServers": {"chorepilot": {"type": "http", "url": endpoint,
                                                        "headers": {"Authorization": f"Bearer {tok}"}}}})
    return {"client": client, "person": who["name"], "role": who["role"], "token": tok,
            "endpoint": endpoint,
            "commands": [f"claude mcp add --transport http chorepilot {endpoint} "
                         f"--header \"Authorization: Bearer {tok}\"",
                         f"claude mcp add chorepilot -- chorepilot mcp --as {who['name']}"],
            "config": cfgjson, "qr": _qr_or_none(cfgjson)}


def _qr_or_none(text):
    try:
        return qr.svg(qr.encode(text, ecl="L"), scale=3, border=2)
    except ValueError:
        return None


def mcp_list():
    from .mcp import Tokens
    return [{"client": t.get("client"), "person": t.get("person"),
             "created": time.strftime("%Y-%m-%d", time.localtime(t.get("created") or 0))}
            for t in Tokens().list()]


# ------------------------------------------------------------------ remote access
_TS_CACHE = {"t": 0, "v": None}


def _tailscale():
    if time.time() - _TS_CACHE["t"] < 60:
        return _TS_CACHE["v"]
    v = None
    exe = shutil.which("tailscale") or ("/Applications/Tailscale.app/Contents/MacOS/Tailscale"
                                        if os.path.exists("/Applications/Tailscale.app") else None)
    if exe:
        v = {"installed": True}
        try:
            out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True,
                                 timeout=2.5)
            js = json.loads(out.stdout or "{}")
            me = js.get("Self") or {}
            v.update(running=js.get("BackendState") == "Running",
                     dns=(me.get("DNSName") or "").rstrip(".") or None)
        except (OSError, subprocess.SubprocessError, ValueError):
            v.update(running=None, dns=None)
    _TS_CACHE.update(t=time.time(), v=v)
    return v


def remote_state(server, handler):
    host, port = server.server_address[:2]
    pub = server.public_url
    ts = _tailscale()
    cf = bool(shutil.which("cloudflared"))
    seen = handler.headers.get("Host") or ""
    via_https = handler.headers.get("X-Forwarded-Proto") == "https" or (
        pub and pub.startswith("https://") and seen.split(":")[0] == urllib.parse.urlparse(pub).hostname)
    suggested = f"https://{ts['dns']}" if ts and ts.get("dns") else None
    return {
        "bind": f"{host}:{port}", "loopback_only": host in LOOPBACK,
        "public_url": pub, "secure_cookie": bool(server.secure_cookie or (pub or "").startswith("https://")),
        "allowed_hosts": list(server.allowed_hosts), "this_request": {"host": seen, "https": bool(via_https)},
        "tailscale": ts, "cloudflared": cf, "suggested_public_url": suggested,
        "commands": {
            "tailscale": [f"tailscale serve --bg {port}",
                          f"chorepilot web --public-url {suggested or 'https://<this-machine>.<tailnet>.ts.net'}"],
            "cloudflare": [f"cloudflared tunnel create chorepilot",
                           f"cloudflared tunnel route dns chorepilot home.example.com",
                           f"cloudflared tunnel run --url http://127.0.0.1:{port} chorepilot",
                           "chorepilot web --public-url https://home.example.com"],
            "lan": ["chorepilot web --lan --allow-host house.local"],
        },
    }


def save_public_url(url):
    """Store web.public_url in house.json, so `chorepilot run` serves it the same way."""
    if url and not re.match(r"^https://[A-Za-z0-9.\-]+(:\d+)?/?$", url):
        raise WebSetupError("the public address is an https:// URL with no path, e.g. "
                            "https://house.tailnet.ts.net")
    cfg = onboard.read_house()
    w = dict(cfg.get("web") or {})
    if url:
        w["public_url"] = url.rstrip("/")
    else:
        w.pop("public_url", None)
    cfg["web"] = w
    onboard.write_house_raw(cfg)
    return w


def qr_svg(text):
    return qr.svg(qr.encode(text), scale=4, border=2)
