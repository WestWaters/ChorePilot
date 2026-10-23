"""`chorepilot web`: the household dashboard. stdlib http.server + hand-written HTML/CSS/JS in
chorepilot/webui/ (no CDN, no build step, no npm); it runs on a Pi.

    chorepilot web                                  # http://127.0.0.1:8771 (this machine only)
    chorepilot web --public-url https://house.tailnet.ts.net    # behind `tailscale serve`
    chorepilot web --lan --allow-host house.local   # every interface (trusted LAN; no TLS)

What it does: Home (a "right now" line, Ask the house, what is running, today's chores), device
tiles by room with a detail sheet per device, the family chore board, approvals, a readable
activity timeline (the raw audit log is still there), first-run onboarding and settings, and an
installable app shell (PWA) for phones. Members sign in with their name and ChorePilot PIN;
nothing is readable without signing in.

Safety (all of it applies to every request, local or remote):
  - sessions are random 256-bit ids in an HttpOnly, SameSite=Strict cookie (Secure whenever the
    request came over the --public-url HTTPS address), 12 h sliding expiry, in memory only;
  - every POST needs the session's CSRF token in X-ChorePilot-CSRF, and a browser Origin other
    than this host (or the public URL) is refused; the Host header must be one we serve
    (DNS-rebinding protection); a strict Content-Security-Policy, no inline script or style;
  - 5 wrong PINs for a name (or 20 from one address) lock sign-in for 10 minutes, and 20 for
    a name in a day lock it until the day is over (except on this machine itself);
  - every device action goes through the House: the same policy tiers as the terminal and
    Telegram. Tier 2 (water / heat) needs a yes from the signed-in adult, given on a card that
    the server issued for exactly that action; a strong confirmation needs the typed phrase;
    tier 3 (unlock, open the garage, disarm) needs the owner's PIN or TOTP typed again; kids'
    tier-2 requests go to the approvals queue. The model never answers any of these;
  - the first owner can only be created from this machine (loopback, no proxy headers).
"""
from __future__ import annotations

import hashlib
import json
import os
import ipaddress
import secrets
import socket
import sys
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__, policy, webview
from . import websetup as ws
from .appliance import BackendError
from .chores import UNDO_WINDOW_S, ChoreDB, ChoreError, when

SESSION_S = 12 * 3600
LOCK_S = 600
MAX_FAILS_NAME, MAX_FAILS_IP = 5, 20
DAY_S, MAX_FAILS_NAME_DAY = 86400, 20
CONFIRM_TTL = 120
REQUEST_TIMEOUT_S = 30
MAX_BODY = 64 * 1024
LOOPBACK = ("127.0.0.1", "localhost", "::1")
UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui")
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    "/favicon.svg": ("icon.svg", "image/svg+xml"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/icon-maskable-512.png": ("icon-maskable-512.png", "image/png"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
    "/fonts/OFL.txt": ("fonts/OFL.txt", "text/plain; charset=utf-8"),
    "/fonts/README.txt": ("fonts/README.txt", "text/plain; charset=utf-8"),
    "/fonts/ibm-plex-sans.woff2": ("fonts/ibm-plex-sans.woff2", "font/woff2"),
}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; manifest-src 'self'; worker-src 'self'; font-src 'self'; "
       "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


class Sessions:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.d = {}
        self.fails = {}
        self.lock = threading.Lock()

    def new(self, person_id, cred=None):
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        with self.lock:
            self.d[sid] = {"pid": person_id, "csrf": csrf, "exp": self.clock() + SESSION_S,
                           "cred": cred}
        return sid, csrf

    def get(self, sid):
        with self.lock:
            s = self.d.get(sid or "")
            if not s:
                return None
            if s["exp"] < self.clock():
                self.d.pop(sid, None)
                return None
            s["exp"] = self.clock() + SESSION_S
            return dict(s, sid=sid)

    def drop(self, sid):
        with self.lock:
            self.d.pop(sid or "", None)

    def drop_person(self, pid):
        with self.lock:
            for k in [k for k, v in self.d.items() if v["pid"] == pid]:
                self.d.pop(k, None)

    def _recent(self, key, window=LOCK_S):
        now = self.clock()
        lst = [t for t in self.fails.get(key, []) if now - t < window]
        self.fails[key] = lst
        return lst

    def locked(self, name, ip, local=False):
        """5 wrong PINs for a name or 20 from one address lock sign-in for 10 minutes. On top,
        20 wrong PINs for one name in a day lock that name for the rest of the day, except at
        this machine itself: with 5 tries per 10 minutes alone, a 4-digit PIN falls in about a
        week to someone who can reach the public address."""
        with self.lock:
            n = name.lower()
            return (len(self._recent("n:" + n)) >= MAX_FAILS_NAME
                    or len(self._recent("ip:" + ip)) >= MAX_FAILS_IP
                    or (not local and len(self._recent("d:" + n, DAY_S)) >= MAX_FAILS_NAME_DAY))

    def failed(self, name, ip):
        with self.lock:
            for k in ("n:" + name.lower(), "ip:" + ip):
                self._recent(k).append(self.clock())
            self._recent("d:" + name.lower(), DAY_S).append(self.clock())

    def ok(self, name):
        with self.lock:
            self.fails.pop("n:" + name.lower(), None)
            self.fails.pop("d:" + name.lower(), None)


class ConfirmTokens:
    """A yes on the dashboard is only valid for the exact action the server asked about, in the
    session it asked, once, within two minutes."""

    def __init__(self):
        self.d, self.lock = {}, threading.Lock()

    def issue(self, sid, key):
        t = secrets.token_urlsafe(18)
        with self.lock:
            now = time.time()
            for k in [k for k, v in self.d.items() if v[2] < now]:
                self.d.pop(k, None)
            self.d[t] = (sid, key, now + CONFIRM_TTL)
        return t

    def use(self, token, sid, key):
        with self.lock:
            v = self.d.pop(token or "", None)
        return bool(v and v[0] == sid and v[1] == key and v[2] >= time.time())


def _own_names():
    try:
        n = socket.gethostname().lower().rstrip(".")
    except OSError:
        return set()
    short = n.split(".")[0]
    return {n, short, short + ".local", n if n.endswith(".local") else n + ".local"} - {""}


def lan_host_ok(host):
    """`--lan` without --allow-host: the address you typed works (an IP literal, localhost, or
    this machine's own name / name.local), but not an arbitrary domain. DNS rebinding needs a
    domain the attacker controls pointing at this box, so this keeps a web page somewhere else
    from talking to the dashboard as if it were the same site."""
    if not host:
        return False
    try:
        ipaddress.ip_address(host.split("%")[0])
        return True
    except ValueError:
        pass
    return host in LOOPBACK or host in _own_names()


def content_length(headers, limit):
    """The request body size, refusing what can't be a real body: a negative length would make
    rfile.read() wait for the client to hang up, so one connection could pin a thread."""
    try:
        n = int(headers.get("Content-Length") or 0)
    except ValueError:
        raise ValueError("bad Content-Length") from None
    if n < 0:
        raise ValueError("bad Content-Length")
    if n > limit:
        raise ValueError("body too large")
    return n


def _int_arg(q, key, default, hi):
    try:
        return max(1, min(hi, int((q.get(key) or [default])[0])))
    except ValueError:
        return default


def cred_mark(p):
    """A fingerprint of the sign-in secrets a session was opened with. When the PIN or TOTP
    changes (the owner resets a PIN that leaked, `chorepilot people set-pin` in a terminal),
    every session opened with the old one stops working, in every process."""
    raw = f"{p.get('pin_hash') or ''}|{p.get('totp_secret') or ''}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


# ------------------------------------------------------------------ compatibility helpers
def board(db, now=None):
    """The v0.5 board shape (kept for scripts that read /api/board)."""
    b = webview.board_full(db, None, now)
    b.pop("_pid", None)
    return b


def read_audit(path, limit=100):
    out = []
    for r in reversed(webview.audit_rows(path, limit)):
        out.append({"t": time.strftime("%m-%d %H:%M:%S", time.localtime(r.get("t", 0))),
                    "who": r.get("who"), "source": r.get("source"), "client": r.get("client"),
                    "tool": r.get("tool"), "args": r.get("args"), "ok": r.get("ok"),
                    "error": r.get("error"), "outcome": r.get("outcome")})
    return out


def _load_static(name):
    with open(os.path.join(UI_DIR, name), "rb") as f:
        return f.read()


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = f"ChorePilot/{__version__}"
    protocol_version = "HTTP/1.1"
    timeout = REQUEST_TIMEOUT_S      # a client that stops sending can't hold a thread forever

    def log_message(self, fmt, *a):
        pass

    # -- plumbing
    def _send(self, code, body=b"", ctype="application/json", headers=None, cache="no-store"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, default=str).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "microphone=(self), camera=(), geolocation=(), "
                                               "payment=(), usb=()")
        if self._via_public_https():
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _host(self):
        return (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower() \
            if not (self.headers.get("Host") or "").startswith("[") else \
            (self.headers.get("Host") or "").split("]")[0].strip("[").lower()

    def _host_ok(self):
        allowed = self.server.allowed_hosts
        if allowed:
            return self._host() in allowed
        return lan_host_ok(self._host())

    def _origin_ok(self):
        o = self.headers.get("Origin")
        if not o:
            return True
        u = urlparse(o)
        if u.netloc.lower() == (self.headers.get("Host") or "").lower():
            return True
        pub = self.server.public_url
        return bool(pub) and f"{u.scheme}://{u.netloc}".lower() == pub.lower()

    def _via_public_https(self):
        pub = self.server.public_url or ""
        if not pub.startswith("https://"):
            return False
        if self._host() == (urlparse(pub).hostname or "").lower():
            return True
        return (self.client_address[0] in ("127.0.0.1", "::1")
                and self.headers.get("X-Forwarded-Proto") == "https")

    def _client_ip(self):
        """Who is signing in, for the per-address limit. Behind `tailscale serve` or a
        Cloudflare Tunnel every request comes from 127.0.0.1, so one stranger's wrong PINs
        would lock the whole household out; the proxy's forwarding header names the real
        client. It is trusted only from loopback, where only the proxy can set it."""
        ip = self.client_address[0]
        if ip not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return ip
        for h in ("CF-Connecting-IP", "X-Real-IP"):
            v = (self.headers.get(h) or "").strip()
            if v:
                return v[:64]
        xff = (self.headers.get("X-Forwarded-For") or "").split(",")[-1].strip()
        return xff[:64] or ip

    def _sid(self):
        c = SimpleCookie(self.headers.get("Cookie") or "")
        return c["cp_session"].value if "cp_session" in c else None

    def _session(self):
        s = self.server.sessions.get(self._sid())
        if not s:
            return None, None
        db = ChoreDB(self.server.db_path)
        p = db.person(s["pid"], required=False)
        if not p or not secrets.compare_digest(s.get("cred") or "", cred_mark(p)):
            db.close()
            self.server.sessions.drop(s["sid"])
            return None, None
        s["person"] = p
        return s, db

    def _body(self):
        n = content_length(self.headers, MAX_BODY)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            js = json.loads(raw or b"{}")
        except ValueError:
            raise ValueError("bad JSON") from None
        if not isinstance(js, dict):
            raise ValueError("expected a JSON object")
        return js

    def _cfg(self):
        return json.loads(json.dumps(self.server.cfg_fn()))

    def _house(self, person, db, confirm=None, strong=None, secret=None, cfg=None):
        from .control import Notifier
        from .house import House
        return House(cfg or self._cfg(), db=db, person=person["name"], source="web",
                     watch=self.server.watch, confirm=confirm or (lambda q: False),
                     strong_confirm=strong or (lambda q, p: False),
                     ask_secret=secret or (lambda q: None),
                     notifier=self.server.notifier or Notifier(), log=lambda *a: None,
                     audit_path=self.server.audit_path, client="dashboard",
                     **self.server.house_kw)

    def _cookie(self, sid, max_age=SESSION_S):
        secure = "; Secure" if (self.server.secure_cookie or self._via_public_https()) else ""
        return f"cp_session={sid}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}{secure}"

    # -- GET
    def do_HEAD(self):
        return self.do_GET()

    def do_GET(self):
        if not self._host_ok():
            return self._send(421, {"error": "unexpected Host header"})
        path = urlparse(self.path).path
        if path in STATIC:
            name, ctype = STATIC[path]
            try:
                data = _load_static(name)
            except OSError:
                return self._send(404, {"error": "not found"})
            if name in ("index.html", "sw.js"):
                data = data.replace(b"{{VERSION}}", __version__.encode())
            extra = {"Service-Worker-Allowed": "/"} if name == "sw.js" else None
            cache = "public, max-age=604800" if name.endswith(".woff2") else "no-cache"
            return self._send(200, data, ctype, headers=extra, cache=cache)
        if path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        if path == "/healthz":
            return self._send(200, {"ok": True})
        if path == "/api/setup/state":
            db = ChoreDB(self.server.db_path)
            try:
                return self._send(200, {"needs_owner": not db.owners(), "local": ws.is_local(self),
                                        "version": __version__})
            finally:
                db.close()
        if not path.startswith("/api/"):
            # client-side routes (#/devices ...) all live on "/": anything else is unknown
            return self._send(404, {"error": "not found"})
        s, db = self._session()
        if not s:
            return self._send(401, {"error": "sign in"})
        try:
            return self._get_api(path, s, db)
        except (BackendError, OSError) as e:
            return self._send(502, {"ok": False, "error": str(e)})
        finally:
            db.close()

    def _get_api(self, path, s, db):
        p = s["person"]
        adult = p["role"] in ("owner", "adult")
        owner = p["role"] == "owner"
        q = parse_qs(urlparse(self.path).query)
        if path == "/api/me":
            cfg = self.server.cfg_fn()
            return self._send(200, self._me(s, p, cfg))
        if path == "/api/home":
            return self._send(200, self._home(s, p, db))
        if path == "/api/board":
            b = webview.board_full(db, p["name"])
            b.pop("_pid", None)
            return self._send(200, b)
        if path == "/api/devices":
            return self._send(200, {"devices": self._devices(p, db)})
        if path == "/api/approvals":
            return self._send(200, self._approvals(p, db))
        if path == "/api/activity":
            lim = _int_arg(q, "limit", 80, 300)
            rows = webview.audit_rows(self.server.audit_path, 400)
            if not adult:
                rows = [r for r in rows if r.get("who") == p["name"]]
            return self._send(200, {"items": webview.activity(rows, db, lim), "raw": adult})
        if path == "/api/audit":
            if not adult:
                return self._send(403, {"error": "the audit log is for adults"})
            lim = _int_arg(q, "limit", 100, 500)
            return self._send(200, {"audit": read_audit(self.server.audit_path, lim)})
        if path.startswith("/api/ask/"):
            job = self.server.asks.get(path.split("/")[3], s["sid"])
            if not job:
                return self._send(404, {"error": "no such request"})
            return self._send(200, job.view())
        if path == "/api/system":
            return self._send(200, self._system(adult))
        if path == "/api/settings":
            if not adult:
                return self._send(403, {"error": "settings are for adults"})
            return self._send(200, self._settings(p, db, owner))
        if path == "/api/settings/remote":
            if not owner:
                return self._send(403, {"error": "remote access is set by the owner"})
            return self._send(200, ws.remote_state(self.server, self))
        if path == "/api/setup/homeconnect":
            if not owner:
                return self._send(403, {"error": "owner only"})
            return self._send(200, self.server.hc.view())
        if path == "/api/setup/model/detect":
            if not owner:
                return self._send(403, {"error": "owner only"})
            return self._send(200, {"found": ws.model_detect(self.server.http)})
        return self._send(404, {"error": "not found"})

    # -- views
    def _me(self, s, p, cfg):
        from .webview import PALETTE, initials
        return {"name": p["name"], "role": p["role"], "csrf": s["csrf"],
                "initials": initials(p["name"]), "color": p["id"] % PALETTE,
                "household": cfg.get("household") or "Home", "version": __version__,
                "unit": cfg.get("unit") or "C", "demo": cfg.get("_preset") == "demo"}

    def _devices(self, p, db):
        cache = self.server.dev_cache
        with self.server.dev_lock:
            if cache.get("t", 0) > time.time() - 3:
                return cache["v"]
            cfg = self._cfg()
            h = self._house(p, db, cfg=cfg)
            v = webview.device_views(h, cfg, self.server.run_totals)
            cache.update(t=time.time(), v=v)
            return v

    def _approvals(self, p, db):
        from .confirm import channels
        adult = p["role"] in ("owner", "adult")
        rows = db.approvals()
        mine = [a for a in rows if a.get("person") == p["name"]]
        conf = [c for c in db.pending_confirmations() if c.get("person") == p["name"]]
        web_ok = "web" in channels(self.server.cfg_fn())
        return {"can_decide": adult,
                "approvals": [webview.approval_view(a) for a in (rows if adult else mine)],
                "confirmations": [{"id": c["id"], "question": c["question"], "kind": c["kind"],
                                   "phrase": c["phrase"], "client": c["client"] or c["source"],
                                   "expires_s": max(0, int(c["expires"] - time.time()))}
                                  for c in conf] if web_ok else []}

    def _llm(self, cfg):
        from .webask import llm_target
        url, model = llm_target(cfg)
        if not url:
            return {"configured": False, "ok": False}
        c = self.server.llm_cache
        if c.get("url") == url and c.get("t", 0) > time.time() - 20:
            return c["v"]
        from .onboard import probe_llm
        kw = {"http": self.server.http} if self.server.http else {}
        r = probe_llm(url, "custom", timeout=1.0, **kw)
        v = {"configured": True, "ok": bool(r), "url": url,
             "model": model or ((r or {}).get("models") or [None])[0]}
        c.update(url=url, t=time.time(), v=v)
        return v

    def _system(self, adult):
        from . import webllm
        from .webask import llm_target
        cfg = self.server.cfg_fn()
        url, model = llm_target(cfg)
        out = {"llm": self._llm(cfg), "measured": self.server.llm_stats.view(),
               "host": webllm.host(), "metrics": None, "local": None}
        if url:
            c = self.server.metrics_cache
            if c.get("url") != url or c.get("t", 0) < time.time() - 3:
                c.update(url=url, t=time.time(), v=webllm.server_metrics(url))
            out["metrics"] = c["v"]
            out["local"] = (urlparse(url).hostname or "") in LOOPBACK
        if not adult:
            out["llm"] = {k: v for k, v in out["llm"].items() if k in ("configured", "ok", "model")}
        return out

    def _home(self, s, p, db):
        cfg = self.server.cfg_fn()
        adult = p["role"] in ("owner", "adult")
        devices = webview.safe(lambda: self._devices(p, db), [])
        ap = self._approvals(p, db)
        b = webview.board_full(db, p["name"])
        b.pop("_pid", None)
        llm = self._llm(cfg)
        if not adult:
            llm = {k: v for k, v in llm.items() if k in ("configured", "ok")}
        out = {"me": self._me(s, p, cfg), "devices": devices,
               "facts": webview.right_now(devices, len(ap["approvals"]) if adult else 0,
                                          len(ap["confirmations"])),
               "board": b, "approvals": ap, "llm": llm,
               "activity": webview.activity(
                   [r for r in webview.audit_rows(self.server.audit_path, 200)
                    if adult or r.get("who") == p["name"]], db, 6)}
        if adult:
            out["setup"] = ws.checklist(db, cfg)
        return out

    def _settings(self, p, db, owner):
        cfg = self.server.cfg_fn()
        out = {"household": cfg.get("household") or "Home", "unit": cfg.get("unit") or "C",
               "members": ws.members(db), "checklist": ws.checklist(db, cfg),
               "devices": [{"id": n, "type": d.get("type") or n,
                            "backend": d.get("backend") or cfg.get("backend") or "simulated",
                            "target": d.get("entity") or d.get("haId") or d.get("prefix")}
                           for n, d in (cfg.get("devices") or {}).items()],
               "demo": cfg.get("_preset") == "demo", "is_owner": owner,
               "llm": self._llm(cfg), "version": __version__,
               "policy": {**policy.DEFAULTS, **(cfg.get("policy") or {})}}
        if owner:
            out["telegram"] = ws.telegram_info(cfg)
            out["agents"] = ws.mcp_list()
            out["ha"] = {"url": (cfg.get("ha") or {}).get("url")}
            out["mqtt"] = cfg.get("mqtt")
        return out

    # -- POST
    def do_POST(self):
        if not self._host_ok():
            return self._send(421, {"error": "unexpected Host header"})
        if not self._origin_ok():
            return self._send(403, {"error": "cross-origin request refused"})
        path = urlparse(self.path).path
        try:
            body = self._body()
        except ValueError as e:
            self.close_connection = True       # the body may be unread: don't parse it as a request
            return self._send(400, {"error": str(e)}, headers={"Connection": "close"})
        if path == "/api/login":
            return self._login(body)
        if path == "/api/setup/owner":
            return self._first_owner(body)
        s, db = self._session()
        if not s:
            return self._send(401, {"error": "sign in"})
        try:
            if not secrets.compare_digest(self.headers.get("X-ChorePilot-CSRF") or "", s["csrf"]):
                return self._send(403, {"error": "missing or wrong CSRF token"})
            return self._post_api(path, s, db, body)
        except (ChoreError, ValueError) as e:
            return self._send(400, {"ok": False, "error": str(e)})
        except (BackendError, OSError) as e:
            return self._send(502, {"ok": False, "error": str(e)})
        finally:
            db.close()

    def _post_api(self, path, s, db, body):
        p = s["person"]
        adult = p["role"] in ("owner", "adult")
        owner = p["role"] == "owner"
        parts = path.strip("/").split("/")
        if path == "/api/logout":
            self.server.sessions.drop(s["sid"])
            return self._send(200, {"ok": True}, headers={"Set-Cookie": self._cookie("", 0)})
        if len(parts) == 4 and parts[:2] == ["api", "chores"] and parts[3] == "done":
            return self._done(p, db, int(parts[2]), body)
        if len(parts) == 4 and parts[:2] == ["api", "chores"] and parts[3] == "undo":
            return self._undo(p, db, int(parts[2]))
        if len(parts) == 4 and parts[:2] == ["api", "chores"] and parts[3] == "assign":
            return self._assign(p, db, int(parts[2]), body)
        if len(parts) == 4 and parts[:2] == ["api", "rotations"] and parts[3] == "week":
            return self._week(p, db, int(parts[2]), body)
        if len(parts) == 3 and parts[:2] == ["api", "approvals"]:
            return self._decide(p, db, int(parts[2]), bool(body.get("approve")))
        if len(parts) == 3 and parts[:2] == ["api", "confirmations"]:
            return self._answer_confirmation(p, db, int(parts[2]), body)
        if len(parts) == 4 and parts[:2] == ["api", "devices"]:
            return self._device_action(s, p, db, parts[2], parts[3], body)
        if path == "/api/ask":
            return self._ask(s, p, body)
        if len(parts) == 4 and parts[:2] == ["api", "ask"] and parts[3] == "answer":
            job = self.server.asks.get(parts[2], s["sid"])
            if not job:
                return self._send(404, {"error": "no such request"})
            ok = job.give(int(body.get("id") or 0), body)
            return self._send(200 if ok else 409, {"ok": ok, **job.view()})
        if parts[:2] == ["api", "setup"] or parts[:2] == ["api", "settings"]:
            if not owner:
                return self._send(403, {"ok": False, "error": "only the owner can change setup"})
            return self._setup_post(s, p, db, parts[2:], body)
        return self._send(404, {"error": "not found"} if adult or True else {})

    def _login(self, body):
        name = str(body.get("name") or "").strip()[:64]
        pin = str(body.get("pin") or "").strip()[:64]
        ip = self._client_ip()
        ss = self.server.sessions
        if not name or not pin:
            return self._send(400, {"error": "name and PIN"})
        if ss.locked(name, ip, local=ws.is_local(self)):
            return self._send(429, {"error": "Too many wrong PINs. Wait 10 minutes and try again."})
        db = ChoreDB(self.server.db_path)
        try:
            p = db.person(name, required=False)
            good = bool(p and p["name"].lower() == name.lower() and
                        (db.check_pin(p["name"], pin) or db.check_totp(p["name"], pin)))
            if not good:
                ss.failed(name, ip)
                return self._send(403, {"error": "That name and PIN don't match. Members without "
                                                 "a PIN can get one from the owner."})
            ss.ok(name)
            sid, csrf = ss.new(p["id"], cred_mark(db.person(p["id"])))
            return self._send(200, {"ok": True, "name": p["name"], "role": p["role"], "csrf": csrf},
                              headers={"Set-Cookie": self._cookie(sid)})
        finally:
            db.close()

    def _first_owner(self, body):
        if not ws.is_local(self):
            return self._send(403, {"ok": False, "error": "the owner is created on the computer "
                                    "ChorePilot runs on (open http://127.0.0.1 there), or with "
                                    "`chorepilot setup`"})
        db = ChoreDB(self.server.db_path)
        try:
            with self.server.setup_lock:
                p, lines = ws.create_owner(db, body)
            self.server.dev_cache.clear()
            sid, csrf = self.server.sessions.new(p["id"], cred_mark(db.person(p["id"])))
            return self._send(200, {"ok": True, "name": p["name"], "role": p["role"], "csrf": csrf,
                                    "log": lines}, headers={"Set-Cookie": self._cookie(sid)})
        except ws.WebSetupError as e:
            return self._send(400, {"ok": False, "error": str(e)})
        finally:
            db.close()

    def _done(self, p, db, iid, body):
        inst = db.instance(iid)
        if not inst or inst["state"] != "open":
            return self._send(404, {"ok": False, "error": "that chore is not open"})
        adult = p["role"] in ("owner", "adult")
        if inst["assignee"] and inst["assignee"] != p["name"] and not adult:
            return self._send(403, {"ok": False, "error": "you can only mark your own chores done"})
        by = p["name"] if inst["assignee"] in (None, p["name"]) or not body.get("for_assignee") \
            else inst["assignee"]
        res = db.complete(inst["title"], by=by, instance=iid, verified_by="dashboard",
                          marked_by=p["name"])
        h = self._house(p, db)
        h._audit("complete_chore", {"chore": inst["title"], "person": by}, {"ok": True})
        for f in res["followups"]:
            h.notify(f"'{f['title']}' is now open for {f['assignee'] or 'anyone'}.",
                     log=lambda m: None)
        out = {"ok": True, "id": iid, "chore": inst["title"], "state": res["instance"]["state"],
               "points": inst["points"], "opened": [f["title"] for f in res["followups"]],
               "undo_s": UNDO_WINDOW_S}
        if res["approval"]:
            out["note"] = "Sent to a parent to check"
        return self._send(200, out)

    def _undo(self, p, db, iid):
        """Undo a "done": an adult any time, anyone else only their own mark, for a short while."""
        try:
            res = db.undo_complete(iid, p["name"])
        except ChoreError as e:
            inst = db.instance(iid)
            code = 404 if not inst or inst["state"] not in ("done", "approved",
                                                            "pending_approval") else 403
            self._house(p, db)._audit("undo_chore", {"instance": iid},
                                      {"ok": False, "error": str(e), "policy": "deny"})
            return self._send(code, {"ok": False, "error": str(e)})
        self._house(p, db)._audit("undo_chore", {"chore": res["chore"], "person": res["credited"],
                                                 "points": res["points"]}, {"ok": True})
        return self._send(200, {"ok": True, **res})

    def _assign(self, p, db, iid, body):
        if p["role"] not in ("owner", "adult", "staff"):
            return self._send(403, {"ok": False, "error": "only an adult can reassign chores"})
        inst = db.instance(iid)
        if not inst or inst["state"] != "open":
            return self._send(404, {"ok": False, "error": "that chore is not open"})
        who = db.person(str(body.get("person") or ""))
        db._x("UPDATE instances SET assignee_id=? WHERE id=?", (who["id"], iid))
        self._house(p, db)._audit("assign_chore", {"chore": inst["title"], "person": who["name"]},
                                  {"ok": True})
        return self._send(200, {"ok": True, "chore": inst["title"], "assigned_to": who["name"]})

    def _week(self, p, db, cid, body):
        if p["role"] not in ("owner", "adult", "staff"):
            return self._send(403, {"ok": False, "error": "only an adult can change the rotation"})
        c = db.chore(cid)
        who = db.person(str(body.get("person") or ""))
        r = db.assign(c["id"], who["name"], week=True)
        self._house(p, db)._audit("assign_chore", {"chore": c["title"], "person": who["name"],
                                                   "whole_week": True}, {"ok": True})
        return self._send(200, {"ok": True, "chore": c["title"], "assigned_to": r["assignee"]})

    def _decide(self, p, db, aid, approve):
        if p["role"] not in ("owner", "adult"):
            return self._send(403, {"ok": False, "error": "approvals are decided by an adult"})
        res = self._house(p, db).approve(aid, approve=approve)
        self.server.dev_cache.clear()
        return self._send(200 if res.get("ok") else 409, res)

    def _answer_confirmation(self, p, db, cid, body):
        from .confirm import channels
        if "web" not in channels(self.server.cfg_fn()):
            return self._send(403, {"ok": False, "error": "answer this in Telegram or the terminal"})
        c = db.confirmation(cid)
        if not c or c["state"] != "pending" or c.get("person") != p["name"]:
            return self._send(404, {"ok": False, "error": "nothing is waiting for you there"})
        r = db.answer_confirmation(cid, bool(body.get("yes")), "web", answer=body.get("phrase"))
        return self._send(200, {"ok": True, "state": (r or {}).get("state", "expired")})

    # -- device actions: the House decides, the dashboard only collects the human's answer
    def _device_action(self, s, p, db, nick, action, body):
        from .control import validate_args
        from .devtypes import get_type
        from .devtypes.program import TOOL_TIERS
        params = body.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        cfg = self._cfg()
        probe = self._house(p, db, cfg=cfg)
        strong, phrase = False, None
        if probe.bridge and nick in probe.program_nicks():
            name = {"start": "start_program"}.get(action, action)
            if name not in ("start_program", "pause", "resume", "cancel"):
                return self._send(400, {"ok": False, "error": f"the {nick} can't {action!r}"})
            be = probe.bridge.fleet.get(nick)
            m = be.machine
            targs = {} if probe.bridge.legacy else {"appliance": nick}
            if name == "start_program":
                targs["program"] = str(params.get("program") or m.default_program)
                opts = {k: v for k, v in params.items() if k != "program"
                        and v not in (None, "", False)}
                if opts:
                    targs["options"] = opts
            validate_args(name, targs, probe.bridge.fleet)       # ValueError -> 400
            why = probe.bridge._interlocks(name, nick, probe.bridge._status_or_error(be))
            if why:
                return self._send(409, {"ok": False, "error": why})
            tier, tool = TOOL_TIERS[name], name
            label = m.programs[targs["program"]].label if name == "start_program" else ""
            question = (f"Start {label} on the {webview.pretty(nick).lower()}? It uses {m.uses}."
                        if name == "start_program" else
                        f"Resume the {webview.pretty(nick).lower()}? It uses {m.uses}.")
            dtype = get_type(m.kind)
        else:
            d = probe.devices.get(nick)
            if d is None:
                return self._send(404, {"ok": False, "error": f"no device called {nick!r}"})
            dtype = d.dtype
            a, prm = dtype.validate(action, params, d.cfg)       # ValueError -> 400
            why = dtype.interlock(a, prm, d.adapter.status(), d.cfg)
            if why:
                return self._send(409, {"ok": False, "error": why})
            tier = dtype.tier_for(a, prm, d.cfg)
            act = dtype.actions[a]
            strong, phrase = act.confirm == "strong", act.phrase or "YES"
            from .webask import human
            question = human(dtype.confirmation(a, prm, webview.pretty(nick).lower()))
            tool, targs = dtype.tool, {"device": nick, "action": a, **params}
        dec = policy.decide(p, tier, "web", probe.policy, strong=strong)
        key = hashlib.sha256(json.dumps([nick, tool, targs], sort_keys=True, default=str)
                             .encode()).hexdigest()
        confirm = strongf = secret = None
        ask = {"tier": tier, "question": question, "device": nick}
        if dec.outcome == "confirm":
            if not self.server.confirms.use(body.get("token"), s["sid"], key):
                return self._send(200, {"ok": False, "needs": "confirm", **ask,
                                        "token": self.server.confirms.issue(s["sid"], key)})
            confirm = lambda q: True  # noqa: E731 - the person said yes to exactly this
        elif dec.outcome == "strong_confirm":
            if not body.get("phrase"):
                return self._send(200, {"ok": False, "needs": "phrase", "phrase": phrase, **ask})
            typed = str(body["phrase"])
            strongf = lambda q, ph: typed.strip() == ph  # noqa: E731
        elif dec.outcome == "second_factor":
            if not body.get("code"):
                return self._send(200, {"ok": False, "needs": "code", **ask,
                                        "why": "Security action: enter your PIN or "
                                               "authenticator code again"})
            code = str(body["code"])[:64]
            secret = lambda q: code  # noqa: E731
        h = self._house(p, db, confirm=confirm, strong=strongf, secret=secret, cfg=cfg)
        res = h.call(tool, targs)
        self.server.dev_cache.clear()
        out = {k: v for k, v in res.items() if k not in ("would_send", "detail", "sent")}
        if isinstance(out.get("status"), dict):
            out["status"] = {k: v for k, v in out["status"].items() if k not in ("detail", "options")}
        if res.get("queued_for_approval"):
            out["queued"] = True
            out["message"] = "Sent to a parent for approval."
        return self._send(200, out)

    def _ask(self, s, p, body):
        from .webask import llm_target
        text = str(body.get("text") or "").strip()[:500]
        if not text:
            return self._send(400, {"ok": False, "error": "ask something"})
        cfg = self._cfg()
        url, model = llm_target(cfg)
        if not url:
            return self._send(200, {"ok": False, "no_model": True,
                                    "error": "No assistant model is set up yet. The owner can add "
                                             "one in Settings; device controls work without it."})
        jobs = self.server.asks
        srv = self

        def make_house(job, db):
            return srv._house(p, db, confirm=job.confirm, strong=job.strong, secret=job.secret,
                              cfg=json.loads(json.dumps(cfg)))
        from .control import chat
        job = jobs.start(self.server, s["sid"], p["name"], text, make_house, url, model,
                         chat_fn=self.server.llm_stats.wrap(self.server.chat_fn or chat))
        return self._send(200, {"ok": True, **job.view()})

    # -- setup / settings (owner)
    def _setup_post(self, s, p, db, rest, body):
        step = "/".join(rest)
        srv = self.server
        try:
            with srv.setup_lock:
                if step == "members":
                    person, lines = ws.add_member(db, body)
                    out = {"ok": True, "person": {"name": person["name"], "role": person["role"]},
                           "members": ws.members(db)}
                elif step == "pin":
                    out = ws.set_pin(db, body)
                elif step == "simulated":
                    out = {"ok": True, "log": ws.simulated(db)}
                elif step == "ha/discover":
                    out = {"ok": True, **ws.ha_discover(str(body.get("url") or ""),
                                                        str(body.get("token") or ""), srv.http)}
                elif step == "ha/import":
                    out = {"ok": True, "log": ws.ha_import(db, body, srv.http)}
                elif step == "mqtt":
                    out = {"ok": True, "log": ws.mqtt(db, body)}
                elif step == "homeconnect":
                    cid = str(body.get("client_id") or "").strip()
                    if not cid:
                        raise ws.WebSetupError("your Home Connect developer app's Client ID")
                    srv.hc.start(srv.db_path, cid, srv.hc_login)
                    out = {"ok": True, **srv.hc.view()}
                elif step == "model":
                    out = {"ok": True, **ws.model_use(db, body, srv.http)}
                    srv.llm_cache.clear()
                elif step == "model/test":
                    from .webask import llm_target
                    url, model = llm_target(self._cfg())
                    url = str(body.get("url") or url or "")
                    if not url:
                        raise ws.WebSetupError("no model URL yet")
                    from .control import chat
                    out = ws.model_test(url, body.get("model") or model,
                                        srv.llm_stats.wrap(srv.chat_fn or chat))
                elif step == "chores":
                    out = {"ok": True, **ws.default_chores(db)}
                elif step == "telegram":
                    out = {"ok": True, **ws.telegram_save(db, str(body.get("token") or ""),
                                                          srv.tg_api)}
                elif step == "telegram/link":
                    out = {"ok": True, **ws.telegram_link(db, srv.cfg_fn(), str(body.get("name")))}
                elif step == "mcp":
                    out = {"ok": True, **ws.mcp_token(db, body, None)}
                elif step == "remote":
                    w = ws.save_public_url(str(body.get("public_url") or "").strip())
                    out = {"ok": True, "web": w, "restart": True}
                elif step == "household":
                    from .onboard import read_house, write_house_raw
                    c = read_house()
                    if body.get("household"):
                        c["household"] = str(body["household"]).strip()[:60]
                    if str(body.get("unit") or "").upper() in ("C", "F"):
                        c["unit"] = str(body["unit"]).upper()
                    write_house_raw(c)
                    out = {"ok": True}
                else:
                    return self._send(404, {"ok": False, "error": "not found"})
        except (ws.WebSetupError, ChoreError) as e:
            return self._send(400, {"ok": False, "error": str(e)})
        srv.dev_cache.clear()
        return self._send(200, out)


# ------------------------------------------------------------------ server
def make_server(host="127.0.0.1", port=8771, cfg=None, log=None, db_path=None, audit_path=None,
                notifier=None, allowed_hosts=None, secure_cookie=False, public_url=None,
                chat_fn=None, http=None, tg_api=None, hc_login=None, watch=False, house_kw=None):
    from .env import config_dir
    from .housecfg import load_house
    from .webask import AskJobs
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    httpd.cfg_fn = (lambda: cfg) if cfg is not None else load_house
    httpd.sessions = Sessions()
    httpd.confirms = ConfirmTokens()
    httpd.asks = AskJobs()
    httpd.hc = ws.HCLogin()
    httpd.db_path = db_path
    httpd.audit_path = audit_path or os.path.join(config_dir(), "audit.log")
    httpd.notifier = notifier
    httpd.dev_cache, httpd.dev_lock, httpd.run_totals = {}, threading.Lock(), {}
    httpd.llm_cache, httpd.metrics_cache = {}, {}
    from . import webllm
    httpd.llm_stats = webllm.Stats()
    threading.Thread(target=webllm.host, daemon=True, name="probe").start()
    httpd.setup_lock = threading.Lock()
    httpd.chat_fn, httpd.http, httpd.tg_api, httpd.hc_login = chat_fn, http, tg_api, hc_login
    httpd.watch, httpd.house_kw = watch, dict(house_kw or {})
    public_url = (public_url or "").rstrip("/") or None
    httpd.public_url = public_url
    httpd.secure_cookie = secure_cookie
    hosts = list(LOOPBACK) if host in LOOPBACK else []
    if allowed_hosts:
        hosts += [h for h in allowed_hosts if h]
    elif host not in LOOPBACK and not public_url:
        hosts = []          # --lan with no --allow-host: IP literals + this machine's names
    if public_url:
        hosts.append(urlparse(public_url).hostname or "")
        if host not in LOOPBACK and not allowed_hosts:
            hosts += list(LOOPBACK)
    httpd.allowed_hosts = tuple(dict.fromkeys(h.lower() for h in hosts if h))
    httpd.app_log = log or (lambda m: print(m, file=sys.stderr))
    return httpd


def cmd_web(args):
    from .housecfg import load_house
    cfg = load_house()
    w = cfg.get("web") or {}
    host = "0.0.0.0" if args.lan else (args.host or w.get("host", "127.0.0.1"))
    port = args.port if args.port is not None else w.get("port", 8771)
    public = args.public_url or w.get("public_url")
    log = lambda m: print(m, file=sys.stderr, flush=True)  # noqa: E731
    if public and not public.startswith("https://"):
        raise SystemExit("chorepilot web: --public-url must be an https:// address (Tailscale "
                         "serve or a Cloudflare Tunnel give you one); plain http would send PINs "
                         "in clear text")
    if host not in LOOPBACK:
        log("  !! WARNING: the dashboard is listening on the network with no TLS. PINs cross the "
            "LAN in clear text: use it on a trusted network only, or keep it on 127.0.0.1 and "
            "use --public-url behind Tailscale serve / a Cloudflare Tunnel.")
    allowed = tuple(args.allow_host or w.get("allow_hosts") or ()) or None
    httpd = make_server(host, port, cfg=None, log=log, allowed_hosts=allowed, public_url=public,
                        watch=True)
    shown = "127.0.0.1" if host == "0.0.0.0" else host
    log(f"  [web       ] ChorePilot dashboard on http://{shown}:{httpd.server_address[1]}/"
        + (f"  and {public}/" if public else "") + "  (sign in with your name and PIN)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def add_parser(sub):
    sp = sub.add_parser("web", help="the household dashboard (home, devices, chores, approvals, "
                        "activity, setup) on http://127.0.0.1:8771")
    sp.add_argument("--host", help="bind address (default 127.0.0.1)")
    sp.add_argument("--port", type=int)
    sp.add_argument("--lan", action="store_true", help="listen on every interface (trusted LAN)")
    sp.add_argument("--allow-host", action="append", help="extra Host header to accept, e.g. "
                    "house.local (DNS-rebinding protection; with --lan and no --allow-host, IP "
                    "addresses and this machine's own name are accepted)")
    sp.add_argument("--public-url", help="the https:// address this dashboard is reached at from "
                    "outside (tailscale serve / Cloudflare Tunnel): accepted as Host and Origin, "
                    "Secure cookies and HSTS on it")
    sp.set_defaults(func=cmd_web)
