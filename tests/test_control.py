"""Appliance control: tool schemas, backends (HA REST, MQTT, Home Connect) against mocks,
the safety policy (confirmation, dry-run, interlocks, rate limit), and the agent loop.

Nothing here touches a real appliance or the network beyond 127.0.0.1.
"""
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chorepilot import appliance as ap  # noqa: E402
from chorepilot import control as ct  # noqa: E402
from chorepilot import serve  # noqa: E402


def sim(tmp, **kw):
    return ap.SimulatedBackend(path=os.path.join(tmp, "sim.json"), speed=kw.pop("speed", 600),
                               **kw)


def bridge(backend, **kw):
    kw.setdefault("ask", lambda q: True)
    kw.setdefault("limiter", ct.RateLimiter(min_start_gap_s=0))
    return ct.Bridge(backend, log=lambda *a: None, **kw)


class FakeHTTP:
    """Records requests; answers from a {(method, url-suffix): (code, json)} table."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, headers=None, body=None, timeout=15):
        self.calls.append((method, url, headers or {}, body))
        for (m, suffix), resp in self.routes.items():
            if m == method and url.endswith(suffix):
                return resp() if callable(resp) else resp
        return 404, {"error": "not found"}


# ------------------------------------------------------------------ schemas
class TestToolSchemas(unittest.TestCase):
    def test_openai_shape(self):
        names = set()
        for t in ct.TOOLS:
            self.assertEqual(t["type"], "function")
            fn = t["function"]
            self.assertRegex(fn["name"], r"^[a-z_]+$")
            self.assertTrue(fn["description"])
            self.assertEqual(fn["parameters"]["type"], "object")
            for r in fn["parameters"].get("required", []):
                self.assertIn(r, fn["parameters"]["properties"])
            names.add(fn["name"])
        self.assertEqual(names, {"get_status", "start_program", "pause", "resume", "cancel",
                                 "notify_when_done"})
        json.dumps(ct.TOOLS)  # serialisable

    def test_program_enum_matches_backends(self):
        start = next(t for t in ct.TOOLS if t["function"]["name"] == "start_program")
        enum = start["function"]["parameters"]["properties"]["program"]["enum"]
        self.assertEqual(set(enum), set(ap.PROGRAMS))
        self.assertEqual(set(enum), set(ap.HC_PROGRAMS))
        self.assertEqual(set(enum), set(ap.HA_DEFAULT["programs"]))
        opts = start["function"]["parameters"]["properties"]["options"]["properties"]
        self.assertEqual(set(opts), set(ap.OPTIONS))

    def test_validate(self):
        self.assertEqual(ct.validate_args("start_program", {"program": "ECO"}),
                         {"program": "eco", "options": {}})
        self.assertEqual(ct.validate_args("start_program", {"program": "auto", "options":
                                          {"half_load": "true", "delay_minutes": "30"}}),
                         {"program": "auto", "options": {"half_load": True, "delay_minutes": 30}})
        for bad in ({"program": "rm -rf"}, {}, {"program": "eco", "options": {"temp": 90}},
                    {"program": "eco", "options": {"delay_minutes": 9999}}):
            with self.assertRaises(ValueError):
                ct.validate_args("start_program", bad)
        with self.assertRaises(ValueError):
            ct.validate_args("open_door", {})


# ------------------------------------------------------------------ simulated + policy
class TestBridgePolicy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_start_pause_resume_cancel(self):
        b = bridge(sim(self.tmp))
        r = b.call("start_program", {"program": "quick"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["status"]["state"], "running")
        self.assertEqual(r["status"]["program"], "quick")
        self.assertGreater(r["status"]["remaining_s"], 40 * 60)
        self.assertFalse(b.call("start_program", {"program": "eco"})["ok"])  # already running
        self.assertEqual(b.call("pause")["status"]["state"], "paused")
        self.assertEqual(b.call("resume")["status"]["state"], "running")
        self.assertEqual(b.call("cancel")["status"]["state"], "ready")
        self.assertFalse(b.call("cancel")["ok"])

    def test_sim_finishes(self):
        be = sim(self.tmp, speed=1e6)
        bridge(be).call("start_program", {"program": "prerinse"})
        time.sleep(0.01)
        self.assertEqual(be.status()["state"], "finished")

    def test_confirmation_declined(self):
        asked = []
        b = bridge(sim(self.tmp), ask=lambda q: asked.append(q) or False)
        r = b.call("start_program", {"program": "eco"})
        self.assertFalse(r["ok"])
        self.assertTrue(r["declined"])
        self.assertIn("water and heat", asked[0])
        self.assertEqual(b.backend.status()["state"], "ready")

    def test_confirmation_scope(self):
        asked = []
        be = sim(self.tmp)
        be.apply(be.plan("start", "eco"))
        b = bridge(be, ask=lambda q: asked.append(q) or True)          # heat: pause is free
        b.call("pause")
        self.assertEqual(asked, [])
        b.call("resume")
        self.assertEqual(len(asked), 1)
        b2 = bridge(be, confirm="all", ask=lambda q: asked.append(q) or True)
        b2.call("cancel")
        self.assertEqual(len(asked), 2)
        never = bridge(sim(tempfile.mkdtemp()), confirm="never",
                       ask=lambda q: self.fail("must not ask"))
        self.assertTrue(never.call("start_program", {"program": "eco"})["ok"])

    def test_non_tty_never_auto_confirms(self):
        self.assertFalse(ct.tty_confirm("start?"))  # unittest stdin is not a tty

    def test_dry_run_sends_nothing(self):
        b = bridge(sim(self.tmp), dry_run=True, ask=lambda q: self.fail("no prompt in dry run"))
        r = b.call("start_program", {"program": "intensive", "options": {"extra_dry": True}})
        self.assertTrue(r["dry_run"])
        self.assertEqual(r["would_send"][0]["program"], "intensive")
        self.assertEqual(b.backend.status()["state"], "ready")

    def test_interlocks(self):
        be = sim(self.tmp)
        be.set(door="open")
        r = bridge(be).call("start_program", {"program": "eco"})
        self.assertIn("door is open", r["error"])
        be.set(door="closed", remote_start_allowed=False)
        r = bridge(be).call("start_program", {"program": "eco"})
        self.assertIn("remote start", r["error"])
        self.assertEqual(be.status()["state"], "ready")

    def test_rate_limit(self):
        lim = ct.RateLimiter(max_actions=2, window_s=600, min_start_gap_s=0,
                             path=os.path.join(self.tmp, "rl.json"))
        b = bridge(sim(self.tmp), limiter=lim)
        self.assertTrue(b.call("start_program", {"program": "eco"})["ok"])
        self.assertTrue(b.call("cancel")["ok"])
        r = b.call("start_program", {"program": "eco"})
        self.assertIn("rate limit", r["error"])
        gap = ct.RateLimiter(min_start_gap_s=60)
        self.assertIsNone(gap.check("start_program", now=1000))
        gap.record("start_program", now=1000)
        self.assertIn("between starts", gap.check("start_program", now=1010))
        self.assertIsNone(gap.check("pause", now=1010))

    def test_bad_args_reported_not_raised(self):
        r = bridge(sim(self.tmp)).call("start_program", {"program": "plasma"})
        self.assertFalse(r["ok"])
        self.assertIn("invalid arguments", r["error"])

    def test_notify_when_done(self):
        be = sim(self.tmp, speed=1e5)
        spawned = []
        b = bridge(be, watch_spawner=lambda m: spawned.append(m) or "test")
        self.assertFalse(b.call("notify_when_done")["ok"])          # nothing running
        b.call("start_program", {"program": "prerinse"})
        self.assertTrue(b.call("notify_when_done", {"message": "done!"})["ok"])
        self.assertEqual(spawned, ["The dishes are done. done!"])
        sent = []

        class N:
            def send(self, msg, log=None):
                sent.append(msg)
        st = ct.wait_until_done(be, "done!", poll_s=0.01, notifier=N(), log=lambda *a: None,
                                sleep=lambda s: time.sleep(min(s, 0.01)))
        self.assertEqual(st["state"], "finished")
        self.assertEqual(sent, ["done!"])
        # cancelled mid-run: the notification must not claim the dishes are clean
        be2 = sim(tempfile.mkdtemp(), speed=1)
        bridge(be2).call("start_program", {"program": "eco"})
        polls = iter([None, lambda: be2.apply(be2.plan("cancel"))])
        sent.clear()
        ct.wait_until_done(be2, "done!", poll_s=0.01, notifier=N(), log=lambda *a: None,
                           sleep=lambda s: (next(polls, None) or (lambda: None))())
        self.assertIn("stopped before finishing", sent[0])


# ------------------------------------------------------------------ Home Assistant
class TestHomeAssistant(unittest.TestCase):
    def routes(self, op="run"):
        st = lambda s, **a: (200, {"state": s, "attributes": a})  # noqa: E731
        return {
            ("GET", "/api/states/sensor.dishwasher_operation_state"): st(op),
            ("GET", "/api/states/binary_sensor.dishwasher_door"): st("off"),
            ("GET", "/api/states/binary_sensor.dishwasher_remote_start"): st("on"),
            ("GET", "/api/states/sensor.dishwasher_program_finish_time"):
                st(time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() + 3600))),
            ("GET", "/api/states/select.dishwasher_active_program"):
                st("dishcare_dishwasher_program_eco_50"),
            ("POST", "/api/services/select/select_option"): (200, []),
            ("POST", "/api/services/button/press"): (200, []),
        }

    def test_status(self):
        http = FakeHTTP(self.routes())
        be = ap.HomeAssistantBackend(url="http://ha:8123", token="t0k", http=http)
        s = be.status()
        self.assertEqual((s["state"], s["program"], s["door"], s["remote_start_allowed"]),
                         ("running", "eco", "closed", True))
        self.assertAlmostEqual(s["remaining_s"], 3600, delta=5)
        self.assertEqual(http.calls[0][2]["Authorization"], "Bearer t0k")

    def test_start_and_buttons(self):
        http = FakeHTTP(self.routes(op="ready"))
        be = ap.HomeAssistantBackend(url="http://ha:8123", token="t", http=http)
        r = bridge(be).call("start_program", {"program": "quick"})
        self.assertTrue(r["ok"], r)
        post = [c for c in http.calls if c[0] == "POST"]
        self.assertEqual(post[0][1], "http://ha:8123/api/services/select/select_option")
        self.assertEqual(post[0][3], {"entity_id": "select.dishwasher_active_program",
                                      "option": "dishcare_dishwasher_program_quick_45"})
        self.assertEqual(be.plan("cancel")[0]["body"], {"entity_id": "button.dishwasher_stop_program"})

    def test_custom_entities_and_missing_token(self):
        be = ap.HomeAssistantBackend(url="http://ha", token=None, cfg={
            "device": "kitchen_dw", "entities": {"door": "binary_sensor.dw_door_contact"}})
        self.assertEqual(be.ent["operation_state"], "sensor.kitchen_dw_operation_state")
        self.assertEqual(be.ent["door"], "binary_sensor.dw_door_contact")
        old = os.environ.pop("HA_TOKEN", None)
        try:
            with self.assertRaises(ap.BackendError):
                ap.HomeAssistantBackend(url="http://ha", http=FakeHTTP({})).status()
        finally:
            if old:
                os.environ["HA_TOKEN"] = old

    def test_offline_entities(self):
        be = ap.HomeAssistantBackend(url="http://ha", token="t", http=FakeHTTP({}))
        self.assertEqual(be.status()["state"], "offline")
        r = bridge(be).call("start_program", {"program": "eco"})
        self.assertIn("offline", r["error"])


# ------------------------------------------------------------------ Home Connect
class TestHomeConnect(unittest.TestCase):
    HA = "SIEMENS-SN658X06TE-68A40E000001"

    def routes(self, state="Ready"):
        root = f"/api/homeappliances/{self.HA}"
        return {
            ("GET", "/api/homeappliances"): (200, {"data": {"homeappliances": [
                {"haId": "OVEN-1", "type": "Oven"}, {"haId": self.HA, "type": "Dishwasher"}]}}),
            ("GET", root + "/status"): (200, {"data": {"status": [
                {"key": "BSH.Common.Status.OperationState",
                 "value": f"BSH.Common.EnumType.OperationState.{state}"},
                {"key": "BSH.Common.Status.DoorState",
                 "value": "BSH.Common.EnumType.DoorState.Closed"},
                {"key": "BSH.Common.Status.RemoteControlStartAllowed", "value": True}]}}),
            ("GET", root + "/programs/active"): (200, {"data": {
                "key": "Dishcare.Dishwasher.Program.Eco50",
                "options": [{"key": "BSH.Common.Option.RemainingProgramTime", "value": 9000}]}})
            if state == "Run" else (404, {"error": {"key": "SDK.Error.NoProgramActive"}}),
            ("PUT", root + "/programs/active"): (204, None),
            ("DELETE", root + "/programs/active"): (204, None),
            ("PUT", root + "/commands/BSH.Common.Command.PauseProgram"): (204, None),
        }

    def be(self, http):
        return ap.HomeConnectBackend(base="https://simulator.home-connect.com", token="abc",
                                     http=http)

    def test_status_discovers_dishwasher(self):
        http = FakeHTTP(self.routes(state="Run"))
        s = self.be(http).status()
        self.assertEqual((s["state"], s["program"], s["remaining_s"], s["door"],
                          s["remote_start_allowed"]), ("running", "eco", 9000, "closed", True))
        self.assertEqual(http.calls[0][2]["Accept"], ap.HC_TYPE)

    def test_start_body(self):
        http = FakeHTTP(self.routes())
        r = bridge(self.be(http)).call("start_program", {"program": "eco", "options": {
            "half_load": True, "delay_minutes": 30, "extra_dry": False}})
        self.assertTrue(r["ok"], r)
        put = [c for c in http.calls if c[0] == "PUT"][0]
        self.assertTrue(put[1].endswith(f"/{self.HA}/programs/active"))
        self.assertEqual(put[2]["Content-Type"], ap.HC_TYPE)
        self.assertEqual(put[3], {"data": {"key": "Dishcare.Dishwasher.Program.Eco50", "options": [
            {"key": "Dishcare.Dishwasher.Option.HalfLoad", "value": True},
            {"key": "BSH.Common.Option.StartInRelative", "value": 1800}]}})

    def test_pause_cancel_and_429(self):
        be = self.be(FakeHTTP(self.routes(state="Run")))
        self.assertEqual(be.plan("pause")[0]["body"],
                         {"data": {"key": "BSH.Common.Command.PauseProgram", "value": True}})
        self.assertEqual(be.plan("cancel")[0]["method"], "DELETE")
        self.assertTrue(bridge(be).call("pause")["ok"])
        limited = self.be(FakeHTTP({("GET", "/api/homeappliances"): (429, {})}))
        r = bridge(limited).call("get_status")
        self.assertIn("rate limit", r["error"])

    def test_device_flow(self):
        polls = iter([(400, {"error": "authorization_pending"}), (400, {"error": "slow_down"}),
                      (200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 86400})])
        http = FakeHTTP({
            ("POST", "/security/oauth/device_authorization"): (200, {
                "device_code": "DC", "user_code": "WDJB-MJHT",
                "verification_uri": "https://api.home-connect.com/security/oauth/device_verify",
                "expires_in": 600, "interval": 1}),
            ("POST", "/security/oauth/token"): lambda: next(polls)})
        lines = []
        tok = ap.device_flow_login(client_id="CID", http=http, log=lines.append,
                                   sleep=lambda s: None)
        self.assertEqual(tok["access_token"], "AT")
        self.assertIn("WDJB-MJHT", lines[0])
        self.assertIn(b"grant_type=device_code", http.calls[-1][3])

    def test_token_file_is_private(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "sub", "tok.json")
        ap.write_secret(p, {"access_token": "x"})
        self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
        old = os.environ.pop("HOME_CONNECT_TOKEN", None)
        try:
            be = ap.HomeConnectBackend(token_path=p, http=FakeHTTP({}))
            self.assertEqual(be.token(), "x")
        finally:
            if old:
                os.environ["HOME_CONNECT_TOKEN"] = old


# ------------------------------------------------------------------ MQTT
class FakeBroker(threading.Thread):
    """One-connection-at-a-time MQTT 3.1.1 broker: CONNACK, SUBACK + retained, logs PUBLISH."""

    def __init__(self, retained):
        super().__init__(daemon=True)
        self.retained, self.published, self.connects = retained, [], []
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(5)
        self.port = self.srv.getsockname()[1]

    @staticmethod
    def _read(c):
        h = c.recv(1)
        if not h:
            return None, None
        mult, n = 1, 0
        while True:
            b = c.recv(1)[0]
            n += (b & 0x7F) * mult
            mult *= 128
            if not b & 0x80:
                break
        body = b""
        while len(body) < n:
            body += c.recv(n - len(body))
        return h[0] >> 4, body

    def run(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            while True:
                t, body = self._read(c)
                if t is None or t == 14:
                    break
                if t == 1:
                    self.connects.append(body)
                    c.sendall(b"\x20\x02\x00\x00")
                elif t == 8:
                    pid = body[:2]
                    i, topics = 2, []
                    while i < len(body):
                        n = struct.unpack("!H", body[i:i + 2])[0]
                        topics.append(body[i + 2:i + 2 + n].decode())
                        i += 3 + n
                    c.sendall(b"\x90" + bytes([2 + len(topics)]) + pid + b"\x00" * len(topics))
                    for tp in topics:
                        if tp in self.retained:
                            pl = ap._mstr(tp) + self.retained[tp].encode()
                            c.sendall(b"\x31" + ap._varint(len(pl)) + pl)
                elif t == 3:
                    n = struct.unpack("!H", body[:2])[0]
                    self.published.append((body[2:2 + n].decode(), body[2 + n:].decode()))
            c.close()


class TestMQTT(unittest.TestCase):
    def setUp(self):
        os.environ["DISHSOAKER_MQTT_WAIT"] = "0.3"
        self.broker = FakeBroker({"dishwasher/sensor/operation_state/state": "Ready",
                                  "dishwasher/binary_sensor/door/state": "OFF",
                                  "dishwasher/binary_sensor/remote_start/state": "ON"})
        self.broker.start()

    def tearDown(self):
        self.broker.srv.close()

    def test_status_and_start(self):
        be = ap.MQTTBackend(host=f"127.0.0.1:{self.broker.port}")
        s = be.status()
        self.assertEqual((s["state"], s["door"], s["remote_start_allowed"]),
                         ("ready", "closed", True))
        r = bridge(be).call("start_program", {"program": "eco"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.broker.published, [("dishwasher/select/program/command", "eco"),
                                                 ("dishwasher/button/start/command", "PRESS")])

    def test_dry_run_publishes_nothing(self):
        be = ap.MQTTBackend(host=f"127.0.0.1:{self.broker.port}",
                            cfg={"programs": {"eco": "Eco 50"}})
        r = bridge(be, dry_run=True).call("start_program", {"program": "eco"})
        self.assertEqual(r["would_send"][0], {"topic": "dishwasher/select/program/command",
                                              "payload": "Eco 50"})
        self.assertEqual(self.broker.published, [])

    def test_offline_last_will(self):
        self.broker.retained["dishwasher/status"] = "offline"
        be = ap.MQTTBackend(host=f"127.0.0.1:{self.broker.port}")
        self.assertEqual(be.status()["state"], "offline")
        self.assertIn("offline", bridge(be).call("start_program", {"program": "eco"})["error"])

    def test_credentials_from_env(self):
        os.environ["MQTT_USER"], os.environ["MQTT_PASSWORD"] = "u", "p"
        try:
            ap.MQTTBackend(host=f"127.0.0.1:{self.broker.port}").status()
        finally:
            del os.environ["MQTT_USER"], os.environ["MQTT_PASSWORD"]
        self.assertTrue(self.broker.connects[-1].endswith(ap._mstr("u") + ap._mstr("p")))


# ------------------------------------------------------------------ agent loop
class TestAgentLoop(unittest.TestCase):
    def test_native_and_text_tool_calls(self):
        native = {"tool_calls": [{"id": "c1", "function": {
            "name": "start_program", "arguments": "{\"program\": \"eco\"}"}}]}
        self.assertEqual(ct.parse_tool_calls(native)[0]["args"], {"program": "eco"})
        text = {"content": '<tool_call>\n{"name": "start_program", "arguments": '
                           '{"program": "quick"}}\n</tool_call>'}
        self.assertEqual(ct.parse_tool_calls(text)[0]["name"], "start_program")
        self.assertEqual(ct.parse_tool_calls({"content": "hi"}), [])

    def test_run_agent(self):
        seen = []

        def fake_chat(url, messages, model=None):
            seen.append(messages)
            if messages[-1]["role"] == "user":
                self.assertIn("ready", messages[0]["content"])
                return {"choices": [{"message": {"tool_calls": [{"id": "a", "function": {
                    "name": "start_program", "arguments": "{\"program\": \"auto\"}"}}]}}]}
            res = json.loads(messages[-1]["content"])
            return {"choices": [{"message": {"content": "Started." if res["ok"] else "Failed."}}]}
        b = bridge(sim(tempfile.mkdtemp()))
        answer, trace = ct.run_agent("wash my dishes", b, "http://x", chat_fn=fake_chat,
                                     log=lambda *a: None)
        self.assertEqual(answer, "Started.")
        self.assertEqual(b.backend.status()["program"], "auto")
        self.assertEqual(seen[-1][-2]["tool_calls"][0]["function"]["name"], "start_program")


class TestSpeedArgs(unittest.TestCase):
    def test_flags_follow_build(self):
        serve._HELP_CACHE["new"] = "--spec-type none,draft-simple --spec-draft-n-max N --jinja"
        serve._HELP_CACHE["old"] = "-md, --model-draft FNAME --draft-max N"
        self.assertEqual(serve.speed_args("new", draft="d.gguf", draft_max=8),
                         ["-md", "d.gguf", "--spec-type", "draft-simple", "--spec-draft-n-max", "8"])
        self.assertEqual(serve.speed_args("new", spec="ngram-mod"), ["--spec-type", "ngram-mod"])
        self.assertEqual(serve.speed_args("old", draft="d.gguf", draft_max=8),
                         ["-md", "d.gguf", "--draft-max", "8"])
        with self.assertRaises(SystemExit):
            serve.speed_args("old", spec="draft-mtp")
        self.assertEqual(serve.speed_args("new"), [])


if __name__ == "__main__":
    unittest.main()
