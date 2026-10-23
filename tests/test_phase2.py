"""ChorePilot phase 2: the MCP server (stdio + streamable HTTP) through the policy engine, the
Telegram bot against a fake Bot API, and push events (HA websocket, Home Connect SSE, MQTT)
with fakes. Nothing touches a real device, bot or network beyond 127.0.0.1.
"""
import io
import json
import os
import socket
import stat
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_chorepilot import Quiet, Tmp, demo_db  # noqa: E402

from chorepilot import mcp, push  # noqa: E402
from chorepilot import telegram as tg  # noqa: E402
from chorepilot.chores import ChoreDB  # noqa: E402
from chorepilot.confirm import Broker, terminal_answer  # noqa: E402
from chorepilot.control import RateLimiter  # noqa: E402
from chorepilot.house import Snapshot  # noqa: E402
from chorepilot.housecfg import PRESETS  # noqa: E402


def demo_cfg(**policy):
    cfg = json.loads(json.dumps(PRESETS["demo"]))
    if policy:
        cfg["policy"] = policy
    return cfg


class MCPBase(Tmp):
    def setUp(self):
        super().setUp()
        os.environ["CHOREPILOT_SIM_SPEED"] = "60000"
        self.dbp = os.path.join(self.tmp, "chores.db")
        db = demo_db(self.dbp)
        db.set_pin("Alex", "2468")
        db.close()
        self.audit = os.path.join(self.tmp, "audit.log")
        self.answer = None          # what "the human" does while a confirmation waits

    def server(self, cfg=None, timeout=0.6, answer=None):
        def announce(c):
            if self.answer:
                db = ChoreDB(self.dbp)
                self.answer(db, c)
                db.close()
        make = mcp.house_factory(cfg or demo_cfg(), timeout=timeout, log=lambda m: None,
                                 notifier=Quiet(), watch=False, db_path=self.dbp,
                                 announce=announce, limiter=RateLimiter(min_start_gap_s=0),
                                 snapshot=Snapshot(os.path.join(self.tmp, "snap.json")),
                                 audit_path=self.audit, defer=lambda a, op: None)
        return mcp.MCPServer(make, log=lambda m: None)

    def call(self, srv, name, args=None, person="Alex"):
        s = {"person": person, "label": "test-agent", "client_info": "pytest 1"}
        r = srv.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                        "params": {"name": name, "arguments": args or {}}}, s)
        return r["result"]["structuredContent"], r["result"]


class TestMCPPolicy(MCPBase):
    def test_initialize_and_tool_listing(self):
        srv = self.server()
        s = {"person": "Alex"}
        r = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18",
                                   "clientInfo": {"name": "grok-bot", "version": "2"}}}, s)
        self.assertEqual(r["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", r["result"]["capabilities"])
        self.assertIsNone(srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, s))
        tools = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, s)["result"]["tools"]
        names = {t["name"] for t in tools}
        self.assertTrue({"get_status", "start_program", "door_lock", "who_has", "whoami"} <= names)
        gs = next(t for t in tools if t["name"] == "get_status")
        self.assertTrue(gs["annotations"]["readOnlyHint"])
        self.assertEqual(gs["inputSchema"]["type"], "object")
        r = srv.handle({"jsonrpc": "2.0", "id": 3, "method": "nope"}, s)
        self.assertEqual(r["error"]["code"], -32601)
        r = srv.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                        "params": {"name": "rm_rf", "arguments": {}}}, s)
        self.assertEqual(r["error"]["code"], -32602)

    def test_tier0_and_tier1_run_directly(self):
        srv = self.server()
        out, raw = self.call(srv, "get_status", {"appliance": "dishwasher"})
        self.assertTrue(out["ok"], out)
        self.assertFalse(raw["isError"])
        out, _ = self.call(srv, "set_light", {"action": "turn_on"})
        self.assertTrue(out["ok"], out)
        self.assertEqual((out["policy"], out["tier"]), ("allow", 1))
        out, _ = self.call(srv, "who_has", {"chore": "dishes"})
        self.assertEqual(out["this_week"], "Jordan")

    def test_agents_max_tier_config(self):
        srv = self.server(demo_cfg(agents_max_tier=0))
        out, _ = self.call(srv, "set_light", {"action": "turn_on"})
        self.assertFalse(out["ok"])
        self.assertIn("external agent", out["error"])

    def test_tier3_unlock_always_denied_even_if_config_says_3(self):
        srv = self.server(demo_cfg(agents_max_tier=3))
        self.answer = lambda db, c: db.answer_confirmation(c["id"], True, "telegram")
        for args in ({"action": "unlock"}, {"action": "unlock", "code": "2468"}):
            out, raw = self.call(srv, "door_lock", args)
            self.assertFalse(out["ok"])
            self.assertTrue(raw["isError"])
        out, _ = self.call(srv, "door_lock", {"action": "unlock"})
        self.assertEqual(out["policy"], "deny")
        self.assertIn("never", out["error"])
        db = ChoreDB(self.dbp)
        self.assertEqual(db.pending_confirmations(), [])
        self.assertEqual(db.db.execute("SELECT COUNT(*) FROM confirmations").fetchone()[0], 0)

    def test_tier2_waits_for_the_humans_confirmation(self):
        srv = self.server()
        # 1. nobody answers: timeout = no, nothing runs, even if the agent says "confirmed"
        out, _ = self.call(srv, "start_program", {"appliance": "dishwasher", "program": "auto",
                                                  "confirm": True, "yes": "y"})
        self.assertFalse(out["ok"], out)
        self.assertEqual(out["confirmation"]["state"], "expired")
        self.assertIn("did not confirm", out["error"])
        st, _ = self.call(srv, "get_status", {"appliance": "dishwasher"})
        self.assertNotEqual(st["state"], "running")
        # 2. the person says no on Telegram
        self.answer = lambda db, c: db.answer_confirmation(c["id"], False, "telegram")
        out, _ = self.call(srv, "start_program", {"appliance": "dishwasher", "program": "auto"})
        self.assertFalse(out["ok"])
        self.assertEqual(out["confirmation"]["state"], "no")
        # 3. the person says yes: it runs, and the start is credited to them
        self.answer = lambda db, c: db.answer_confirmation(c["id"], True, "telegram")
        out, _ = self.call(srv, "start_program", {"appliance": "dishwasher", "program": "auto"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["confirmation"], {"id": 3, "state": "yes", "via": "telegram"})
        db = ChoreDB(self.dbp)
        self.assertEqual([e["actor"] for e in db.events() if e["event"] == "started"], ["Alex"])
        c = db.confirmation(3)
        self.assertEqual((c["person"], c["client"], c["source"]), ("Alex", "test-agent / pytest 1", "mcp"))

    def test_strong_confirmation_needs_the_exact_phrase(self):
        srv = self.server()
        self.answer = lambda db, c: db.answer_confirmation(c["id"], True, "telegram", answer="yes")
        out, _ = self.call(srv, "oven", {"action": "preheat", "temperature": 400})
        self.assertFalse(out["ok"], out)
        self.answer = lambda db, c: db.answer_confirmation(c["id"], True, "telegram", answer="PREHEAT")
        out, _ = self.call(srv, "oven", {"action": "preheat", "temperature": 400})
        self.assertTrue(out["ok"], out)

    def test_kid_token_goes_to_the_approval_queue(self):
        srv = self.server()
        out, _ = self.call(srv, "water_zone", {"action": "water", "minutes": 5}, person="Jordan")
        self.assertFalse(out["ok"])
        self.assertTrue(out["queued_for_approval"])
        self.assertEqual(ChoreDB(self.dbp).pending_confirmations(), [])

    def test_no_identity_is_a_guest_not_the_owner(self):
        srv = self.server()
        out, _ = self.call(srv, "set_light", {"action": "turn_on"}, person=None)
        self.assertFalse(out["ok"])
        out, _ = self.call(srv, "whoami", person=None)
        self.assertEqual(out["role"], "guest")

    def test_audit_has_the_client(self):
        srv = self.server()
        self.call(srv, "set_light", {"action": "turn_on"})
        with open(self.audit) as f:
            rec = json.loads(f.read().splitlines()[-1])
        self.assertEqual((rec["source"], rec["client"], rec["who"]), ("mcp", "test-agent / pytest 1", "Alex"))

    def test_codes_never_reach_the_audit_log(self):
        srv = self.server()
        self.call(srv, "door_lock", {"action": "unlock", "code": "2468"})
        with open(self.audit) as f:
            text = f.read()
        self.assertNotIn("2468", text)
        self.assertIn('"code": "***"', text)

    def test_terminal_answer_needs_a_tty(self):
        db = ChoreDB(self.dbp)
        c = db.add_confirmation("Alex", "Start?", source="mcp", client="x")
        with self.assertRaises(ValueError):
            terminal_answer(db, c["id"], isatty=lambda: False)
        self.assertEqual(terminal_answer(db, c["id"], ask=lambda q: "y", isatty=lambda: True, say=lambda m: None)["state"], "yes")
        c = db.add_confirmation("Alex", "Preheat?", kind="phrase", phrase="PREHEAT")
        self.assertEqual(terminal_answer(db, c["id"], ask=lambda q: "y", isatty=lambda: True, say=lambda m: None)["state"], "no")
        c = db.add_confirmation("Alex", "Water?")
        self.assertEqual(terminal_answer(db, c["id"], deny=True)["state"], "no")
        with self.assertRaises(ValueError):
            terminal_answer(db, c["id"], deny=True)

    def test_broker_expiry_and_answer(self):
        db = ChoreDB(":memory:")
        db.add_person("Alex", "owner")
        b = Broker(db, "Alex", timeout=5, poll=0.01, log=lambda m: None,
                   sleep=lambda s: db.answer_confirmation(1, True, "telegram"))
        self.assertTrue(b.confirm("Start?"))
        b = Broker(db, "Alex", timeout=0.05, poll=0.01, log=lambda m: None)
        self.assertFalse(b.confirm("Start?"))
        self.assertEqual(b.last["state"], "expired")
        self.assertFalse(Broker(db, None, log=lambda m: None).confirm("x"))

    def test_stdio_transport(self):
        srv = self.server()
        lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2099-01-01", "clientInfo": {"name": "c"}}},
                 {"jsonrpc": "2.0", "method": "notifications/initialized"},
                 {"jsonrpc": "2.0", "id": 2, "method": "ping"},
                 {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                  "params": {"name": "door_lock", "arguments": {"action": "unlock"}}}]
        out = io.StringIO()
        mcp.serve_stdio(srv, "Alex", "claude-desktop",
                        stdin=io.StringIO("\n".join(json.dumps(x) for x in lines) + "\nnot json\n"),
                        stdout=out)
        got = {m.get("id"): m for m in map(json.loads, out.getvalue().splitlines())}
        self.assertEqual(got[1]["result"]["protocolVersion"], mcp.PROTOCOLS[0])
        self.assertEqual(got[2]["result"], {})
        self.assertTrue(got[3]["result"]["isError"])
        self.assertEqual(got[None]["error"]["code"], -32700)


class TestMCPHTTP(MCPBase):
    def setUp(self):
        super().setUp()
        self.tokens = mcp.Tokens(os.path.join(self.tmp, "cfg", "mcp-tokens.json"))
        self.tok = self.tokens.add("Alex", "grok-bot")
        self.kid = self.tokens.add("Jordan", "sam-agent")
        self.httpd = mcp.make_http_server(self.server(), "127.0.0.1", 0, self.tokens,
                                          log=lambda m: None)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/mcp"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def post(self, body, token=None, sid=None, origin=None, method="POST", url=None):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if token:
            h["Authorization"] = f"Bearer {token}"
        if sid:
            h["Mcp-Session-Id"] = sid
        if origin:
            h["Origin"] = origin
        req = urllib.request.Request(url or self.url, data=json.dumps(body).encode() if body is not None
                                     else None, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None), r.headers
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, (json.loads(raw) if raw else None), e.headers

    def init(self, token):
        code, js, h = self.post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                 "params": {"protocolVersion": "2025-06-18",
                                            "clientInfo": {"name": "grok", "version": "1"}}}, token)
        self.assertEqual(code, 200, js)
        return h["Mcp-Session-Id"]

    def test_token_file_is_private_and_hashed(self):
        mode = stat.S_IMODE(os.stat(self.tokens.path).st_mode)
        self.assertEqual(mode, 0o600)
        with open(self.tokens.path) as f:
            self.assertNotIn(self.tok, f.read())

    def test_auth_required(self):
        body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        self.assertEqual(self.post(body)[0], 401)
        code, _, h = self.post(body, token="cp_wrong")
        self.assertEqual(code, 401)
        self.assertIn("Bearer", h["WWW-Authenticate"])
        self.assertEqual(self.post(body, token=self.tok, origin="https://evil.example")[0], 403)
        self.assertEqual(self.post(body, token=self.tok, origin="http://localhost:3000")[0], 200)
        self.assertEqual(self.post(body, token=self.tok, url=self.url.replace("/mcp", "/x"))[0], 404)

    def test_sessions(self):
        sid = self.init(self.tok)
        tl = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        self.assertEqual(self.post(tl, self.tok)[0], 400)                    # no session
        self.assertEqual(self.post(tl, self.tok, sid="bogus")[0], 404)
        self.assertEqual(self.post(tl, self.kid, sid=sid)[0], 404)           # not your session
        code, js, _ = self.post(tl, self.tok, sid=sid)
        self.assertEqual(code, 200)
        self.assertIn("whoami", [t["name"] for t in js["result"]["tools"]])
        code, js, _ = self.post({"jsonrpc": "2.0", "method": "notifications/initialized"}, self.tok, sid)
        self.assertEqual((code, js), (202, None))
        self.assertEqual(self.post(None, self.tok, sid, method="GET")[0], 405)
        self.assertEqual(self.post(None, self.tok, sid, method="DELETE")[0], 200)
        self.assertEqual(self.post(tl, self.tok, sid=sid)[0], 404)

    def test_calls_act_as_the_tokens_person(self):
        sid = self.init(self.tok)
        call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "whoami", "arguments": {}}}
        _, js, _ = self.post(call, self.tok, sid)
        who = js["result"]["structuredContent"]
        self.assertEqual((who["person"], who["role"], who["agent_max_tier"]), ("Alex", "owner", 2))
        self.assertIn("grok-bot", who["client"])
        call["params"] = {"name": "door_lock", "arguments": {"action": "unlock"}}
        _, js, _ = self.post(call, self.tok, sid)
        self.assertEqual(js["result"]["structuredContent"]["policy"], "deny")
        ksid = self.init(self.kid)
        call["params"] = {"name": "whoami", "arguments": {}}
        _, js, _ = self.post(call, self.kid, ksid)
        self.assertEqual(js["result"]["structuredContent"]["role"], "kid")

    def test_revoke(self):
        self.assertEqual(self.tokens.revoke("grok-bot"), 1)
        self.assertEqual(self.post({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, self.tok)[0], 401)


# ====================================================================== Telegram
class FakeBotAPI:
    """Records every Bot API call; getUpdates hands out queued updates."""

    def __init__(self):
        self.calls, self.updates, self.mid, self.lock = [], [], 100, threading.Lock()

    def call(self, method, **p):
        with self.lock:
            self.calls.append((method, p))
            if method == "getUpdates":
                ups, self.updates = self.updates, []
                return ups
            if method == "getMe":
                return {"id": 1, "username": "chorepilot_test_bot"}
            if method == "sendMessage":
                self.mid += 1
                return {"message_id": self.mid, "chat": {"id": p["chat_id"]}, "text": p["text"]}
            return True

    def sent(self, chat=None):
        with self.lock:
            return [p for m, p in self.calls if m == "sendMessage"
                    and (chat is None or p["chat_id"] == chat)]

    def wait_for(self, pred, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            hits = [p for p in self.sent() if pred(p)]
            if hits:
                return hits[-1]
            time.sleep(0.01)
        raise AssertionError(f"no matching message; sent: {[p['text'] for p in self.sent()]}")


_uid = [1000]


def msg(user, text, chat=None, private=True):
    _uid[0] += 1
    return {"update_id": _uid[0], "message": {
        "message_id": _uid[0], "from": {"id": user}, "text": text,
        "chat": {"id": chat or user, "type": "private" if private else "group"}}}


def press(user, data, chat=None, mid=1):
    _uid[0] += 1
    return {"update_id": _uid[0], "callback_query": {
        "id": str(_uid[0]), "from": {"id": user}, "data": data,
        "message": {"message_id": mid, "chat": {"id": chat or user, "type": "private"}}}}


ALEX, SAM, JORDAN, STRANGER = 11, 12, 13, 99


class TestTelegram(MCPBase):
    def setUp(self):
        super().setUp()
        self.api = FakeBotAPI()
        self.db = ChoreDB(self.dbp)
        self.model_saw = []
        self.script = []
        self.addCleanup(self.db.close)

    def bot(self, threaded=True, llm=True):
        dbp, tmp = self.dbp, self.tmp

        def make(person, confirm, strong, secret):
            return _tg_house(tmp, dbp, person, confirm, strong, secret)

        def fake_chat(url, messages, tools=None, model=None):
            self.model_saw.append(json.dumps(messages))
            return {"choices": [{"message": self.script.pop(0)}]}
        return tg.Bot(self.api, make, self.db, llm_url="http://llm" if llm else None,
                      chat_fn=fake_chat, log=lambda m: None, wait_s=3, threaded=threaded,
                      state_path=os.path.join(self.tmp, "tg-state.json"))

    def link(self, bot, name, uid):
        code = self.db.new_link_code(name)
        bot.handle(msg(uid, f"/link {code}"))
        return code

    def test_unlinked_users_can_only_link(self):
        b = self.bot(threaded=False)
        b.handle(msg(STRANGER, "unlock the front door"))
        self.assertIn("don't know you", self.api.sent()[-1]["text"])
        self.assertEqual(self.model_saw, [])
        b.handle(msg(STRANGER, "/link WRONGCODE"))
        self.assertIn("wrong or expired", self.api.sent()[-1]["text"])
        code = self.db.new_link_code("Sam")
        b.handle(msg(SAM, f"/link {code}", chat=-500, private=False))     # never in a group
        self.assertIn("private chat", self.api.sent()[-1]["text"])
        b.handle(msg(SAM, f"/link {code.lower()}"))
        self.assertIn("you are Sam (adult)", self.api.sent()[-1]["text"])
        self.assertEqual(self.db.person_by_handle("telegram_id", SAM)["name"], "Sam")
        b.handle(msg(STRANGER, f"/link {code}"))                           # single use
        self.assertIn("wrong or expired", self.api.sent()[-1]["text"])
        for _ in range(5):
            b.handle(msg(STRANGER + 1, "/link NOPE1234"))
        b.handle(msg(STRANGER + 1, f"/link {self.db.new_link_code('Riley')}"))
        self.assertIn("Too many", self.api.sent()[-1]["text"])
        b.handle(msg(SAM, "/unlink"))
        self.assertIsNone(self.db.person_by_handle("telegram_id", SAM))

    def test_chat_goes_to_the_agent_with_buttons_for_tier2(self):
        b = self.bot()
        self.link(b, "Alex", ALEX)
        self.script = [{"tool_calls": [{"id": "1", "function": {
            "name": "water_zone", "arguments": json.dumps({"action": "water", "minutes": 10})}}]},
            {"content": "The lawn is being watered."}]
        b.handle(msg(ALEX, "water the lawn for 10 minutes"))
        q = self.api.wait_for(lambda p: p.get("reply_markup"))
        data = q["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        self.assertTrue(data.startswith("c:") and data.endswith(":y"))
        b.handle(press(STRANGER, data, chat=ALEX))                           # not theirs
        self.assertIn(("answerCallbackQuery"), [m for m, _ in self.api.calls])
        b.handle(press(ALEX, data))
        self.api.wait_for(lambda p: p["text"] == "The lawn is being watered.")
        for t in b.workers:
            t.join(5)
        self.assertIn("Talking to: Alex (owner)", self.model_saw[0])
        db = ChoreDB(self.dbp)
        self.assertEqual(db.events()[0]["actor"], "Alex")

    def test_pin_only_in_private_chat_deleted_and_never_shown_to_the_model(self):
        b = self.bot()
        b.household_chat = -42                        # the household's own group
        self.link(b, "Alex", ALEX)
        unlock = {"tool_calls": [{"id": "1", "function": {"name": "door_lock",
                                                          "arguments": json.dumps({"action": "unlock"})}}]}
        # in a group: refused, nothing asked
        self.script = [dict(unlock), {"content": "I couldn't unlock it."}]
        b.handle(msg(ALEX, "unlock the front door", chat=-42, private=False))
        self.api.wait_for(lambda p: "only ask for that in a private chat" in p["text"])
        self.api.wait_for(lambda p: p["text"] == "I couldn't unlock it.")
        for t in b.workers:
            t.join(5)
        # in private: asked, the code message is deleted, the model never sees it
        self.script = [dict(unlock), {"content": "Unlocked."}]
        b.handle(msg(ALEX, "unlock the front door"))
        self.api.wait_for(lambda p: "PIN or TOTP" in p["text"])
        pin = msg(ALEX, "2468")
        b.handle(pin)
        self.api.wait_for(lambda p: p["text"] == "Unlocked.")
        for t in b.workers:
            t.join(5)
        self.assertIn(("deleteMessage", {"chat_id": ALEX, "message_id": pin["message"]["message_id"]}),
                      self.api.calls)
        self.assertFalse(any("2468" in s for s in self.model_saw))
        self.assertFalse(any("2468" in p["text"] for p in self.api.sent()))
        with open(os.path.join(self.tmp, "audit.log")) as f:
            self.assertNotIn("2468", f.read())

    def test_mcp_confirmation_is_answered_with_a_button(self):
        b = self.bot(threaded=False)
        self.link(b, "Alex", ALEX)
        self.link(b, "Sam", SAM)
        c = self.db.add_confirmation("Alex", "Start auto on the dishwasher?", source="mcp",
                                     client="grok-bot")
        b.tick()
        q = self.api.sent(ALEX)[-1]
        self.assertIn("grok-bot asks", q["text"])
        b.tick()                                                         # pushed only once
        self.assertEqual(len([p for p in self.api.sent(ALEX) if "grok-bot" in p["text"]]), 1)
        b.handle(press(SAM, f"m:{c['id']}:y"))                           # not Sam's to answer
        self.assertEqual(self.db.confirmation(c["id"])["state"], "pending")
        b.handle(press(ALEX, f"m:{c['id']}:y"))
        self.assertEqual(self.db.confirmation(c["id"])["state"], "yes")
        late = self.db.add_confirmation("Alex", "Water the lawn?", ttl=0.01)
        b.tick()
        time.sleep(0.02)
        b.tick()                                  # expired: the button message says so
        edits = [p for m, p in self.api.calls if m == "editMessageText"]
        self.assertTrue(edits[-1]["text"].endswith("-> expired"), edits)
        self.assertEqual(self.db.confirmation(late["id"])["state"], "expired")
        self.assertEqual(self.db.confirmation(c["id"])["answered_via"], "telegram")
        c = self.db.add_confirmation("Alex", "Preheat the oven?", kind="phrase", phrase="PREHEAT")
        b.tick()
        self.assertIn("Type PREHEAT", self.api.sent(ALEX)[-1]["text"])
        b.handle(msg(ALEX, "PREHEAT"))
        self.assertEqual(self.db.confirmation(c["id"])["state"], "yes")

    def test_approvals_to_parents_and_announcements(self):
        b = self.bot()
        for n, u in (("Alex", ALEX), ("Sam", SAM), ("Jordan", JORDAN)):
            self.link(b, n, u)
        kid = _tg_house(self.tmp, self.dbp, "Jordan", None, None, None)
        r = kid.call("water_zone", {"action": "water", "minutes": 5})
        aid = r["queued_for_approval"]
        b.tick()
        for parent in (ALEX, SAM):
            m = [p for p in self.api.sent(parent) if p.get("reply_markup")][-1]
            self.assertIn(f"Approval needed #{aid}", m["text"])
        self.assertFalse([p for p in self.api.sent(JORDAN) if "Approval needed" in p["text"]])
        b.handle(press(JORDAN, f"a:{aid}:y"))                            # a kid can't approve
        self.assertEqual(self.db.approval(aid)["state"], "pending")
        b.handle(press(SAM, f"a:{aid}:y"))          # the approval is the parent's yes
        self.api.wait_for(lambda p: p["chat_id"] == JORDAN and "Sam approved" in p["text"])
        for t in b.workers:
            t.join(5)
        self.assertEqual(ChoreDB(self.dbp).approval(aid)["state"], "approved")
        # a device-verified completion is announced to the household
        for ln in self.db.apply_event("dishwasher", "finished", "dishwasher"):
            self.db.outbox_add(ln)
        b.tick()
        self.assertTrue(any("verified by the dishwasher" in p["text"] for p in self.api.sent(JORDAN)))

    def test_no_model_configured_and_commands(self):
        b = self.bot(threaded=False, llm=False)
        self.link(b, "Jordan", JORDAN)
        b.handle(msg(JORDAN, "hello"))
        self.assertIn("No model", self.api.sent()[-1]["text"])
        b.handle(msg(JORDAN, "/approvals"))
        self.assertIn("Only a parent", self.api.sent()[-1]["text"])
        b.handle(msg(JORDAN, "/chores"))
        self.assertTrue(self.api.sent()[-1]["text"])

    def test_token_file_must_be_private(self):
        os.environ.pop("CHOREPILOT_TELEGRAM_TOKEN", None)
        p = os.path.join(self.tmp, "tok")
        tg.save_token("123456:" + "A" * 35, p)
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        self.assertEqual(tg.load_token(p), "123456:" + "A" * 35)
        os.chmod(p, 0o644)
        with self.assertRaises(tg.TelegramError):
            tg.load_token(p)
        with self.assertRaises(tg.TelegramError):
            tg.save_token("not a token", p)

    def test_api_errors_never_contain_the_token(self):
        api = tg.TelegramAPI("123:SECRETSECRETSECRETSECRETSECRET", base="http://127.0.0.1:9")
        with self.assertRaises(tg.TelegramError) as cm:
            api.call("getMe")
        self.assertNotIn("SECRET", str(cm.exception))


def _tg_house(tmp, dbp, person, confirm, strong, secret):
    from chorepilot.house import House
    return House(demo_cfg(), db=ChoreDB(dbp), person=person, source="telegram",
                 confirm=confirm or (lambda q: False), strong_confirm=strong or (lambda q, p: False),
                 ask_secret=secret or (lambda q: None), notifier=Quiet(), log=lambda *a: None,
                 limiter=RateLimiter(min_start_gap_s=0), watch=False, defer=lambda a, op: None,
                 snapshot=Snapshot(os.path.join(tmp, "snap.json")),
                 audit_path=os.path.join(tmp, "audit.log"), client="telegram")


# ====================================================================== push events
HC_STREAM = b"""event: KEEP-ALIVE
data:
id: SIEMENS-SN123

event: STATUS
data: {"haId": "SIEMENS-SN123", "items": [{"key": "BSH.Common.Status.OperationState", "value": "BSH.Common.EnumType.OperationState.Finished", "timestamp": 1}]}
id: SIEMENS-SN123

: a comment
event: NOTIFY
data: {"haId": "SIEMENS-SN123", "items": [{"key": "BSH.Common.Option.ProgramProgress", "value": 40}]}
id: SIEMENS-SN123

event: EVENT
data: {"haId": "OTHER-1", "items": [{"key": "BSH.Common.Event.ProgramFinished", "value": "BSH.Common.EnumType.EventPresentState.Present"}]}
id: OTHER-1

event: DISCONNECTED
data: {"haId": "SIEMENS-SN123"}
id: SIEMENS-SN123

"""


class TestPush(Tmp):
    def test_sse_parser(self):
        evs = list(push.parse_sse(io.BytesIO(HC_STREAM)))
        self.assertEqual([e["event"] for e in evs],
                         ["KEEP-ALIVE", "STATUS", "NOTIFY", "EVENT", "DISCONNECTED"])
        self.assertEqual(push.hc_items(evs[0]), (None, []))
        ha, items = push.hc_items(evs[1])
        self.assertEqual((ha, items[0]["key"]), ("SIEMENS-SN123", "BSH.Common.Status.OperationState"))
        multi = list(push.parse_sse(["data: a\n", "data: b\n", "\n"]))
        self.assertEqual(multi[0]["data"], "a\nb")

    def test_hc_session_emits_only_relevant_mapped_events(self):
        got = []
        push.hc_session(io.BytesIO(HC_STREAM), {"SIEMENS-SN123": "dishwasher"},
                        lambda n, i: got.append((n, i["event"])))
        # STATUS OperationState + DISCONNECTED; progress ticks, keep-alives, other haIds ignored
        self.assertEqual(got, [("dishwasher", "STATUS"), ("dishwasher", "DISCONNECTED")])

    def test_ha_websocket_session_over_a_real_socket(self):
        a, b = socket.socketpair()
        events = [
            {"id": 1, "type": "result", "success": True},
            {"id": 1, "type": "event", "event": {"data": {"entity_id": "light.kitchen",
                                                          "old_state": {"state": "off"},
                                                          "new_state": {"state": "on"}}}},
            {"id": 1, "type": "event", "event": {"data": {
                "entity_id": "sensor.dishwasher_operation_state",
                "old_state": {"state": "run"}, "new_state": {"state": "finished"}}}},
            {"id": 1, "type": "event", "event": {"data": {
                "entity_id": "sensor.dishwasher_operation_state",
                "old_state": {"state": "finished"}, "new_state": {"state": "finished"}}}},
        ]
        seen = {}
        threading.Thread(target=_fake_ha_server, args=(b, events, seen), daemon=True).start()
        ws = push.WebSocket("ws://ha.local:8123/api/websocket", sock=a)
        got = []
        with self.assertRaises(push.WSClosed):
            push.ha_session(ws, "TOKEN", {"sensor.dishwasher_operation_state": "dishwasher"},
                            lambda n, i: got.append((n, i["state"])))
        ws.close()
        self.assertEqual(got, [("dishwasher", "finished")])
        self.assertEqual(seen["auth"], {"type": "auth", "access_token": "TOKEN"})
        self.assertEqual(seen["sub"], {"id": 1, "type": "subscribe_events",
                                       "event_type": "state_changed"})
        self.assertTrue(seen["pong"])
        self.assertEqual(push.ha_ws_url("https://ha.example:8123/"), "wss://ha.example:8123/api/websocket")

    def test_ha_bad_token(self):
        a, b = socket.socketpair()
        threading.Thread(target=_fake_ha_server, args=(b, [], {}, False), daemon=True).start()
        ws = push.WebSocket("ws://x/api/websocket", sock=a)
        with self.assertRaises(PermissionError):
            push.ha_session(ws, "BAD", {}, print)
        ws.close()

    def test_mqtt_session_skips_retained_and_repeats(self):
        class Cli:
            subscribed = None

            def subscribe(self, topics):
                self.subscribed = topics

            def messages(self, stop):
                yield from [("vacuum/state", "cleaning"), ("vacuum/state", "cleaning"),
                            ("vacuum/state", "docked"), ("other/x", "1")]
        got, cli = [], Cli()
        push.mqtt_session(cli, {"vacuum/state": "vacuum"}, lambda n, i: got.append((n, i["payload"])))
        self.assertEqual(cli.subscribed, ["vacuum/state"])
        self.assertEqual(got, [("vacuum", "docked")])

    def test_backoff(self):
        bo = push.Backoff(base=1, cap=10, rand=lambda: 0.5)
        self.assertEqual([round(bo.next()) for _ in range(6)], [1, 2, 4, 8, 10, 10])
        bo.reset()
        self.assertEqual(round(bo.next()), 1)

    def test_reconnect_loop(self):
        tries = []

        def once():
            tries.append(1)
            raise ConnectionError("down")
        logs = []
        push._loop("fake", once, lambda: len(tries) >= 3, logs.append, sleep=lambda s: None,
                   backoff=push.Backoff(base=0.001, cap=0.002))
        self.assertEqual(len(tries), 3)
        self.assertIn("reconnecting", logs[0])

    def test_push_map_for_ha_and_mqtt_devices(self):
        cfg = demo_cfg()
        cfg["ha"] = {"url": "http://ha.local:8123"}
        cfg["devices"]["thermostat"].update(backend="ha", entity="climate.hall")
        cfg["devices"]["vacuum"].update(backend="mqtt", host="broker:1884")
        os.environ["HA_TOKEN"] = "t"
        try:
            h = _push_house(self.tmp, cfg)
            m = push.push_map(h)
        finally:
            os.environ.pop("HA_TOKEN")
        self.assertEqual(m["ha"]["climate.hall"], "thermostat")
        self.assertEqual(m["ha_url"], "http://ha.local:8123")
        self.assertIn("vacuum", m["mqtt"][("broker", 1884)].values())

    def test_a_push_verifies_the_chore_immediately(self):
        os.environ["CHOREPILOT_SIM_SPEED"] = "1"            # the vacuum keeps cleaning ...
        h = _push_house(self.tmp, demo_cfg(), person="Riley")
        self.assertTrue(h.call("vacuum", {"action": "start"})["ok"])
        logs = []
        r = push.PushRunner(h, log=logs.append, interval=3600, safety=3600, debounce=0.01)
        fired = threading.Event()

        def fake_source():
            time.sleep(0.2)
            h.devices["vacuum"].adapter.set(state="docked", end=None)   # ... until it docks
            r.emit("vacuum", {"source": "mqtt"})
            r.emit("vacuum", {"source": "mqtt"})   # duplicates collapse
            fired.set()
            r.stopped.wait()
        r.sources = lambda: [("fake", fake_source, {"vacuum"})]
        threading.Thread(target=lambda: (fired.wait(5), time.sleep(1.5), r.stop()), daemon=True).start()
        r.run()
        self.assertTrue(any("vacuum changed (mqtt)" in ln for ln in logs), logs)
        done = [ln for ln in logs if "Vacuum the living room" in ln]
        self.assertTrue(done and "done by Riley" in done[0], logs)
        self.assertEqual(len(done), 1)


def _push_house(tmp, cfg, person="Alex"):
    from chorepilot.house import House
    return House(cfg, db=demo_db(), person=person, confirm=lambda q: True,
                 strong_confirm=lambda q, p: False, ask_secret=lambda q: None, notifier=Quiet(),
                 log=lambda *a: None, limiter=RateLimiter(min_start_gap_s=0), watch=False,
                 defer=lambda a, op: None, snapshot=Snapshot(os.path.join(tmp, "snap.json")),
                 audit_path=os.path.join(tmp, "audit.log"))


def _fake_ha_server(sock, events, seen, accept=True):
    """Server side of the HA websocket API on a socketpair (unmasked frames, one ping)."""
    import base64
    import hashlib
    import struct
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += sock.recv(4096)
    key = [ln.split(b": ", 1)[1] for ln in buf.split(b"\r\n") if ln.lower().startswith(b"sec-websocket-key")][0]
    acc = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
    sock.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                 b"Sec-WebSocket-Accept: " + acc + b"\r\n\r\n")

    def frame(b1, data):
        n = len(data)
        head = bytes([b1]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack("!H", n))
        sock.sendall(head + data)

    def send(op, data):
        frame(0x80 | op, data)

    def recv():
        h = sock.recv(2)
        n = h[1] & 0x7F
        if n == 126:
            n = struct.unpack("!H", sock.recv(2))[0]
        mask = sock.recv(4)
        data = b""
        while len(data) < n:
            data += sock.recv(n - len(data))
        return h[0] & 0x0F, bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    send(1, json.dumps({"type": "auth_required", "ha_version": "2026.10"}).encode())
    seen["auth"] = json.loads(recv()[1])
    if not accept:
        send(1, json.dumps({"type": "auth_invalid", "message": "bad"}).encode())
        return
    send(1, json.dumps({"type": "auth_ok"}).encode())
    seen["sub"] = json.loads(recv()[1])
    send(9, b"hi")                                       # ping -> the client must pong
    op, data = recv()
    seen["pong"] = op == 0xA and data == b"hi"
    for i, e in enumerate(events):
        payload = json.dumps(e).encode()
        if i == 1:                                      # one fragmented message
            frame(0x01, payload[:10])
            frame(0x80, payload[10:])
        else:
            send(1, payload)
    send(8, struct.pack("!H", 1000))
    try:
        recv()
    except (OSError, IndexError):
        pass
    sock.close()


if __name__ == "__main__":
    unittest.main()
