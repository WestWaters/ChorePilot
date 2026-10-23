"""Bosch Home Connect appliances: the SMV6ZCX42E / WAT286H9SG / WTW876H8SG profiles, the
multi-appliance fleet, model-specific validation, the Home Connect / HA request bodies for
these models, the simulator's laundry behaviour, and the washer -> dryer chain.

Nothing here touches a real appliance or the network.
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chorepilot import appliance as ap  # noqa: E402
from chorepilot import control as ct  # noqa: E402
from chorepilot import machines as mc  # noqa: E402

PRESET = ap.PRESETS["bosch"]

# Program keys that exist: the public docs (api-docs.home-connect.com/programs-and-options)
# plus aiohomeconnect's ProgramKey enum (keys real devices report). Every key a profile uses
# must be one of these.
KNOWN_KEYS = set("""
Dishcare.Dishwasher.Program.Auto2 Dishcare.Dishwasher.Program.Eco50
Dishcare.Dishwasher.Program.Intensiv70 Dishcare.Dishwasher.Program.NightWash
Dishcare.Dishwasher.Program.Glas40 Dishcare.Dishwasher.Program.Kurz60
Dishcare.Dishwasher.Program.Quick45 Dishcare.Dishwasher.Program.MachineCare
Dishcare.Dishwasher.Program.Normal65 Dishcare.Dishwasher.Program.PreRinse
LaundryCare.Washer.Program.Cotton LaundryCare.Washer.Program.EasyCare
LaundryCare.Washer.Program.Mix LaundryCare.Washer.Program.DelicatesSilk
LaundryCare.Washer.Program.Wool LaundryCare.Washer.Program.Sensitive
LaundryCare.Washer.Program.Curtains LaundryCare.Washer.Program.DownDuvet.Duvet
LaundryCare.Washer.Program.Super153045.Super1530 LaundryCare.Washer.Program.Super153045.Super15
LaundryCare.Washer.Program.Rinse.RinseSpinDrain LaundryCare.Washer.Program.Spin.SpinDrain
LaundryCare.Washer.Program.DrumClean
LaundryCare.Dryer.Program.Cotton LaundryCare.Dryer.Program.Synthetic LaundryCare.Dryer.Program.Mix
LaundryCare.Dryer.Program.Delicates LaundryCare.Dryer.Program.WoolFinish
LaundryCare.Dryer.Program.TimeCold LaundryCare.Dryer.Program.TimeWarm
LaundryCare.Dryer.Program.BusinessShirts LaundryCare.Dryer.Program.Blankets
LaundryCare.Dryer.Program.DownFeathers LaundryCare.Dryer.Program.Outdoor
LaundryCare.Dryer.Program.Towels LaundryCare.Dryer.Program.Super40 LaundryCare.Dryer.Program.Hygiene
""".split())


def fleet(tmp, speed=600, **per):
    """The preset, simulated, with each appliance's state file in `tmp`."""
    members = {}
    for nick, e in PRESET["appliances"].items():
        m = mc.machine_for(model=e["model"])
        members[nick] = ap.SimulatedBackend(path=os.path.join(tmp, f"{nick}.json"), speed=speed,
                                            machine=m, appliance=nick, **per.get(nick, {}))
    return ap.Fleet(members, "dishwasher", {"washing machine": "washer", "tumble dryer": "dryer"})


def bridge(f, **kw):
    kw.setdefault("ask", lambda q: True)
    kw.setdefault("limiter", ct.RateLimiter(min_start_gap_s=0, max_actions=100))
    return ct.Bridge(f, log=lambda *a: None, **kw)


class Sink:
    def __init__(self):
        self.sent = []

    def send(self, msg, log=None):
        self.sent.append(msg)


# ------------------------------------------------------------------ profiles
class TestProfiles(unittest.TestCase):
    def test_three_models(self):
        dw, wm, td = (mc.machine_for(model=m) for m in ("SMV6ZCX42E", "WAT286H9SG", "WTW876H8SG"))
        self.assertEqual((dw.kind, wm.kind, td.kind), ("dishwasher", "washer", "dryer"))
        # SG spec: Economy, Auto 45-65, Intensive, Short 60, Quick 45, Glass 40, Silence (+Machine Care)
        self.assertEqual(set(dw.programs), {"auto", "eco", "intensive", "silence", "glass", "short",
                                            "quick", "machine_care"})
        # SG spec: no half load / hygiene on this model; manual: delay start up to 24 h
        self.assertEqual(set(dw.options), {"extra_dry", "intensive_zone", "speed_perfect",
                                           "delay_minutes"})
        self.assertEqual(dw.options["delay_minutes"].hc, "BSH.Common.Option.StartInRelative")
        # washer manual: temperatures and spin speeds on the panel
        self.assertEqual(wm.options["temperature"].values, ("cold", "20", "30", "40", "60", "90"))
        self.assertEqual(wm.options["spin"].values, ("0", "400", "600", "800", "1200", "1400"))
        self.assertEqual(wm.options["finish_in_minutes"].hc, "BSH.Common.Option.FinishInRelative")
        # dryer manual: only three drying targets; no ExtraDry on this model
        self.assertEqual(td.options["dryness"].values, ("iron", "cupboard", "cupboard_plus"))
        self.assertEqual(td.options["dryness"].hc_values["cupboard_plus"],
                         "LaundryCare.Dryer.EnumType.DryingTarget.CupboardDryPlus")
        self.assertEqual(td.programs["cotton"].minutes, 128)       # manual p.56
        self.assertEqual((td.options["duration_minutes"].lo, td.options["duration_minutes"].hi,
                          td.options["duration_minutes"].step), (20, 210, 10))
        self.assertIsNone(dw.child_lock)                           # SG spec: no child safety device
        self.assertTrue(wm.child_lock and td.child_lock)

    def test_every_key_exists(self):
        for model in mc.MODELS:
            m = mc.machine_for(model=model)
            for p in m.programs.values():
                self.assertIn(p.hc, KNOWN_KEYS, f"{model} {p.name}")
            for o in m.options.values():
                for v in o.hc_values.values():
                    self.assertRegex(v, r"^LaundryCare\.(Washer|Dryer)\.EnumType\.")
            self.assertTrue(m.sources and m.remote_start and m.remote_start_disarms)

    def test_unknown_model(self):
        with self.assertRaises(KeyError):
            mc.machine_for(model="WAX32EH0BY")
        with self.assertRaises(KeyError):
            mc.machine_for("washer", "SMV6ZCX42E")


# ------------------------------------------------------------------ validation
class TestValidation(unittest.TestCase):
    def setUp(self):
        self.f = fleet(tempfile.mkdtemp())

    def v(self, name, args):
        return ct.validate_args(name, args, self.f)

    def test_washer_cotton_40(self):
        self.assertEqual(self.v("start_program", {"appliance": "washer", "program": "cotton",
                                                  "temperature": 40}),
                         {"appliance": "washer", "program": "cotton",
                          "options": {"temperature": "40"}})
        # what small models actually send
        for args in ({"appliance": "washing machine", "program": "Cotton 40"},
                     {"appliance": "WAT286H9SG", "program": "cottons", "temperature": "40°C"},
                     {"program": "cotton_40", "appliance": "washer"},
                     {"appliance": "washer", "program": "cotton",
                      "options": {"temp": "40C"}}):
            self.assertEqual(self.v("start_program", args)["options"], {"temperature": "40"}, args)
        out = self.v("start_program", {"appliance": "washer", "program": "cotton",
                                       "spin": "1400 rpm", "finish_in_minutes": "2h"})
        self.assertEqual(out["options"], {"spin": "1400", "finish_in_minutes": 120})
        # 30-minute steps on this machine
        self.assertEqual(self.v("start_program", {"appliance": "washer", "program": "mix",
                                                  "finish_in_minutes": 100})["options"],
                         {"finish_in_minutes": 120})

    def test_washer_rejects_what_the_machine_cant(self):
        for bad in ({"appliance": "washer", "program": "cotton", "temperature": 95},
                    {"appliance": "washer", "program": "easy_care", "temperature": 90},  # max 60
                    {"appliance": "washer", "program": "cotton", "spin": 1600},
                    {"appliance": "washer", "program": "cotton", "speed_perfect": True,
                     "eco_perfect": True},
                    {"appliance": "washer", "program": "dry_clean"},
                    {"appliance": "washer", "program": "cotton", "dryness": "cupboard"},
                    {"appliance": "fridge", "program": "cotton"}):
            with self.assertRaises(ValueError, msg=bad):
                self.v("start_program", bad)

    def test_dryer_extra_dry(self):
        for args in ({"appliance": "dryer", "program": "cotton", "dryness": "extra dry"},
                     {"appliance": "dryer", "program": "cotton", "dryness": "extra"},
                     {"appliance": "dryer", "program": "cotton", "extra_dry": True},
                     {"appliance": "tumble dryer", "program": "cottons",
                      "drying_target": "cupboard dry plus"}):
            self.assertEqual(self.v("start_program", args)["options"],
                             {"dryness": "cupboard_plus"}, args)
        self.assertEqual(self.v("start_program", {"appliance": "dryer", "program": "time_warm",
                                                  "duration_minutes": 45})["options"],
                         {"duration_minutes": 50})
        with self.assertRaises(ValueError):       # duration only on time programs
            self.v("start_program", {"appliance": "dryer", "program": "cotton",
                                     "duration_minutes": 40})
        with self.assertRaises(ValueError):       # no dryness on wool finish
            self.v("start_program", {"appliance": "dryer", "program": "wool_finish",
                                     "dryness": "iron"})

    def test_dishwasher_model_options(self):
        out = self.v("start_program", {"program": "auto", "speed_boost": True, "delay": 30})
        self.assertEqual(out, {"appliance": "dishwasher", "program": "auto",
                               "options": {"speed_perfect": True, "delay_minutes": 30}})
        self.assertEqual(self.v("start_program", {"program": "night"})["program"], "silence")
        with self.assertRaises(ValueError):       # not on the SMV6ZCX42E
            self.v("start_program", {"appliance": "dishwasher", "program": "auto",
                                     "half_load": True})

    def test_appliance_guessed_from_program(self):
        self.assertEqual(self.v("start_program", {"program": "super40"})["appliance"], "dryer")
        self.assertEqual(self.v("start_program", {"program": "drum_clean"})["appliance"], "washer")


# ------------------------------------------------------------------ backends for these models
class FakeHTTP:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, headers=None, body=None, timeout=15):
        self.calls.append((method, url, headers or {}, body))
        for (m, suffix), resp in self.routes.items():
            if m == method and url.endswith(suffix):
                return resp() if callable(resp) else resp
        return 404, {"error": {"key": "SDK.Error.NotFound"}}


IDS = {"dishwasher": "BOSCH-SMV6ZCX42E-68A40E1", "washer": "BOSCH-WAT286H9SG-68A40E2",
       "dryer": "BOSCH-WTW876H8SG-68A40E3"}


def hc_routes(state="Ready", remote=True, local=False, child=False, door="Closed"):
    lst = [{"haId": "SIEMENS-OVEN", "type": "Oven", "vib": "HB678GBS6"},
           {"haId": IDS["dishwasher"], "type": "Dishwasher", "vib": "SMV6ZCX42E", "connected": True},
           {"haId": IDS["washer"], "type": "Washer", "vib": "WAT286H9SG", "connected": True},
           {"haId": IDS["dryer"], "type": "Dryer", "vib": "WTW876H8SG", "connected": True}]
    r = {("GET", "/api/homeappliances"): (200, {"data": {"homeappliances": lst}})}
    for ha in IDS.values():
        root = f"/api/homeappliances/{ha}"
        r[("GET", root + "/status")] = (200, {"data": {"status": [
            {"key": "BSH.Common.Status.OperationState",
             "value": f"BSH.Common.EnumType.OperationState.{state}"},
            {"key": "BSH.Common.Status.DoorState", "value": f"BSH.Common.EnumType.DoorState.{door}"},
            {"key": "BSH.Common.Status.RemoteControlStartAllowed", "value": remote},
            {"key": "BSH.Common.Status.RemoteControlActive", "value": True},
            {"key": "BSH.Common.Status.LocalControlActive", "value": local}]}})
        r[("GET", root + "/settings/BSH.Common.Setting.ChildLock")] = (200, {"data": {
            "key": "BSH.Common.Setting.ChildLock", "value": child}})
        r[("PUT", root + "/programs/active")] = (204, None)
    r[("GET", f"/api/homeappliances/{IDS['washer']}/programs/active")] = (200, {"data": {
        "key": "LaundryCare.Washer.Program.Cotton", "options": [
            {"key": "LaundryCare.Washer.Option.Temperature",
             "value": "LaundryCare.Washer.EnumType.Temperature.GC40"},
            {"key": "BSH.Common.Option.RemainingProgramTime", "value": 7200}]}}) \
        if state == "Run" else (404, {"error": {"key": "SDK.Error.NoProgramActive"}})
    return r


def hc_fleet(http):
    ap._HC_LIST_CACHE.clear()
    return ap.Fleet({
        n: ap.HomeConnectBackend(base="https://api.home-connect.com", token="T", http=http,
                                 machine=mc.machine_for(model=e["model"]), appliance=n)
        for n, e in PRESET["appliances"].items()}, "dishwasher")


class TestHomeConnectModels(unittest.TestCase):
    def test_discovery_by_type_and_model(self):
        http = FakeHTTP(hc_routes())
        f = hc_fleet(http)
        self.assertEqual({n: b.appliance_id() for n, b in f}, IDS)
        lists = [c for c in http.calls if c[1].endswith("/api/homeappliances")]
        self.assertEqual(len(lists), 1)            # one listing serves all three

    def test_washer_start_body(self):
        http = FakeHTTP(hc_routes())
        b = bridge(hc_fleet(http))
        r = b.call("start_program", {"appliance": "washer", "program": "cotton", "temperature": 40,
                                     "spin": 1400, "finish_in_minutes": 180, "i_dos": True})
        self.assertTrue(r["ok"], r)
        put = [c for c in http.calls if c[0] == "PUT"][0]
        self.assertTrue(put[1].endswith(f"/{IDS['washer']}/programs/active"))
        self.assertEqual(put[3], {"data": {"key": "LaundryCare.Washer.Program.Cotton", "options": [
            {"key": "LaundryCare.Washer.Option.Temperature",
             "value": "LaundryCare.Washer.EnumType.Temperature.GC40"},
            {"key": "LaundryCare.Washer.Option.SpinSpeed",
             "value": "LaundryCare.Washer.EnumType.SpinSpeed.RPM1400"},
            {"key": "BSH.Common.Option.FinishInRelative", "value": 10800, "unit": "seconds"},
            {"key": "LaundryCare.Washer.Option.IDos1Active", "value": True}]}})

    def test_dryer_and_dishwasher_bodies(self):
        f = hc_fleet(FakeHTTP(hc_routes()))
        d = f.get("dryer").plan("start", "cotton", {"dryness": "cupboard_plus"})[0]["body"]
        self.assertEqual(d, {"data": {"key": "LaundryCare.Dryer.Program.Cotton", "options": [
            {"key": "LaundryCare.Dryer.Option.DryingTarget",
             "value": "LaundryCare.Dryer.EnumType.DryingTarget.CupboardDryPlus"}]}})
        t = f.get("dryer").plan("start", "time_warm", {"duration_minutes": 60})[0]["body"]
        self.assertEqual(t["data"]["options"], [{"key": "BSH.Common.Option.Duration", "value": 3600,
                                                 "unit": "seconds"}])
        w = f.get("washer").plan("start", "mix", {"speed_perfect": True})[0]["body"]
        self.assertEqual(w["data"]["options"], [{"key": "LaundryCare.Common.Option.VarioPerfect",
                                                 "value": "LaundryCare.Common.EnumType.VarioPerfect.SpeedPerfect"}])
        dw = f.get("dishwasher").plan("start", "auto", {"delay_minutes": 30,
                                                        "intensive_zone": True})[0]["body"]
        self.assertEqual(dw["data"]["key"], "Dishcare.Dishwasher.Program.Auto2")
        self.assertIn({"key": "BSH.Common.Option.StartInRelative", "value": 1800, "unit": "seconds"},
                      dw["data"]["options"])

    def test_status_and_child_lock(self):
        f = hc_fleet(FakeHTTP(hc_routes(state="Run", child=True, door="Locked")))
        s = f.get("washer").status()
        self.assertEqual((s["state"], s["program"], s["options"], s["remaining"], s["door"],
                          s["child_lock"]), ("running", "cotton", {"temperature": "40"},
                                             "2 h 0 min", "locked", True))
        self.assertIsNone(f.get("dishwasher").status()["child_lock"])   # model has none

    def test_interlocks_from_home_connect_flags(self):
        b = bridge(hc_fleet(FakeHTTP(hc_routes(remote=False))))
        r = b.call("start_program", {"appliance": "washer", "program": "cotton"})
        self.assertIn("detergent drawer", r["error"])          # model-specific instructions
        b = bridge(hc_fleet(FakeHTTP(hc_routes(local=True))))
        self.assertIn("at the machine", b.call("start_program", {"appliance": "dryer",
                                                                 "program": "cotton"})["error"])

    def test_409_is_explained(self):
        routes = hc_routes()
        routes[("PUT", f"/api/homeappliances/{IDS['dryer']}/programs/active")] = (409, {"error": {
            "key": "SDK.Error.UnsupportedProgram", "description": "Program not supported"}})
        r = bridge(hc_fleet(FakeHTTP(routes))).call("start_program", {"appliance": "dryer",
                                                                      "program": "wool_finish"})
        self.assertIn("UnsupportedProgram", r["error"])


class TestHomeAssistantModels(unittest.TestCase):
    def test_set_program_and_options(self):
        m = mc.machine_for(model="WAT286H9SG")
        be = ap.HomeAssistantBackend(url="http://ha", token="t", machine=m, appliance="washer",
                                     cfg={"device_id": "abc123"})
        op = be.plan("start", "cotton", {"temperature": "40", "spin": "1200",
                                         "finish_in_minutes": 90})[0]
        self.assertEqual(op["url"], "http://ha/api/services/home_connect/set_program_and_options")
        self.assertEqual(op["body"], {
            "device_id": "abc123", "affects_to": "active_program",
            "program": "laundry_care_washer_program_cotton",
            "laundry_care_washer_option_temperature": "laundry_care_washer_enum_type_temperature_g_c_40",
            "laundry_care_washer_option_spin_speed": "laundry_care_washer_enum_type_spin_speed_r_p_m_1200",
            "b_s_h_common_option_finish_in_relative": 5400})
        dry = ap.HomeAssistantBackend(url="http://ha", token="t", appliance="dryer",
                                      machine=mc.machine_for(model="WTW876H8SG"))
        op = dry.plan("start", "towels")[0]
        self.assertEqual(op["body"], {"entity_id": "select.dryer_active_program",
                                      "option": "laundry_care_dryer_program_towels"})


# ------------------------------------------------------------------ simulator + bridge
class TestSimulatedLaundry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_three_at_once(self):
        f = fleet(self.tmp)
        b = bridge(f, limiter=ct.RateLimiter(min_start_gap_s=60))   # gap is per appliance
        self.assertTrue(b.call("start_program", {"program": "auto"})["ok"])
        r = b.call("start_program", {"appliance": "washer", "program": "cotton", "temperature": "40"})
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["status"]["door"], r["status"]["options"]), ("locked", {"temperature": "40"}))
        r = b.call("start_program", {"appliance": "dryer", "program": "super40"})
        self.assertTrue(r["ok"], r)
        self.assertAlmostEqual(r["status"]["remaining_s"], 40 * 60, delta=120)
        allst = b.call("get_status")["appliances"]
        self.assertEqual({k: v["state"] for k, v in allst.items()},
                         {"dishwasher": "running", "washer": "running", "dryer": "running"})
        again = b.call("start_program", {"appliance": "washer", "program": "mix"})
        self.assertIn("already running on the washer", again["error"])

    def test_remote_start_disarms(self):
        f = fleet(self.tmp, speed=1e6)
        w = f.get("washer")
        bridge(f).call("start_program", {"appliance": "washer", "program": "super_quick"})
        with self.assertRaises(ap.BackendError):
            w.set(door="open")                           # locked while running
        time.sleep(0.01)
        st = w.status()
        self.assertEqual((st["state"], st["remote_start_allowed"]), ("finished", False))
        r = bridge(f).call("start_program", {"appliance": "washer", "program": "cotton"})
        self.assertIn("Remote Start", r["error"])
        w.set(door="open")
        self.assertEqual(w.status()["state"], "ready")
        w.set(door="closed", remote_start_allowed=True)
        self.assertTrue(bridge(f).call("start_program", {"appliance": "washer",
                                                         "program": "cotton"})["ok"])

    def test_finish_in_delays_start(self):
        f = fleet(self.tmp)
        r = bridge(f).call("start_program", {"appliance": "dryer", "program": "cotton",
                                             "dryness": "cupboard", "finish_in_minutes": 300})
        self.assertEqual(r["status"]["state"], "delayed")
        self.assertAlmostEqual(r["status"]["remaining_s"], 300 * 60, delta=700)

    def test_confirmation_names_the_appliance(self):
        asked = []
        b = bridge(fleet(self.tmp), ask=lambda q: asked.append(q) or False)
        r = b.call("start_program", {"appliance": "dryer", "program": "cotton"})
        self.assertTrue(r["declined"])
        self.assertIn("dryer (Bosch WTW876H8SG", asked[0])
        self.assertIn("uses heat", asked[0])

    def test_tools_schema(self):
        tools = ct.build_tools(fleet(self.tmp))
        by = {t["function"]["name"]: t["function"] for t in tools}
        self.assertEqual(set(by), {"get_status", "start_program", "pause", "resume", "cancel",
                                   "notify_when_done", "start_when_done"})
        props = by["start_program"]["parameters"]["properties"]
        self.assertEqual(props["appliance"]["enum"], ["dishwasher", "washer", "dryer"])
        self.assertEqual(props["dryness"]["enum"], ["iron", "cupboard", "cupboard_plus"])
        self.assertIn("washer only", props["temperature"]["description"])
        self.assertEqual(by["start_when_done"]["parameters"]["required"],
                         ["wait_for", "then_start", "program"])
        self.assertLess(len(json.dumps(tools)), 9000)     # fits a small model's context
        sp = ct.system_prompt(bridge(fleet(self.tmp)))
        self.assertIn("cupboard_plus", sp)
        self.assertLess(len(sp), 2200)


class TestChain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = ct.ChainStore(os.path.join(self.tmp, "chains.json"))

    def b(self, f, **kw):
        kw.setdefault("chains", self.store)
        return bridge(f, chain_spawner=lambda cid: "test", **kw)

    def test_schedule_rules(self):
        f = fleet(self.tmp)
        b = self.b(f)
        r = b.call("start_when_done", {"after": "washer", "appliance": "dryer", "program": "cotton"})
        self.assertIn("washer isn't running", r["error"])
        b.call("start_program", {"appliance": "washer", "program": "cotton", "temperature": 40})
        asked = []
        b2 = self.b(f, ask=lambda q: asked.append(q) or True)
        r = b2.call("start_when_done", {"wait_for": "washer", "then_start": "dryer",
                                        "program": "cotton", "dryness": "extra dry"})
        self.assertTrue(r["ok"], r)
        self.assertIn("press Remote Start on the dryer", r["user_must"])
        self.assertIn("Remote Start", asked[0])
        self.assertEqual(self.store.pending()[0]["options"], {"dryness": "cupboard_plus"})
        # a washer program for the dryer is rejected with the dryer's own list
        r = b2.call("start_when_done", {"wait_for": "washer", "then_start": "dryer",
                                        "program": "rinse"})
        self.assertIn("choose from cotton, synthetic", r["error"])
        # cancel on an idle dryer cancels the scheduled start
        r = b2.call("cancel", {"appliance": "dryer"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.store.pending(), [])
        # dry run schedules nothing
        r = self.b(f, dry_run=True).call("start_when_done", {"then_start": "dryer",
                                                             "program": "cotton"})
        self.assertTrue(r["dry_run"])
        self.assertEqual(self.store.pending(), [])

    def _run(self, f, b, cid, human):
        sink = Sink()
        clock = {"n": 0}

        def sleep(s):
            clock["n"] += 1
            human(clock["n"])
            time.sleep(0.002)
        out = ct.run_chain(f, cid, b, notifier=sink, chains=self.store, poll_s=0.001,
                           handoff_poll_s=0.001, log=lambda *a: None, sleep=sleep)
        return out, sink.sent

    def test_washer_then_dryer(self):
        f = fleet(self.tmp, speed=2e6)
        b = self.b(f)
        b.call("start_program", {"appliance": "washer", "program": "cotton", "temperature": 40})
        cid = b.call("start_when_done", {"after": "washer", "appliance": "dryer",
                                         "program": "cotton", "dryness": "cupboard_plus"}) and \
            self.store.pending()[0]["id"]
        dryer = f.get("dryer")
        loaded = {}

        def human(n):          # the dryer was armed (empty) all along; that must not count
            st = f.get("washer").status()["state"]
            if st == "finished" and not loaded and n > 3:
                loaded["at"] = n
                dryer.set(door="open")
                dryer.set(door="closed", remote_start_allowed=True)
        out, sent = self._run(f, b, cid, human)
        self.assertEqual(out, "done")
        self.assertTrue(loaded)
        self.assertIn("The washing is done. Move it into the dryer", sent[0])
        self.assertIn("The dryer started: cotton (dryness=cupboard_plus)", sent[1])
        self.assertEqual(sent[-1], "The laundry is dry.")
        self.assertEqual(self.store.pending(), [])

    def test_detached_runner_uses_the_scheduling_yes(self):
        # the live run's bug: the detached runner has no TTY, so asking again meant "no"
        f = fleet(self.tmp, speed=2e6)
        self.b(f).call("start_program", {"appliance": "washer", "program": "super_quick"})
        self.b(f).call("start_when_done", {"wait_for": "washer", "then_start": "dryer",
                                           "program": "super40"})
        cid = self.store.pending()[0]["id"]
        runner = self.b(f, ask=lambda q: False)            # like `control chain` in the background
        human = lambda n: n == 4 and f.get("dryer").set(door="open") is None and \
            f.get("dryer").set(door="closed", remote_start_allowed=True)  # noqa: E731
        out, sent = self._run(f, runner, cid, human)
        self.assertEqual(out, "done", sent)
        self.assertEqual(runner.confirm, "heat")           # restored
        # a chain without the flag (not scheduled through the bridge) still asks -> refused
        self.store.add({"id": "x", "after": "washer", "target": "dryer", "program": "super40",
                        "options": {}})
        f.get("washer").apply(f.get("washer").plan("start", "super_quick"))
        f.get("dryer").set(state="ready", program=None)
        out, sent = self._run(f, runner, "x", lambda n: n == 4 and f.get("dryer").set(
            door="open") is None and f.get("dryer").set(door="closed", remote_start_allowed=True))
        self.assertEqual(out, "start-failed")
        self.assertIn("did not confirm", sent[-1])

    def test_prearmed_empty_dryer_is_not_started(self):
        f = fleet(self.tmp, speed=2e6)
        b = self.b(f)
        b.call("start_program", {"appliance": "washer", "program": "super_quick"})
        b.call("start_when_done", {"after": "washer", "appliance": "dryer", "program": "cotton"})
        cid = self.store.pending()[0]["id"]
        sink = Sink()
        out = ct.run_chain(f, cid, b, notifier=sink, chains=self.store, poll_s=0.001,
                           handoff_poll_s=0.001, handoff_timeout_s=0.05, log=lambda *a: None,
                           sleep=lambda s: time.sleep(0.002))
        self.assertEqual(out, "timeout")             # armed all along, never loaded
        self.assertIn("Gave up", sink.sent[-1])
        self.assertEqual(f.get("dryer").status()["state"], "ready")

    def test_washer_cancelled_means_no_dryer(self):
        f = fleet(self.tmp, speed=1)
        b = self.b(f)
        b.call("start_program", {"appliance": "washer", "program": "cotton"})
        b.call("start_when_done", {"after": "washer", "appliance": "dryer", "program": "towels"})
        cid = self.store.pending()[0]["id"]
        out, sent = self._run(f, b, cid, lambda n: n == 2 and f.get("washer").apply(
            f.get("washer").plan("cancel")))
        self.assertEqual(out, "after-stopped")
        self.assertIn("will NOT be started", sent[0])
        self.assertEqual(f.get("dryer").status()["state"], "ready")


class TestAgentWithFleet(unittest.TestCase):
    def test_chain_request(self):
        f = fleet(tempfile.mkdtemp())
        store = ct.ChainStore(ct.ChainStore.MEMORY)
        b = bridge(f, chains=store, chain_spawner=lambda cid: "test")
        b.call("start_program", {"appliance": "washer", "program": "cotton"})
        seen = []

        def fake_chat(url, messages, tools=None, model=None):
            seen.append(tools)
            if messages[-1]["role"] == "user":
                self.assertIn("washer (Bosch WAT286H9SG): now running cotton", messages[0]["content"])
                return {"choices": [{"message": {"tool_calls": [{"id": "a", "function": {
                    "name": "start_when_done", "arguments": json.dumps({
                        "after": "washer", "appliance": "dryer", "program": "cotton",
                        "dryness": "extra dry"})}}]}}]}
            res = json.loads(messages[-1]["content"])
            return {"choices": [{"message": {"content": "Scheduled." if res["ok"] else res["error"]}}]}
        answer, _ = ct.run_agent("dry the clothes extra dry when the wash finishes", b, "http://x",
                                 chat_fn=fake_chat, log=lambda *a: None)
        self.assertEqual(answer, "Scheduled.")
        self.assertEqual(store.pending()[0]["options"], {"dryness": "cupboard_plus"})
        self.assertIn("start_when_done", [t["function"]["name"] for t in seen[0]])


class TestConfig(unittest.TestCase):
    def test_init_preset_and_fleet(self):
        d = tempfile.mkdtemp()
        os.environ["DISHSOAKER_CONFIG_DIR"] = d
        try:
            from chorepilot.cli import main
            main(["control", "init", "--backend", "simulated"])
            cfg = ap.load_config()
            self.assertEqual(cfg["backend"], "simulated")
            f = ap.make_fleet(cfg)
            self.assertEqual([(n, b.machine.model, b.name) for n, b in f],
                             [("dishwasher", "SMV6ZCX42E", "simulated"),
                              ("washer", "WAT286H9SG", "simulated"),
                              ("dryer", "WTW876H8SG", "simulated")])
            self.assertEqual(f.resolve("washing machine"), "washer")
            with self.assertRaises(SystemExit):
                main(["control", "init"])                    # won't overwrite
            hc = ap.make_fleet(dict(cfg, backend="homeconnect"))
            self.assertEqual(hc.get("dryer").machine.hc_type, "Dryer")
        finally:
            del os.environ["DISHSOAKER_CONFIG_DIR"]

    def test_program_key_override(self):
        cfg = json.loads(json.dumps(PRESET))
        cfg["appliances"]["washer"]["program_keys"] = {
            "super_quick": "LaundryCare.Washer.Program.Super153045.Super30"}
        f = ap.make_fleet(cfg)
        self.assertEqual(f.get("washer").plan("start", "super_quick")[0]["body"]["data"]["key"],
                         "LaundryCare.Washer.Program.Super153045.Super30")
        cfg["appliances"]["washer"]["program_keys"] = {"plasma": "x"}
        with self.assertRaises(KeyError):
            ap.make_fleet(cfg)

    def test_legacy_config_is_one_dishwasher(self):
        f = ap.make_fleet({"backend": "simulated"})
        self.assertEqual(f.names(), ["dishwasher"])
        self.assertIsNone(f.get().machine.model)


if __name__ == "__main__":
    unittest.main()
