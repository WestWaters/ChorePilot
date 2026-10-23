"""The policy engine: role x safety tier x source -> what has to happen before an action runs.

The decision is made from the AUTHENTICATED person (who is at the terminal / whose chat handle
sent the message / whose token an MCP client holds), never from anything the model says. Chore
titles, device names and chat text are untrusted input.

Outcomes:
  allow            just do it
  confirm          an adult says yes (y/N at the terminal, a button in chat)
  strong_confirm   the adult types a phrase (oven preheat: PREHEAT); --yes does not cover it
  approval         queued for a parent/owner (kids asking for tier-2 things)
  second_factor    owner only + PIN or TOTP typed by the human (tier 3)
  deny             not allowed for this person / source

Defaults (override in house.json "policy"):
  kids_tier1: true      kids may switch lights, start the vacuum, lock a door
  guests_tier1: false   guests may only read
  staff_tier2: true     small-business staff can start the dishwasher etc. (with a yes)
  agents_max_tier: 2    MCP / external agents: the highest tier they may ask for (0, 1 or 2).
                        Tier 2 still needs the human's confirmation on their own channel
                        (Telegram button / terminal), never anything the agent sends.
                        Tier 3 (unlock) is ALWAYS denied to an external agent: the setting is
                        clamped to 2, whatever house.json says.
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULTS = {"kids_tier1": True, "guests_tier1": False, "staff_tier2": True, "agents_max_tier": 2}
EXTERNAL = ("mcp", "agent", "external")


@dataclass
class Decision:
    outcome: str
    reason: str = ""
    tier: int = 0

    @property
    def ok(self):
        return self.outcome != "deny"


def decide(person, tier, source="cli", settings=None, strong=False):
    """person: {"name", "role"} (or None = unknown, treated as guest)."""
    s = {**DEFAULTS, **(settings or {})}
    role = (person or {}).get("role") or "guest"
    name = (person or {}).get("name") or "someone"
    ext = source in EXTERNAL
    if tier <= 0:
        return Decision("allow", "read-only", tier)
    if source == "schedule" and tier >= 3:
        return Decision("deny", "security actions never run on a schedule", tier)
    if ext and tier >= 3:
        return Decision("deny", "an external agent can never do security actions (unlock); do it "
                        "from your own chat or the terminal", tier)
    if ext and tier > min(int(s["agents_max_tier"]), 2):
        return Decision("deny", "an external agent can't do that on its own; do it from your own "
                        "chat or the terminal", tier)
    if tier == 1:
        if role in ("owner", "adult", "staff"):
            return Decision("allow", "", tier)
        if role == "kid":
            return (Decision("allow", "", tier) if s["kids_tier1"] else
                    Decision("approval", f"{name} needs a parent's OK for that", tier))
        return (Decision("allow", "", tier) if s["guests_tier1"] else
                Decision("deny", "guests can only look", tier))
    if tier == 2:
        if role in ("owner", "adult") or (role == "staff" and s["staff_tier2"]):
            return Decision("strong_confirm" if strong else "confirm",
                            "uses water or heat: an adult confirms", tier)
        if role == "kid":
            return Decision("approval", f"{name} asked; a parent has to approve it", tier)
        return Decision("deny", f"a {role} can't start water or heat appliances", tier)
    # tier 3: security
    if role != "owner":
        return Decision("deny", "only the owner can do that (security action)", tier)
    return Decision("second_factor", "security action: owner PIN or TOTP required", tier)
