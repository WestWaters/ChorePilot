"""ChorePilot for Hermes Agent: the house and the chore board as Hermes tools.

The plugin is a thin client of ChorePilot's own MCP server (`chorepilot run`, or
`chorepilot mcp --http`), on this machine, on the LAN or over Tailscale. ChorePilot keeps every
decision: the token belongs to one household person, and ChorePilot's policy engine decides from
that person, never from what the model sends:

  tier 0-1 (read, lights, vacuum, AC in the band, locking)  run
  tier 2 (water / heat: dishwasher, washer, sprinkler, oven) wait for that person's own yes in
                                                             Telegram or the dashboard; no answer = no
  a kid's token                                              goes to the parents' approval queue
  tier 3 (unlock, garage open, disarm)                       always refused

Settings (asked for by `hermes plugins install`, kept in Hermes's .env):
  CHOREPILOT_URL    e.g. http://127.0.0.1:8770/mcp, http://house.local:8770/mcp,
                    https://house.tailnet-name.ts.net/mcp
  CHOREPILOT_TOKEN  from `chorepilot mcp token add <person> --client hermes` (stored hashed there)
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .client import DEFAULT_TIMEOUT, DEFAULT_URL, ChorePilotError, Client

logger = logging.getLogger(__name__)

TOOLSET = "chorepilot"
VERSION = "0.7.0"
RULES = ("ChorePilot decides what is allowed from the token's person, not from you: reads and "
         "tier-1 actions run; water/heat actions (dishwasher, washer, sprinkler, coffee, oven) wait "
         "for that person to confirm on their own phone (Telegram or the ChorePilot dashboard), and "
         "nothing you send counts as their yes; a kid's request goes to the parents' approval "
         "queue; unlocking, opening the garage and disarming are never available. Only say "
         "something happened when the result has ok: true.")


def _secret(name, default=None):
    """Hermes's profile-scoped secret lookup when running inside Hermes; the environment
    otherwise (tests, a plain Python shell)."""
    try:
        from agent.secret_scope import get_secret
    except ImportError:
        return os.environ.get(name, default)
    return get_secret(name, default)


_clients = {}


def client():
    url = (_secret("CHOREPILOT_URL") or DEFAULT_URL).strip()
    token = (_secret("CHOREPILOT_TOKEN") or "").strip()
    try:
        timeout = float(_secret("CHOREPILOT_TIMEOUT") or DEFAULT_TIMEOUT)
    except ValueError:
        timeout = DEFAULT_TIMEOUT
    key = (url, token, timeout)
    if key not in _clients:
        _clients.clear()
        _clients[key] = Client(url, token, timeout=timeout, client_name="hermes-agent",
                               client_version=VERSION)
    return _clients[key]


def _available():
    return bool((_secret("CHOREPILOT_TOKEN") or "").strip())


def _run(name, args):
    try:
        out = client().call(name, args)
    except ChorePilotError as e:
        return json.dumps({"ok": False, "error": str(e)})
    except Exception as e:  # noqa: BLE001 - a tool must answer, never raise into Hermes
        logger.exception("chorepilot %s failed", name)
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})
    conf = out.get("confirmation")
    if conf and out.get("ok"):
        out["note"] = f"confirmed by the person ({conf.get('via')}), then done"
    elif conf:
        out["note"] = ("the person did not say yes on their own channel; nothing was done. Tell "
                       "them so; don't retry unless they ask again")
    elif out.get("policy") == "approval":
        out["note"] = "queued for a parent's approval; tell the person it is waiting, not done"
    return json.dumps(out, default=str)


def _pick(args, *keys):
    return {k: args[k] for k in keys if args.get(k) not in (None, "")}


# ------------------------------------------------------------------ handlers
def status(args, **kw):
    return _run("get_status", _pick(args or {}, "appliance"))


def chores(args, **kw):
    return _run("list_chores", _pick(args or {}, "person", "scope"))


def who_has(args, **kw):
    return _run("who_has", _pick(args or {}, "chore"))


def done(args, **kw):
    return _run("complete_chore", _pick(args or {}, "chore", "person"))


def whoami(args, **kw):
    return _run("whoami", {})


def tools(args, **kw):
    try:
        listed = client().list_tools()
    except ChorePilotError as e:
        return json.dumps({"ok": False, "error": str(e)})
    return json.dumps({"ok": True, "tools": [
        {"name": t["name"], "description": t.get("description", "").split(" Writes go through")[0],
         "arguments": (t.get("inputSchema") or {}).get("properties") or {},
         "read_only": bool((t.get("annotations") or {}).get("readOnlyHint"))}
        for t in listed]})


def call(args, **kw):
    args = args or {}
    name = str(args.get("tool") or "").strip()
    if not name:
        return json.dumps({"ok": False, "error": "say which ChorePilot tool (see chorepilot_tools)"})
    arguments = args.get("arguments") or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except ValueError:
            return json.dumps({"ok": False, "error": "arguments must be a JSON object"})
    if not isinstance(arguments, dict):
        return json.dumps({"ok": False, "error": "arguments must be a JSON object"})
    # the target tool's arguments may also sit next to "tool" (small models do that)
    flat = {k: v for k, v in args.items() if k not in ("tool", "arguments") and v not in (None, "")}
    return _run(name, {**flat, **arguments})


# ------------------------------------------------------------------ schemas
def _obj(props=None, required=()):
    return {"type": "object", "properties": props or {}, "required": list(required)}


def _catalog(listed, limit=2400):
    """'set_light(device: living_room_light; action: on|off|dim; brightness)' lines for the
    chorepilot_call description, from the house's live tool list."""
    lines = []
    for t in listed:
        if t["name"] in ("get_status", "list_chores", "who_has", "complete_chore", "whoami"):
            continue
        parts = []
        for k, v in ((t.get("inputSchema") or {}).get("properties") or {}).items():
            enum = v.get("enum") if isinstance(v, dict) else None
            parts.append(f"{k}: {'|'.join(map(str, enum[:8]))}" if enum else k)
        lines.append(f"{t['name']}({'; '.join(parts[:6])})")
    text = "\n".join(lines)
    return text if len(text) <= limit else text[:limit].rsplit("\n", 1)[0] + "\n..."


def schemas(listed=None):
    call_desc = ("Run any ChorePilot house or chore tool by name: devices (lights, climate, "
                 "vacuum, blinds, dishwasher, washer, sprinkler, oven, ...) and chores (assign, "
                 "remind, create). " + RULES)
    if listed:
        call_desc += "\nThis house's tools:\n" + _catalog(listed)
    else:
        call_desc += " Call chorepilot_tools first for the tool names and their arguments."
    return [
        ({"name": "chorepilot_status",
          "description": "Read the house: one device's state (or every device): on/off, program, "
                         "time left, temperatures, doors, alarms. Read-only.",
          "parameters": _obj({"appliance": {"type": "string", "description":
                                            "device name, e.g. dishwasher; empty = all"}})},
         status),
        ({"name": "chorepilot_chores",
          "description": "The household chore board: open chores, optionally for one person. "
                         "Read-only.",
          "parameters": _obj({"person": {"type": "string", "description": "a household member"},
                              "scope": {"type": "string", "description": "e.g. today, week, all"}})},
         chores),
        ({"name": "chorepilot_who_has",
          "description": "Who has a chore this week / next, and when it was last done. Read-only.",
          "parameters": _obj({"chore": {"type": "string", "description": "e.g. Dishes"}},
                             ["chore"])},
         who_has),
        ({"name": "chorepilot_done",
          "description": "Mark a chore done (by the token's person unless another is named). "
                         + RULES,
          "parameters": _obj({"chore": {"type": "string"},
                              "person": {"type": "string", "description": "who did it"}},
                             ["chore"])},
         done),
        ({"name": "chorepilot_whoami",
          "description": "Which household person this connection acts as, their role, and what "
                         "an agent may do in this house. Read-only.",
          "parameters": _obj()},
         whoami),
        ({"name": "chorepilot_tools",
          "description": "List every ChorePilot tool this house offers, with its arguments, "
                         "for chorepilot_call. Read-only.",
          "parameters": _obj()},
         tools),
        ({"name": "chorepilot_call",
          "description": call_desc,
          "parameters": {**_obj({
              "tool": {"type": "string", "description": "the ChorePilot tool, e.g. set_light"},
              "arguments": {"type": "object", "description":
                            "that tool's arguments, e.g. {\"device\": \"living_room_light\", "
                            "\"action\": \"turn_on\"}; they may also be given directly below"},
              "device": {"type": "string", "description": "device name, e.g. living_room_light"},
              "appliance": {"type": "string", "description": "appliance, e.g. dishwasher"},
              "action": {"type": "string", "description": "e.g. turn_on, turn_off, start, lock"},
              "program": {"type": "string", "description": "appliance program, e.g. eco"},
              "temperature": {"type": "number"},
              "chore": {"type": "string"},
              "person": {"type": "string"}}, ["tool"]), "additionalProperties": True}},
         call),
    ]


TOOL_NAMES = [s["name"] for s, _ in schemas()]


def register(ctx):
    listed = None
    if _available():
        try:                    # a quick look at the house, for the chorepilot_call description
            c = client()
            listed = Client(c.url, c.token, timeout=2.0).list_tools(timeout=2.0)
        except Exception as e:  # noqa: BLE001 - offline at start: chorepilot_tools still works
            logger.info("chorepilot: house not reachable at load (%s); tools load anyway", e)
    for schema, handler in schemas(listed):
        ctx.register_tool(name=schema["name"], toolset=TOOLSET, schema=schema, handler=handler,
                          check_fn=_available, emoji="🏠")
    skills = Path(__file__).parent / "skills"
    if hasattr(ctx, "register_skill") and skills.is_dir():
        for child in sorted(skills.iterdir()):
            if (child / "SKILL.md").exists():
                ctx.register_skill(child.name, child / "SKILL.md")


__all__ = ["register", "Client", "ChorePilotError"]
