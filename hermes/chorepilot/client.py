"""A small MCP client for ChorePilot's streamable-HTTP server (`chorepilot mcp --http`).

Stdlib only, so the plugin installs with nothing extra. It speaks just the slice of MCP that
ChorePilot's server offers: initialize, tools/list, tools/call, JSON responses (no SSE), an
Mcp-Session-Id it reuses until the server forgets it.

Nothing here decides anything. Every policy check (tier 2 waits for the person's own
confirmation, tier 3 is denied, a kid's token goes to the approval queue) happens inside
ChorePilot, from the token, never from what this client sends.
"""
from __future__ import annotations

import ipaddress
import json
import threading
import urllib.error
import urllib.request
from urllib.parse import urlparse

PROTOCOL = "2025-06-18"
DEFAULT_URL = "http://127.0.0.1:8770/mcp"
# ChorePilot waits up to 120 s (mcp.confirm_timeout_s) for a tier-2 yes; leave room for it.
DEFAULT_TIMEOUT = 150.0


class ChorePilotError(Exception):
    pass


def plain_http_ok(url):
    """Plain http:// only to this machine or a private network (LAN, Tailscale 100.64/10,
    *.local, *.ts.net, *.lan, *.home.arpa). Anything else needs https: the bearer token would
    otherwise cross the internet in the clear."""
    p = urlparse(url)
    if p.scheme == "https":
        return True
    if p.scheme != "http":
        return False
    host = (p.hostname or "").lower()
    if host in ("localhost",) or host.endswith((".local", ".lan", ".home.arpa", ".ts.net",
                                                ".internal")):
        return True
    if "." not in host:                       # a bare LAN hostname: "nas", "pi"
        return bool(host)
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip in ipaddress.ip_network("100.64.0.0/10")


class Client:
    def __init__(self, url, token, timeout=DEFAULT_TIMEOUT, client_name="hermes-agent",
                 client_version="", opener=None):
        url = (url or DEFAULT_URL).strip()
        if not url.rstrip("/").endswith("/mcp"):
            url = url.rstrip("/") + "/mcp"
        if not plain_http_ok(url):
            raise ChorePilotError(
                f"refusing {url}: use https:// (tailscale serve, a Cloudflare tunnel) or a LAN / "
                "Tailscale address; the token must not cross the internet in plain HTTP")
        if not token:
            raise ChorePilotError("no ChorePilot token: run `chorepilot mcp token add <person> "
                                  "--client hermes` and put it in CHOREPILOT_TOKEN")
        self.url, self.token, self.timeout = url, token.strip(), float(timeout)
        self.client_info = {"name": client_name, "version": client_version or "0"}
        self.session = None
        self.instructions = ""
        self.lock = threading.Lock()
        self._open = opener or urllib.request.urlopen

    # ---------------------------------------------------------------- transport
    def _post(self, body, timeout=None, session=True):
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "Authorization": f"Bearer {self.token}",
                   "MCP-Protocol-Version": PROTOCOL}
        if session and self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=headers,
                                     method="POST")
        try:
            with self._open(req, timeout=timeout or self.timeout) as r:
                sid = r.headers.get("Mcp-Session-Id")
                raw = r.read()
                return r.status, sid, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read() or b"null")
            except ValueError:
                detail = None
            finally:
                e.close()
            return e.code, None, detail
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            reason = getattr(e, "reason", e)
            raise ChorePilotError(f"can't reach ChorePilot at {self.url}: {reason}. Is "
                                  "`chorepilot run` (or `chorepilot mcp --http`) running?") from None

    def _init(self, timeout=None):
        status, sid, body = self._post({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                                        "params": {"protocolVersion": PROTOCOL,
                                                   "capabilities": {},
                                                   "clientInfo": self.client_info}},
                                       timeout=timeout, session=False)
        if status == 401:
            raise ChorePilotError("ChorePilot refused the token (401). Make a new one with "
                                  "`chorepilot mcp token add <person> --client hermes`")
        if status != 200 or not sid or not isinstance(body, dict) or "result" not in body:
            raise ChorePilotError(f"ChorePilot did not start an MCP session (HTTP {status}: {body})")
        self.session = sid
        self.instructions = body["result"].get("instructions") or ""
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout=timeout)

    def rpc(self, method, params=None, timeout=None):
        """One request; starts (or restarts, after a server restart) the session as needed."""
        with self.lock:
            for attempt in (1, 2):
                if not self.session:
                    self._init(timeout)
                status, _, body = self._post({"jsonrpc": "2.0", "id": 1, "method": method,
                                              "params": params or {}}, timeout=timeout)
                if status in (400, 404) and attempt == 1:
                    self.session = None              # the server forgot us: start over once
                    continue
                if status == 401:
                    raise ChorePilotError("ChorePilot refused the token (401); it may have been "
                                          "revoked")
                if status != 200 or not isinstance(body, dict):
                    raise ChorePilotError(f"ChorePilot answered HTTP {status}: {body}")
                if "error" in body:
                    raise ChorePilotError(body["error"].get("message") or str(body["error"]))
                return body.get("result") or {}
        raise ChorePilotError("ChorePilot session could not be established")

    # ---------------------------------------------------------------- the MCP surface
    def list_tools(self, timeout=None):
        return self.rpc("tools/list", timeout=timeout).get("tools") or []

    def call(self, name, arguments=None):
        """tools/call -> ChorePilot's result object (ok, policy, tier, confirmation, ...)."""
        res = self.rpc("tools/call", {"name": name, "arguments": arguments or {}})
        out = res.get("structuredContent")
        if not isinstance(out, dict):
            text = "".join(c.get("text", "") for c in res.get("content") or []
                           if c.get("type") == "text")
            try:
                out = json.loads(text)
            except ValueError:
                out = {"ok": not res.get("isError"), "result": text}
        return out
