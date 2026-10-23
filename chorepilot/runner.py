"""`chorepilot run`: everything that is configured, in one process.

    events     device events -> chores (Home Assistant websocket, Home Connect SSE, MQTT;
               polling for the rest)                         if any device is configured
    telegram   the household bot                             if a bot token is saved
    mcp        streamable HTTP for outside agents            if an MCP token exists
    web        the dashboard on http://127.0.0.1:8771        unless --no-web

Each part runs in its own thread and is restarted (with backoff) if it crashes; one part
failing never stops the others. SIGTERM / Ctrl-C stops everything. `chorepilot service`
writes a systemd user unit or a launchd agent that keeps `chorepilot run` running.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time

from .env import getenv

SYSTEMD_UNIT = """[Unit]
Description=ChorePilot, your smart-home AI agent assistant
After=network-online.target
Wants=network-online.target

[Service]
ExecStart={exec}
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1
{env}
[Install]
WantedBy=default.target
"""

LAUNCHD_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
{args}
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key><string>1</string>
{env}  </dict>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""
LABEL = "com.chorepilot.run"


def _now():
    return time.strftime("%H:%M:%S")


class Component:
    def __init__(self, name, start, stop=None):
        self.name, self._start, self._stop = name, start, stop
        self.thread = None
        self.restarts = 0


class Runner:
    def __init__(self, cfg, log=None, stopped=None):
        self.cfg = cfg
        self.log = log or (lambda m: print(m, file=sys.stderr, flush=True))
        self.stopped = stopped or threading.Event()
        self.components = []

    def add(self, name, start, stop=None):
        self.components.append(Component(name, start, stop))

    def _supervise(self, c):
        delay = 2.0
        while not self.stopped.is_set():
            t0 = time.time()
            try:
                c._start()
                if self.stopped.is_set():
                    return
                self.log(f"  [run       ] {c.name} exited; restarting in {delay:g}s")
            except Exception as e:  # noqa: BLE001 - keep the other parts alive
                self.log(f"  [run       ] {c.name} crashed: {e!r}; restarting in {delay:g}s")
            c.restarts += 1
            if time.time() - t0 > 300:
                delay = 2.0
            self.stopped.wait(delay)
            delay = min(delay * 2, 300)

    def start(self):
        for c in self.components:
            c.thread = threading.Thread(target=self._supervise, args=(c,), daemon=True,
                                        name=f"chorepilot-{c.name}")
            c.thread.start()
        return self

    def stop(self):
        self.stopped.set()
        for c in self.components:
            if c._stop:
                try:
                    c._stop()
                except Exception:  # noqa: BLE001
                    pass

    def wait(self):
        try:
            while not self.stopped.wait(1.0):
                pass
        except KeyboardInterrupt:
            pass
        self.stop()


def build(cfg, args, log):
    """The Runner for this house.json and these flags; -> (runner, [(part, why it's on/off)])."""
    from .mcp import Tokens
    from .telegram import TelegramError, load_token
    r = Runner(cfg, log)
    notes = []
    llm = cfg.get("llm") or {}
    url = getattr(args, "url", None) or llm.get("url")

    if cfg.get("devices") and not args.no_events:
        def events():
            from .house import House
            from .push import PushRunner
            h = House(cfg, source="events", watch=False, log=log)
            pr = PushRunner(h, log=log, interval=args.interval, safety=args.safety)
            r._push = pr
            try:
                pr.run()
            finally:
                h.db.close()
        r.add("events", events, lambda: getattr(r, "_push", None) and r._push.stop())
        notes.append(("events", f"{len(cfg['devices'])} device(s)"))
    else:
        notes.append(("events", "off (no devices)" if not cfg.get("devices") else "off (--no-events)"))

    tok = None
    if not args.no_telegram:
        try:
            tok = load_token()
        except TelegramError:
            tok = None
    if tok:
        def telegram():
            from .chores import ChoreDB
            from .telegram import Bot, TelegramAPI, house_factory
            tcfg = cfg.get("telegram") or {}
            db = ChoreDB()
            bot = Bot(TelegramAPI(tok), house_factory(cfg), db, llm_url=url,
                      model=getattr(args, "llm", None) or llm.get("model"), log=log,
                      household_chat=tcfg.get("household_chat"), remind_at=tcfg.get("remind_at"))
            try:
                bot.run(stop=r.stopped.is_set)
            finally:
                db.close()
        r.add("telegram", telegram)
        notes.append(("telegram", "on" + ("" if url else " (no model URL: chat answers are "
                                                          "limited to /chores and /done)")))
    else:
        notes.append(("telegram", "off (--no-telegram)" if args.no_telegram else
                      "off (no bot token: chorepilot setup telegram)"))

    mcfg = cfg.get("mcp") or {}
    if Tokens().list() and not args.no_mcp:
        holder = {}

        def mcp():
            from .mcp import MCPServer, house_factory, make_http_server
            srv = MCPServer(house_factory(log=log), log=log)
            host = getenv("MCP_HOST") or mcfg.get("host", "127.0.0.1")
            httpd = make_http_server(srv, host, int(args.mcp_port or mcfg.get("port", 8770)),
                                     Tokens(), mcfg.get("allow_origins") or (), log=log)
            holder["httpd"] = httpd
            log(f"  [mcp       ] streamable HTTP on http://{host}:{httpd.server_address[1]}/mcp")
            try:
                httpd.serve_forever(poll_interval=0.5)
            finally:
                httpd.server_close()
        r.add("mcp", mcp, lambda: holder.get("httpd") and holder["httpd"].shutdown())
        notes.append(("mcp", f"{len(Tokens().list())} token(s)"))
    else:
        notes.append(("mcp", "off (--no-mcp)" if args.no_mcp else
                      "off (no tokens: chorepilot setup mcp)"))

    wcfg = cfg.get("web") or {}
    if not args.no_web and wcfg.get("enabled", True):
        wholder = {}

        def web():
            from .web import make_server
            host = getenv("WEB_HOST") or wcfg.get("host", "127.0.0.1")
            port = args.web_port if args.web_port is not None else wcfg.get("port", 8771)
            httpd = make_server(host, int(port), cfg=None, log=log,
                                public_url=wcfg.get("public_url"),
                                allowed_hosts=tuple(wcfg.get("allow_hosts") or ()) or None)
            wholder["httpd"] = httpd
            log(f"  [web       ] dashboard on http://{host}:{httpd.server_address[1]}/")
            try:
                httpd.serve_forever(poll_interval=0.5)
            finally:
                httpd.server_close()
        r.add("web", web, lambda: wholder.get("httpd") and wholder["httpd"].shutdown())
        wport = args.web_port if args.web_port is not None else wcfg.get("port", 8771)
        notes.append(("web", wcfg.get("public_url") or
                      f"http://{getenv('WEB_HOST') or wcfg.get('host', '127.0.0.1')}:{wport}/"))
    else:
        notes.append(("web", "off"))
    return r, notes


def cmd_run(args):
    from .housecfg import house_path, load_house
    cfg = load_house()
    log = lambda m: print(m, file=sys.stderr, flush=True)  # noqa: E731
    if cfg.get("_preset") == "demo":
        log(f"  [run       ] no {house_path()} yet: run `chorepilot setup` first (using the "
            "simulated demo house for now)")
    r, notes = build(cfg, args, log)
    log(f"ChorePilot run ({cfg.get('household') or 'this house'}):")
    for part, why in notes:
        log(f"  {part:<9} {why}")
    if not r.components:
        raise SystemExit("nothing to run: configure devices, Telegram or MCP with `chorepilot setup`")
    if args.check:
        return r
    signal.signal(signal.SIGTERM, lambda *a: r.stopped.set())
    r.start().wait()
    return r


# ------------------------------------------------------------------ service files
def _exec_args():
    exe = shutil.which("chorepilot")
    return [exe, "run"] if exe else [sys.executable, "-m", "chorepilot", "run"]


def _passthrough_env():
    keep = ("CHOREPILOT_CONFIG_DIR", "CHOREPILOT_CACHE_DIR", "CHOREPILOT_LLM_URL", "HA_URL")
    return {k: os.environ[k] for k in keep if os.environ.get(k)}


def systemd_unit(args=None, env=None):
    args = args or _exec_args()
    env = _passthrough_env() if env is None else env
    lines = "".join(f"Environment={k}={v}\n" for k, v in env.items())
    return SYSTEMD_UNIT.format(exec=" ".join(args), env=lines)


def launchd_plist(args=None, env=None, log=None):
    from xml.sax.saxutils import escape
    args = args or _exec_args()
    env = _passthrough_env() if env is None else env
    log = log or os.path.expanduser("~/Library/Logs/chorepilot.log")
    a = "\n".join(f"    <string>{escape(x)}</string>" for x in args)
    e = "".join(f"    <key>{escape(k)}</key><string>{escape(v)}</string>\n" for k, v in env.items())
    return LAUNCHD_PLIST.format(label=LABEL, args=a, env=e, log=escape(log))


def service_path(kind):
    if kind == "launchd":
        return os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                        "systemd", "user", "chorepilot.service")


def cmd_service(args):
    kind = args.kind or ("launchd" if sys.platform == "darwin" else "systemd")
    text = launchd_plist() if kind == "launchd" else systemd_unit()
    path = service_path(kind)
    if args.action == "print":
        print(text, end="")
        return
    if args.action == "uninstall":
        if kind == "launchd":
            print(f"run: launchctl bootout gui/{os.getuid()}/{LABEL}")
        else:
            print("run: systemctl --user disable --now chorepilot")
        if os.path.exists(path):
            os.remove(path)
            print(f"removed {path}")
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    print(f"wrote {path}")
    if kind == "launchd":
        cmds = [["launchctl", "bootstrap", f"gui/{os.getuid()}", path]]
        print(f"start it now (and at every login):\n  launchctl bootstrap gui/{os.getuid()} {path}")
    else:
        cmds = [["systemctl", "--user", "daemon-reload"],
                ["systemctl", "--user", "enable", "--now", "chorepilot"]]
        print("start it now (and at every login):\n  systemctl --user daemon-reload && "
              "systemctl --user enable --now chorepilot\n  (on a headless box also: "
              "loginctl enable-linger $USER)")
    if args.enable:
        for c in cmds:
            subprocess.run(c, check=False)


def add_parser(sub):
    sp = sub.add_parser("run", help="start everything configured: Telegram, device events, MCP, "
                        "the web dashboard")
    sp.add_argument("--url", default=getenv("LLM_URL"), help="model server (default: house.json llm)")
    sp.add_argument("--llm", help="model name to send")
    sp.add_argument("--no-telegram", action="store_true")
    sp.add_argument("--no-events", action="store_true")
    sp.add_argument("--no-mcp", action="store_true")
    sp.add_argument("--no-web", action="store_true")
    sp.add_argument("--mcp-port", type=int)
    sp.add_argument("--web-port", type=int)
    sp.add_argument("-i", "--interval", type=float, default=30, help="poll interval for devices "
                    "with no push source (seconds)")
    sp.add_argument("--safety", type=float, default=900, help="safety poll for push devices")
    sp.add_argument("--check", action="store_true", help="print what would run, then exit")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("service", help="keep `chorepilot run` running: a systemd user unit "
                        "(Linux) or a launchd agent (macOS)")
    sp.add_argument("action", nargs="?", default="print", choices=["print", "install", "uninstall"])
    sp.add_argument("--kind", choices=["systemd", "launchd"])
    sp.add_argument("--enable", action="store_true", help="install: also start it now")
    sp.set_defaults(func=cmd_service)
