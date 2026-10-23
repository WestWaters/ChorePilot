"""`chorepilot mcp`: the house and the chore board as an MCP server, for outside agents (xAI's
Grok Bot, Claude Desktop / Claude Code, OpenClaw, any MCP client).

Transports (Model Context Protocol, JSON-RPC 2.0; stdlib only, so it still runs on a Pi):
  stdio            `chorepilot mcp --as Alex`: the client starts this process itself.
  streamable HTTP  `chorepilot mcp --http [--port 8770]`: POST /mcp, JSON responses,
                   Mcp-Session-Id sessions. Binds 127.0.0.1; `--lan` binds every interface
                   (with a warning). Every request needs `Authorization: Bearer <token>`; there is
                   no unauthenticated mode. Browser Origins other than localhost are refused
                   (DNS-rebinding protection), unless allowed with --allow-origin.

Identity: an HTTP token belongs to one household person (`chorepilot mcp token add Alex
--client grok-bot`); the agent acts with that person's role. Over stdio it is `--as`, as on the
CLI. The client's label and its clientInfo go into every audit line.

Safety (the same policy engine as everywhere, with source="mcp"):
  tier 0 (read) and tier 1 (lights, vacuum, AC in the band, locking): allowed, up to
      `policy.agents_max_tier` (0, 1 or 2);
  tier 2 (water / heat): the person confirms on THEIR channel (Telegram button, typed PREHEAT,
      or `chorepilot confirm` at a terminal); timeout = no. Nothing in the tool arguments can
      confirm. A kid's token sends it to the parents' approval queue instead;
  tier 3 (unlock): always denied, whatever the config says.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import __version__
from .agent import brief
from .appliance import write_secret
from .env import config_dir

PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
READ_ONLY = {"get_status", "who_has", "list_chores", "whoami"}
INSTRUCTIONS = (
    "ChorePilot runs this household's devices and chore board. Read tools are free. Writes go "
    "through the household's policy engine: water/heat actions (dishwasher, washer, sprinkler, "
    "coffee, oven) wait for the person to confirm on their own phone or terminal and fail if "
    "they don't; door unlocks are never available to agents. Never claim something happened "
    "unless the result has ok: true.")


def _err(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _ok(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


# ------------------------------------------------------------------ tokens
class Tokens:
    """~/.config/chorepilot/mcp-tokens.json (0600): sha256(token) -> person, client label.
    The token itself is shown once, at creation, and never stored."""

    def __init__(self, path=None):
        self.path = path or os.path.join(config_dir(), "mcp-tokens.json")

    def _load(self):
        try:
            with open(self.path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def add(self, person, client):
        tok = "cp_" + secrets.token_urlsafe(32)
        d = self._load()
        d[hashlib.sha256(tok.encode()).hexdigest()] = {"person": person, "client": client,
                                                       "created": round(time.time())}
        write_secret(self.path, d)
        return tok

    def check(self, token):
        if not token:
            return None
        h = hashlib.sha256(token.encode()).hexdigest()
        for k, v in self._load().items():
            if hmac.compare_digest(k, h):
                return dict(v, hash=k)
        return None

    def list(self):
        return list(self._load().values())

    def revoke(self, client):
        d = self._load()
        keep = {k: v for k, v in d.items() if v.get("client") != client}
        write_secret(self.path, keep)
        return len(d) - len(keep)


# ------------------------------------------------------------------ the protocol core
class MCPServer:
    """Transport-independent JSON-RPC handler. `make_house(person, client)` builds a House
    (one per call: each thread gets its own SQLite connection) with source="mcp" and the
    Broker's confirm functions."""

    def __init__(self, make_house, log=None):
        self.make_house = make_house
        self.log = log or (lambda m: print(m, file=sys.stderr))

    def handle(self, msg, session):
        """One JSON-RPC message -> a response dict, or None for notifications/responses."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return _err(msg.get("id") if isinstance(msg, dict) else None, -32600, "invalid request")
        mid, method = msg.get("id"), msg.get("method")
        if method is None:
            return None                        # a response to something we sent: ignore
        if "id" not in msg:
            if method == "notifications/initialized":
                session["initialized"] = True
            return None
        params = msg.get("params") or {}
        try:
            if method == "initialize":
                want = params.get("protocolVersion")
                session["protocol"] = want if want in PROTOCOLS else PROTOCOLS[0]
                ci = params.get("clientInfo") or {}
                session["client_info"] = f"{ci.get('name', '?')} {ci.get('version', '')}".strip()
                return _ok(mid, {"protocolVersion": session["protocol"],
                                 "capabilities": {"tools": {"listChanged": False}},
                                 "serverInfo": {"name": "chorepilot", "title": "ChorePilot",
                                                "version": __version__},
                                 "instructions": INSTRUCTIONS})
            if method == "ping":
                return _ok(mid, {})
            if method == "tools/list":
                return _ok(mid, {"tools": self.tools(session)})
            if method == "tools/call":
                return _ok(mid, self.call(session, params.get("name"), params.get("arguments") or {}))
            if method in ("resources/list", "prompts/list", "resources/templates/list"):
                key = {"resources/list": "resources", "prompts/list": "prompts",
                       "resources/templates/list": "resourceTemplates"}[method]
                return _ok(mid, {key: []})
            return _err(mid, -32601, f"method not found: {method}")
        except _BadParams as e:
            return _err(mid, -32602, str(e))
        except Exception as e:  # noqa: BLE001 - never kill the server on one bad call
            self.log(f"  [mcp       ] {method} failed: {e!r}")
            return _err(mid, -32603, f"internal error: {e}")

    def label(self, session):
        return " / ".join(x for x in (session.get("label"), session.get("client_info")) if x) or "mcp"

    def tools(self, session):
        h = self.make_house(session.get("person"), self.label(session))
        try:
            out = []
            for t in h.tools(None):
                f = t["function"]
                name = f["name"]
                tool = {"name": name, "description": f.get("description", ""),
                        "inputSchema": f.get("parameters") or {"type": "object", "properties": {}}}
                ro = name in READ_ONLY
                tool["annotations"] = {"readOnlyHint": ro, "openWorldHint": False}
                if not ro:
                    tool["description"] += (" Writes go through the household's policy: water/heat "
                                            "actions wait for the person's own confirmation "
                                            "(Telegram/terminal); unlocking is never allowed.")
                out.append(tool)
            out.append({"name": "whoami", "description": "Who this connection acts as (person, "
                        "role) and what an agent may do here.",
                        "inputSchema": {"type": "object", "properties": {}},
                        "annotations": {"readOnlyHint": True, "openWorldHint": False}})
            return out
        finally:
            h.db.close()

    def call(self, session, name, args):
        if not isinstance(name, str) or not name:
            raise _BadParams("tools/call needs a tool name")
        if not isinstance(args, dict):
            raise _BadParams("arguments must be an object")
        h = self.make_house(session.get("person"), self.label(session))
        try:
            if name == "whoami":
                from . import policy
                p = h.person
                cap = min(int({**policy.DEFAULTS, **h.policy}["agents_max_tier"]), 2)
                res = {"ok": True, "person": p["name"] if p else None,
                       "role": p["role"] if p else "guest", "client": self.label(session),
                       "agent_max_tier": cap,
                       "rules": "tier 0-1 run directly; tier 2 waits for the person's own "
                                "confirmation; tier 3 (unlock) is never available to agents"}
            else:
                known = {t["function"]["name"] for t in h.tools(None)}
                if name not in known:
                    raise _BadParams(f"unknown tool {name!r}")
                res = h.call(name, args)
            out = brief(res)
            for k in ("policy", "tier"):
                if res.get(k) is not None:
                    out[k] = res[k]
            broker = getattr(h, "broker", None)
            if broker and broker.last:
                out["confirmation"] = broker.last
                if res.get("declined"):
                    out["error"] = (f"{h.me} did not confirm on their own channel (confirmation "
                                    f"#{broker.last['id']}: {broker.last['state']}); nothing was done")
            return {"content": [{"type": "text", "text": json.dumps(out, default=str)}],
                    "structuredContent": json.loads(json.dumps(out, default=str)),
                    "isError": not res.get("ok")}
        finally:
            h.db.close()


class _BadParams(ValueError):
    pass


def house_factory(cfg=None, timeout=None, log=None, notifier=None, watch=True, db_path=None,
                  announce=None, **house_kw):
    """make_house for the server: a fresh House + ChoreDB per call, source "mcp", the Broker
    for confirmations, PINs never asked (tier 3 is denied anyway)."""
    from .chores import ChoreDB
    from .confirm import DEFAULT_TIMEOUT, Broker
    from .control import Notifier
    from .house import House
    from .housecfg import load_house
    log = log or (lambda m: print(m, file=sys.stderr))
    base = cfg if cfg is not None else load_house()
    tmo = timeout or ((base.get("mcp") or {}).get("confirm_timeout_s")) or DEFAULT_TIMEOUT

    def make(person, client):
        db = ChoreDB(db_path) if db_path else ChoreDB()
        note = notifier or Notifier()

        def push(c):
            note.send(f"{client} asks: {c['question']} Answer in Telegram or with "
                      f"`chorepilot confirm {c['id']}`.", log=lambda m: None)
        broker = Broker(db, person, "mcp", client, timeout=tmo, log=log,
                        announce=announce or push)
        h = House(json.loads(json.dumps(base)), db=db, person=person, source="mcp", yes=False,
                  confirm=broker.confirm, strong_confirm=broker.strong,
                  ask_secret=lambda q: None, notifier=note, log=log, watch=watch, client=client,
                  **house_kw)
        if person is None:
            h.person = None     # never fall back to "the only owner" for a remote caller
        h.broker = broker
        return h
    return make


# ------------------------------------------------------------------ stdio
def serve_stdio(server, person, label, stdin=None, stdout=None):
    """Newline-delimited JSON-RPC on stdin/stdout. Everything else goes to stderr (stdout is
    the protocol). tools/call runs in a thread so pings are answered during a confirm wait."""
    stdin = stdin or sys.stdin
    out = stdout or sys.stdout
    if stdout is None:
        sys.stdout = sys.stderr                # nothing but protocol on the real stdout
    lock = threading.Lock()
    session = {"person": person, "label": label}
    threads = []

    def write(resp):
        if resp is not None:
            with lock:
                out.write(json.dumps(resp) + "\n")
                out.flush()

    def one(msg):
        write(server.handle(msg, session))
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            write(_err(None, -32700, "parse error"))
            continue
        msgs = msg if isinstance(msg, list) else [msg]
        for m in msgs:
            if isinstance(m, dict) and m.get("method") == "tools/call":
                t = threading.Thread(target=one, args=(m,), daemon=True)
                t.start()
                threads.append(t)
            else:
                one(m)
    for t in threads:
        t.join()


# ------------------------------------------------------------------ streamable HTTP
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]")


def origin_ok(origin, allowed=()):
    if not origin:
        return True                            # not a browser
    if origin in allowed:
        return True
    try:
        host = urlparse(origin).hostname
    except ValueError:
        return False
    return host in LOCAL_HOSTS


class _Handler(BaseHTTPRequestHandler):
    server_version = "chorepilot-mcp"
    protocol_version = "HTTP/1.1"
    timeout = 30                      # a client that stops sending can't hold a thread forever

    def log_message(self, fmt, *a):
        self.server.app_log(f"  [mcp http  ] {self.address_string()} {fmt % a}")

    def _send(self, code, body=None, headers=None):
        data = b"" if body is None else json.dumps(body).encode()
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _gate(self):
        if urlparse(self.path).path != self.server.mcp_path:
            self._send(404, {"error": "not found"})
            return None
        if not origin_ok(self.headers.get("Origin"), self.server.allowed_origins):
            self._send(403, {"error": "origin not allowed"})
            return None
        auth = self.headers.get("Authorization") or ""
        tok = auth[7:].strip() if auth[:7].lower() == "bearer " else None
        entry = self.server.tokens.check(tok)
        if not entry:
            self._send(401, {"error": "a valid bearer token is required "
                             "(chorepilot mcp token add <person> --client <name>)"},
                       {"WWW-Authenticate": 'Bearer realm="chorepilot"'})
            return None
        return entry

    def do_GET(self):
        if self._gate() is not None:
            # no server-initiated stream: the spec allows 405 here
            self._send(405, {"error": "this server does not offer an SSE stream"}, {"Allow": "POST, DELETE"})

    def do_DELETE(self):
        entry = self._gate()
        if entry is None:
            return
        sid = self.headers.get("Mcp-Session-Id")
        with self.server.lock:
            s = self.server.sessions.get(sid)
            if s and s["token"] == entry["hash"]:
                del self.server.sessions[sid]
                return self._send(200, {})
        self._send(404, {"error": "unknown session"})

    def do_POST(self):
        entry = self._gate()
        if entry is None:
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0:
            self.close_connection = True
            return self._send(400, {"error": "bad Content-Length"}, {"Connection": "close"})
        if n > 1_000_000:
            return self._send(413, {"error": "too large"})
        try:
            msg = json.loads(self.rfile.read(n) or b"null")
        except ValueError:
            return self._send(400, _err(None, -32700, "parse error"))
        msgs = msg if isinstance(msg, list) else [msg]
        headers = {}
        is_init = any(isinstance(m, dict) and m.get("method") == "initialize" for m in msgs)
        sid = self.headers.get("Mcp-Session-Id")
        with self.server.lock:
            if is_init:
                sid = secrets.token_hex(16)
                self.server.sessions[sid] = {"token": entry["hash"], "person": entry["person"],
                                             "label": entry.get("client")}
                headers["Mcp-Session-Id"] = sid
            if not sid:
                return self._send(400, _err(None, -32600, "missing Mcp-Session-Id (initialize first)"))
            session = self.server.sessions.get(sid)
            if not session or session["token"] != entry["hash"]:
                return self._send(404, _err(None, -32600, "unknown or expired session"))
        out = [r for r in (self.server.mcp.handle(m, session) for m in msgs) if r is not None]
        if not out:
            return self._send(202, None, headers)
        self._send(200, out if isinstance(msg, list) else out[0], headers)


def make_http_server(server, host="127.0.0.1", port=8770, tokens=None, allowed_origins=(),
                     path="/mcp", log=None):
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.daemon_threads = True
    httpd.mcp = server
    httpd.tokens = tokens or Tokens()
    httpd.sessions = {}
    httpd.lock = threading.Lock()
    httpd.allowed_origins = tuple(allowed_origins or ())
    httpd.mcp_path = path
    httpd.app_log = log or (lambda m: print(m, file=sys.stderr))
    return httpd


# ------------------------------------------------------------------ CLI
def cmd_mcp(args):
    words = list(args.words or [])
    if words[:1] == ["token"]:
        return _cmd_token(args, words[1:])
    if words and words[0] not in ("serve", "stdio", "http"):
        raise SystemExit("usage: chorepilot mcp [--http] | chorepilot mcp token add|list|revoke")
    http = args.http or words[:1] == ["http"]
    log = lambda m: print(m, file=sys.stderr, flush=True)  # noqa: E731
    server = MCPServer(house_factory(timeout=args.confirm_timeout, log=log,
                                     backend=args.backend), log=log)
    if not http:
        from .chores import ChoreDB
        db = ChoreDB()
        who = args.as_person or os.environ.get("CHOREPILOT_USER")
        if not who:
            owners = [p for p in db.people() if p["role"] == "owner"]
            who = owners[0]["name"] if len(owners) == 1 else None
        person = db.person(who, required=False) if who else None
        db.close()
        log(f"  [mcp       ] stdio server: acting as {person['name'] + ' (' + person['role'] + ')' if person else 'a guest'}"
            f", client {args.client}")
        serve_stdio(server, person["name"] if person else None, args.client)
        return
    tokens = Tokens()
    if not tokens.list():
        raise SystemExit("no MCP tokens yet: `chorepilot mcp token add <person> --client <name>` "
                         "first (HTTP never runs without authentication)")
    host = "0.0.0.0" if args.lan else args.host
    if args.lan or host not in ("127.0.0.1", "localhost", "::1"):
        log("  !! WARNING: the MCP server is listening on the network. Anyone who can reach this "
            "port and holds a token can act as that token's person (tier 2 still needs their own "
            "confirmation; unlock is never allowed). There is no TLS here: put it behind a TLS "
            "tunnel or reverse proxy, never a plain port-forward.")
    httpd = make_http_server(server, host, args.port, tokens, args.allow_origin or (), log=log)
    log(f"  [mcp       ] streamable HTTP on http://{host}:{httpd.server_address[1]}/mcp "
        f"({len(tokens.list())} token(s))")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def _cmd_token(args, words):
    t = Tokens()
    act = words[0] if words else "list"
    if act == "add":
        if len(words) < 2:
            raise SystemExit("usage: chorepilot mcp token add <person> --client <name>")
        from .chores import ChoreDB
        p = ChoreDB().person(words[1])
        tok = t.add(p["name"], args.client)
        print(f"token for {args.client}, acting as {p['name']} ({p['role']}). It is shown once; "
              f"store it in the client's config:\n{tok}")
    elif act == "list":
        for e in t.list():
            print(f"{e['client']:<20} {e['person']:<12} "
                  f"{time.strftime('%Y-%m-%d', time.localtime(e.get('created', 0)))}")
        if not t.list():
            print("no tokens")
    elif act == "revoke":
        print(f"revoked {t.revoke(words[1] if len(words) > 1 else args.client)} token(s)")
    else:
        raise SystemExit("usage: chorepilot mcp token add|list|revoke")


def add_parser(sub, as_arg):
    sp = sub.add_parser("mcp", help="MCP server for outside agents (Grok Bot, Claude, OpenClaw): "
                        "stdio, or --http; `mcp token add <person> --client <name>`")
    sp.add_argument("words", nargs="*", help="(nothing) = stdio; http; token add|list|revoke ...")
    sp.add_argument("--http", action="store_true", help="streamable HTTP instead of stdio")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8770)
    sp.add_argument("--lan", action="store_true", help="listen on every interface (trusted LAN "
                    "or behind a TLS tunnel only)")
    sp.add_argument("--allow-origin", action="append", help="extra browser Origin to accept")
    sp.add_argument("--client", default="mcp-client", help="client label (audit log; tokens)")
    sp.add_argument("--confirm-timeout", type=float, help="seconds a tier-2 confirmation waits "
                    "(default 120; then it is a no)")
    sp.add_argument("--backend", choices=["simulated", "ha", "matter", "mqtt", "homeconnect"])
    as_arg(sp)
    sp.set_defaults(func=cmd_mcp)
