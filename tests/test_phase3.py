"""ChorePilot phase 3: first-run onboarding (`chorepilot setup`), the QR encoder for TOTP
enrolment, ownership, the new device types (covers, garage doors, alarm panels, EV chargers,
water heaters, air purifiers, humidifiers, media players), the web dashboard, `chorepilot run`
and the service files. Nothing touches a real device or network beyond 127.0.0.1.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.cookies import SimpleCookie

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_chorepilot import Quiet, Tmp, demo_db, house  # noqa: E402

from chorepilot import env, onboard, policy, qr, runner, web  # noqa: E402
from chorepilot.chores import ChoreDB, ChoreError, totp  # noqa: E402
from chorepilot.cli import build_parser  # noqa: E402
from chorepilot.devtypes import get_type  # noqa: E402
from chorepilot.push import ha_ws_url  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def setup_args(argv):
    return build_parser().parse_args(["setup", "--non-interactive", "--no-probe", *argv])


def run_setup(argv, http=None, answers=None, **kw):
    a = setup_args(argv)
    io = onboard.IO(answers is not None, answers=answers, out=lambda m="": None)
    st = onboard.Setup(io, a, http=http or (lambda *x, **y: (_ for _ in ()).throw(
        onboard.BackendError("offline"))), **kw)
    sec = a.section or "all"
    st.run(list(onboard.SECTIONS) if sec == "all" else [sec])
    st.db.close()
    return st


class P3(Tmp):
    def setUp(self):
        super().setUp()
        self._env3 = {k: os.environ.pop(k, None) for k in (
            "HA_TOKEN", "HA_URL", "CHOREPILOT_TELEGRAM_TOKEN", "MQTT_USER", "MQTT_PASSWORD",
            "HOME_CONNECT_CLIENT_ID", "CHOREPILOT_OWNER_PIN", "CHOREPILOT_WEB_HOST")}

    def tearDown(self):
        for k, v in self._env3.items():
            if v is not None:
                os.environ[k] = v
        super().tearDown()

    def cfg_path(self, *p):
        return os.path.join(os.environ["CHOREPILOT_CONFIG_DIR"], *p)


# ====================================================================== QR
class TestQR(unittest.TestCase):
    def test_structure_and_format_information(self):
        m = qr.encode("otpauth://totp/ChorePilot:Alex?secret=JBSWY3DPEHPK3PXP&issuer=ChorePilot")
        n = len(m)
        self.assertEqual((n - 17) % 4, 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):        # finder centres are dark
            self.assertTrue(m[cy][cx])
            self.assertFalse(m[cy - 2][cx])                       # the light ring
        self.assertTrue(all(m[6][i] == (i % 2 == 0) for i in range(8, n - 8)))   # timing
        self.assertTrue(m[n - 8][8])                              # the dark module
        bits = 0
        pos = [(8, i) for i in range(6)] + [(8, 7), (8, 8), (7, 8)] + \
            [(14 - i, 8) for i in range(9, 15)]
        for i, (x, y) in enumerate(pos):
            bits |= int(m[y][x]) << i
        bits ^= 0x5412
        self.assertEqual(bits >> 13, 0)                           # level M
        data = bits >> 10
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        self.assertEqual(bits & 0x3FF, rem)                       # BCH check bits agree

    def test_matches_a_reference_encoder_bit_for_bit(self):
        exe = shutil.which("qrencode")
        if not exe:
            self.skipTest("qrencode not installed")
        for text in ("hello", "otpauth://totp/ChorePilot:Riley?secret=" + "A" * 32 + "&issuer=ChorePilot",
                     "x" * 150):
            out = subprocess.run([exe, "-t", "ASCII", "-m", "0", "-l", "M", "-8", text],
                                 capture_output=True, text=True).stdout
            ref = [[c == "#" for c in r[::2]] for r in out.split("\n") if r]
            pos = [(8, i) for i in range(6)] + [(8, 7), (8, 8), (7, 8)] + \
                [(14 - i, 8) for i in range(9, 15)]
            bits = sum(int(ref[y][x]) << i for i, (x, y) in enumerate(pos)) ^ 0x5412
            self.assertEqual(qr.encode(text, mask=(bits >> 10) & 7), ref, text[:20])

    def test_renderers(self):
        m = qr.encode("ChorePilot")
        self.assertTrue(qr.png(m).startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn("<svg", qr.svg(m))
        t = qr.terminal(m, ansi=False).split("\n")
        self.assertEqual(len(t), (len(m) + 4 + 1) // 2)
        with self.assertRaises(ValueError):
            qr.encode("x" * 400)


# ====================================================================== onboarding
class TestSetup(P3):
    def test_non_interactive_first_run_makes_the_installer_the_owner(self):
        run_setup(["--household", "The Lees", "--owner", "Alex", "--owner-pin", "2468", "--totp",
                   "--member", "Sam:adult:1357", "--member", "Jordan:kid", "--member", "Pat:guest",
                   "--model-url", "none", "--simulated", "--mcp-client", "grok-bot",
                   "--unit", "F"])
        db = ChoreDB()
        self.assertEqual([(p["name"], p["role"]) for p in db.people()],
                         [("Alex", "owner"), ("Sam", "adult"), ("Jordan", "kid"), ("Pat", "guest")])
        self.assertTrue(db.check_pin("Alex", "2468") and db.check_pin("Sam", "1357"))
        self.assertTrue(db.person("Alex")["totp_secret"])
        self.assertEqual(db.person("Jordan")["quiet_hours"], "21:00-07:00")
        cfg = onboard.read_house()
        self.assertEqual((cfg["household"], cfg["unit"], cfg["local_person"]), ("The Lees", "F", "Alex"))
        self.assertIn("garage", cfg["devices"])
        titles = [c["title"] for c in db.chores()]
        self.assertIn("Run the dishwasher", titles)
        self.assertIn("Take out the trash", titles)
        dish = db.chore("run the dishwasher")
        self.assertEqual(dish["verify"], "dishwasher.finished")
        self.assertNotIn(db.person("Pat")["id"], dish["rotation"])          # guests do no chores
        self.assertTrue(os.path.exists(self.cfg_path("chores.json")))
        toks = json.load(open(self.cfg_path("mcp-tokens.json")))
        self.assertEqual([v["person"] for v in toks.values()], ["Alex"])
        self.assertEqual(stat.S_IMODE(os.stat(self.cfg_path("chorepilot.db")).st_mode), 0o600)
        db.close()

    def test_nothing_personal_is_preinstalled(self):
        db = ChoreDB()
        self.assertEqual(db.people(), [])
        self.assertEqual(db.chores(), [])
        db.close()

    def test_rerun_is_idempotent_and_a_second_owner_needs_an_owners_code(self):
        base = ["--owner", "Alex", "--owner-pin", "2468", "--member", "Sam:adult",
                "--model-url", "none"]
        run_setup(base)
        run_setup(base)
        db = ChoreDB()
        self.assertEqual(len(db.people()), 2)
        n = len(db.chores())
        db.close()
        with self.assertRaises(onboard.SetupError):
            run_setup(["owner", "--owner", "Sam"])
        with self.assertRaises(onboard.SetupError):
            run_setup(["owner", "--owner", "Sam", "--auth", "0000"])
        run_setup(["owner", "--owner", "Sam", "--auth", "2468"])
        db = ChoreDB()
        self.assertEqual([p["name"] for p in db.owners()], ["Alex", "Sam"])
        self.assertEqual(len(db.chores()), n)
        db.close()

    def test_chores_json_round_trip(self):
        run_setup(["--owner", "Alex", "--member", "Sam:adult", "--model-url", "none"])
        path = self.cfg_path("chores.json")
        doc = json.load(open(path))
        doc["chores"][0]["points"] = 5
        doc["chores"].append({"title": "Feed the cat", "schedule": "daily at 08:00",
                              "rotation": ["Sam"]})
        json.dump(doc, open(path, "w"))
        run_setup(["chores", "--from", path])
        db = ChoreDB()
        self.assertEqual(db.chore(doc["chores"][0]["title"])["points"], 5)
        self.assertEqual(db.chore("feed the cat")["rotation"], [db.person("Sam")["id"]])
        db.close()

    def test_scripted_interactive_run(self):
        uri = {}
        answers = ["The Ortegas", "C", "Alex", "2468", "2468", "y",   # household, unit, owner, PIN x2, TOTP
                   "CODE",                                            # the authenticator code (filled in)
                   "Sam", "adult", "", "Jordan", "kid", "", "",       # members, then done
                   "",                                                # model URL: none
                   "y", "", "n", "",                                  # simulated devices; no HA/HC/MQTT
                   "",                                                # no Telegram token
                   "",                                                # no agents
                   "1,3"]                                             # keep chores 1 and 3

        class Live(onboard.IO):
            def _input(self, prompt, hide=False):
                v = super()._input(prompt, hide)
                if v == "CODE":
                    db = ChoreDB()
                    v = totp(db.person("Alex")["totp_secret"])
                    db.close()
                    uri["ok"] = True
                return v
        a = setup_args([])
        st = onboard.Setup(Live(True, answers=answers, out=lambda m="": None), a,
                           http=lambda *x, **y: (_ for _ in ()).throw(onboard.BackendError("x")))
        st.run(list(onboard.SECTIONS))
        self.assertTrue(uri.get("ok"))
        db = st.db
        self.assertEqual([p["name"] for p in db.people()], ["Alex", "Sam", "Jordan"])
        self.assertEqual(len(db.chores()), 2)
        self.assertEqual(onboard.read_house()["household"], "The Ortegas")
        self.assertNotIn("comfort", onboard.read_house()["devices"]["thermostat"])   # Celsius house

    def test_model_detection(self):
        def http(method, url, headers=None, body=None, timeout=15):
            if url == "http://127.0.0.1:11434/v1/models":
                return 200, {"data": [{"id": "qwen2.5:3b"}]}
            if url == "http://127.0.0.1:8080/v1/models":
                return 200, {"data": [{"id": "Qwen2.5-1.5B-Instruct-Q4_K_S.gguf"}]}
            raise onboard.BackendError("refused")
        found = onboard.detect_llms(http=http)
        self.assertEqual([f["kind"] for f in found], ["llama-server", "ollama"])
        run_setup(["--owner", "Alex", "--model-url", "auto"], http=http)
        self.assertEqual(onboard.read_house()["llm"],
                         {"url": "http://127.0.0.1:8080", "kind": "llama-server",
                          "model": "Qwen2.5-1.5B-Instruct-Q4_K_S.gguf"})
        run_setup(["model", "--model-url", "http://brain.local:11434/v1"], http=lambda *a, **k: (
            200, {"data": [{"id": "llama3.2"}]}))
        self.assertEqual(onboard.read_house()["llm"]["url"], "http://brain.local:11434")

    def test_home_assistant_discovery_and_mapping(self):
        states = [
            {"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Kitchen"}},
            {"entity_id": "cover.garage_door", "state": "closed", "attributes": {"device_class": "garage"}},
            {"entity_id": "cover.bedroom", "state": "open", "attributes": {"device_class": "blind"}},
            {"entity_id": "alarm_control_panel.home", "state": "disarmed", "attributes": {}},
            {"entity_id": "switch.wallbox_charging", "state": "off", "attributes": {}},
            {"entity_id": "fan.bedroom_air_purifier", "state": "off", "attributes": {}},
            {"entity_id": "valve.gas_main", "state": "open", "attributes": {"device_class": "gas"}},
            {"entity_id": "sensor.temperature", "state": "21", "attributes": {}},
            {"entity_id": "media_player.living_room", "state": "idle", "attributes": {}},
        ]
        seen = []

        def http(method, url, headers=None, body=None, timeout=15):
            seen.append((url, (headers or {}).get("Authorization")))
            if headers.get("Authorization") != "Bearer good":
                return 401, {"message": "401: Unauthorized"}
            return (200, {"message": "API running."}) if url.endswith("/api/") else (200, states)
        with self.assertRaises(onboard.SetupError):
            onboard.ha_discover("http://ha:8123", "bad", http=http)
        msg, found = onboard.ha_discover("http://ha:8123/", "good", http=http)
        types = {d["entity"]: d["type"] for d in found}
        self.assertEqual(types, {"light.kitchen": "light", "cover.garage_door": "garage_door",
                                 "cover.bedroom": "cover", "alarm_control_panel.home": "alarm_panel",
                                 "switch.wallbox_charging": "ev_charger",
                                 "fan.bedroom_air_purifier": "air_purifier",
                                 "media_player.living_room": "media_player"})
        run_setup(["--owner", "Alex", "--model-url", "none", "--ha-url", "http://ha:8123",
                   "--ha-token", "good"], http=http)
        cfg = onboard.read_house()
        self.assertEqual(cfg["ha"], {"url": "http://ha:8123"})
        self.assertEqual(cfg["devices"]["garage_door"], {"type": "garage_door", "backend": "ha",
                                                         "entity": "cover.garage_door"})
        self.assertNotIn("good", json.dumps(cfg))                       # the token is not in house.json
        self.assertEqual(env.secret("ha_token"), "good")
        self.assertEqual(stat.S_IMODE(os.stat(env.secrets_path()).st_mode), 0o600)

    def test_home_connect_discovery(self):
        def http(method, url, headers=None, body=None, timeout=15):
            return 200, {"data": {"homeappliances": [
                {"haId": "BOSCH-SMV6ZCX42E-1", "type": "Dishwasher", "vib": "SMV6ZCX42E",
                 "brand": "Bosch", "name": "Dishwasher", "connected": True},
                {"haId": "SIEMENS-WM-2", "type": "Washer", "vib": "WM14X", "brand": "Siemens"},
                {"haId": "NEFF-OV-3", "type": "Oven", "vib": "B57", "brand": "Neff"},
                {"haId": "X-HOOD", "type": "Hood"}]}}
        run_setup(["--owner", "Alex", "--model-url", "none", "--homeconnect",
                   "--hc-client-id", "cid"], http=http,
                  hc_login=lambda: {"access_token": "at", "refresh_token": "rt", "expires_at": 9e9})
        devs = onboard.read_house()["devices"]
        self.assertEqual(devs["dishwasher"], {"type": "dishwasher", "backend": "homeconnect",
                                              "haId": "BOSCH-SMV6ZCX42E-1", "model": "SMV6ZCX42E"})
        self.assertNotIn("model", devs["washer"])                       # unknown model: generic
        self.assertEqual(devs["oven"]["type"], "oven")
        self.assertEqual(len(devs), 3)
        self.assertEqual(stat.S_IMODE(os.stat(self.cfg_path("homeconnect-token.json")).st_mode), 0o600)

    def test_telegram_section_saves_the_token_and_prints_link_codes(self):
        out = []

        class API:
            def __init__(self, tok):
                self.tok = tok

            def call(self, m):
                return {"username": "our_house_bot", "first_name": "Our House"}
        a = setup_args(["--owner", "Alex", "--member", "Sam:adult", "--model-url", "none",
                        "--telegram-token", "123456:" + "A" * 35, "--telegram-link"])
        st = onboard.Setup(onboard.IO(False, out=out.append), a, tg_api=API,
                           http=lambda *x, **y: (_ for _ in ()).throw(onboard.BackendError("x")))
        st.run(list(onboard.SECTIONS))
        text = "\n".join(out)
        self.assertIn("@our_house_bot", text)
        codes = re.findall(r"(Alex|Sam)\s+/link ([A-Z0-9]{8})", text)
        self.assertEqual([c[0] for c in codes], ["Alex", "Sam"])
        self.assertEqual(st.db.use_link_code(codes[1][1])["name"], "Sam")
        self.assertEqual(stat.S_IMODE(os.stat(self.cfg_path("telegram-token")).st_mode), 0o600)

    def test_manual_device_flag(self):
        run_setup(["--owner", "Alex", "--model-url", "none",
                   "--device", "garage=garage@ha:cover.garage", "--device", "kids_speaker=speaker@mqtt:kids/speaker"])
        devs = onboard.read_house()["devices"]
        self.assertEqual(devs["garage"], {"type": "garage_door", "backend": "ha", "entity": "cover.garage"})
        self.assertEqual(devs["kids_speaker"]["prefix"], "kids/speaker")
        with self.assertRaises(onboard.SetupError):
            run_setup(["devices", "--device", "x=teleporter"])


# ====================================================================== ownership
class TestOwnership(P3):
    def test_last_owner_cannot_be_demoted_and_transfer(self):
        db = ChoreDB(":memory:")
        db.add_person("Alex", "owner")
        db.add_person("Sam", "adult")
        with self.assertRaises(ChoreError):
            db.set_role("Alex", "adult")
        db.transfer_owner("Alex", "Sam")
        self.assertEqual([p["name"] for p in db.owners()], ["Sam"])
        self.assertEqual(db.person("Alex")["role"], "adult")
        db.transfer_owner("Sam", "Alex", keep=True)
        self.assertEqual(len(db.owners()), 2)

    def test_cli_ownership_changes_need_an_owners_code(self):
        run_setup(["--owner", "Alex", "--owner-pin", "2468", "--member", "Sam:adult",
                   "--model-url", "none", "--chores", "none"])
        p = build_parser()
        with self.assertRaises(SystemExit):
            p.parse_args(["people", "set-role", "Sam", "--role", "owner"]).func(
                p.parse_args(["people", "set-role", "Sam", "--role", "owner"]))
        a = p.parse_args(["people", "transfer-owner", "Sam", "--code", "1111"])
        with self.assertRaises(SystemExit):
            a.func(a)
        a = p.parse_args(["people", "transfer-owner", "Sam", "--code", "2468"])
        a.func(a)
        db = ChoreDB()
        self.assertEqual([o["name"] for o in db.owners()], ["Sam"])
        self.assertEqual(onboard.read_house()["local_person"], "Sam")
        db.close()

    def test_the_local_person_is_who_set_it_up(self):
        db = demo_db()
        h = house(self.tmp, None, db)
        self.assertEqual(h.me, "Alex")                       # the only owner
        db.add_person("Casey", "owner")
        self.assertIsNone(house(self.tmp, None, db).me)      # two owners: nobody by default
        h = house(self.tmp, None, db)
        h.cfg["local_person"] = "Casey"
        self.assertEqual(h._who(None)["name"], "Casey")


# ====================================================================== new device types
class TestNewDeviceTypes(P3):
    def test_tiers(self):
        def tier(t, a, args=None, cfg=None):
            dt = get_type(t)
            act, params = dt.validate(a, args or {}, cfg or {})
            return dt.tier_for(act, params, cfg or {})
        self.assertEqual(tier("garage", "open"), 3)
        self.assertEqual(tier("garage", "close"), 1)
        self.assertEqual(tier("blinds", "open"), 1)
        self.assertEqual(tier("alarm", "arm"), 1)
        self.assertEqual(tier("alarm", "disarm"), 3)
        self.assertEqual(tier("ev", "start"), 2)
        self.assertEqual(tier("ev", "stop"), 1)
        self.assertEqual(tier("water_heater", "set_temperature", {"temperature": 120}, {"unit": "F"}), 2)
        self.assertEqual(tier("purifier", "on"), 1)
        self.assertEqual(tier("humidifier", "set_humidity", {"humidity": 45}), 1)
        self.assertEqual(tier("tv", "set_volume", {"volume": 20}), 1)

    def test_codes_are_never_accepted_from_the_model(self):
        for t, a in (("garage_door", "open"), ("alarm_panel", "disarm")):
            with self.assertRaises(ValueError):
                get_type(t).validate(a, {"code": "1234"})

    def test_garage_open_owner_plus_second_factor_never_agents_kids_or_schedules(self):
        owner, kid = {"name": "Alex", "role": "owner"}, {"name": "Jordan", "role": "kid"}
        adult = {"name": "Sam", "role": "adult"}
        self.assertEqual(policy.decide(owner, 3, "cli").outcome, "second_factor")
        self.assertEqual(policy.decide(owner, 3, "mcp", {"agents_max_tier": 3}).outcome, "deny")
        self.assertEqual(policy.decide(owner, 3, "schedule").outcome, "deny")
        self.assertEqual(policy.decide(kid, 3, "cli").outcome, "deny")
        self.assertEqual(policy.decide(adult, 3, "telegram").outcome, "deny")
        db = demo_db()
        db.set_pin("Alex", "2468")
        h = house(self.tmp, "Alex", db, secret=lambda q: "2468")
        self.assertEqual(h.call("garage_door", {"action": "close"})["error"],
                         "the garage door is already closed")
        r = h.call("garage_door", {"action": "open"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["policy"], "second_factor")
        r = house(self.tmp, "Alex", db, secret=lambda q: "0000").call("garage_door", {"action": "close"})
        self.assertTrue(r["ok"])                                         # close needs no code
        r = house(self.tmp, "Alex", db, source="mcp").call("garage_door", {"action": "open"})
        self.assertFalse(r["ok"])
        self.assertIn("external agent", r["error"])
        r = house(self.tmp, "Jordan", db).call("garage_door", {"action": "open"})
        self.assertIn("only the owner", r["error"])

    def test_alarm(self):
        db = demo_db()
        db.set_pin("Alex", "2468")
        r = house(self.tmp, "Jordan", db).call("alarm_panel", {"action": "arm_away"})
        self.assertTrue(r["ok"], r)                                      # kids may arm (tier 1)
        self.assertEqual(r["status"]["state"], "armed_away")
        self.assertIn("already armed away", house(self.tmp, "Jordan", db).call(
            "alarm_panel", {"action": "away"})["error"])
        r = house(self.tmp, "Sam", db).call("alarm_panel", {"action": "disarm"})
        self.assertIn("only the owner", r["error"])
        r = house(self.tmp, "Alex", db, secret=lambda q: "2468").call("alarm_panel", {"action": "off"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"]["state"], "disarmed")
        ops = get_type("alarm_panel").ha_ops("disarm", {}, "alarm_control_panel.home",
                                             {"alarm_code": "9876"})
        self.assertEqual(ops, [{"service": "alarm_control_panel.alarm_disarm",
                                "data": {"entity_id": "alarm_control_panel.home", "code": "9876"}}])

    def test_dry_run_never_shows_the_panels_code_to_the_model(self):
        cfg = json.loads(json.dumps(onboard.PRESETS["demo"]))
        cfg["devices"]["alarm"] = {"type": "alarm_panel", "backend": "ha",
                                   "entity": "alarm_control_panel.home", "alarm_code": "9876"}
        db = demo_db()
        db.set_pin("Alex", "2468")
        from chorepilot.house import House
        from chorepilot.control import RateLimiter
        h = House(cfg, db=db, person="Alex", dry_run=True, notifier=Quiet(), log=lambda *a: None,
                  ask_secret=lambda q: "2468", watch=False, limiter=RateLimiter(min_start_gap_s=0),
                  audit_path=os.path.join(self.tmp, "a.log"),
                  adapter_kw={"alarm": {"http": lambda *a, **k: (200, {"state": "armed_away"}),
                                        "token": "t"}})
        r = h.call("alarm_panel", {"action": "disarm"})
        self.assertTrue(r["dry_run"], r)
        self.assertNotIn("9876", json.dumps(r))
        self.assertEqual(r["would_send"][0]["body"]["code"], "***")

    def test_ev_charger(self):
        os.environ["CHOREPILOT_SIM_SPEED"] = "100000"
        db = demo_db()
        r = house(self.tmp, "Jordan", db).call("ev_charger", {"action": "start"})
        self.assertIn("queued_for_approval", r)
        h = house(self.tmp, "Alex", db)
        self.assertIn("6..32", h.call("ev_charger", {"action": "start", "current": 48})["error"])
        r = h.call("ev_charger", {"action": "start_charging", "amps": 16})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["policy"], "confirm")
        self.assertEqual(h.asked[0], "Start charging the car on the car_charger at 16 A? It uses "
                                     "a lot of electricity.")
        dt = get_type("ev_charger")
        self.assertEqual(dt.events({"state": "charging"}, {"state": "full"}), ["finished"])
        ops = dt.ha_ops("start_charging", {"current": 16}, "switch.wallbox",
                        {"entities": {"current": "number.wallbox_max_current"}})
        self.assertEqual([o["service"] for o in ops], ["number.set_value", "switch.turn_on"])
        with self.assertRaises(ValueError):
            dt.ha_ops("set_current", {"current": 16}, "switch.wallbox", {})

    def test_water_heater_limits(self):
        dt = get_type("water_heater")
        _, p = dt.validate("set_temperature", {"temperature": 50}, {"unit": "F"})
        self.assertEqual((p["temperature"], p["converted_from"]), (122, "50 C"))
        with self.assertRaises(ValueError) as e:
            dt.validate("set_temperature", {"temperature": 150}, {"unit": "F"})
        self.assertIn("scald", str(e.exception))
        _, p = dt.validate("set_temperature", {"temperature": 150}, {"unit": "F", "max_temp": 150})
        self.assertEqual(p["temperature"], 150)
        with self.assertRaises(ValueError):
            dt.validate("set_temperature", {"temperature": 80}, {"unit": "C"})
        self.assertEqual(dt.ha_ops("set_mode", {"operation_mode": "heat_pump"}, "water_heater.tank", {}),
                         [{"service": "water_heater.set_operation_mode",
                           "data": {"entity_id": "water_heater.tank", "operation_mode": "heat_pump"}}])
        _, p = dt.validate("set_mode", {"mode": "boost"}, {})
        self.assertEqual(p["operation_mode"], "high_demand")

    def test_media_purifier_humidifier_blinds(self):
        db = demo_db()
        r = house(self.tmp, "Riley", db).call("media_player", {"action": "volume", "volume": 95})
        self.assertIn("0..80", r["error"])                                # max_volume 80
        r = house(self.tmp, "Riley", db).call("media_player", {"action": "set_volume", "volume": 25})
        self.assertTrue(r["ok"])
        self.assertEqual(get_type("media_player").ha_ops("set_volume", {"volume": 25}, "media_player.x", {}),
                         [{"service": "media_player.volume_set",
                           "data": {"entity_id": "media_player.x", "volume_level": 0.25}}])
        with self.assertRaises(ValueError):
            get_type("humidifier").validate("set_humidity", {"humidity": 75}, {})
        self.assertTrue(house(self.tmp, "Alex", db).call("air_purifier", {"action": "on", "speed": 40})["ok"])
        r = house(self.tmp, "Alex", db).call("set_cover", {"action": "set_position", "position": 30})
        self.assertEqual(r["status"]["position"], 30)
        self.assertEqual(get_type("cover").ha_ops("set_position", {"position": 30}, "cover.b", {}),
                         [{"service": "cover.set_cover_position",
                           "data": {"entity_id": "cover.b", "position": 30}}])

    def test_router_keeps_the_garage_and_the_front_door_apart(self):
        h = house(self.tmp, "Alex")
        self.assertEqual(h.tool_groups("close the garage door"), {"garage_door"})
        self.assertIn("lock", h.tool_groups("lock the front door"))
        self.assertIn("alarm_panel", h.tool_groups("arm the alarm"))
        self.assertIn("ev_charger", h.tool_groups("charge the car"))
        self.assertEqual(h.tool_groups("set the water heater to 120"), {"water_heater"})
        self.assertIn("valve", h.tool_groups("water the lawn for 10 minutes"))
        self.assertIn("media_player", h.tool_groups("turn the music down"))


# ====================================================================== web dashboard
class TestWeb(P3):
    def setUp(self):
        super().setUp()
        os.environ["CHOREPILOT_SIM_SPEED"] = "60000"
        self.dbp = os.path.join(self.tmp, "chores.db")
        db = demo_db(self.dbp)
        db.set_pin("Alex", "2468")
        db.set_pin("Jordan", "1111")
        db.set_pin("Sam", "1357")
        db.close()
        cfg = json.loads(json.dumps(onboard.PRESETS["demo"]))
        self.audit = os.path.join(self.tmp, "audit.log")
        self.httpd = web.make_server("127.0.0.1", 0, cfg=cfg, db_path=self.dbp,
                                     audit_path=self.audit, notifier=Quiet())
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def req(self, path, body=None, cookie=None, csrf=None, headers=None, method=None):
        h = {"Content-Type": "application/json", **(headers or {})}
        if cookie:
            h["Cookie"] = f"cp_session={cookie}"
        if csrf:
            h["X-ChorePilot-CSRF"] = csrf
        r = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None
                                   else None, headers=h, method=method)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read() or b"{}"), resp.headers
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}"), e.headers

    def login(self, name, pin):
        code, js, hdr = self.req("/api/login", {"name": name, "pin": pin})
        self.assertEqual(code, 200, js)
        c = SimpleCookie(hdr["Set-Cookie"])
        self.assertIn("HttpOnly", hdr["Set-Cookie"])
        self.assertIn("SameSite=Strict", hdr["Set-Cookie"])
        return c["cp_session"].value, js["csrf"]

    def test_page_and_nothing_without_signing_in(self):
        with urllib.request.urlopen(self.base + "/") as r:
            html = r.read().decode()
            self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
        self.assertNotIn("http://", html.replace("http://www.w3.org", ""))   # no CDNs
        self.assertNotIn("https://", html)
        for p in ("/api/board", "/api/devices", "/api/approvals", "/api/audit", "/api/me"):
            self.assertEqual(self.req(p)[0], 401)

    def test_login_lockout_and_host_check(self):
        for _ in range(5):
            self.assertEqual(self.req("/api/login", {"name": "Alex", "pin": "0000"})[0], 403)
        self.assertEqual(self.req("/api/login", {"name": "Alex", "pin": "2468"})[0], 429)
        self.assertEqual(self.req("/", headers={"Host": "evil.example"}, method="GET")[0], 421)

    def test_board_done_approvals_and_csrf(self):
        kid, kcsrf = self.login("Jordan", "1111")
        code, me, _ = self.req("/api/me", cookie=kid)
        self.assertEqual((me["name"], me["role"]), ("Jordan", "kid"))
        db = ChoreDB(self.dbp)
        inst = db.open_instance("empty the dishwasher", assignee="Jordan")
        other = db.open_instance("vacuum the living room", assignee="Riley")
        db.close()
        code, b, _ = self.req("/api/board", cookie=kid)
        self.assertIn(inst["id"], [i["id"] for i in b["open"]])
        self.assertEqual(self.req(f"/api/chores/{inst['id']}/done", {}, cookie=kid)[0], 403)  # no CSRF
        self.assertEqual(self.req(f"/api/chores/{other['id']}/done", {}, cookie=kid, csrf=kcsrf)[0], 403)
        code, r, _ = self.req(f"/api/chores/{inst['id']}/done", {}, cookie=kid, csrf=kcsrf)
        self.assertEqual((code, r["state"]), (200, "pending_approval"))
        self.assertEqual(self.req("/api/audit", cookie=kid)[0], 403)
        code, a, _ = self.req("/api/approvals", cookie=kid)
        self.assertFalse(a["can_decide"])
        aid = a["approvals"][0]["id"]
        self.assertEqual(self.req(f"/api/approvals/{aid}", {"approve": True}, cookie=kid, csrf=kcsrf)[0], 403)
        adult, acsrf = self.login("Sam", "1357")
        code, r, _ = self.req(f"/api/approvals/{aid}", {"approve": True}, cookie=adult, csrf=acsrf,
                              headers={"Origin": "http://evil.example"})
        self.assertEqual(code, 403)                                       # cross-origin
        code, r, _ = self.req(f"/api/approvals/{aid}", {"approve": True}, cookie=adult, csrf=acsrf)
        self.assertEqual((code, r["state"]), (200, "approved"))
        code, au, _ = self.req("/api/audit", cookie=adult)
        self.assertEqual(au["audit"][0]["tool"], "approve")
        self.assertEqual(au["audit"][0]["source"], "web")
        code, d, _ = self.req("/api/devices", cookie=adult)
        names = {x["name"] for x in d["devices"]}
        self.assertTrue({"dishwasher", "garage", "car_charger", "living_room_speaker"} <= names)
        self.assertEqual(self.req("/api/logout", {}, cookie=adult, csrf=acsrf)[0], 200)
        self.assertEqual(self.req("/api/board", cookie=adult)[0], 401)

    def test_kid_device_request_is_approved_and_runs_as_the_adult(self):
        db = ChoreDB(self.dbp)
        from chorepilot.house import House
        from chorepilot.control import RateLimiter
        h = House(json.loads(json.dumps(onboard.PRESETS["demo"])), db=db, person="Jordan",
                  notifier=Quiet(), log=lambda *a: None, watch=False,
                  limiter=RateLimiter(min_start_gap_s=0), audit_path=self.audit,
                  defer=lambda a, op: None)
        r = h.call("water_zone", {"action": "water", "minutes": 5})
        aid = r["queued_for_approval"]
        db.close()
        adult, csrf = self.login("Sam", "1357")
        code, r, _ = self.req(f"/api/approvals/{aid}", {"approve": True}, cookie=adult, csrf=csrf)
        self.assertEqual(code, 200, r)
        self.assertEqual(r["result"]["status"]["state"], "watering")


# ====================================================================== run + packaging
class TestRunAndPackaging(P3):
    def args(self, *a):
        return build_parser().parse_args(["run", *a])

    def test_build_picks_what_is_configured(self):
        cfg = {"household": "x", "devices": {}}
        r, notes = runner.build(cfg, self.args("--no-web"), lambda m: None)
        self.assertEqual(r.components, [])
        self.assertIn("no devices", dict(notes)["events"])
        run_setup(["--owner", "Alex", "--model-url", "none", "--simulated", "--mcp-client", "claude"])
        os.environ["CHOREPILOT_TELEGRAM_TOKEN"] = "1:" + "A" * 35
        r, notes = runner.build(onboard.read_house(), self.args(), lambda m: None)
        self.assertEqual([c.name for c in r.components], ["events", "telegram", "mcp", "web"])
        r, notes = runner.build(onboard.read_house(), self.args("--no-telegram", "--no-mcp"),
                                lambda m: None)
        self.assertEqual([c.name for c in r.components], ["events", "web"])

    def test_supervisor_restarts_a_crashing_part_and_stops(self):
        r = runner.Runner({}, log=lambda m: None)
        calls = []

        def bad():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("boom")
            r.stopped.wait(5)
        r.add("bad", bad)
        import chorepilot.runner as rn
        old = rn.Runner._supervise

        def fast(self, c):           # same loop, short backoff for the test
            while not self.stopped.is_set():
                try:
                    c._start()
                except Exception:
                    c.restarts += 1
                    self.stopped.wait(0.01)
        rn.Runner._supervise = fast
        try:
            r.start()
            for _ in range(200):
                if len(calls) >= 3:
                    break
                time.sleep(0.01)
            r.stop()
        finally:
            rn.Runner._supervise = old
        self.assertEqual(len(calls), 3)
        self.assertEqual(r.components[0].restarts, 2)

    def test_run_starts_the_dashboard_and_events(self):
        run_setup(["--owner", "Alex", "--owner-pin", "2468", "--model-url", "none", "--simulated"])
        logs = []
        r, _ = runner.build(onboard.read_house(), self.args("--web-port", "0", "--no-telegram"),
                            logs.append)
        r.start()
        try:
            for _ in range(300):
                if any("dashboard on" in m for m in logs) and any("[push" in m for m in logs):
                    break
                time.sleep(0.02)
            url = next(m for m in logs if "dashboard on" in m).split("on ")[1].strip()
            with urllib.request.urlopen(url + "healthz", timeout=5) as resp:
                self.assertEqual(json.loads(resp.read()), {"ok": True})
        finally:
            r.stop()

    def test_service_files(self):
        u = runner.systemd_unit(["/x/chorepilot", "run"], env={})
        self.assertIn("ExecStart=/x/chorepilot run", u)
        self.assertIn("Restart=on-failure", u)
        pl = runner.launchd_plist(["/x/chorepilot", "run"], env={"A": "b&c"}, log="/tmp/l")
        self.assertIn("<string>/x/chorepilot</string>", pl)
        self.assertIn("b&amp;c", pl)
        a = build_parser().parse_args(["service", "install", "--kind", "systemd"])
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.tmp, "xdg")
        try:
            a.func(a)
        finally:
            os.environ.pop("XDG_CONFIG_HOME")
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "xdg", "systemd", "user",
                                                    "chorepilot.service")))

    def test_packaging_files_exist(self):
        for p in ("Dockerfile", "docker-compose.yml", "packaging/homeassistant/chorepilot/config.yaml",
                  "packaging/homeassistant/chorepilot/run.sh",
                  "packaging/homeassistant/chorepilot/Dockerfile",
                  "packaging/systemd/chorepilot.service", "packaging/launchd/com.chorepilot.run.plist",
                  "LICENSE"):
            self.assertTrue(os.path.exists(os.path.join(ROOT, p)), p)
        with open(os.path.join(ROOT, "packaging/homeassistant/chorepilot/run.sh")) as f:
            sh = f.read()
        self.assertIn("exec chorepilot run", sh)
        self.assertIn("SUPERVISOR_TOKEN", sh)
        with open(os.path.join(ROOT, "pyproject.toml")) as f:
            self.assertIn('chorepilot = "chorepilot.cli:main"', f.read())

    def test_supervisor_websocket_url_and_secret_precedence(self):
        self.assertEqual(ha_ws_url("http://supervisor/core"), "ws://supervisor/core/websocket")
        self.assertEqual(ha_ws_url("https://ha.example:8123"), "wss://ha.example:8123/api/websocket")
        env.set_secret("ha_token", "from-file")
        self.assertEqual(env.secret("ha_token", "HA_TOKEN"), "from-file")
        os.environ["HA_TOKEN"] = "from-env"
        self.assertEqual(env.secret("ha_token", "HA_TOKEN"), "from-env")
        os.environ.pop("HA_TOKEN")


# ====================================================================== nothing personal
class TestNothingPersonal(unittest.TestCase):
    def test_no_personal_names_or_handles_in_tracked_files(self):
        clt = "/Library/Developer/CommandLineTools/usr/bin/git"
        git = clt if os.path.exists(clt) else shutil.which("git") or "git"
        try:
            untracked = set(subprocess.run([git, "-C", ROOT, "ls-files", "-o", "--exclude-standard"],
                                           capture_output=True, text=True, timeout=20).stdout.split())
        except (OSError, subprocess.SubprocessError):
            untracked = set()
        banned = {ln.strip() for ln in open(os.path.join(ROOT, "tests", "banned-words.sha256"))
                  if ln.strip() and not ln.startswith("#")}
        files = [f for f in os.listdir(ROOT) if os.path.isfile(os.path.join(ROOT, f))]
        for d in ("chorepilot", "tests", "scripts", "packaging", "docs"):
            for base, dirs, names in os.walk(os.path.join(ROOT, d)):
                dirs[:] = [x for x in dirs if x not in ("__pycache__", "src")]
                files += [os.path.relpath(os.path.join(base, n), ROOT) for n in names]
        hits = []
        for f in files:
            if f.startswith("docs/") and f in untracked:      # someone's local notes
                continue
            if not f.endswith((".py", ".md", ".sh", ".toml", ".yml", ".yaml", ".json", ".txt",
                               ".service", ".plist", "Dockerfile", ".gitignore", ".js", ".css",
                               ".html", ".svg", ".webmanifest", ".mjs")):
                continue
            with open(os.path.join(ROOT, f), encoding="utf-8", errors="replace") as fh:
                for w in set(re.findall(r"[a-z0-9]+", fh.read().lower())):
                    if hashlib.sha256(w.encode()).hexdigest() in banned:
                        hits.append(f)
        self.assertEqual(sorted(set(hits)), [])


if __name__ == "__main__":
    unittest.main()
