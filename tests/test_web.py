"""ChorePilot 0.6 dashboard: the app shell and its security headers, device actions through the
policy tiers (confirm tokens, typed phrases, PIN re-entry, the approvals queue), "Ask the house"
through the same agent path as the terminal, browser onboarding, remote-access settings, the
readable activity timeline, and the device-type hooks every tile is drawn from. Nothing touches
a real device or network beyond 127.0.0.1.
"""
import json
import os
import re
import struct
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
import zlib
from http.cookies import SimpleCookie

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_chorepilot import Quiet, Tmp, demo_db  # noqa: E402

from chorepilot import onboard, web, webview  # noqa: E402
from chorepilot.chores import ChoreDB  # noqa: E402
from chorepilot.control import RateLimiter  # noqa: E402
from chorepilot.devtypes import all_types  # noqa: E402
from chorepilot.house import Snapshot  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, "chorepilot", "webui")


def fake_llm(script):
    """An OpenAI-compatible chat function that plays back tool calls, then a final answer."""
    calls = list(script)

    def chat(url, messages, tools=None, model=None, timeout=300, **kw):
        nxt = calls.pop(0) if calls else "Done."
        if isinstance(nxt, tuple):
            name, args = nxt
            return {"choices": [{"message": {"content": "", "tool_calls": [{
                "id": f"c{len(calls)}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)}}]}}]}
        return {"choices": [{"message": {"content": nxt}}]}
    return chat


class WebBase(Tmp):
    cfg_extra = {}
    public_url = None
    host_header = None

    def setUp(self):
        super().setUp()
        os.environ["CHOREPILOT_SIM_SPEED"] = "60000"
        self.dbp = os.path.join(self.tmp, "chores.db")
        db = demo_db(self.dbp)
        db.set_pin("Alex", "2468")
        db.set_pin("Jordan", "1111")
        db.set_pin("Sam", "1357")
        db.close()
        self.cfg = json.loads(json.dumps(onboard.PRESETS["demo"]))
        self.cfg.update(self.cfg_extra)
        self.audit = os.path.join(self.tmp, "audit.log")
        self.chat = None
        self.httpd = web.make_server(
            "127.0.0.1", 0, cfg=self.cfg, db_path=self.dbp, audit_path=self.audit,
            notifier=Quiet(), public_url=self.public_url,
            chat_fn=lambda *a, **k: self.chat(*a, **k),
            house_kw={"limiter": RateLimiter(max_actions=100, min_start_gap_s=0),
                      "defer": lambda a, op: None,
                      "snapshot": Snapshot(os.path.join(self.tmp, "snap.json"))})
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def req(self, path, body=None, sess=None, headers=None, method=None, raw=False):
        h = {"Content-Type": "application/json", **(headers or {})}
        if sess:
            h["Cookie"] = f"cp_session={sess[0]}"
            h.setdefault("X-ChorePilot-CSRF", sess[1])
        r = urllib.request.Request(self.base + path, data=json.dumps(body).encode()
                                   if body is not None else None, headers=h, method=method)
        try:
            with urllib.request.urlopen(r, timeout=15) as resp:
                data = resp.read()
                return resp.status, (data if raw else json.loads(data or b"{}")), resp.headers
        except urllib.error.HTTPError as e:
            with e:
                data = e.read()
                try:
                    data = json.loads(data or b"{}")
                except ValueError:
                    pass
                return e.code, data, e.headers

    def login(self, name, pin, headers=None):
        code, js, hdr = self.req("/api/login", {"name": name, "pin": pin}, headers=headers)
        self.assertEqual(code, 200, js)
        return SimpleCookie(hdr["Set-Cookie"])["cp_session"].value, js["csrf"], hdr["Set-Cookie"]

    def sess(self, name, pin):
        s, c, _ = self.login(name, pin)
        return (s, c)

    def act(self, sess, dev, action, params=None, **extra):
        return self.req(f"/api/devices/{dev}/{action}", {"params": params or {}, **extra}, sess)[:2]

    def audit_rows(self):
        return webview.audit_rows(self.audit)


# ====================================================================== the shell
class TestShell(WebBase):
    def test_static_files_and_csp(self):
        for path, ctype in (("/", "text/html"), ("/app.js", "javascript"), ("/app.css", "text/css"),
                            ("/sw.js", "javascript"), ("/manifest.webmanifest", "manifest"),
                            ("/icon.svg", "svg"), ("/icon-192.png", "image/png"),
                            ("/apple-touch-icon.png", "image/png")):
            code, body, hdr = self.req(path, raw=True)
            self.assertEqual(code, 200, path)
            self.assertIn(ctype, hdr["Content-Type"])
            csp = hdr["Content-Security-Policy"]
            for d in ("default-src 'self'", "script-src 'self'", "style-src 'self'",
                      "frame-ancestors 'none'", "object-src 'none'"):
                self.assertIn(d, csp)
            self.assertNotIn("unsafe-inline", csp)
            self.assertEqual(hdr["X-Frame-Options"], "DENY")
            self.assertEqual(hdr["Cross-Origin-Opener-Policy"], "same-origin")
            self.assertIn("microphone=(self)", hdr["Permissions-Policy"])
            self.assertIsNone(hdr.get("Strict-Transport-Security"))
        sw = self.req("/sw.js", raw=True)[1].decode()
        self.assertIn("chorepilot-shell-" + __import__("chorepilot").__version__ + "", sw)
        self.assertIn('startsWith("/api/")', sw)                 # never caches API data

    def test_no_cdn_no_inline_code_no_emoji(self):
        html = open(os.path.join(UI, "index.html")).read()
        self.assertNotRegex(html, r"<script>[^<]")               # no inline script
        self.assertNotIn("style=", html)
        self.assertNotIn("<style", html)
        js = open(os.path.join(UI, "app.js")).read()
        css = open(os.path.join(UI, "app.css")).read()
        self.assertNotIn('style="', js)                         # CSP blocks inline style attrs
        for text in (html, js, css):
            # nothing is loaded from elsewhere (addresses in placeholders and help text are fine)
            loads = re.findall(r"(?:src=|href=|url\(|fetch\(|import\(|@import)\s*[\"']?(https?:)?//", text)
            self.assertEqual([x for x in loads if x], [])
            self.assertNotRegex(text, r"(?:src|href)=\"https?://")
            self.assertIsNone(re.search("[\U0001F300-\U0001FAFF☀-➿]", text))
        for bad in ("#00ffff", "#0ff", "cyan", "teal", "backdrop-filter: blur(40"):
            self.assertNotIn(bad, css.lower())

    def test_self_hosted_font_licence_and_theme(self):
        code, data, hdr = self.req("/fonts/ibm-plex-sans.woff2", raw=True)
        self.assertEqual((code, data[:4]), (200, b"wOF2"))
        self.assertEqual(hdr["Content-Type"], "font/woff2")
        code, lic, _ = self.req("/fonts/OFL.txt", raw=True)
        self.assertIn(b"SIL Open Font License", lic)
        css = open(os.path.join(UI, "app.css")).read()
        self.assertEqual(re.findall(r"url\((?!\"/fonts/)", css), [])          # fonts only from here
        self.assertNotIn("76b900", css.lower())                             # our own accent
        self.assertNotRegex(open(os.path.join(UI, "app.js")).read() + css, r"(?i)nvidia")
        code, js, _ = self.req("/theme.js", raw=True)
        self.assertIn(b'"dark"', js)                                       # dark is the default
        html = open(os.path.join(UI, "index.html")).read()
        self.assertIn('data-theme="dark"', html)

    def test_icons_are_valid_pngs(self):
        for name, size in (("icon-192.png", 192), ("icon-512.png", 512),
                           ("icon-maskable-512.png", 512), ("apple-touch-icon.png", 180)):
            data = open(os.path.join(UI, name), "rb").read()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            w, h = struct.unpack(">II", data[16:24])
            self.assertEqual((w, h), (size, size))
            self.assertEqual(zlib.crc32(data[12:29]) & 0xFFFFFFFF, struct.unpack(">I", data[29:33])[0])
        man = json.load(open(os.path.join(UI, "manifest.webmanifest")))
        self.assertEqual(man["display"], "standalone")
        self.assertTrue(any(i.get("purpose") == "maskable" for i in man["icons"]))

    def test_everything_needs_sign_in(self):
        for p in ("/api/home", "/api/devices", "/api/activity", "/api/settings", "/api/ask/x",
                  "/api/settings/remote"):
            self.assertEqual(self.req(p)[0], 401, p)
        self.assertEqual(self.act(None, "living_room_light", "turn_on")[0], 401)
        self.assertEqual(self.req("/api/ask", {"text": "hi"})[0], 401)
        code, st, _ = self.req("/api/setup/state")
        self.assertEqual(st, {"needs_owner": False, "local": True, "version": __import__("chorepilot").__version__})


# ====================================================================== device actions
class TestDeviceActions(WebBase):
    def test_home_is_built_from_the_registry(self):
        s = self.sess("Alex", "2468")
        code, h, _ = self.req("/api/home", sess=s)
        self.assertEqual(code, 200)
        devs = {d["id"]: d for d in h["devices"]}
        self.assertEqual(len(devs), 19)
        self.assertEqual(devs["living_room_light"]["room"], "Living room")
        self.assertEqual(devs["living_room_light"]["display"], "Light")
        self.assertEqual(devs["dishwasher"]["room"], "Kitchen")
        self.assertEqual(devs["living_room_light"]["primary"]["action"], "turn_on")
        self.assertIsNone(devs["thermostat"]["primary"])           # tap opens the sheet
        self.assertEqual(devs["front_door"]["primary"]["action"], "lock")
        names = [c["name"] for c in devs["lawn_sprinkler"]["controls"]]
        self.assertEqual(names, ["water", "stop"])
        mins = devs["lawn_sprinkler"]["controls"][0]["params"][0]
        self.assertEqual((mins["lo"], mins["hi"]), (1, 30))          # house.json max_minutes
        prog = devs["dishwasher"]["controls"][0]["params"][0]
        self.assertIn("eco", prog["values"])
        self.assertTrue(devs["dishwasher"]["program_appliance"])
        self.assertEqual({p["name"] for p in h["board"]["people"]}, {"Alex", "Sam", "Jordan", "Riley"})
        self.assertIn("setup", h)
        self.assertFalse(h["llm"]["configured"])
        kid = self.sess("Jordan", "1111")
        self.assertNotIn("setup", self.req("/api/home", sess=kid)[1])

    def test_tier1_runs_and_is_audited(self):
        s = self.sess("Alex", "2468")
        code, r = self.act(s, "living_room_light", "turn_on", {"brightness": 40})
        self.assertEqual((code, r["ok"], r["status"]["brightness"]), (200, True, 40))
        row = self.audit_rows()[-1]
        self.assertEqual((row["source"], row["client"], row["tool"]), ("web", "dashboard", "set_light"))
        self.assertEqual(self.act(s, "living_room_light", "explode")[0], 400)
        self.assertEqual(self.act(s, "no_such_thing", "turn_on")[0], 404)
        self.assertEqual(self.act(s, "living_room_light", "turn_on", {"brightness": 400})[0], 400)
        bad = (s[0], "wrong-csrf")
        self.assertEqual(self.act(bad, "living_room_light", "turn_off")[0], 403)

    def test_tier2_needs_a_server_issued_yes_for_exactly_that_action(self):
        s = self.sess("Alex", "2468")
        code, r = self.act(s, "lawn_sprinkler", "water", {"minutes": 5})
        self.assertEqual((r["needs"], r["tier"]), ("confirm", 2))
        self.assertIn("lawn sprinkler", r["question"])
        tok = r["token"]
        self.assertEqual(self.audit_rows(), [])                     # asking is not an action
        # the token is bound to the action: a different duration is a different question
        code, r2 = self.act(s, "lawn_sprinkler", "water", {"minutes": 25}, token=tok)
        self.assertEqual(r2["needs"], "confirm")
        # ... to the session
        other = self.sess("Sam", "1357")
        code, r3 = self.act(other, "lawn_sprinkler", "water", {"minutes": 5}, token=r2["token"])
        self.assertEqual(r3["needs"], "confirm")
        # and it works once
        code, r = self.act(s, "lawn_sprinkler", "water", {"minutes": 5})
        code, ok = self.act(s, "lawn_sprinkler", "water", {"minutes": 5}, token=r["token"])
        self.assertTrue(ok["ok"], ok)
        self.assertEqual(ok["status"]["state"], "watering")
        code, again = self.act(s, "lawn_sprinkler", "stop")
        self.assertTrue(again["ok"])
        code, reuse = self.act(s, "lawn_sprinkler", "water", {"minutes": 5}, token=r["token"])
        self.assertEqual(reuse["needs"], "confirm")

    def test_program_appliance_start_confirm_then_runs_with_credit(self):
        s = self.sess("Sam", "1357")
        code, r = self.act(s, "dishwasher", "start_program", {"program": "eco"})
        self.assertEqual(r["needs"], "confirm")
        self.assertIn("uses water and heat", r["question"])
        code, r = self.act(s, "dishwasher", "start_program", {"program": "eco"}, token=r["token"])
        self.assertTrue(r["ok"], r)
        self.assertNotIn("sent", r)                                  # no local paths to the page
        db = ChoreDB(self.dbp)
        self.assertEqual(db.last_starter("dishwasher")["name"], "Sam")
        db.close()
        code, r = self.act(s, "dishwasher", "start_program", {"program": "nonsense"})
        self.assertEqual(code, 400)

    def test_tier3_needs_the_pin_again_and_kids_never(self):
        s = self.sess("Alex", "2468")
        code, r = self.act(s, "front_door", "unlock")
        self.assertEqual((r["needs"], r["tier"]), ("code", 3))
        code, r = self.act(s, "front_door", "unlock", code="0000")
        self.assertFalse(r["ok"])
        self.assertIn("wrong", r["error"])
        code, r = self.act(s, "front_door", "unlock", code="2468")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["status"]["state"], "unlocked")
        self.assertEqual(self.audit_rows()[-1]["outcome"], "second_factor")
        self.assertNotIn("2468", open(self.audit).read())
        adult = self.sess("Sam", "1357")
        code, r = self.act(adult, "garage", "open", code="1357")
        self.assertEqual(r["policy"], "deny")
        kid = self.sess("Jordan", "1111")
        code, r = self.act(kid, "front_door", "lock")
        self.assertTrue(r["ok"])                                    # kids_tier1
        code, r = self.act(kid, "front_door", "unlock", code="1111")
        self.assertEqual(r["policy"], "deny")

    def test_strong_confirmation_needs_the_typed_phrase(self):
        s = self.sess("Alex", "2468")
        code, r = self.act(s, "oven", "preheat", {"temperature": 400})
        self.assertEqual(r["needs"], "phrase")
        code, bad = self.act(s, "oven", "preheat", {"temperature": 400}, phrase="yes")
        self.assertFalse(bad["ok"])
        code, ok = self.act(s, "oven", "preheat", {"temperature": 400}, phrase=r["phrase"])
        self.assertTrue(ok["ok"], ok)

    def test_kid_tier2_goes_to_approvals_and_the_adult_approves(self):
        kid = self.sess("Jordan", "1111")
        code, r = self.act(kid, "lawn_sprinkler", "water", {"minutes": 10})
        self.assertTrue(r["queued"])
        self.assertIn("parent", r["message"])
        adult = self.sess("Sam", "1357")
        code, a, _ = self.req("/api/approvals", sess=adult)
        ap = a["approvals"][0]
        self.assertEqual(ap["summary"], "Jordan wants to water the lawn sprinkler, 10 min")
        self.assertEqual(ap["initials"], "J")
        self.assertEqual(self.req(f"/api/approvals/{ap['id']}", {"approve": True}, kid)[0], 403)
        code, r, _ = self.req(f"/api/approvals/{ap['id']}", {"approve": True}, adult)
        self.assertEqual((code, r["state"]), (200, "approved"))
        code, h, _ = self.req("/api/home", sess=adult)
        sprinkler = next(d for d in h["devices"] if d["id"] == "lawn_sprinkler")
        self.assertTrue(sprinkler["active"])
        self.assertEqual(sprinkler["progress"]["total_s"], 600)


# ====================================================================== ask the house
class TestAsk(WebBase):
    cfg_extra = {"llm": {"url": "http://127.0.0.1:9", "model": "tiny"}}

    def wait(self, sess, job, until=("waiting", "done", "error"), t=10):
        end = time.time() + t
        while time.time() < end:
            code, v, _ = self.req(f"/api/ask/{job}", sess=sess)
            if v["state"] in until:
                return v
            time.sleep(0.05)
        self.fail(f"ask job stuck: {v}")

    def test_tier2_confirm_card_then_runs(self):
        self.chat = fake_llm([("set_climate", {"device": "thermostat", "temperature": 80}),
                              "Set to 80."])
        s = self.sess("Alex", "2468")
        code, r, _ = self.req("/api/ask", {"text": "set the AC to 80"}, s)
        self.assertTrue(r["ok"])
        v = self.wait(s, r["job"])
        self.assertEqual(v["state"], "waiting")
        self.assertEqual(v["pending"]["kind"], "confirm")
        self.assertIn("80°F", v["pending"]["question"])
        other = self.sess("Sam", "1357")                             # not your question
        self.assertEqual(self.req(f"/api/ask/{r['job']}", sess=other)[0], 404)
        self.assertEqual(self.req(f"/api/ask/{r['job']}/answer", {"id": 1, "yes": True}, other)[0], 404)
        self.assertEqual(self.req(f"/api/ask/{r['job']}/answer", {"id": 7, "yes": True}, s)[0], 409)
        code, a, _ = self.req(f"/api/ask/{r['job']}/answer", {"id": v["pending"]["id"], "yes": True}, s)
        self.assertEqual(code, 200)
        v = self.wait(s, r["job"], ("done", "error"))
        self.assertEqual((v["state"], v["answer"]), ("done", "Set to 80."))
        kinds = [x["kind"] for x in v["steps"]]
        self.assertEqual(kinds, ["model", "question", "tool", "model"])   # a console trace
        self.assertEqual(v["steps"][2]["state"], "done")
        self.assertEqual(v["steps"][0]["calls"], 1)
        row = self.audit_rows()[-1]
        self.assertEqual((row["tool"], row["source"], row["ok"]), ("set_climate", "web", True))

    def test_no_means_nothing_happens(self):
        self.chat = fake_llm([("water_zone", {"action": "water", "minutes": 5}), "Okay, not watering."])
        s = self.sess("Alex", "2468")
        r = self.req("/api/ask", {"text": "water the lawn"}, s)[1]
        v = self.wait(s, r["job"])
        self.req(f"/api/ask/{r['job']}/answer", {"id": v["pending"]["id"], "yes": False}, s)
        v = self.wait(s, r["job"], ("done",))
        tools = [x for x in v["steps"] if x["kind"] == "tool"]
        self.assertEqual(tools[-1]["state"], "declined")
        self.assertFalse(self.audit_rows()[-1]["ok"])

    def test_security_via_ask_asks_for_the_pin(self):
        self.chat = fake_llm([("door_lock", {"action": "unlock"}), "Unlocked."])
        s = self.sess("Alex", "2468")
        r = self.req("/api/ask", {"text": "unlock the front door"}, s)[1]
        v = self.wait(s, r["job"])
        self.assertEqual(v["pending"]["kind"], "code")
        self.req(f"/api/ask/{r['job']}/answer", {"id": v["pending"]["id"], "code": "2468"}, s)
        v = self.wait(s, r["job"], ("done",))
        tools = [x for x in v["steps"] if x["kind"] == "tool"]
        self.assertEqual(tools[-1]["state"], "done")
        self.assertNotIn("2468", json.dumps(v))

    def test_kid_request_is_queued_not_asked(self):
        self.chat = fake_llm([("water_zone", {"action": "water", "minutes": 5}), "Asked a parent."])
        kid = self.sess("Jordan", "1111")
        r = self.req("/api/ask", {"text": "water the lawn"}, kid)[1]
        v = self.wait(kid, r["job"], ("done",))
        self.assertIsNone(v["pending"])
        self.assertEqual([x for x in v["steps"] if x["kind"] == "tool"][0]["state"], "queued")
        adult = self.sess("Sam", "1357")
        self.assertEqual(len(self.req("/api/approvals", sess=adult)[1]["approvals"]), 1)

    def test_model_failure_is_a_friendly_error(self):
        def boom(*a, **k):
            raise OSError("urlopen error [Errno 61] Connection refused")
        self.chat = boom
        s = self.sess("Alex", "2468")
        r = self.req("/api/ask", {"text": "hello"}, s)[1]
        v = self.wait(s, r["job"], ("error",))
        self.assertIn("did not answer", v["error"])


class TestSystemPanel(WebBase):
    cfg_extra = {"llm": {"url": "http://127.0.0.1:9", "model": "tiny"}}

    def test_system_panel_degrades_and_measures(self):
        from chorepilot import webllm
        self.assertEqual(webllm.prom("# HELP x\nllamacpp:predicted_tokens_seconds 41.5\n"
                                     "vllm:gpu_cache_usage_perc{model=\"m\"} 0.25\n"),
                         {"llamacpp:predicted_tokens_seconds": 41.5, "vllm:gpu_cache_usage_perc": 0.25})
        s = self.sess("Alex", "2468")
        code, r, _ = self.req("/api/system", sess=s)
        self.assertEqual(code, 200)
        self.assertEqual(r["measured"], {"calls": 0})
        self.assertTrue(r["local"])
        self.assertIsNone(r["metrics"]["server"])                     # nothing answers on :9
        self.assertIn("label", r["host"])
        self.chat = lambda *a, **k: {"choices": [{"message": {"content": "ready"}}],
                                     "timings": {"predicted_per_second": 88.0, "prompt_ms": 120}}
        self.req("/api/setup/model/test", {}, s)
        m = self.req("/api/system", sess=s)[1]["measured"]
        self.assertEqual((m["tok_s"], m["ttft_s"]), (88.0, 0.12))
        kid = self.sess("Jordan", "1111")
        self.assertNotIn("url", self.req("/api/system", sess=kid)[1]["llm"])


class TestAskWithoutModel(WebBase):
    def test_no_model_degrades_with_a_hint(self):
        s = self.sess("Alex", "2468")
        code, r, _ = self.req("/api/ask", {"text": "set the AC to 72"}, s)
        self.assertEqual(code, 200)
        self.assertTrue(r["no_model"])
        self.assertIn("device controls work", r["error"])


# ====================================================================== onboarding
class TestOnboarding(Tmp):
    def setUp(self):
        super().setUp()
        self.dbp = os.path.join(self.tmp, "fresh.db")
        self.httpd = web.make_server("127.0.0.1", 0, cfg=None, db_path=self.dbp,
                                     audit_path=os.path.join(self.tmp, "a.log"), notifier=Quiet(),
                                     http=lambda *a, **k: (_ for _ in ()).throw(
                                         onboard.BackendError("offline")))
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.h = WebBase.req.__get__(self)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def test_first_run_owner_then_members_devices_chores_agents(self):
        code, st, _ = self.h("/api/setup/state")
        self.assertTrue(st["needs_owner"])
        # not through a proxy / tunnel, even on loopback
        code, r, _ = self.h("/api/setup/owner", {"name": "Robin", "pin": "1234"},
                            headers={"X-Forwarded-For": "203.0.113.9"})
        self.assertEqual(code, 403)
        code, r, _ = self.h("/api/setup/owner", {"name": "Robin", "pin": "12"})
        self.assertEqual(code, 400)
        code, r, hdr = self.h("/api/setup/owner", {"name": "Robin", "pin": "4321",
                                                  "household": "Hill House", "unit": "C"})
        self.assertEqual(code, 200, r)
        self.assertEqual(r["role"], "owner")
        s = (SimpleCookie(hdr["Set-Cookie"])["cp_session"].value, r["csrf"])
        code, r2, _ = self.h("/api/setup/owner", {"name": "Mallory", "pin": "9999"})
        self.assertEqual(code, 400)                                   # one first owner only
        self.assertFalse(self.h("/api/setup/state")[1]["needs_owner"])
        house = json.load(open(os.path.join(os.environ["CHOREPILOT_CONFIG_DIR"], "house.json")))
        self.assertEqual((house["household"], house["unit"], house["local_person"]),
                         ("Hill House", "C", "Robin"))
        code, m, _ = self.h("/api/setup/members", {"name": "Kit", "role": "kid", "pin": "2222"}, s)
        self.assertEqual(code, 200, m)
        self.assertEqual({x["name"] for x in m["members"]}, {"Robin", "Kit"})
        self.assertEqual(self.h("/api/setup/members", {"name": "Bad", "role": "owner"}, s)[0], 400)
        code, sim, _ = self.h("/api/setup/simulated", {}, s)
        self.assertEqual(code, 200)
        code, ch, _ = self.h("/api/setup/chores", {}, s)
        self.assertIn("Take out the trash", ch["added"])
        code, tok, _ = self.h("/api/setup/mcp", {"client": "claude", "person": "Kit"}, s)
        self.assertTrue(tok["token"].startswith("cp_"))
        self.assertIn("<svg", tok["qr"])
        code, link, _ = self.h("/api/setup/telegram/link", {"name": "Kit"}, s)
        self.assertEqual(len(link["code"]), 8)
        code, mdl, _ = self.h("/api/setup/model", {"url": "http://127.0.0.1:9", "model": "q"}, s)
        self.assertEqual(mdl["llm"]["url"], "http://127.0.0.1:9")
        self.assertEqual(self.h("/api/setup/model", {"url": "ftp://x"}, s)[0], 400)
        code, sett, _ = self.h("/api/settings", sess=s)
        self.assertEqual(sett["household"], "Hill House")
        self.assertTrue(any(r["key"] == "chores" and r["done"] for r in sett["checklist"]))
        # a kid can't change setup
        code, kr, kh = self.h("/api/login", {"name": "Kit", "pin": "2222"})
        kid = (SimpleCookie(kh["Set-Cookie"])["cp_session"].value, kr["csrf"])
        self.assertEqual(self.h("/api/setup/members", {"name": "X", "role": "adult"}, kid)[0], 403)
        self.assertEqual(self.h("/api/settings", sess=kid)[0], 403)

    def test_ha_import_respects_the_chosen_entities(self):
        states = [{"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Kitchen"}},
                  {"entity_id": "lock.front", "state": "locked", "attributes": {}}]

        def http(method, url, headers=None, body=None, timeout=10):
            return (200, {"message": "API running."}) if url.endswith("/api/") else (200, states)
        self.httpd.http = http
        code, r, hdr = self.h("/api/setup/owner", {"name": "Robin", "pin": "4321"})
        s = (SimpleCookie(hdr["Set-Cookie"])["cp_session"].value, r["csrf"])
        code, d, _ = self.h("/api/setup/ha/discover", {"url": "http://ha.local:8123", "token": "t"}, s)
        self.assertEqual({e["entity"] for e in d["entities"]}, {"light.kitchen", "lock.front"})
        code, r, _ = self.h("/api/setup/ha/import", {"url": "http://ha.local:8123", "token": "t",
                                                     "entities": ["light.kitchen"]}, s)
        self.assertEqual(code, 200, r)
        house = json.load(open(os.path.join(os.environ["CHOREPILOT_CONFIG_DIR"], "house.json")))
        self.assertEqual(list(house["devices"]), ["kitchen"])
        self.assertNotIn("token", json.dumps(house))


# ====================================================================== remote access
class TestRemote(WebBase):
    public_url = "https://house.example.ts.net"

    def test_public_host_origin_secure_cookie_and_hsts(self):
        pub = {"Host": "house.example.ts.net"}
        code, js, hdr = self.req("/", headers=pub, raw=True)
        self.assertEqual(code, 200)
        self.assertEqual(hdr["Strict-Transport-Security"], "max-age=31536000")
        s, c, cookie = self.login("Alex", "2468", headers=pub)
        self.assertIn("; Secure", cookie)
        _, _, local_cookie = self.login("Alex", "2468")
        self.assertNotIn("Secure", local_cookie)                     # plain http on loopback
        ok = self.req("/api/devices/living_room_light/turn_on", {"params": {}}, (s, c),
                      headers={**pub, "Origin": "https://house.example.ts.net"})
        self.assertEqual(ok[0], 200)
        bad = self.req("/api/devices/living_room_light/turn_on", {"params": {}}, (s, c),
                       headers={**pub, "Origin": "https://evil.example"})
        self.assertEqual(bad[0], 403)
        self.assertEqual(self.req("/", headers={"Host": "evil.example"})[0], 421)
        # the first owner can't be created through the public address
        self.assertFalse(self.req("/api/setup/state", headers=pub)[1]["local"])

    def test_remote_settings_owner_only(self):
        owner = self.sess("Alex", "2468")
        code, r, _ = self.req("/api/settings/remote", sess=owner)
        self.assertEqual(code, 200)
        self.assertEqual(r["public_url"], "https://house.example.ts.net")
        self.assertIn("house.example.ts.net", r["allowed_hosts"])
        self.assertTrue(any("tailscale serve" in c for c in r["commands"]["tailscale"]))
        adult = self.sess("Sam", "1357")
        self.assertEqual(self.req("/api/settings/remote", sess=adult)[0], 403)
        code, r, _ = self.req("/api/setup/remote", {"public_url": "http://plain.example"}, owner)
        self.assertEqual(code, 400)
        code, r, _ = self.req("/api/setup/remote", {"public_url": "https://h.example.ts.net"}, owner)
        self.assertEqual(r["web"]["public_url"], "https://h.example.ts.net")

    def test_cli_refuses_plain_http_public_url(self):
        from chorepilot.cli import build_parser
        a = build_parser().parse_args(["web", "--public-url", "http://x.example", "--port", "0"])
        with self.assertRaises(SystemExit):
            a.func(a)


# ====================================================================== chores, activity, confirmations
class TestBoardAndActivity(WebBase):
    def test_board_done_reassign_week_and_activity(self):
        adult = self.sess("Sam", "1357")
        db = ChoreDB(self.dbp)
        inst = db.open_instance("vacuum the living room", assignee="Riley")
        db.close()
        code, r, _ = self.req(f"/api/chores/{inst['id']}/assign", {"person": "Jordan"}, adult)
        self.assertEqual(r["assigned_to"], "Jordan")
        kid = self.sess("Jordan", "1111")
        self.assertEqual(self.req(f"/api/chores/{inst['id']}/assign", {"person": "Riley"}, kid)[0], 403)
        code, r, _ = self.req(f"/api/chores/{inst['id']}/done", {}, kid)
        self.assertEqual((r["state"], r["points"]), ("done", 1))
        code, b, _ = self.req("/api/board", sess=kid)
        me = next(p for p in b["people"] if p["name"] == "Jordan")
        self.assertEqual((me["done_week"], me["streak"], me["points"]), (1, 1, 1))
        dishes = next(x for x in b["rotations"] if x["chore"] == "Dishes")
        self.assertIn("dishwasher finishes", dishes["auto"])
        code, r, _ = self.req(f"/api/rotations/{dishes['chore_id']}/week", {"person": "Riley"}, adult)
        self.assertEqual(r["assigned_to"], "Riley")
        code, act, _ = self.req("/api/activity", sess=adult)
        texts = [i["text"] for i in act["items"]]
        self.assertIn("Jordan finished vacuum the living room, +1", texts)
        self.assertTrue(any("gave Vacuum the living room to Jordan" in t for t in texts))
        code, kact, _ = self.req("/api/activity", sess=kid)
        self.assertFalse(any(i["text"].startswith("Sam") for i in kact["items"] if i["kind"] == "action"))
        self.assertEqual(self.req("/api/audit", sess=kid)[0], 403)

    def test_agent_confirmation_answered_on_the_dashboard(self):
        db = ChoreDB(self.dbp)
        c = db.add_confirmation("Sam", "Start the dishwasher? It uses water and heat.", source="mcp",
                                client="claude")
        db.close()
        sam = self.sess("Sam", "1357")
        code, a, _ = self.req("/api/approvals", sess=sam)
        self.assertEqual(a["confirmations"][0]["client"], "claude")
        alex = self.sess("Alex", "2468")
        self.assertEqual(self.req("/api/approvals", sess=alex)[1]["confirmations"], [])
        self.assertEqual(self.req(f"/api/confirmations/{c['id']}", {"yes": True}, alex)[0], 404)
        code, r, _ = self.req(f"/api/confirmations/{c['id']}", {"yes": True}, sam)
        self.assertEqual(r["state"], "yes")
        db = ChoreDB(self.dbp)
        self.assertEqual(db.confirmation(c["id"])["answered_via"], "web")
        db.close()


class TestWebChannelCanBeTurnedOff(WebBase):
    cfg_extra = {"mcp": {"confirm_channels": ["telegram"]}}

    def test_web_answers_refused(self):
        db = ChoreDB(self.dbp)
        c = db.add_confirmation("Sam", "Start?", source="mcp", client="x")
        db.close()
        sam = self.sess("Sam", "1357")
        self.assertEqual(self.req("/api/approvals", sess=sam)[1]["confirmations"], [])
        self.assertEqual(self.req(f"/api/confirmations/{c['id']}", {"yes": True}, sam)[0], 403)


# ====================================================================== registry hooks
class TestDashboardHooks(Tmp):
    def test_every_type_draws_a_tile_and_a_sheet(self):
        for name, t in all_types().items():
            st = t.sim_status(t.sim_init({}), time.time(), 1.0) if not name in (
                "dishwasher", "washer", "dryer") else {"state": "ready"}
            self.assertIsInstance(t.ui_state(st), str, name)
            self.assertIsInstance(t.ui_active(st), bool, name)
            for c in t.ui_controls({}):
                self.assertIn(c["tier"], (0, 1, 2, 3))
            p = t.ui_primary(st, {})
            if p:
                self.assertIn(p["action"], t.actions, name)
            self.assertTrue(t.room, name)
            self.assertEqual(t.ui_state({"state": "offline"}), "Not responding", name)

    def test_rooms_and_names(self):
        from chorepilot.devtypes import get_type
        self.assertEqual(webview.room_of("kitchen_light", get_type("light"), {}), "Kitchen")
        self.assertEqual(webview.room_of("lamp", get_type("light"), {"room": "office"}), "Office")
        self.assertEqual(webview.short_name("living_room_light", "Living room", get_type("light"), {}),
                         "Light")
        self.assertEqual(webview.short_name("garage", "Garage", get_type("garage_door"), {}),
                         "Garage door")
        self.assertEqual(webview.initials("Ana Maria"), "AM")

    def test_telegram_start_deep_link_links_the_account(self):
        from chorepilot.telegram import Bot
        db = demo_db(os.path.join(self.tmp, "t.db"))
        code = db.new_link_code("Sam")
        sent = []

        class API:
            def call(self, method, **p):
                sent.append((method, p))
                return {"message_id": 1}
        bot = Bot(API(), lambda *a: None, db, log=lambda m: None)
        bot.handle({"message": {"message_id": 5, "text": f"/start {code}", "chat": {"id": 9, "type": "private"},
                                "from": {"id": 42}}})
        self.assertEqual(bot.linked(42)["name"], "Sam")
        db.close()


if __name__ == "__main__":
    unittest.main()
