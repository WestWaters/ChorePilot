"""`chorepilot setup`: first-run onboarding. Whoever installs ChorePilot creates the household and
becomes its OWNER; nothing about any particular household is baked into the package.

    chorepilot setup                     # the whole wizard, interactive
    chorepilot setup owner|members|model|devices|telegram|mcp|chores|status
    chorepilot setup --non-interactive --household "The Lees" --owner Alex --owner-pin 2468 \\
        --member Sam:adult --member Jordan:kid --model-url auto --chores default

Sections (each one can be re-run on its own):
  owner     the household's name, the owner (PIN and/or TOTP with a QR code for an
            authenticator app), the temperature unit. Adding a second owner later needs an
            existing owner's code; `chorepilot people transfer-owner` hands ownership over.
  members   adults / kids / guests (kids get quiet hours), optional PINs for the dashboard.
  model     finds a running llama-server / Ollama / LM Studio / vLLM / any OpenAI-compatible
            URL, and recommends a GGUF for this machine (probe + recommend) when none runs.
  devices   Home Assistant (URL + long-lived token, entity discovery, suggested mapping),
            Home Connect (device-flow login, the appliances on your account), an MQTT broker,
            single devices by hand (--device), or the simulated demo devices to try it out.
  telegram  a walkthrough to create YOUR OWN bot with @BotFather, the token (stored 0600),
            one-time /link codes for each member.
  mcp       a token per outside agent (Grok Bot, Claude, any MCP client), shown once.
  chores    sane defaults (dishes, laundry, trash ...) fitted to the devices and the people;
            also written to chores.json, which you can edit and re-import with --from.

Secrets go to ~/.config/chorepilot/secrets.json (0600) or their own 0600 files, never to
house.json. Environment variables still win (HA_TOKEN, CHOREPILOT_TELEGRAM_TOKEN, ...).
"""
from __future__ import annotations

import getpass
import json
import locale
import os
import re
import sys

from .appliance import BackendError, http_json
from .chores import ROLES, ChoreDB, ChoreError
from .env import config_dir, secret, set_secret
from .housecfg import PRESETS, house_path

SECTIONS = ("owner", "members", "model", "devices", "telegram", "mcp", "chores")
F_COUNTRIES = ("US", "LR", "MM", "BS", "KY", "PW", "FM", "MH")


# ------------------------------------------------------------------ prompting
class IO:
    """Interactive prompts, or (non-interactive) defaults only. `answers` scripts a run."""

    def __init__(self, interactive=True, answers=None, out=None):
        self.interactive = interactive
        self.answers = list(answers) if answers is not None else None
        self.out = out or (lambda m="": print(m, flush=True))

    def say(self, msg=""):
        self.out(msg)

    def _input(self, prompt, hide=False):
        if self.answers is not None:
            if not self.answers:
                raise SystemExit("setup: the scripted answers ran out")
            v = self.answers.pop(0)
            self.out(prompt + ("***" if hide else v))
            return v
        try:
            return (getpass.getpass(prompt) if hide else input(prompt)).strip()
        except EOFError:
            return ""

    def ask(self, q, default=None):
        if not self.interactive:
            return default
        v = self._input(f"  {q}" + (f" [{default}]" if default not in (None, "") else "") + ": ")
        return v if v else default

    def yes(self, q, default=True):
        if not self.interactive:
            return default
        v = self._input(f"  {q} [{'Y/n' if default else 'y/N'}]: ").lower()
        return default if not v else v.startswith("y")

    def secret(self, q):
        if not self.interactive:
            return None
        return self._input(f"  {q}: ", hide=True) or None


# ------------------------------------------------------------------ house.json (raw)
def read_house(path=None):
    """The house.json on disk ({} if there is none): setup never edits the demo fallback."""
    try:
        with open(path or house_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_house_raw(cfg, path=None):
    path = path or house_path()
    d = os.path.dirname(path)
    if not os.path.isdir(d):
        os.makedirs(d, mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)
    return path


def guess_unit():
    try:
        loc = locale.getlocale()[0] or os.environ.get("LANG", "")
    except ValueError:
        loc = os.environ.get("LANG", "")
    m = re.search(r"_([A-Z]{2})", loc or "")
    return "F" if m and m.group(1) in F_COUNTRIES else "C"


# ------------------------------------------------------------------ model endpoints
LLM_CANDIDATES = (
    ("llama-server", "http://127.0.0.1:8080"),
    ("ollama", "http://127.0.0.1:11434"),
    ("lm-studio", "http://127.0.0.1:1234"),
    ("vllm", "http://127.0.0.1:8000"),
    ("llamafile / other", "http://127.0.0.1:8081"),
)


def probe_llm(url, kind="openai-compatible", http=http_json, timeout=1.5):
    """{"kind", "url", "models"} if `url` answers like an OpenAI-compatible server, else None."""
    url = url.rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    try:
        code, js = http("GET", url + "/v1/models", timeout=timeout)
    except BackendError:
        return None
    if code == 200 and isinstance(js, dict):
        models = [m.get("id") for m in js.get("data") or [] if isinstance(m, dict)]
        return {"kind": kind, "url": url, "models": [m for m in models if m]}
    if kind == "ollama":
        try:
            code, js = http("GET", url + "/api/tags", timeout=timeout)
        except BackendError:
            return None
        if code == 200 and isinstance(js, dict):
            return {"kind": kind, "url": url, "models": [m.get("name") for m in js.get("models", [])]}
    return None


def detect_llms(http=http_json, candidates=LLM_CANDIDATES, extra=()):
    found = []
    for kind, url in list(candidates) + [("custom", u) for u in extra]:
        r = probe_llm(url, kind, http=http)
        if r:
            found.append(r)
    return found


def local_recommendations(limit=3):
    """[(name, path, verdict, advice)] of GGUFs on disk that fit this machine, best first."""
    try:
        from .models import scan
        from .probe import probe
        from .recommend import recommend
        dev = probe()
        recs = [r for r in recommend(scan([]), dev, None) if r.verdict in ("RUNS", "TIGHT")]
        return dev, [(r.spec.name, r.spec.source, r.verdict, r.advice) for r in recs[:limit]]
    except Exception:  # noqa: BLE001 - a probe failure must not stop onboarding
        return None, []


def size_hint(usable_gb):
    if usable_gb is None:
        return "a 1.5B-4B instruct model with tool calling (Qwen2.5 / Qwen3 class), Q4"
    if usable_gb < 2:
        return "a 0.5B-1.5B instruct model at Q4 (Qwen2.5-1.5B-Instruct Q4_K_S is ~1 GB)"
    if usable_gb < 6:
        return "a 1.5B-4B instruct model at Q4-Q5 (Qwen2.5-3B / Qwen3-4B class)"
    if usable_gb < 14:
        return "a 7B-9B instruct model at Q4_K_M (Qwen2.5-7B-Instruct / Llama-3.1-8B class)"
    return "a 14B+ instruct model, or a 30B MoE (Qwen3-30B-A3B class) at Q4"


# ------------------------------------------------------------------ Home Assistant discovery
def _slug(s):
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", str(s).lower())).strip("_") or "device"


def ha_type_for(entity_id, attrs):
    dom, _, obj = entity_id.partition(".")
    name = (obj + " " + str((attrs or {}).get("friendly_name", ""))).lower()
    dc = str((attrs or {}).get("device_class") or "").lower()
    if dom in ("light", "climate", "lock", "vacuum", "water_heater", "humidifier", "media_player"):
        return dom
    if dom == "valve":
        return None if dc == "gas" else "valve"
    if dom == "cover":
        return "garage_door" if dc in ("garage", "gate") or "garage" in name or "gate" in name \
            else "cover"
    if dom == "alarm_control_panel":
        return "alarm_panel"
    if dom == "fan" and ("purif" in name or "air" in name or (attrs or {}).get("preset_modes")):
        return "air_purifier" if "purif" in name or "air" in name else None
    if dom == "switch":
        if re.search(r"sprinkl|irrigat|zone|lawn|garden", name):
            return "valve"
        if re.search(r"\bev\b|charg|wallbox|evse", name):
            return "ev_charger"
    return None


def ha_discover(url, token, http=http_json):
    """-> (message, [{"nick", "type", "entity", "name", "state"}]). Raises SetupError if HA
    can't be reached or refuses the token."""
    url = url.rstrip("/")
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        code, js = http("GET", url + "/api/", h, timeout=8)
    except BackendError as e:
        raise SetupError(f"can't reach Home Assistant at {url}: {e}") from None
    if code == 401:
        raise SetupError("Home Assistant refused the token (401): create a long-lived access "
                         "token under your profile > Security")
    if code != 200:
        raise SetupError(f"Home Assistant answered HTTP {code}: {js}")
    code, states = http("GET", url + "/api/states", h, timeout=15)
    if code != 200 or not isinstance(states, list):
        raise SetupError(f"could not list entities: HTTP {code}")
    out, used = [], set()
    for s in states:
        eid, attrs = s.get("entity_id", ""), s.get("attributes") or {}
        t = ha_type_for(eid, attrs)
        if not t:
            continue
        nick = _slug(eid.split(".", 1)[1])
        base, i = nick, 2
        while nick in used:
            nick, i = f"{base}_{i}", i + 1
        used.add(nick)
        out.append({"nick": nick, "type": t, "entity": eid,
                    "name": attrs.get("friendly_name"), "state": s.get("state")})
    out.sort(key=lambda d: (d["type"], d["nick"]))
    return f"Home Assistant {js.get('message', 'API running') if isinstance(js, dict) else ''}".strip(), out


# ------------------------------------------------------------------ Home Connect discovery
HC_TYPES = {"Dishwasher": "dishwasher", "Washer": "washer", "Dryer": "dryer",
            "WasherDryer": "washer", "CoffeeMaker": "coffee_maker", "Oven": "oven",
            "FridgeFreezer": "fridge", "Refrigerator": "fridge", "Freezer": "fridge"}


def hc_discover(token, base="https://api.home-connect.com", http=http_json):
    from .machines import MODELS
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.bsh.sdk.v1+json"}
    code, js = http("GET", base.rstrip("/") + "/api/homeappliances", h, timeout=15)
    if code != 200:
        raise SetupError(f"Home Connect listing failed: HTTP {code} {js}")
    out, used = [], set()
    for a in js["data"]["homeappliances"]:
        t = HC_TYPES.get(a.get("type"))
        if not t:
            continue
        nick, i = t, 2
        while nick in used:
            nick, i = f"{t}_{i}", i + 1
        used.add(nick)
        e = {"nick": nick, "type": t, "haId": a.get("haId"), "name": a.get("name") or a.get("type"),
             "brand": a.get("brand"), "connected": a.get("connected")}
        vib = str(a.get("vib") or "").upper()
        if vib in MODELS:
            e["model"] = vib
        out.append(e)
    return out


# ------------------------------------------------------------------ default chores
def default_chores(devices, people):
    """Chores that fit the devices you have and the people who live there. Kids get the
    small ones (empty the dishwasher, bins); adults the machine runs."""
    types = {(d.get("type") or n): n for n, d in (devices or {}).items()}
    everyone = [p["name"] for p in people if p["role"] in ("owner", "adult", "kid")]
    adults = [p["name"] for p in people if p["role"] in ("owner", "adult")] or everyone
    kids = [p["name"] for p in people if p["role"] == "kid"]
    helpers = kids or everyone
    out = []
    if "dishwasher" in types:
        out.append(dict(title="Run the dishwasher", schedule="daily at 19:30", rotation=everyone,
                        rotate="week", points=2, verify=f"{types['dishwasher']}.finished",
                        followups=["Empty the dishwasher"]))
        out.append(dict(title="Empty the dishwasher", schedule="on demand", rotation=helpers,
                        points=1, needs_approval=bool(kids)))
    else:
        out.append(dict(title="Dishes", schedule="daily at 19:30", rotation=everyone,
                        rotate="week", points=2))
    wash = types.get("washer")
    out.append(dict(title="Laundry", schedule="every monday and thursday at 18:00",
                    rotation=adults, points=2,
                    **({"verify": f"{types.get('dryer') or wash}.finished",
                        "followups": ["Fold the laundry"]} if wash else
                       {"followups": ["Fold the laundry"]})))
    out.append(dict(title="Fold the laundry", schedule="on demand", rotation=helpers, points=1,
                    needs_approval=bool(kids)))
    out.append(dict(title="Take out the trash", schedule="every tuesday and friday at 20:00",
                    rotation=helpers, points=1, needs_approval=bool(kids)))
    out.append(dict(title="Recycling", schedule="every sunday at 18:00", rotation=everyone,
                    points=1))
    if "vacuum" in types:
        out.append(dict(title="Vacuum the living room", schedule="every 3 days", rotation=helpers,
                        points=1, verify=f"{types['vacuum']}.finished"))
    else:
        out.append(dict(title="Vacuum", schedule="every saturday at 10:00", rotation=everyone,
                        points=2))
    out.append(dict(title="Clean the bathroom", schedule="every saturday at 11:00",
                    rotation=adults, points=3))
    if "valve" in types:
        out.append(dict(title="Water the lawn", schedule="weekends at 07:00", rotation=adults,
                        points=1, verify=f"{types['valve']}.finished"))
    return [c for c in out if c.get("rotation")]


# ------------------------------------------------------------------ the wizard
class SetupError(RuntimeError):
    pass


class Setup:
    def __init__(self, io, args, db=None, http=http_json, hc_login=None, tg_api=None):
        self.io, self.a = io, args
        self.db = db or ChoreDB()
        self.http = http
        self.hc_login = hc_login
        self.tg_api = tg_api
        self.cfg = read_house()

    def save(self):
        self.cfg.pop("_preset", None)
        self.cfg.pop("_from", None)
        return write_house_raw(self.cfg)

    def arg(self, name, default=None):
        v = getattr(self.a, name, None)
        return default if v in (None, [], "") else v

    # ---------------------------------------------------------------- owner
    def owner(self):
        io, db = self.io, self.db
        io.say("\n== The household and its owner ==")
        if self.arg("household"):
            self.cfg["household"] = self.a.household
        elif not self.cfg.get("household"):
            self.cfg["household"] = io.ask("What should ChorePilot call this household?",
                                           "our home") or "our home"
        self.cfg["unit"] = (self.arg("unit") or self.cfg.get("unit")
                            or io.ask("Temperature unit, C or F?", guess_unit()) or "C").upper()[:1]
        self.cfg.setdefault("backend", "simulated")
        self.cfg.setdefault("devices", {})
        owners = db.owners()
        name = self.arg("owner")
        if owners and not name:
            io.say(f"  owner{'s' if len(owners) > 1 else ''}: "
                   + ", ".join(p["name"] for p in owners))
            if io.interactive and io.yes("Add another owner (co-owner)?", False):
                name = io.ask("Their name")
            else:
                self.save()
                return owners[0]
        if not name:
            name = io.ask("Your name (you will be the owner)", getpass.getuser().capitalize())
        if not name:
            raise SetupError("an owner name is needed: --owner NAME")
        existing = db.person(name, required=False)
        if existing and existing["name"].lower() != name.lower():
            existing = None
        if owners and not (existing and existing["role"] == "owner"):
            self._require_owner_code(owners, f"make {name} an owner")
        if existing:
            if existing["role"] != "owner":
                db.set_role(existing["name"], "owner")
            p = db.person(existing["id"])
        else:
            p = db.add_person(name, "owner")
        io.say(f"  {p['name']} is {'an' if owners else 'the'} owner of {self.cfg['household']}")
        self.cfg.setdefault("local_person", p["name"])
        self._factors(p, owner=True)
        self.save()
        return p

    def _require_owner_code(self, owners, why):
        code = self.arg("auth") or self.io.secret(f"An existing owner's PIN or TOTP code (to {why})")
        if not code or not any(self.db.check_pin(o["name"], code) or self.db.check_totp(o["name"], code)
                               for o in owners):
            raise SetupError(f"an existing owner's PIN or TOTP code is needed to {why} (--auth)")

    def _factors(self, p, owner=False):
        io, db, name = self.io, self.db, p["name"]
        pin = self.arg("owner_pin") if owner else None
        pin = pin or (os.environ.get("CHOREPILOT_OWNER_PIN") if owner else None)
        if not pin and io.interactive:
            while True:
                pin = io.secret(f"Choose a PIN for {name} (4-12 digits; unlocks doors with the "
                                "owner's say-so, and the dashboard)" if owner else
                                f"PIN for {name} (dashboard login; blank = none)")
                if not pin:
                    break
                again = io.secret("Type it again")
                if again == pin:
                    break
                io.say("  they did not match; try again")
        if pin:
            try:
                db.set_pin(name, pin)
                io.say(f"  PIN set for {name} (stored as a PBKDF2 hash)")
            except ChoreError as e:
                raise SetupError(str(e)) from None
        want_totp = self.arg("totp") if owner else False
        if owner and not want_totp and io.interactive:
            want_totp = io.yes("Also enrol an authenticator app (TOTP) for the owner?", True)
        if want_totp:
            self.enrol_totp(name)
        if owner and not pin and not want_totp and not db.has_second_factor(name):
            io.say(f"  !! no PIN or TOTP yet: unlocking, opening the garage and disarming stay "
                   f"blocked until `chorepilot people set-pin {name}`")

    def enrol_totp(self, name):
        from . import qr
        io = self.io
        secret_, uri = self.db.set_totp(name)
        io.say(f"\n  Scan this with your authenticator app (Google Authenticator, Aegis, 1Password, "
               f"Authy ...):\n")
        try:
            io.say(qr.terminal(qr.encode(uri), ansi=sys.stdout.isatty()))
        except ValueError:
            pass
        io.say(f"\n  or enter the key by hand: {secret_}\n")
        out = self.arg("qr_file")
        if out:
            data = qr.png(qr.encode(uri)) if out.endswith(".png") else qr.svg(qr.encode(uri)).encode()
            fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            io.say(f"  QR code also written to {out} (mode 0600: it contains the secret; delete it "
                   "after scanning)")
        if io.interactive:
            for _ in range(3):
                code = io.ask("Type the 6-digit code the app shows now (blank to skip the check)")
                if not code:
                    break
                if self.db.check_totp(name, code):
                    io.say("  authenticator app verified")
                    break
                io.say("  that code does not match; check the phone's clock and try again")
        return secret_, uri

    # ---------------------------------------------------------------- members
    def members(self):
        io, db = self.io, self.db
        io.say("\n== Household members ==")
        for spec in self.arg("member", []) or []:
            parts = spec.split(":")
            name, role = parts[0].strip(), (parts[1] if len(parts) > 1 else "adult").strip().lower()
            pin = parts[2].strip() if len(parts) > 2 else None
            self._add_member(name, role, pin)
        if io.interactive:
            io.say("  Roles: adult (runs things, approves kids' requests), kid (asks; water/heat "
                   "needs a parent), guest (can only look).")
            while True:
                name = io.ask("Add a member: name (blank when done)")
                if not name:
                    break
                role = (io.ask("Role: adult, kid or guest", "adult") or "adult").lower()
                pin = io.secret(f"PIN for {name} (dashboard login; blank = none)")
                self._add_member(name, role, pin)
        for p in db.people():
            io.say(f"  {p['name']:<12} {p['role']:<6} {p['quiet_hours'] or ''}")
        return db.people()

    def _add_member(self, name, role, pin=None):
        if role not in ROLES or role == "owner":
            raise SetupError(f"role for {name} must be adult, kid, guest or staff "
                             "(owners: `chorepilot setup owner`)")
        p = self.db.person(name, required=False)
        if p and p["name"].lower() == name.lower():
            self.io.say(f"  {p['name']} is already here ({p['role']})")
        else:
            quiet = "21:00-07:00" if role == "kid" else None
            p = self.db.add_person(name, role, quiet_hours=quiet)
            self.io.say(f"  added {p['name']} ({role})" + (f", quiet hours {quiet}" if quiet else ""))
        if pin:
            try:
                self.db.set_pin(p["name"], pin)
            except ChoreError as e:
                raise SetupError(f"{p['name']}: {e}") from None

    # ---------------------------------------------------------------- model
    def model(self):
        io = self.io
        io.say("\n== The local model ==")
        want = self.arg("model_url")
        llm = dict(self.cfg.get("llm") or {})
        if want and want not in ("auto", "none"):
            r = probe_llm(want, "custom", http=self.http)
            llm = {"url": (r or {}).get("url") or want.rstrip("/"), "kind": (r or {}).get("kind", "custom")}
            if r and r["models"]:
                llm["model"] = self.arg("model_name") or r["models"][0]
            io.say(f"  using {llm['url']}" + ("" if r else " (not answering right now; saved anyway)"))
        elif want == "none":
            llm = {}
            io.say("  no model server: `chorepilot ask` starts the best local GGUF on demand")
        else:
            found = detect_llms(http=self.http)
            for i, r in enumerate(found, 1):
                io.say(f"  [{i}] {r['kind']:<18} {r['url']:<26} "
                       f"{', '.join(r['models'][:3]) or '(no model list)'}")
            pick = None
            if found:
                pick = found[0]
                if io.interactive and len(found) > 1:
                    n = io.ask("Which one?", "1")
                    pick = found[int(n) - 1] if str(n).isdigit() and 0 < int(n) <= len(found) else found[0]
            elif io.interactive:
                url = io.ask("No model server found on this machine. URL of one on the LAN "
                             "(blank = start a local GGUF on demand)")
                if url:
                    pick = probe_llm(url, "custom", http=self.http) or {"url": url.rstrip("/"),
                                                                         "kind": "custom", "models": []}
            if pick:
                llm = {"url": pick["url"], "kind": pick["kind"]}
                models = pick.get("models") or []
                name = self.arg("model_name")
                if not name and len(models) > 1 and io.interactive:
                    name = io.ask("Model name to use", models[0])
                if name or models:
                    llm["model"] = name or models[0]
                io.say(f"  using {llm['url']} ({llm['kind']})"
                       + (f", model {llm['model']}" if llm.get("model") else ""))
            else:
                llm = {}
                io.say("  no model server found.")
        if not llm.get("url") and not self.arg("no_probe"):
            dev, recs = local_recommendations()
            if recs:
                io.say("  GGUFs on this machine that fit (best first); `chorepilot ask` starts the "
                       "first one when needed:")
                for name, path, verdict, adv in recs:
                    io.say(f"    {verdict:<5} {name}  ({adv})")
            else:
                usable = dev.usable_gb() if dev else None
                io.say(f"  nothing on disk fits yet. For this machine"
                       + (f" (~{usable:.1f} GB usable)" if usable else "")
                       + f", get {size_hint(usable)}, e.g. with Ollama (`ollama pull qwen2.5:3b`) "
                       "or a GGUF into ~/models; `chorepilot recommend --catalog` lists exact files.")
        if llm:
            self.cfg["llm"] = llm
        else:
            self.cfg.pop("llm", None)
        self.save()
        return llm

    # ---------------------------------------------------------------- devices
    def devices(self):
        io = self.io
        io.say("\n== Devices ==")
        devs = self.cfg.setdefault("devices", {})
        did = False
        if self.arg("simulated") or (io.interactive and not devs and io.yes(
                "Add the simulated demo devices to try ChorePilot first (no hardware touched)?",
                False)):
            fahrenheit = (self.cfg.get("unit") or "C") == "F"
            for n, d in PRESETS["demo"]["devices"].items():
                d = dict(d, backend="simulated")
                if not fahrenheit:             # the demo's limits are in Fahrenheit
                    d.pop("comfort", None)
                    d.pop("max_temp", None)
                devs.setdefault(n, d)
            io.say(f"  added {len(PRESETS['demo']['devices'])} simulated devices")
            did = True
        ha_url = self.arg("ha_url") or (io.ask("Home Assistant URL (blank = skip)",
                                               (self.cfg.get("ha") or {}).get("url"))
                                        if io.interactive else None)
        if ha_url:
            self._ha(ha_url)
            did = True
        if self.arg("homeconnect") or (io.interactive and io.yes(
                "Connect Bosch / Siemens / Neff / Gaggenau appliances through Home Connect?", False)):
            self._homeconnect()
            did = True
        mq = self.arg("mqtt_host") or (io.ask("MQTT broker host[:port] (blank = skip)")
                                       if io.interactive else None)
        if mq:
            self._mqtt(mq)
            did = True
        for spec in self.arg("device", []) or []:
            self._manual(spec)
            did = True
        if not did and not devs:
            io.say("  no devices yet: re-run `chorepilot setup devices`, or add them to "
                   f"{house_path()} by hand")
        for n, d in devs.items():
            io.say(f"  {n:<22} {d.get('type'):<13} {d.get('backend') or self.cfg.get('backend')}"
                   + (f"  {d.get('entity') or d.get('haId') or d.get('prefix') or ''}"))
        self.save()
        return devs

    def _ha(self, url):
        io = self.io
        token = self.arg("ha_token") or secret("ha_token", "HA_TOKEN") or io.secret(
            "Home Assistant long-lived access token (profile > Security > Long-lived tokens)")
        if not token:
            raise SetupError("a Home Assistant token is needed (--ha-token or $HA_TOKEN)")
        msg, found = ha_discover(url, token, http=self.http)
        io.say(f"  {msg}: {len(found)} controllable entities found")
        set_secret("ha_token", token)
        self.cfg["ha"] = dict(self.cfg.get("ha") or {}, url=url.rstrip("/"))
        mode = self.arg("ha_import", "suggested")
        chosen = found
        if io.interactive and found:
            for i, d in enumerate(found, 1):
                io.say(f"   [{i:>2}] {d['nick']:<24} {d['type']:<13} {d['entity']:<36} {d['state']}"
                       + (f"  ({d['name']})" if d.get("name") else ""))
            pick = io.ask("Which to add? all, none, or numbers like 1,3,5-8", "all")
            chosen = _pick(found, pick)
        elif mode == "none":
            chosen = []
        pick = getattr(self.a, "ha_entities", None)     # the dashboard's checkbox list
        if pick is not None:
            chosen = [d for d in found if d["entity"] in set(pick)]
        devs = self.cfg.setdefault("devices", {})
        for d in chosen:
            if d["nick"] in devs and devs[d["nick"]].get("entity") != d["entity"]:
                d = dict(d, nick=d["nick"] + "_ha")
            entry = {"type": d["type"], "backend": "ha", "entity": d["entity"]}
            fname = str(d.get("name") or "").strip().lower()
            if fname and _slug(fname) != d["nick"]:
                entry["aliases"] = [fname]
            devs[d["nick"]] = entry
        io.say(f"  mapped {len(chosen)} Home Assistant entities (token saved to secrets.json, 0600)")

    def _homeconnect(self):
        io = self.io
        from .appliance import HC_TOKEN_FILE, device_flow_login, write_secret
        cid = self.arg("hc_client_id") or secret("home_connect_client_id", "HOME_CONNECT_CLIENT_ID")
        if not cid:
            io.say("  Home Connect needs a (free) developer app once: sign in at "
                   "https://developer.home-connect.com, register an application with OAuth flow "
                   "'Device Flow', and copy its Client ID.")
            cid = io.ask("Client ID")
        if not cid:
            raise SetupError("Home Connect needs a client id (--hc-client-id)")
        set_secret("home_connect_client_id", cid)
        login = self.hc_login or (lambda: device_flow_login(client_id=cid, log=io.say))
        tok = login()
        write_secret(os.path.join(config_dir(), HC_TOKEN_FILE), tok)
        found = hc_discover(tok["access_token"], http=self.http)
        devs = self.cfg.setdefault("devices", {})
        for d in found:
            e = {"type": d["type"], "backend": "homeconnect", "haId": d["haId"]}
            if d.get("model"):
                e["model"] = d["model"]
            devs[d["nick"]] = e
            io.say(f"  {d['nick']:<14} {d.get('brand') or ''} {d['name']} ({d['haId']})")
        io.say(f"  {len(found)} Home Connect appliance(s); token saved (0600, refreshed automatically)")

    def _mqtt(self, hostport):
        host, _, port = hostport.partition(":")
        self.cfg["mqtt"] = {"host": host, "port": int(port or self.arg("mqtt_port") or 1883)}
        user = self.arg("mqtt_user") or self.io.ask("MQTT user (blank = none)")
        if user:
            set_secret("mqtt_user", user)
            pw = self.arg("mqtt_password") or self.io.secret("MQTT password")
            if pw:
                set_secret("mqtt_password", pw)
        self.io.say(f"  MQTT broker {host}:{self.cfg['mqtt']['port']} saved; add ESPHome devices "
                    "with --device nick=type@mqtt:<topic prefix>")

    def _manual(self, spec):
        """nick=type[@backend][:entity-or-prefix], e.g. garage=garage_door@ha:cover.garage_door"""
        from .devtypes import get_type
        m = re.fullmatch(r"([A-Za-z0-9_\-]+)=([A-Za-z0-9_ \-]+?)(?:@(\w+))?(?::(.+))?", spec.strip())
        if not m:
            raise SetupError(f"--device {spec!r}: use nick=type[@backend][:entity or prefix]")
        nick, t, be, target = m.groups()
        try:
            t = get_type(t).name
        except KeyError as e:
            raise SetupError(str(e)) from None
        e = {"type": t}
        if be:
            e["backend"] = be
        if target:
            e["prefix" if be == "mqtt" else "haId" if be == "homeconnect" else "entity"] = target
        self.cfg.setdefault("devices", {})[_slug(nick)] = e
        self.io.say(f"  added {_slug(nick)} ({t}{'@' + be if be else ''})")

    # ---------------------------------------------------------------- telegram
    def telegram(self):
        from .telegram import TelegramAPI, TelegramError, load_token, save_token
        io, db = self.io, self.db
        io.say("\n== Telegram (your own household bot) ==")
        tok = self.arg("telegram_token")
        have = None
        try:
            have = load_token()
        except TelegramError:
            pass
        if not tok and io.interactive:
            if have and not io.yes("A bot token is already saved. Replace it?", False):
                tok = None
            else:
                io.say("  1. In Telegram, open @BotFather and send /newbot.\n"
                       "  2. Pick a display name (e.g. 'Our House') and a username ending in 'bot'.\n"
                       "  3. BotFather replies with a token like 123456789:AA...; paste it here.\n"
                       "  The bot is yours alone: ChorePilot talks to Telegram outbound only "
                       "(long polling), no port is opened.")
                tok = io.secret("Bot token (blank = skip Telegram)")
        if tok:
            try:
                path = save_token(tok)
            except TelegramError as e:
                raise SetupError(str(e)) from None
            have = tok.strip()
            io.say(f"  token saved to {path} (0600)")
            if not self.arg("no_verify"):
                try:
                    me = (self.tg_api or TelegramAPI)(have).call("getMe")
                    io.say(f"  connected: @{me.get('username')} ({me.get('first_name')})")
                    self.cfg.setdefault("telegram", {})["bot"] = me.get("username")
                except TelegramError as e:
                    io.say(f"  !! Telegram did not accept it yet ({e}); check the token")
        if not have:
            io.say("  skipped (re-run `chorepilot setup telegram` any time)")
            self.save()
            return None
        if self.arg("telegram_link") or (io.interactive and io.yes(
                "Make one-time /link codes for the members now?", True)):
            bot = (self.cfg.get("telegram") or {}).get("bot")
            io.say(f"  Each person sends their code to {'@' + bot if bot else 'the bot'} in a "
                   "PRIVATE chat within 10 minutes:")
            for p in db.people():
                if json.loads(p["handles"] or "{}").get("telegram_id"):
                    io.say(f"    {p['name']:<12} already linked")
                    continue
                io.say(f"    {p['name']:<12} /link {db.new_link_code(p['name'])}")
            io.say("  New codes any time: `chorepilot telegram link <name>`")
        self.save()
        return have

    # ---------------------------------------------------------------- mcp
    def mcp(self):
        from .mcp import Tokens
        io, db = self.io, self.db
        io.say("\n== Outside agents (MCP) ==")
        specs = list(self.arg("mcp_client", []) or [])
        if io.interactive:
            io.say("  An MCP token lets an agent (Grok Bot, Claude Desktop / Code, any MCP client) "
                   "use the house as one person. Water/heat still needs that person's yes on their "
                   "own phone; unlock, garage-open and disarm are never available to agents.")
            while True:
                c = io.ask("Agent name for a new token, e.g. grok-bot or claude (blank when done)")
                if not c:
                    break
                owner = (db.owners() or [{}])[0].get("name")
                who = io.ask("Acts as which person?", owner)
                specs.append(f"{c}:{who}")
        tokens = Tokens()
        made = []
        for spec in specs:
            client, _, who = spec.partition(":")
            who = who or (db.owners() or [{}])[0].get("name")
            if not who:
                raise SetupError("create the owner first (`chorepilot setup owner`)")
            p = db.person(who)
            tok = tokens.add(p["name"], client)
            made.append((client, p["name"], tok))
            io.say(f"  {client} acts as {p['name']} ({p['role']}). Token, shown once:\n    {tok}")
        if made:
            io.say("  Use it with:  chorepilot run   (or `chorepilot mcp --http`), endpoint "
                   "http://127.0.0.1:8770/mcp, header Authorization: Bearer <token>\n"
                   "  Local clients can skip tokens: claude mcp add chorepilot -- chorepilot mcp "
                   f"--as {made[0][1]}")
        elif not tokens.list():
            io.say("  no agent tokens (re-run `chorepilot setup mcp` any time)")
        return made

    # ---------------------------------------------------------------- chores
    def chores(self):
        io, db = self.io, self.db
        io.say("\n== Chores ==")
        src = self.arg("chores_from")
        mode = self.arg("chores", "default")
        if src:
            with open(src) as f:
                items = json.load(f)
            items = items.get("chores", items) if isinstance(items, dict) else items
        elif mode == "none":
            io.say("  no default chores")
            return []
        else:
            items = default_chores(self.cfg.get("devices"), db.people())
            if not items:
                io.say("  add people first; chores rotate between them")
                return []
            for i, c in enumerate(items, 1):
                io.say(f"   [{i}] {c['title']:<24} {c['schedule']:<36} {', '.join(c['rotation'])}")
            if io.interactive:
                keep = io.ask("Keep which? all, none, or numbers like 1,2,4", "all")
                items = _pick(items, keep)
        out = []
        for c in items:
            c = dict(c)
            title = c.pop("title")
            try:
                row, created = db.upsert_chore(title, **c)
            except ChoreError as e:
                io.say(f"  !! {title}: {e}")
                continue
            out.append(row)
            io.say(f"  {'added' if created else 'updated'} {row['title']}")
        if not src and out:
            path = os.path.join(config_dir(), "chores.json")
            with open(path, "w") as f:
                json.dump({"chores": [dict(c) for c in items]}, f, indent=2)
            io.say(f"  edit {path} and run `chorepilot setup chores --from {path}` to change "
                   "them, or use `chorepilot chores add / rm / assign`")
        db.materialise()
        return out

    # ---------------------------------------------------------------- status
    def status(self):
        from .mcp import Tokens
        from .telegram import TelegramError, load_token
        io, db, cfg = self.io, self.db, self.cfg
        owners = db.owners()
        try:
            load_token()
            tg = True
        except TelegramError:
            tg = False
        linked = sum(1 for p in db.people() if json.loads(p["handles"] or "{}").get("telegram_id"))
        rows = [
            ("household", bool(cfg.get("household")), cfg.get("household") or "chorepilot setup owner"),
            ("owner", bool(owners), ", ".join(
                p["name"] + (" (PIN/TOTP)" if db.has_second_factor(p["name"]) else " (no PIN!)")
                for p in owners) or "chorepilot setup owner"),
            ("members", len(db.people()) > len(owners), f"{len(db.people())} people"),
            ("model", bool((cfg.get("llm") or {}).get("url")),
             (cfg.get("llm") or {}).get("url") or "local GGUF on demand (chorepilot setup model)"),
            ("devices", bool(cfg.get("devices")), f"{len(cfg.get('devices') or {})} configured"),
            ("telegram", tg, f"token saved, {linked} linked" if tg else "chorepilot setup telegram"),
            ("mcp", bool(Tokens().list()), f"{len(Tokens().list())} agent token(s)"),
            ("chores", bool(db.chores()), f"{len(db.chores())} chores"),
        ]
        io.say(f"ChorePilot setup ({house_path()}):")
        for name, ok, note in rows:
            io.say(f"  [{'x' if ok else ' '}] {name:<10} {note}")
        return rows

    def run(self, sections):
        for s in sections:
            getattr(self, s)()
        if len(sections) > 1:
            self.io.say("")
            self.status()
            self.io.say("\nNext: `chorepilot run` starts everything configured (Telegram, device "
                        "events, MCP, the dashboard on http://127.0.0.1:8771).")


def _pick(items, spec):
    spec = (spec or "all").strip().lower()
    if spec in ("all", "a", "y", "yes"):
        return list(items)
    if spec in ("none", "n", "no", "0"):
        return []
    keep = set()
    for part in re.split(r"[,\s]+", spec):
        if "-" in part:
            a, _, b = part.partition("-")
            if a.isdigit() and b.isdigit():
                keep |= set(range(int(a), int(b) + 1))
        elif part.isdigit():
            keep.add(int(part))
    return [x for i, x in enumerate(items, 1) if i in keep]


# ------------------------------------------------------------------ CLI
def cmd_setup(args):
    interactive = not args.non_interactive and sys.stdin.isatty()
    if not args.non_interactive and not sys.stdin.isatty():
        print("(no terminal: running non-interactively; pass flags, see `chorepilot setup -h`)")
    io = IO(interactive)
    st = Setup(io, args)
    sec = args.section or "all"
    try:
        if sec == "status":
            st.status()
            return
        if sec == "all":
            if interactive:
                io.say("Welcome to ChorePilot, your smart-home AI agent assistant. This sets up "
                       "YOUR household; you become its owner. Ctrl-C any time; every step can be "
                       "re-run on its own (`chorepilot setup devices` ...).")
            st.run(list(SECTIONS))
        else:
            if sec != "owner" and not st.db.owners():
                raise SetupError("no owner yet: run `chorepilot setup` (or `setup owner`) first")
            st.run([sec])
    except (SetupError, ChoreError, BackendError) as e:
        raise SystemExit(f"chorepilot setup: {e}")
    except KeyboardInterrupt:
        raise SystemExit("\nsetup interrupted; what was done so far is saved")


def add_parser(sub):
    sp = sub.add_parser("setup", help="first-run onboarding: you become the owner; members, "
                        "model, devices, Telegram, MCP, chores (re-run any section)")
    sp.add_argument("section", nargs="?", choices=["all", "status", *SECTIONS])
    sp.add_argument("--non-interactive", "-n", action="store_true",
                    help="never prompt; take everything from the flags")
    g = sp.add_argument_group("owner")
    g.add_argument("--household", help="the household's name")
    g.add_argument("--owner", help="the owner's name (you)")
    g.add_argument("--owner-pin", help="the owner's PIN (or $CHOREPILOT_OWNER_PIN)")
    g.add_argument("--totp", action="store_true", help="enrol an authenticator app (prints a QR)")
    g.add_argument("--qr-file", help="also write the TOTP QR code to this .png / .svg (0600)")
    g.add_argument("--unit", choices=["C", "F", "c", "f"])
    g.add_argument("--auth", help="an existing owner's PIN/TOTP (to add a co-owner)")
    g = sp.add_argument_group("members")
    g.add_argument("--member", action="append", help="NAME:ROLE[:PIN], roles adult, kid, guest, "
                   "staff (repeatable)")
    g = sp.add_argument_group("model")
    g.add_argument("--model-url", help="auto (detect), none, or a URL (OpenAI-compatible)")
    g.add_argument("--model-name", help="model name to send (Ollama / multi-model servers)")
    g.add_argument("--no-probe", action="store_true", help="skip the hardware probe")
    g = sp.add_argument_group("devices")
    g.add_argument("--simulated", action="store_true", help="add the simulated demo devices")
    g.add_argument("--ha-url", help="Home Assistant URL, e.g. http://homeassistant.local:8123")
    g.add_argument("--ha-token", help="HA long-lived access token (or $HA_TOKEN)")
    g.add_argument("--ha-import", choices=["suggested", "none"], default=None,
                   help="non-interactive: map every discovered entity (default) or none")
    g.add_argument("--homeconnect", action="store_true", help="Home Connect device-flow login")
    g.add_argument("--hc-client-id", help="your Home Connect developer app's client id")
    g.add_argument("--mqtt-host", help="MQTT broker host[:port]")
    g.add_argument("--mqtt-port", type=int)
    g.add_argument("--mqtt-user")
    g.add_argument("--mqtt-password")
    g.add_argument("--device", action="append", help="nick=type[@backend][:entity or prefix] "
                   "(repeatable), e.g. garage=garage_door@ha:cover.garage_door")
    g = sp.add_argument_group("telegram")
    g.add_argument("--telegram-token", help="your bot's token from @BotFather")
    g.add_argument("--telegram-link", action="store_true", help="print /link codes for everyone")
    g.add_argument("--no-verify", action="store_true", help="don't call Telegram's getMe")
    g = sp.add_argument_group("mcp")
    g.add_argument("--mcp-client", action="append", help="AGENT[:PERSON] (repeatable), e.g. "
                   "grok-bot or claude:Sam")
    g = sp.add_argument_group("chores")
    g.add_argument("--chores", choices=["default", "none"], default=None)
    g.add_argument("--from", dest="chores_from", help="import chores from a JSON file")
    sp.set_defaults(func=cmd_setup)

