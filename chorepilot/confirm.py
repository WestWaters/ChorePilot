"""Confirmations that a human answers on their own channel, never the agent that asked.

When an outside agent (an MCP client: Grok Bot, Claude, OpenClaw ...) asks for a tier-2 action
(start the dishwasher, water the lawn, preheat the oven), the policy engine says "confirm". The
agent can't answer that itself: nothing it sends counts. Instead the Broker

  1. writes a pending confirmation to the household database (who must answer, the question,
     which client asked, an expiry),
  2. pushes it to the person: a Telegram inline button (y/N) or a "type PREHEAT" prompt, if
     `chorepilot telegram` is running; a desktop notification; a line on the server's stderr,
  3. waits for the answer, and treats a timeout as NO.

Answers come only from
  * Telegram: the button press / typed phrase from that person's linked Telegram account,
  * the dashboard: that person, signed in with their own PIN, on the Approvals page, or
  * the terminal: `chorepilot confirm <id>` at an interactive terminal (a TTY; a pipe or an
    agent's non-interactive shell can't answer yes).

`mcp.confirm_channels` in house.json can narrow this, e.g. ["telegram"] when the MCP client
also has a shell on this machine (Claude Code, OpenClaw) and you don't want the terminal path.
"""
from __future__ import annotations

import sys
import time

DEFAULT_TIMEOUT = 120
CHANNELS = ("telegram", "terminal", "web")


def channels(cfg):
    got = ((cfg or {}).get("mcp") or {}).get("confirm_channels") or list(CHANNELS)
    return [c for c in got if c in CHANNELS]


class Broker:
    """Confirm functions for a House whose caller is an outside agent."""

    def __init__(self, db, person, source="mcp", client=None, timeout=DEFAULT_TIMEOUT, poll=0.5,
                 sleep=time.sleep, announce=None, log=None):
        self.db, self.person, self.source, self.client = db, person, source, client
        self.timeout, self.poll, self.sleep = float(timeout), poll, sleep
        self.announce = announce
        self.log = log or (lambda m: print(m, file=sys.stderr))
        self.last = None

    def confirm(self, question):
        return self._ask(question, "yes", None)

    def strong(self, question, phrase):
        return self._ask(question, "phrase", phrase)

    def _ask(self, question, kind, phrase):
        if not self.person:
            return False
        c = self.db.add_confirmation(self.person, question, kind, phrase, self.source,
                                     self.client, ttl=self.timeout)
        how = f"type {phrase}" if kind == "phrase" else "y/N"
        self.log(f"  [confirm   ] #{c['id']} for {c['person']} ({how}), asked by "
                 f"{self.client or self.source}: {question}  -> answer in Telegram or "
                 f"`chorepilot confirm {c['id']}`")
        if self.announce:
            try:
                self.announce(c)
            except Exception as e:  # noqa: BLE001 - a failed push just means a timeout
                self.log(f"  [confirm   ] could not push #{c['id']}: {e}")
        end = time.time() + self.timeout
        while True:
            cur = self.db.confirmation(c["id"])
            if cur["state"] != "pending":
                break
            if time.time() >= end:
                self.db._x("UPDATE confirmations SET state='expired' WHERE id=? AND "
                           "state='pending'", (c["id"],))
                cur = self.db.confirmation(c["id"])
                break
            self.sleep(self.poll)
        self.last = {"id": cur["id"], "state": cur["state"], "via": cur["answered_via"]}
        self.log(f"  [confirm   ] #{cur['id']}: {cur['state']}"
                 + (f" (via {cur['answered_via']})" if cur["answered_via"] else ""))
        return cur["state"] == "yes"


def terminal_answer(db, cid, deny=False, ask=input, isatty=None, say=print):
    """`chorepilot confirm <id>`: a human at an interactive terminal answers. Returns the row."""
    c = db.confirmation(cid)
    if not c or c["state"] != "pending":
        raise ValueError(f"no pending confirmation #{cid}")
    if deny:
        return db.answer_confirmation(cid, False, "terminal")
    tty = isatty() if isatty else (sys.stdin is not None and sys.stdin.isatty())
    if not tty:
        raise ValueError("a yes has to be typed at an interactive terminal (or pressed in "
                         "Telegram); `--deny` works anywhere")
    say(f"  #{c['id']} for {c['person'] or '?'}, asked by {c['client'] or c['source']}:")
    if c["kind"] == "phrase":
        got = ask(f"  !! {c['question']}\n  !! Type {c['phrase']} to confirm (anything else "
                  "cancels): ")
        return db.answer_confirmation(cid, True, "terminal", answer=got)
    got = ask(f"  ?? {c['question']} [y/N] ").strip().lower()
    return db.answer_confirmation(cid, got in ("y", "yes"), "terminal")
