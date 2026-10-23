"""ChorePilot: device-type plugins + adapters, the policy engine, the chore core (schedules,
rotations, verification by device events, follow-ups, approvals), the House tool router and
the agent loop. Everything is simulated or mocked; nothing touches a real device or network.
"""
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chorepilot import adapters as ad  # noqa: E402
from chorepilot import env, policy  # noqa: E402
from chorepilot import schedule as sch  # noqa: E402
from chorepilot.agent import run as run_agent  # noqa: E402
from chorepilot.chores import ChoreDB, ChoreError, totp  # noqa: E402
from chorepilot.control import RateLimiter  # noqa: E402
from chorepilot.devtypes import all_types, get_type  # noqa: E402
from chorepilot.events import poll_once  # noqa: E402
from chorepilot.house import House, Snapshot  # noqa: E402
from chorepilot.housecfg import PRESETS, from_control, program_fleet_cfg  # noqa: E402


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env = {k: os.environ.get(k) for k in ("CHOREPILOT_CONFIG_DIR", "CHOREPILOT_CACHE_DIR",
                                                    "CHOREPILOT_NOTIFY_DESKTOP", "CHOREPILOT_SIM_SPEED",
                                                    "CHOREPILOT_USER", "CHOREPILOT_BACKEND",
                                                    "DISHSOAKER_BACKEND")}
        os.environ["CHOREPILOT_CONFIG_DIR"] = os.path.join(self.tmp, "cfg")
        os.environ["CHOREPILOT_CACHE_DIR"] = os.path.join(self.tmp, "cache")
        os.environ["CHOREPILOT_NOTIFY_DESKTOP"] = "0"
        for k in ("CHOREPILOT_USER", "CHOREPILOT_BACKEND", "DISHSOAKER_BACKEND", "CHOREPILOT_SIM_SPEED"):
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)


class Quiet:
    def __init__(self):
        self.sent = []

    def send(self, message, log=print):
        self.sent.append(message)


def demo_db(path=":memory:"):
    db = ChoreDB(path)
    for n, r in (("Alex", "owner"), ("Sam", "adult"), ("Jordan", "kid"), ("Riley", "kid")):
        db.add_person(n, r)
    db.add_chore("Dishes", "daily at 19:30", ["Jordan", "Sam", "Riley", "Alex"], rotate="week",
                 verify="dishwasher.finished", followups=["Empty the dishwasher"], points=2)
    db.add_chore("Empty the dishwasher", "on demand", ["Riley", "Jordan", "Sam"], needs_approval=True)
    db.add_chore("Vacuum the living room", "every 3 days", ["Riley", "Jordan"], verify="vacuum.finished")
    db.add_chore("Water the lawn", "weekends", ["Alex", "Sam"], verify="lawn_sprinkler.finished")
    return db


def house(tmp, person="Alex", db=None, answers=None, strong=None, secret=None, **kw):
    cfg = json.loads(json.dumps(PRESETS["demo"]))
    asked = []

    def confirm(q):
        asked.append(q)
        return (answers or [True]).pop(0) if answers is not None else True
    h = House(cfg, db=db or demo_db(), person=person, confirm=confirm,
              strong_confirm=strong or (lambda q, p: False), ask_secret=secret or (lambda q: None),
              notifier=Quiet(), log=lambda *a: None, limiter=RateLimiter(min_start_gap_s=0),
              watch=False, defer=lambda adapter, op: asked.append(("deferred", op)),
              snapshot=Snapshot(os.path.join(tmp, "snap.json")),
              audit_path=os.path.join(tmp, "audit.log"), **kw)
    h.asked = asked
    return h


# ====================================================================== plugins
class TestDeviceTypes(Tmp):
    def test_registry_has_the_types_and_aliases(self):
        names = set(all_types())
        for t in ("dishwasher", "washer", "dryer", "light", "climate", "valve", "coffee_maker",
                  "oven", "fridge", "vacuum", "lock"):
            self.assertIn(t, names)
        self.assertEqual(get_type("thermostat").name, "climate")
        self.assertEqual(get_type("AC").name, "climate")
        self.assertEqual(get_type("sprinkler").name, "valve")
        self.assertEqual(get_type("espresso").name, "coffee_maker")
        with self.assertRaises(KeyError):
            get_type("teleporter")

    def test_every_generic_type_has_a_valid_tool_schema_and_tiers(self):
        for name, t in all_types().items():
            for a in t.actions.values():
                self.assertIn(a.tier, (0, 1, 2, 3), f"{name}.{a.name}")
            if not t.tool:
                continue
            s = t.tool_schema(["x"])["function"]
            self.assertEqual(s["name"], t.tool)
            self.assertIn("action", s["parameters"]["properties"])
            json.dumps(s)

    def test_climate_units_band_and_limits(self):
        c = get_type("climate")
        a, p = c.validate(None, {"temperature": 72}, {"unit": "F"})
        self.assertEqual((a, p["temperature"]), ("set_temperature", 72))
        self.assertEqual(c.tier_for(a, p, {"unit": "F"}), 1)
        a, p = c.validate("set", {"temp": "80"}, {"unit": "F"})
        self.assertEqual(c.tier_for(a, p, {"unit": "F"}), 2)        # outside comfort -> confirm
        a, p = c.validate(None, {"temperature": 72}, {"unit": "C"})  # "72" in a Celsius house
        self.assertEqual(p["temperature"], 22.0)
        self.assertIn("72 F", p["converted_from"])
        with self.assertRaises(ValueError):
            c.validate(None, {"temperature": 99}, {"unit": "F"})
        _, p = c.validate("set_mode", {"mode": "AC"}, {})
        self.assertEqual(p["hvac_mode"], "cool")

    def test_valve_duration_cap_and_ha_close_is_scheduled(self):
        v = get_type("valve")
        with self.assertRaises(ValueError):
            v.validate("water", {"minutes": 45}, {"max_minutes": 30})
        _, p = v.validate("water", {"duration": "10"}, {"max_minutes": 30})
        ops = v.ha_ops("water", p, "valve.front_lawn", {})
        self.assertEqual(ops[0]["service"], "valve.open_valve")
        self.assertEqual((ops[1]["service"], ops[1]["delay_s"]), ("valve.close_valve", 600))
        ops = v.ha_ops("water", p, "switch.zone_1", {})
        self.assertEqual(ops[0]["service"], "switch.turn_on")
        ops = v.ha_ops("water", p, "switch.zone_1",
                       {"duration_service": {"service": "rachio.start_watering", "unit": "s"}})
        self.assertEqual(ops, [{"service": "rachio.start_watering",
                                "data": {"entity_id": "switch.zone_1", "duration": 600}}])

    def test_sim_adapter_valve_closes_by_itself_and_emits_finished(self):
        v = get_type("valve")
        a = ad.SimAdapter("lawn", v, {}, path=os.path.join(self.tmp, "v.json"), speed=6000)
        before = a.status()
        a.apply(a.plan("water", {"minutes": 1}))
        mid = a.status()
        self.assertEqual(mid["state"], "watering")
        self.assertEqual(v.events(before, mid), ["started"])
        time.sleep(0.02)
        end = a.status()
        self.assertEqual(end["state"], "idle")
        self.assertEqual(v.events(mid, end), ["finished"])

    def test_oven_limits_hc_body_and_interlocks(self):
        o = get_type("oven")
        with self.assertRaises(ValueError):
            o.validate("preheat", {"temperature": 600}, {"unit": "F"})
        a, p = o.validate("preheat", {"temperature": 400}, {"unit": "F"})
        self.assertEqual(p["duration_minutes"], 60)                  # auto-off is always set
        body = o.hc_ops(a, p, {})[0]["body"]["data"]
        self.assertEqual(body["key"], "Cooking.Oven.Program.HeatingMode.TopBottomHeating")
        opts = {x["key"]: x["value"] for x in body["options"]}
        self.assertEqual(opts["Cooking.Oven.Option.SetpointTemperature"], 204)
        self.assertEqual(opts["BSH.Common.Option.Duration"], 3600)
        self.assertTrue(opts["Cooking.Oven.Option.FastPreHeat"])
        self.assertIn("door", o.interlock(a, p, {"state": "off", "door": "open"}, {}))
        self.assertIn("remote start", o.interlock(a, p, {"state": "off", "remote_start_allowed": False}, {}))
        self.assertIn("own controls", o.interlock(a, p, {"state": "off", "local_control": True}, {}))
        self.assertIn("switched off", o.interlock(a, p, {"state": "off"}, {"allow_remote_preheat": False}))

    def test_coffee_hc_body_and_tank(self):
        c = get_type("coffee_maker")
        a, p = c.validate("make", {"drink": "flat white", "strength": "strong", "size": 120}, {})
        body = c.hc_ops(a, p, {})[0]["body"]["data"]
        self.assertEqual(body["key"], "ConsumerProducts.CoffeeMaker.Program.Beverage.Cappuccino")
        self.assertIn({"key": "ConsumerProducts.CoffeeMaker.Option.BeanAmount",
                       "value": "ConsumerProducts.CoffeeMaker.EnumType.BeanAmount.Strong"}, body["options"])
        self.assertIn("tank", c.interlock(a, p, {"state": "ready", "water_tank_empty": True}, {}))

    def test_fridge_food_safe_range_and_door_alarm(self):
        f = get_type("fridge")
        with self.assertRaises(ValueError):
            f.validate("set_temperature", {"compartment": "fridge", "temperature": 50}, {"unit": "F"})
        a = ad.SimAdapter("fridge", f, {"unit": "F"}, path=os.path.join(self.tmp, "f.json"), speed=600)
        s0 = a.status()
        a.set(freezer_door="open")
        a.status()                      # starts the door timer
        time.sleep(0.25)                # > 2 simulated minutes at 600x
        s1 = a.status()
        self.assertEqual(s1["state"], "alarm")
        self.assertEqual(f.events(s0, s1), ["door_alarm"])

    def test_param_codes_from_the_model_are_rejected(self):
        with self.assertRaises(ValueError):
            get_type("lock").validate("unlock", {"pin": "1234"}, {})

    def test_user_plugin_dir_is_loaded(self):
        import chorepilot.devtypes as dt
        d = os.path.join(os.environ["CHOREPILOT_CONFIG_DIR"], "devtypes")
        os.makedirs(d)
        with open(os.path.join(d, "kettle.py"), "w") as f:
            f.write("from chorepilot.devtypes import register\nfrom chorepilot.devtypes.base import "
                    "Action, DeviceType\n@register\nclass Kettle(DeviceType):\n    name='kettle'\n"
                    "    tool='kettle'\n    actions={'boil': Action('boil','Boil', tier=2, uses='heat')}\n")
        dt._loaded = False
        try:
            self.assertEqual(get_type("kettle").actions["boil"].tier, 2)
        finally:
            dt._REGISTRY.pop("kettle", None)


class FakeHTTP:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, headers=None, body=None, timeout=15):
        self.calls.append((method, url, body))
        for (m, suffix), resp in self.routes.items():
            if m == method and url.endswith(suffix):
                return resp
        return 404, None


class TestAdapters(Tmp):
    def test_ha_light_and_climate(self):
        http = FakeHTTP({("GET", "/api/states/light.kitchen"): (200, {"state": "on", "attributes": {"brightness": 128}}),
                         ("POST", "/api/services/light/turn_on"): (200, [])})
        a = ad.make_adapter("kitchen", get_type("light"), {"entity": "light.kitchen"}, backend="ha",
                            http=http, url="http://ha", token="t")
        self.assertEqual(a.status()["brightness"], 50)
        ops = a.plan("turn_on", {"brightness": 30})
        self.assertEqual(ops[0]["url"], "http://ha/api/services/light/turn_on")
        self.assertEqual(ops[0]["body"], {"entity_id": "light.kitchen", "brightness_pct": 30})
        a.apply(ops)
        self.assertEqual(http.calls[-1][0], "POST")
        c = get_type("climate", {"unit": "F"})
        st = c.ha_status({"state": "cool", "attributes": {"temperature": 72, "current_temperature": 75,
                                                          "hvac_action": "cooling"}}, {})
        self.assertEqual((st["target"], st["current"], st["hvac_action"]), (72, 75, "cooling"))

    def test_ha_delayed_close_goes_to_defer(self):
        http = FakeHTTP({("POST", "/api/services/valve/open_valve"): (200, [])})
        later = []
        a = ad.make_adapter("lawn", get_type("valve"), {"entity": "valve.lawn"}, backend="matter",
                            http=http, url="http://ha", token="t", defer=lambda adp, op: later.append(op))
        self.assertEqual(a.name, "matter")
        res = a.apply(a.plan("water", {"minutes": 5}))
        self.assertEqual(res["scheduled"], 1)
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(later[0]["url"], "http://ha/api/services/valve/close_valve")

    def test_deferred_store_runner(self):
        store = ad.DeferredStore(os.path.join(self.tmp, "d.json"))
        jid = store.add("lawn", {"method": "POST", "url": "u", "body": {}, "delay_s": 0.01})
        sent = []

        class A:
            def send(self, op):
                sent.append(op)
        self.assertEqual(ad.run_deferred(jid, lambda n: A(), sleep=time.sleep, store=store), "sent")
        self.assertEqual(sent[0]["url"], "u")
        self.assertEqual(store.all(), {})

    def test_homeconnect_oven_status_and_plan(self):
        hc = {("GET", "/api/homeappliances"): (200, {"data": {"homeappliances": [
                  {"haId": "OVEN1", "type": "Oven", "connected": True}]}}),
              ("GET", "/OVEN1/status"): (200, {"data": {"status": [
                  {"key": "BSH.Common.Status.OperationState", "value": "BSH.Common.EnumType.OperationState.Ready"},
                  {"key": "BSH.Common.Status.DoorState", "value": "BSH.Common.EnumType.DoorState.Closed"},
                  {"key": "BSH.Common.Status.RemoteControlStartAllowed", "value": True}]}}),
              ("GET", "/programs/active"): (404, None)}
        a = ad.make_adapter("oven", get_type("oven"), {}, backend="homeconnect", http=FakeHTTP(hc),
                            token="tok")
        st = a.status()
        self.assertEqual((st["state"], st["door"], st["remote_start_allowed"]), ("off", "closed", True))
        ops = a.plan("preheat", {"temperature": 180, "mode": "convection", "duration_minutes": 30, "unit": "C"})
        self.assertEqual(ops[0]["method"], "PUT")
        self.assertTrue(ops[0]["url"].endswith("/OVEN1/programs/active"))

    def test_mqtt_lock_ops(self):
        a = ad.make_adapter("front", get_type("lock"), {"prefix": "door/lock/front"}, backend="mqtt")
        self.assertEqual(a.plan("lock", {}), [{"topic": "door/lock/front/command", "payload": "LOCK"}])


# ====================================================================== policy
class TestPolicy(unittest.TestCase):
    def test_matrix(self):
        owner, adult, kid, guest = ({"name": n, "role": r} for n, r in
                                    (("M", "owner"), ("A", "adult"), ("S", "kid"), ("G", "guest")))
        d = policy.decide
        self.assertEqual(d(guest, 0).outcome, "allow")
        self.assertEqual(d(kid, 1).outcome, "allow")
        self.assertEqual(d(kid, 1, settings={"kids_tier1": False}).outcome, "approval")
        self.assertEqual(d(guest, 1).outcome, "deny")
        self.assertEqual(d(adult, 2).outcome, "confirm")
        self.assertEqual(d(adult, 2, strong=True).outcome, "strong_confirm")
        self.assertEqual(d(kid, 2).outcome, "approval")
        self.assertEqual(d(guest, 2).outcome, "deny")
        self.assertEqual(d(adult, 3).outcome, "deny")
        self.assertEqual(d(kid, 3).outcome, "deny")
        self.assertEqual(d(owner, 3).outcome, "second_factor")
        self.assertEqual(d(owner, 3, source="mcp").outcome, "deny")      # never an agent alone
        self.assertEqual(d(owner, 3, source="schedule").outcome, "deny")
        self.assertEqual(d(owner, 2, source="mcp").outcome, "confirm")   # the human still says yes
        self.assertEqual(d(None, 1).outcome, "deny")                     # unknown = guest


# ====================================================================== schedules + chores
class TestSchedule(unittest.TestCase):
    def test_parse_and_describe(self):
        cases = {"daily at 8pm": "daily at 20:00", "every monday and thursday at 18:00":
                 "every Monday and Thursday at 18:00", "weekdays": "weekdays", "weekends at 7am":
                 "weekends at 07:00", "every 3 days": "every 3 days", "every other tuesday":
                 "every other Tuesday", "3 days after done": "3 days after it's done",
                 "on demand": "on demand", "FREQ=WEEKLY;BYDAY=MO,FR;BYHOUR=9": "every Monday and Friday at 09:00"}
        for text, want in cases.items():
            self.assertEqual(sch.describe(sch.parse(text)), want, text)
        with self.assertRaises(ValueError):
            sch.parse("whenever the moon is full")

    def test_next_due(self):
        anchor = datetime(2026, 10, 19, 9, 0)                 # a Monday
        s = sch.parse("every monday and thursday at 18:00")
        self.assertEqual(sch.next_due(s, datetime(2026, 10, 19, 19, 0), anchor), datetime(2026, 10, 22, 18, 0))
        s = sch.parse("every other monday at 08:00")
        self.assertEqual(sch.next_due(s, datetime(2026, 10, 19, 9, 0), anchor), datetime(2026, 11, 2, 8, 0))
        s = sch.parse("every 3 days")
        self.assertEqual(sch.next_due(s, datetime(2026, 10, 20, 1, 0), anchor), datetime(2026, 10, 22, 0, 0))
        self.assertIsNone(sch.next_due(sch.parse("on demand"), anchor))


class TestChores(unittest.TestCase):
    def setUp(self):
        self.db = demo_db()

    def test_fuzzy_lookup(self):
        self.assertEqual(self.db.chore("dishes")["title"], "Dishes")
        self.assertEqual(self.db.chore("empty the dishwasher")["title"], "Empty the dishwasher")
        self.assertEqual(self.db.chore("vacuum")["title"], "Vacuum the living room")
        self.assertEqual(self.db.chore("lawn")["title"], "Water the lawn")
        with self.assertRaises(ChoreError):
            self.db.chore("feed the dragon")

    def test_week_rotation_and_override(self):
        self.assertEqual(self.db.week_assignee("dishes")["name"], "Jordan")
        nxt = self.db.week_assignee("dishes", time.time() + 7 * 86400)
        self.assertEqual(nxt["name"], "Sam")
        self.db.assign("dishes", "Riley", week=True)
        self.assertEqual(self.db.who_has("dishes")["this_week"], "Riley")
        self.db.swap("dishes", "Riley", "Alex")
        self.assertEqual(self.db.who_has("dishes")["this_week"], "Alex")

    def test_round_robin_and_least_points(self):
        a = self.db.open_instance("empty the dishwasher")["assignee"]
        b = self.db.open_instance("empty the dishwasher")["assignee"]
        c = self.db.open_instance("empty the dishwasher")["assignee"]
        self.assertEqual([a, b, c], ["Riley", "Jordan", "Sam"])
        self.db.add_chore("Mop", None, ["Sam", "Alex"], strategy="least_points", points=3)
        self.db.complete("mop", by="Sam")
        self.assertEqual(self.db.next_assignee("mop")["name"], "Alex")

    def test_device_event_completes_credits_starter_and_opens_followup(self):
        self.db.record_event("dishwasher", "started", "dishwasher", actor="Alex")
        lines = self.db.apply_event("dishwasher", "finished", "dishwasher")
        self.assertIn("'Dishes' done by Alex (covering for Jordan), verified by the dishwasher.", lines)
        self.assertIn("'Empty the dishwasher' is now open for Riley.", lines)
        done = self.db.instances("done", chore="dishes")[0]
        self.assertEqual(done["verified_by"], "device:dishwasher.finished")
        self.assertEqual(self.db.points()["Alex"], 2)
        # finishing again does not stack a second "empty it"
        self.db.apply_event("dishwasher", "finished", "dishwasher")
        self.assertEqual(len(self.db.instances("open", chore="empty the dishwasher")), 1)

    def test_event_without_starter_credits_the_assignee(self):
        lines = self.db.apply_event("vacuum", "finished", "vacuum")
        self.assertTrue(lines[0].startswith("'Vacuum the living room' done by Riley"), lines)

    def test_kid_self_report_needs_approval(self):
        self.db.open_instance("empty the dishwasher")              # Riley's
        res = self.db.complete("empty dishwasher", by="Riley")
        self.assertEqual(res["instance"]["state"], "pending_approval")
        aid = res["approval"]["id"]
        with self.assertRaises(ChoreError):
            self.db.decide(aid, "Jordan")                             # a kid can't approve
        self.db.decide(aid, "Sam")
        self.assertEqual(self.db.instance(res["instance"]["id"])["state"], "approved")
        self.assertEqual(self.db.points()["Riley"], 1)

    def test_materialise_opens_due_and_marks_missed(self):
        t = [datetime(2026, 10, 19, 6, 0).timestamp()]
        db = ChoreDB(":memory:", clock=lambda: t[0])
        db.add_person("Sam", "adult")
        db.add_chore("Bins", "daily at 07:00", ["Sam"])
        t[0] = datetime(2026, 10, 19, 8, 0).timestamp()
        first = db.materialise()
        self.assertEqual(len(first), 1)
        self.assertEqual(db.materialise(), [])                     # idempotent
        t[0] = datetime(2026, 10, 21, 8, 0).timestamp()
        db.materialise()
        states = [i["state"] for i in db.instances(chore="bins")]
        self.assertEqual(states, ["missed", "open"])

    def test_pin_totp_quiet_hours(self):
        self.db.set_pin("Alex", "2468")
        self.assertTrue(self.db.check_pin("Alex", "2468"))
        self.assertFalse(self.db.check_pin("Alex", "1357"))
        self.assertNotIn("2468", self.db.person("Alex")["pin_hash"])
        secret, uri = self.db.set_totp("Alex")
        self.assertTrue(uri.startswith("otpauth://totp/"))
        self.assertTrue(self.db.check_totp("Alex", totp(secret)))
        self.assertFalse(self.db.check_totp("Alex", "000000") and totp(secret) != "000000")
        self.db._x("UPDATE people SET quiet_hours='00:00-23:59' WHERE name='Jordan'")
        self.assertTrue(self.db.in_quiet_hours("Jordan"))

    def test_rfc6238_vector(self):
        # RFC 6238 appendix B, SHA-1 secret "12345678901234567890" at T=59 -> 94287082 (8 digits)
        import base64
        s = base64.b32encode(b"12345678901234567890").decode()
        self.assertEqual(totp(s, at=59, digits=8), "94287082")

    def test_db_file_is_private(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "c.db")
            ChoreDB(p).close()
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        finally:
            shutil.rmtree(d)


# ====================================================================== the House
class TestHouse(Tmp):
    def test_router(self):
        h = house(self.tmp)
        self.assertEqual(h.tool_groups("who has dishes this week?"), {"chores"})
        self.assertEqual(h.tool_groups("I emptied the dishwasher"), {"chores"})
        self.assertEqual(h.tool_groups("wash my dishes"), {"program"})
        self.assertEqual(h.tool_groups("set the AC to 72"), {"climate"})
        self.assertEqual(h.tool_groups("water the lawn for 10 minutes"), {"valve"})
        self.assertEqual(h.tool_groups("lock the front door"), {"lock"})
        self.assertGreater(len(h.tool_groups("hello")), 5)
        names = [t["function"]["name"] for t in h.tools("set the AC to 72")]
        self.assertEqual(names, ["get_status", "set_climate"])

    def test_ac_inside_band_no_confirm_outside_band_confirm(self):
        h = house(self.tmp, answers=[False])
        r = h.call("set_climate", {"temperature": 72})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["status"]["target"], 72)
        self.assertEqual(h.asked, [])
        r = h.call("set_climate", {"temperature": 85})
        self.assertTrue(r.get("declined"), r)
        self.assertIn("comfort band", h.asked[0])

    def test_sprinkler_confirm_and_kid_approval_queue(self):
        h = house(self.tmp)
        r = h.call("water_zone", {"action": "water", "minutes": 10})
        self.assertTrue(r["ok"], r)
        self.assertIn("10 minutes", h.asked[0])
        kid = house(self.tmp, person="Jordan", db=h.db)
        h.call("water_zone", {"action": "stop"})
        r = kid.call("water_zone", {"action": "water", "minutes": 5})
        self.assertFalse(r["ok"])
        aid = r["queued_for_approval"]
        self.assertEqual(kid.devices["lawn_sprinkler"].adapter.status()["state"], "idle")
        h.limiter = RateLimiter(min_start_gap_s=3600)       # a start just happened: too soon
        h.call("water_zone", {"action": "water", "minutes": 1})
        h.call("water_zone", {"action": "stop"})
        res = h.approve(aid)
        self.assertEqual(res["state"], "pending", res)          # stays queued, can be retried
        h.limiter = RateLimiter(min_start_gap_s=0)
        res = h.approve(aid)
        self.assertTrue(res["ok"], res)
        self.assertEqual(h.devices["lawn_sprinkler"].adapter.status()["state"], "watering")

    def test_oven_needs_the_typed_phrase(self):
        h = house(self.tmp, yes=True)                                   # --yes does NOT cover it
        r = h.call("oven", {"action": "preheat", "temperature": 400})
        self.assertTrue(r.get("declined"), r)
        h = house(self.tmp, strong=lambda q, phrase: phrase == "PREHEAT")
        r = h.call("oven", {"action": "preheat", "temperature": 400})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["status"]["state"], "heating")
        r = h.call("oven", {"action": "preheat", "temperature": 350})
        self.assertIn("already on", r["error"])

    def test_unlock_owner_pin_only(self):
        db = demo_db()
        self.assertIn("only the owner", house(self.tmp, "Sam", db).call(
            "door_lock", {"action": "unlock"})["error"])
        self.assertIn("set-pin", house(self.tmp, "Alex", db).call("door_lock", {"action": "unlock"})["error"])
        db.set_pin("Alex", "2468")
        r = house(self.tmp, "Alex", db, secret=lambda q: "1111").call("door_lock", {"action": "unlock"})
        self.assertIn("wrong", r["error"])
        r = house(self.tmp, "Alex", db, source="mcp", secret=lambda q: "2468").call(
            "door_lock", {"action": "unlock"})
        self.assertIn("external agent", r["error"])
        r = house(self.tmp, "Alex", db, secret=lambda q: "2468").call("door_lock", {"action": "unlock"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["status"]["state"], "unlocked")
        r = house(self.tmp, "Jordan", db).call("door_lock", {"action": "lock"})   # locking: tier 1
        self.assertTrue(r["ok"], r)
        r = house(self.tmp, "Alex", db, secret=lambda q: "2468").call(
            "door_lock", {"action": "unlock", "code": "2468"})
        self.assertIn("never accepted from the model", r["error"])

    def test_wrong_codes_lock_out(self):
        db = demo_db()
        db.set_pin("Alex", "2468")
        for _ in range(3):
            house(self.tmp, "Alex", db, secret=lambda q: "0000").call("door_lock", {"action": "unlock"})
        r = house(self.tmp, "Alex", db, secret=lambda q: "2468").call("door_lock", {"action": "unlock"})
        self.assertIn("too many", r["error"])

    def test_program_appliance_kid_queued_adult_confirmed_and_event_chain(self):
        os.environ["CHOREPILOT_SIM_SPEED"] = "60000"
        db = demo_db()
        kid = house(self.tmp, "Jordan", db)
        r = kid.call("start_program", {"appliance": "dishwasher", "program": "quick"})
        self.assertIn("queued_for_approval", r)
        h = house(self.tmp, "Alex", db)
        r = h.call("start_program", {"appliance": "dishwasher", "program": "quick"})
        self.assertTrue(r["ok"], r)
        self.assertTrue(any("dishwasher" in q for q in h.asked))       # the Bridge asked via policy
        time.sleep(0.1)                                                 # 35 min at 60000x
        res = poll_once(h, log=lambda *a: None)
        self.assertEqual([(d, e) for d, e, _ in res], [("dishwasher", "finished")])
        lines = res[0][2]
        self.assertIn("'Dishes' done by Alex (covering for Jordan), verified by the dishwasher.", lines)
        self.assertIn("'Empty the dishwasher' is now open for Riley.", lines)

    def test_small_model_option_fill_ins_are_dropped(self):
        h = house(self.tmp, "Alex", dry_run=True)
        r = h.call("start_program", {"appliance": "dishwasher", "program": "auto", "extra_dry": False,
                                     "temperature": "cold", "spin": "0", "dryness": "iron",
                                     "duration_minutes": 20, "speed_perfect": True})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["would_send"][0]["options"], {"speed_perfect": True})
        r = h.call("start_program", {"appliance": "washer", "program": "cotton", "temperature": "40"})
        self.assertEqual(r["would_send"][0]["options"], {"temperature": "40"})

    def test_vacuum_finish_verifies_chore(self):
        os.environ["CHOREPILOT_SIM_SPEED"] = "60000"
        h = house(self.tmp, "Riley")
        self.assertTrue(h.call("vacuum", {"action": "start"})["ok"])
        time.sleep(0.06)
        res = poll_once(h, log=lambda *a: None)
        self.assertEqual(res[0][:2], ("vacuum", "finished"))
        self.assertTrue(res[0][2][0].startswith("'Vacuum the living room' done by Riley"))

    def test_chore_tools(self):
        h = house(self.tmp, "Sam")
        r = h.call("who_has", {"chore": "dishes"})
        self.assertEqual((r["chore"], r["this_week"]), ("Dishes", "Jordan"))
        r = h.call("assign_chore", {"chore": "dishes", "person": "Riley", "whole_week": True})
        self.assertTrue(r["ok"], r)
        self.assertEqual(h.call("who_has", {"chore": "dishes"})["this_week"], "Riley")
        r = house(self.tmp, "Jordan", h.db).call("assign_chore", {"chore": "dishes", "person": "Riley"})
        self.assertIn("only an adult", r["error"])
        r = house(self.tmp, "Jordan", h.db).call("complete_chore", {"chore": "dishes", "person": "Riley"})
        self.assertIn("own chores", r["error"])
        h.db.open_instance("empty the dishwasher")
        r = h.call("remind", {"person": "Riley"})
        self.assertEqual(r["reminded"], ["Riley"])
        self.assertTrue(any("Empty the dishwasher" in m for m in h.notifier.sent))
        r = h.call("list_chores", {})
        self.assertEqual(r["chores"][0]["chore"], "Empty the dishwasher")
        r = h.call("complete_chore", {"chore": "empty the dishwasher", "person": "me"})
        self.assertEqual((r["done_by"], r["state"]), ("Sam", "done"))

    def test_status_all_prompt_dry_run_and_audit(self):
        h = house(self.tmp, dry_run=True)
        st = h.call("get_status", {})
        self.assertIn("thermostat", st["devices"])
        self.assertIn("dishwasher", st["devices"])
        self.assertEqual(h.call("get_status", {"appliance": "AC"})["device"], "thermostat")
        r = h.call("set_light", {"action": "turn_on", "brightness": 40})
        self.assertTrue(r["dry_run"])
        self.assertEqual(h.devices["living_room_light"].adapter.status()["state"], "off")
        p = h.system_prompt("set the AC to 72")
        self.assertIn("thermostat", p)
        self.assertIn("set_climate temperature=72", p)
        self.assertNotIn("Open chores", p)
        self.assertIn("Weekly rotations: Dishes: Jordan", h.system_prompt("who has dishes this week?"))
        log = os.path.join(self.tmp, "audit.log")
        self.assertEqual(stat.S_IMODE(os.stat(log).st_mode), 0o600)
        recs = [json.loads(x) for x in open(log)]
        self.assertEqual(recs[-1]["tool"], "set_light")
        self.assertEqual(recs[-1]["who"], "Alex")


class TestAgent(Tmp):
    def test_agent_loop_with_a_scripted_model(self):
        h = house(self.tmp, "Alex")
        script = [{"tool_calls": [{"id": "1", "function": {"name": "set_climate",
                                                           "arguments": json.dumps({"temperature": 72})}}]},
                  {"content": "The AC is set to 72."}]
        seen = []

        def fake_chat(url, messages, tools=None, model=None):
            seen.append([t["function"]["name"] for t in tools])
            return {"choices": [{"message": script.pop(0)}]}
        answer, trace = run_agent("set the AC to 72", h, "http://x", log=lambda *a: None, chat_fn=fake_chat)
        self.assertEqual(answer, "The AC is set to 72.")
        self.assertEqual(seen[0], ["get_status", "set_climate"])
        self.assertEqual(h.devices["thermostat"].adapter.status()["target"], 72)


# ====================================================================== rename + config
class TestRename(Tmp):
    def test_legacy_env_names_still_work(self):
        os.environ.pop("CHOREPILOT_SIM_SPEED", None)
        os.environ["DISHSOAKER_SIM_SPEED"] = "123"
        try:
            self.assertEqual(env.getenv("SIM_SPEED"), "123")
            os.environ["CHOREPILOT_SIM_SPEED"] = "456"
            self.assertEqual(env.getenv("SIM_SPEED"), "456")
        finally:
            os.environ.pop("DISHSOAKER_SIM_SPEED", None)

    def test_config_dir_migration_copies_once(self):
        old, new = os.path.join(self.tmp, "old"), os.path.join(self.tmp, "new")
        os.makedirs(old)
        with open(os.path.join(old, "control.json"), "w") as f:
            f.write("{}")
        self.assertTrue(env._migrate(old, new))
        self.assertTrue(os.path.exists(os.path.join(new, "control.json")))
        self.assertTrue(os.path.exists(os.path.join(new, "MIGRATED-FROM-DISHSOAKER.txt")))
        self.assertTrue(os.path.exists(os.path.join(old, "control.json")))   # old left in place
        self.assertFalse(env._migrate(old, new))                              # only once

    def test_control_json_becomes_house_devices(self):
        h = from_control({"backend": "homeconnect", "appliances": {"dishwasher": {"model": "SMV6ZCX42E"}}})
        self.assertEqual(h["devices"]["dishwasher"]["type"], "dishwasher")
        f = program_fleet_cfg(h)
        self.assertEqual((f["backend"], f["default"]), ("homeconnect", "dishwasher"))
        self.assertIsNone(program_fleet_cfg({"devices": {"lamp": {"type": "light"}}}))

    def test_cli_aliases(self):
        from chorepilot import cli
        self.assertTrue(callable(cli.wash_main))
        p = cli.build_parser()
        self.assertEqual(p.prog, "chorepilot")
        a = p.parse_args(["chores", "who", "dishes"])
        self.assertEqual((a.action, a.chore), ("who", "dishes"))


if __name__ == "__main__":
    unittest.main()
