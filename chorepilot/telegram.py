"""`chorepilot telegram`: the household's Telegram bot (Bot API, long polling, stdlib urllib).

    chorepilot telegram set-token          # paste the @BotFather token; saved 0600, never in a repo
    chorepilot telegram link Jordan           # prints a one-time code (10 min); Jordan sends /link CODE
    chorepilot telegram run --url http://127.0.0.1:8080      # the bot (any OpenAI-compatible model)

Identity: a message's sender is the Telegram user id the platform reports; it maps to a
household person only through a link made with a one-time code (single use, 10 minutes,
stored hashed). Unlinked accounts can only /link. In groups the bot only works in the household
group set as `telegram.household_chat` (anywhere else it answers /help and points to a private
chat), so chores, device states and approvals never land in a chat with outsiders. Forwarded
messages are never acted on or taken as an answer. Chat text goes to the local model with that
person's identity and role, through the same policy engine as the terminal (source "telegram").

Confirmations:
  tier 2 y/N           inline Yes / No buttons; only the person who asked can press them
  strong (PREHEAT)     "type PREHEAT": the next message from that person in that chat
  tier 3 PIN / TOTP    ONLY in a private chat; the code message is deleted as soon as it is
                       read, never logged, never shown to the model; in a group it is refused
  MCP tier 2           a confirmation an outside agent is waiting for (confirm.py) arrives as a
                       button in the person's private chat
Parents get the approval queue with Approve / Deny buttons. Device-verified chore completions
and follow-ups are announced (to `telegram.household_chat` in house.json, else to every linked
person), reminders go to the person they are for.

The token comes from $CHOREPILOT_TELEGRAM_TOKEN or ~/.config/chorepilot/telegram-token (must be
mode 0600). Nothing here needs an open port: long polling is outbound only.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import stat
import sys
import threading
import time
import urllib.error
import urllib.request

from .env import cache_dir, config_dir, getenv

TOKEN_FILE = "telegram-token"
FORWARD_KEYS = ("forward_origin", "forward_from", "forward_from_chat", "forward_sender_name",
                "forward_date")
HELP = ("I'm ChorePilot. Ask me things like \"who has the dishes this week?\", \"wash my dishes\" "
        "or \"set the AC to 72\".\nCommands: /chores (yours), /done <chore>, /approvals (parents), "
        "/unlink.")


class TelegramError(RuntimeError):
    pass


# ------------------------------------------------------------------ token
def token_path():
    return os.path.join(config_dir(), TOKEN_FILE)


def load_token(path=None):
    tok = getenv("TELEGRAM_TOKEN")
    if tok:
        return tok.strip()
    path = path or token_path()
    try:
        st = os.stat(path)
    except OSError:
        raise TelegramError("no bot token: set CHOREPILOT_TELEGRAM_TOKEN or run "
                            "`chorepilot telegram set-token`") from None
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise TelegramError(f"{path} is readable by other users; run `chmod 600 {path}`")
    with open(path) as f:
        return f.read().strip()


def save_token(tok, path=None):
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{30,}", tok.strip()):
        raise TelegramError("that does not look like a Bot API token (<digits>:<secret>)")
    path = path or token_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok.strip() + "\n")
    os.chmod(path, 0o600)
    return path


# ------------------------------------------------------------------ Bot API
class TelegramAPI:
    """POST https://api.telegram.org/bot<token>/<method> with a JSON body. Errors never carry
    the URL (it contains the token)."""

    def __init__(self, token, base=None, timeout=15):
        self.token = token
        self.base = (base or getenv("TELEGRAM_API") or "https://api.telegram.org").rstrip("/")
        self.timeout = timeout

    def call(self, method, **params):
        body = json.dumps({k: v for k, v in params.items() if v is not None}).encode()
        req = urllib.request.Request(f"{self.base}/bot{self.token}/{method}", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        wait = self.timeout + int(params.get("timeout") or 0)
        try:
            with urllib.request.urlopen(req, timeout=wait) as r:
                js = json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                js = json.loads(e.read() or b"{}")
            except ValueError:
                js = {"ok": False, "description": f"HTTP {e.code}"}
        except (OSError, ValueError) as e:
            raise TelegramError(f"{method}: {type(e).__name__}") from None
        if not js.get("ok"):
            raise TelegramError(f"{method}: {js.get('description') or 'failed'}")
        return js.get("result")


# ------------------------------------------------------------------ the bot
class _Wait:
    def __init__(self, kind, chat, user, **kw):
        self.kind, self.chat, self.user = kind, chat, user
        self.event, self.value = threading.Event(), None
        self.__dict__.update(kw)


def _tid(p):
    """A person's linked Telegram user id (an int, as the Bot API reports it), or None."""
    v = json.loads(p.get("handles") or "{}").get("telegram_id")
    try:
        return int(v) if v not in (None, "") else None
    except ValueError:
        return None


def _buttons(prefix, yes="Yes", no="No"):
    return {"inline_keyboard": [[{"text": yes, "callback_data": f"{prefix}:y"},
                                 {"text": no, "callback_data": f"{prefix}:n"}]]}


class Bot:
    def __init__(self, api, make_house, db, llm_url=None, model=None, chat_fn=None, log=None,
                 wait_s=120, household_chat=None, remind_at=None, state_path=None,
                 threaded=True):
        self.api, self.make_house, self.db = api, make_house, db
        self.llm_url, self.model, self.chat_fn = llm_url, model, chat_fn
        self.log = log or (lambda m: print(m, file=sys.stderr, flush=True))
        self.wait_s, self.household_chat, self.remind_at = wait_s, household_chat, remind_at
        self.threaded = threaded
        self.offset = None
        self.waits = {}            # nonce -> _Wait (buttons)
        self.text_waits = {}       # (chat, user) -> _Wait (typed phrase / code)
        self.busy = set()
        self.failed_links = {}
        self.lock = threading.Lock()
        self.state_path = state_path or os.path.join(cache_dir(), "telegram-state.json")
        self.state = self._load_state()
        self.workers = []
        self.pushed = {}           # MCP confirmation id -> (chat, message id, text): edited when answered

    # -- persistent cursor (outbox, approvals already announced)
    def _load_state(self):
        try:
            with open(self.state_path) as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}
        st.setdefault("outbox", self.db.outbox_last_id())
        st.setdefault("approvals", [a["id"] for a in self.db.approvals()
                                    if time.time() - a["created"] > 86400])
        st.setdefault("reminded_on", None)
        return st

    def _save_state(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f)
        os.replace(tmp, self.state_path)

    # -- small helpers
    def send(self, chat, text, markup=None):
        try:
            return self.api.call("sendMessage", chat_id=chat, text=text, reply_markup=markup)
        except TelegramError as e:
            self.log(f"  [telegram  ] send failed: {e}")
            return None

    def edit(self, chat, mid, text):
        try:
            self.api.call("editMessageText", chat_id=chat, message_id=mid, text=text)
        except TelegramError:
            pass

    def _household_group(self, chat):
        return self.household_chat not in (None, "") and str(chat) == str(self.household_chat)

    def linked(self, user_id):
        return self.db.person_by_handle("telegram_id", user_id)

    def members(self, roles=None):
        out = []
        for p in self.db.people():
            tid = _tid(p)
            if tid and (roles is None or p["role"] in roles):
                out.append((p, tid))
        return out

    def _spawn(self, fn, *a):
        if not self.threaded:
            return fn(*a)
        t = threading.Thread(target=fn, args=a, daemon=True)
        t.start()
        self.workers.append(t)
        return t

    # -- the loop
    def run(self, stop=None, poll_timeout=25):
        me = self.api.call("getMe")
        self.log(f"  [telegram  ] running as @{me.get('username')} (long polling)")
        while not (stop and stop()):
            self.step(poll_timeout)

    def step(self, poll_timeout=25):
        try:
            ups = self.api.call("getUpdates", offset=self.offset, timeout=poll_timeout,
                                allowed_updates=["message", "callback_query"])
        except TelegramError as e:
            self.log(f"  [telegram  ] {e}; retrying")
            time.sleep(min(5, poll_timeout or 0.1))
            ups = []
        for u in ups or []:
            self.offset = u["update_id"] + 1
            try:
                self.handle(u)
            except Exception as e:  # noqa: BLE001 - one bad update must not stop the bot
                self.log(f"  [telegram  ] update {u.get('update_id')} failed: {e!r}")
        self.tick()

    def handle(self, u):
        if "callback_query" in u:
            return self.on_button(u["callback_query"])
        m = u.get("message")
        if not m or "text" not in m or "from" not in m:
            return None
        chat, user = m["chat"]["id"], m["from"]["id"]
        text = m["text"].strip()
        private = m["chat"].get("type") == "private"
        if any(k in m for k in FORWARD_KEYS):
            # someone else's words: never an answer to a prompt, never a request to act on
            return self.send(chat, "I only act on messages you type yourself, not forwarded ones.")
        cmd0 = text.partition(" ")[0].split("@")[0].lower()
        if not private and not self._household_group(chat) and cmd0 not in ("/start", "/help",
                                                                              "/link"):
            # a group that isn't the household's: chores, devices and approvals stay out of it
            return self.send(chat, "I only talk about the household in a private chat with me, "
                             "or in the household group the owner set (telegram.household_chat).")
        with self.lock:
            w = self.text_waits.pop((chat, user), None)
        if w is not None:
            # an answer to a prompt: never logged, never sent to the model
            if w.kind == "secret":
                try:
                    self.api.call("deleteMessage", chat_id=chat, message_id=m["message_id"])
                except TelegramError:
                    pass
            if w.kind == "dbphrase":
                c = self.db.answer_confirmation(w.cid, True, "telegram", answer=text)
                self.send(chat, "Confirmed." if c and c["state"] == "yes" else "Not confirmed; "
                          "nothing will be done.")
                return None
            w.value = text
            w.event.set()
            return None
        cmd, _, rest = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        if cmd == "/start" and rest.strip():
            # t.me/<bot>?start=<code>: the dashboard's QR code for linking in one tap
            return self.on_link(chat, user, rest.strip(), private)
        if cmd in ("/start", "/help"):
            p = self.linked(user)
            return self.send(chat, HELP if p else "Hi! I'm this household's ChorePilot. Ask the "
                             "owner for a link code (`chorepilot telegram link <your name>`) and "
                             "send it here as /link CODE, in a private chat with me.")
        if cmd == "/link":
            return self.on_link(chat, user, rest.strip(), private)
        p = self.linked(user)
        if not p:
            return self.send(chat, "I don't know you yet. Ask the owner for a link code, then "
                             "send /link CODE in a private chat with me.")
        if cmd == "/unlink":
            self.db.set_handle(p["name"], "telegram_id", None)
            return self.send(chat, f"Unlinked. This Telegram account is no longer {p['name']}.")
        if cmd == "/chores":
            return self.send(chat, self._my_chores(p))
        if cmd == "/approvals":
            return self._approvals_list(chat, p)
        if cmd == "/done":
            return self._spawn(self._tool, chat, user, p, private, "complete_chore",
                               {"chore": rest.strip()})
        if cmd.startswith("/"):
            return self.send(chat, HELP)
        with self.lock:
            if user in self.busy:
                return self.send(chat, "Still working on your last message.")
            self.busy.add(user)
        return self._spawn(self._ask, chat, user, p, private, text)

    # -- linking
    def on_link(self, chat, user, code, private):
        if not private:
            return self.send(chat, "Send /link CODE in a private chat with me, not in a group.")
        if self.linked(user):
            return self.send(chat, f"This Telegram account is already linked to "
                             f"{self.linked(user)['name']}.")
        now = time.time()
        recent = [t for t in self.failed_links.get(user, []) if now - t < 3600]
        if len(recent) >= 5:
            return self.send(chat, "Too many wrong codes; try again in an hour.")
        p = self.db.use_link_code(code) if code else None
        if not p:
            self.failed_links[user] = recent + [now]
            return self.send(chat, "That code is wrong or expired. Ask the owner for a new one.")
        try:
            self.db.set_handle(p["name"], "telegram_id", user)
        except Exception as e:  # noqa: BLE001
            return self.send(chat, f"Could not link: {e}")
        self.log(f"  [telegram  ] linked a Telegram account to {p['name']}")
        return self.send(chat, f"Linked: you are {p['name']} ({p['role']}) in this household.\n"
                         + HELP)

    # -- confirmations for one request (run in the worker thread)
    def _confirmers(self, chat, user, private):
        def confirm(q):
            nonce = secrets.token_hex(6)
            w = _Wait("button", chat, user)
            with self.lock:
                self.waits[nonce] = w
            msg = self.send(chat, q, _buttons(f"c:{nonce}"))
            ok = w.event.wait(self.wait_s) and w.value is True
            with self.lock:
                self.waits.pop(nonce, None)
            if msg:
                self.edit(chat, msg["message_id"], q + ("\n-> confirmed" if ok else
                                                        "\n-> not confirmed"))
            return ok

        def strong(q, phrase):
            w = _Wait("phrase", chat, user)
            with self.lock:
                self.text_waits[(chat, user)] = w
            self.send(chat, f"{q}\nType {phrase} to confirm (anything else cancels).")
            got = w.value if w.event.wait(self.wait_s) else None
            with self.lock:
                self.text_waits.pop((chat, user), None)
            return (got or "").strip() == phrase

        def secret(q):
            if not private:
                self.send(chat, "Security actions need your PIN or code, and I only ask for that "
                          "in a private chat with me. Nothing was done.")
                return None
            w = _Wait("secret", chat, user)
            with self.lock:
                self.text_waits[(chat, user)] = w
            self.send(chat, f"{q}\n(I delete your message as soon as I've read it.)")
            got = w.value if w.event.wait(self.wait_s) else None
            with self.lock:
                self.text_waits.pop((chat, user), None)
            return got
        return confirm, strong, secret

    def _house(self, p, chat, user, private):
        confirm, strong, secret = self._confirmers(chat, user, private)
        return self.make_house(p["name"], confirm, strong, secret)

    def _ask(self, chat, user, p, private, text):
        try:
            if not self.llm_url:
                self.send(chat, "No model is configured for chat (start with --url). Try /chores "
                          "or /done <chore>.")
                return
            from . import agent
            h = self._house(p, chat, user, private)
            try:
                kw = {"chat_fn": self.chat_fn} if self.chat_fn else {}
                answer, _ = agent.run(text, h, self.llm_url, model=self.model,
                                      log=lambda m: None, **kw)
            finally:
                h.db.close()
            self.send(chat, answer or "(no answer)")
        except Exception as e:  # noqa: BLE001
            self.log(f"  [telegram  ] request failed: {e!r}")
            self.send(chat, "Sorry, that failed on my side.")
        finally:
            with self.lock:
                self.busy.discard(user)

    def _tool(self, chat, user, p, private, name, args):
        h = self._house(p, chat, user, private)
        try:
            res = h.call(name, args)
        finally:
            h.db.close()
        if res.get("ok"):
            msg = f"Done: '{res.get('chore')}' by {res.get('done_by')}."
            if res.get("note"):
                msg += f" {res['note']}."
            for f in res.get("opened") or []:
                msg += f"\n'{f['chore']}' is now open for {f['who']}."
        else:
            msg = res.get("error") or "That didn't work."
        self.send(chat, msg)

    def _my_chores(self, p):
        from .chores import when
        self.db.materialise()
        rows = self.db.instances(("open", "pending_approval"), person=p["name"])
        if not rows:
            return "Nothing open for you."
        return "\n".join(f"- {r['title']} (due {when(r['due'])})"
                         + (" (waiting for a parent)" if r["state"] == "pending_approval" else "")
                         for r in rows[:20])

    def _approvals_list(self, chat, p):
        if p["role"] not in ("owner", "adult"):
            return self.send(chat, "Only a parent can see the approval queue.")
        rows = self.db.approvals()
        if not rows:
            return self.send(chat, "No pending approvals.")
        for a in rows:
            self.send(chat, f"#{a['id']}: {a['summary']}", _buttons(f"a:{a['id']}", "Approve", "Deny"))
        return None

    # -- buttons
    def on_button(self, q):
        user = q["from"]["id"]
        data = q.get("data") or ""
        msg = q.get("message") or {}
        chat = (msg.get("chat") or {}).get("id")

        def ack(text=None):
            try:
                self.api.call("answerCallbackQuery", callback_query_id=q["id"], text=text)
            except TelegramError:
                pass
        parts = data.split(":")
        if len(parts) != 3 or parts[2] not in ("y", "n"):
            return ack()
        kind, ref, yes = parts[0], parts[1], parts[2] == "y"
        if kind == "c":
            with self.lock:
                w = self.waits.get(ref)
            if not w:
                return ack("That question has expired.")
            if w.user != user:
                return ack("Only the person who asked can answer this.")
            w.value = yes
            w.event.set()
            return ack("Confirmed" if yes else "Cancelled")
        p = self.linked(user)
        if not p:
            return ack("This Telegram account isn't linked.")
        if kind == "m":
            c = self.db.confirmation(ref)
            if not c or c["state"] != "pending":
                ack("That request is no longer waiting.")
                return None
            if c["person"] is None or c["person"] != p["name"]:
                return ack("Only the person it is for can answer this.")
            if c["kind"] != "yes":
                return ack(f"Type {c['phrase']} instead.")
            self.db.answer_confirmation(c["id"], yes, "telegram")
            self.pushed.pop(c["id"], None)
            if chat and msg.get("message_id"):
                self.edit(chat, msg["message_id"], f"{c['question']}\n-> "
                          + ("confirmed" if yes else "declined"))
            return ack("Confirmed" if yes else "Declined")
        if kind == "a":
            if p["role"] not in ("owner", "adult"):
                return ack("Only a parent can decide this.")
            ack("OK")
            private = (msg.get("chat") or {}).get("type") == "private"
            return self._spawn(self._decide, chat, user, p, private, int(ref), yes,
                               msg.get("message_id"))
        return ack()

    def _decide(self, chat, user, p, private, aid, yes, mid):
        # a worker thread: only the House's own SQLite connection is used here
        h = self._house(p, chat, user, private)
        try:
            try:
                res = h.approve(aid, approve=yes)
            except Exception as e:  # noqa: BLE001
                res = {"ok": False, "error": str(e)}
            a = h.db.approval(aid)
            asker = h.db.person(a["person"], required=False) if a and a.get("person") else None
        finally:
            h.db.close()
        line = f"#{aid}: {a['summary'] if a else ''}\n-> " + (
            ("approved by " if yes else "denied by ") + p["name"] if res.get("ok")
            else res.get("error", "failed"))
        if chat and mid:
            self.edit(chat, mid, line)
        tid = _tid(asker) if asker else None
        if tid and res.get("ok") and asker["name"] != p["name"]:
            self.send(tid, f"{p['name']} {'approved' if yes else 'said no to'}: {a['summary']}.")

    # -- pushes: MCP confirmations, approvals, outbox, daily reminders
    def tick(self):
        for c in self.db.pending_confirmations():
            if "telegram" in c["pushed"].split(","):
                continue
            self.db.mark_pushed(c["id"], "telegram")
            p = self.db.person(c["person"], required=False) if c["person"] else None
            tid = _tid(p) if p else None
            if not tid:
                continue
            who = c["client"] or c["source"] or "an agent"
            if c["kind"] == "phrase":
                w = _Wait("dbphrase", int(tid), int(tid), cid=c["id"])
                with self.lock:
                    self.text_waits[(int(tid), int(tid))] = w
                self.send(tid, f"{who} asks: {c['question']}\nType {c['phrase']} here to confirm "
                          "(anything else cancels).")
            else:
                m = self.send(tid, f"{who} asks: {c['question']}", _buttons(f"m:{c['id']}"))
                if m:
                    self.pushed[c["id"]] = (tid, m["message_id"], f"{who} asks: {c['question']}")
        for cid, (chat, mid, text) in list(self.pushed.items()):
            c = self.db.confirmation(cid)
            if not c or c["state"] != "pending":
                self.pushed.pop(cid)
                self.edit(chat, mid, f"{text}\n-> {c['state'] if c else 'gone'}"
                          + (f" (via {c['answered_via']})" if c and c["answered_via"] else ""))
        for a in self.db.approvals():
            if a["id"] in self.state["approvals"]:
                continue
            self.state["approvals"].append(a["id"])
            for _, tid in self.members(("owner", "adult")):
                self.send(tid, f"Approval needed #{a['id']}: {a['summary']}",
                          _buttons(f"a:{a['id']}", "Approve", "Deny"))
        for row in self.db.outbox_since(self.state["outbox"]):
            self.state["outbox"] = row["id"]
            self._deliver(row)
        if self.remind_at:
            today = time.strftime("%Y-%m-%d")
            if time.strftime("%H:%M") >= self.remind_at and self.state["reminded_on"] != today:
                self.state["reminded_on"] = today
                self.db.materialise()
                for name, msg, quiet in self.db.reminders():
                    if not quiet:
                        self.db.outbox_add(msg, name)
        self.state["approvals"] = self.state["approvals"][-500:]
        self._save_state()

    def _deliver(self, row):
        aud, text = row["audience"], row["text"]
        if re.match(r"Approval needed #\d+", text):
            return                      # sent with buttons from the approval queue instead
        if aud == "household":
            if self.household_chat:
                self.send(self.household_chat, text)
            else:
                for _, tid in self.members():
                    self.send(tid, text)
        elif aud == "parents":
            for _, tid in self.members(("owner", "adult")):
                self.send(tid, text)
        else:
            for p, tid in self.members():
                if p["name"].lower() == str(aud).lower():
                    self.send(tid, text)


# ------------------------------------------------------------------ wiring + CLI
def house_factory(cfg=None, backend=None, log=None):
    from .chores import ChoreDB
    from .control import Notifier
    from .house import House
    from .housecfg import load_house
    base = cfg if cfg is not None else load_house()
    log = log or (lambda m: None)

    def make(person, confirm, strong, secret):
        return House(json.loads(json.dumps(base)), db=ChoreDB(), person=person, source="telegram",
                     confirm=confirm, strong_confirm=strong, ask_secret=secret,
                     notifier=Notifier(), log=log, backend=backend, client="telegram")
    return make


def cmd_telegram(args):
    from .chores import ChoreDB, ChoreError
    act = args.action
    if act == "set-token":
        import getpass
        tok = args.token or (getpass.getpass("Bot API token from @BotFather: ")
                             if sys.stdin.isatty() else sys.stdin.readline())
        print(f"saved to {save_token(tok)} (mode 0600)")
        return
    db = ChoreDB()
    if act == "link":
        if not args.name:
            raise SystemExit("usage: chorepilot telegram link <person>")
        try:
            code = db.new_link_code(args.name)
        except ChoreError as e:
            raise SystemExit(f"chorepilot: {e}")
        print(f"one-time code for {db.person(args.name)['name']} (10 minutes, single use):\n\n"
              f"    /link {code}\n\nsend it to the bot in a private chat.")
        return
    if act == "unlink":
        db.set_handle(args.name, "telegram_id", None)
        print(f"unlinked {args.name}")
        return
    if act == "status":
        for p in db.people():
            tid = json.loads(p["handles"] or "{}").get("telegram_id")
            print(f"{p['name']:<12} {p['role']:<6} {'linked' if tid else '-'}")
        return
    from .housecfg import load_house
    cfg = load_house()
    tcfg = cfg.get("telegram") or {}
    try:
        api = TelegramAPI(load_token())
    except TelegramError as e:
        raise SystemExit(f"chorepilot: {e}")
    log = lambda m: print(m, file=sys.stderr, flush=True)  # noqa: E731
    llm = cfg.get("llm") or {}
    bot = Bot(api, house_factory(cfg, args.backend), db, llm_url=args.url or llm.get("url"),
              model=args.llm or llm.get("model"),
              log=log, wait_s=args.confirm_timeout, household_chat=tcfg.get("household_chat"),
              remind_at=tcfg.get("remind_at"))
    try:
        bot.run()
    except KeyboardInterrupt:
        pass


def add_parser(sub, as_arg):
    sp = sub.add_parser("telegram", help="the household Telegram bot: set-token, link <person>, "
                        "unlink, status, run")
    sp.add_argument("action", nargs="?", default="run",
                    choices=["run", "set-token", "link", "unlink", "status"])
    sp.add_argument("name", nargs="?")
    sp.add_argument("--token", help="set-token: non-interactive (prefer the prompt)")
    sp.add_argument("--url", default=os.environ.get("CHOREPILOT_LLM_URL") or
                    os.environ.get("DISHSOAKER_LLM_URL"), help="the model server for chat")
    sp.add_argument("--llm", help="model name to send (multi-model servers)")
    sp.add_argument("--confirm-timeout", type=float, default=120)
    sp.add_argument("--backend", choices=["simulated", "ha", "matter", "mqtt", "homeconnect"])
    sp.set_defaults(func=cmd_telegram)
