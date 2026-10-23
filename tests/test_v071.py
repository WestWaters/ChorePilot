"""0.7.1: per-tier rate limits, undoing a "done", and the dashboard pieces that go with them."""
import json
import os
import re
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_chorepilot import Tmp, demo_db, house  # noqa: E402
from test_web import UI, WebBase  # noqa: E402

from chorepilot import webview  # noqa: E402
from chorepilot.chores import UNDO_WINDOW_S, ChoreDB, ChoreError  # noqa: E402
from chorepilot.control import RateLimiter  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ====================================================================== rate limits
class TestRateLimiter(Tmp):
    def test_tier1_is_per_device_and_generous(self):
        lim = RateLimiter()
        for i in range(30):
            self.assertIsNone(lim.check("turn_on", now=1000 + i, key="light", tier=1))
            lim.record("turn_on", now=1000 + i, key="light", tier=1)
        self.assertIn("30 actions per minute on the light",
                      lim.check("turn_on", now=1031, key="light", tier=1))
        self.assertIsNone(lim.check("turn_on", now=1031, key="blinds", tier=1))   # other devices
        self.assertIsNone(lim.check("turn_on", now=1061, key="light", tier=1))    # window moved

    def test_tier1_does_not_eat_the_water_heat_budget(self):
        lim = RateLimiter(min_start_gap_s=0)
        for i in range(25):
            lim.record("turn_on", now=1000 + i, key=f"light{i % 5}", tier=1)
        self.assertIsNone(lim.check("start_program", now=1030, key="dishwasher"))

    def test_tier2_and_tier3_stay_strict_and_house_wide(self):
        lim = RateLimiter()
        for i, dev in enumerate(("dishwasher", "washer", "dryer", "oven", "sprinkler", "heater")):
            self.assertIsNone(lim.check("x", now=1000 + i * 100, key=dev, tier=2), dev)
            lim.record("x", now=1000 + i * 100, key=dev, tier=2)
        self.assertIn("6 water / heat actions per 10 min",
                      lim.check("x", now=1550, key="coffee", tier=2))
        gap = RateLimiter()
        gap.record("x", now=1500, key="heater", tier=2)
        self.assertIn("between starts", gap.check("x", now=1530, key="heater", tier=2))
        self.assertIsNone(gap.check("x", now=1530, key="oven", tier=2))           # per device
        self.assertIsNone(lim.check("x", now=1530, key="front_door", tier=3))     # own budget
        for i in range(6):
            lim.record("unlock", now=2000 + i * 61, key="front_door", tier=3)
        self.assertIn("6 security actions", lim.check("unlock", now=2400, key="garage", tier=3))

    def test_agents_are_stricter_and_counted_apart(self):
        lim = RateLimiter()
        for i in range(6):
            self.assertIsNone(lim.check("turn_on", now=1000 + i, key="light", tier=1, agent=True))
            lim.record("turn_on", now=1000 + i, key="light", tier=1, agent=True)
        self.assertIn("6 actions per minute", lim.check("turn_on", now=1007, key="light", tier=1,
                                                        agent=True))
        # a runaway agent never uses up the person's own budget
        self.assertIsNone(lim.check("turn_on", now=1007, key="light", tier=1))
        # and across devices it has a house-wide cap too
        agent = lim.for_caller(agent=True)
        for i in range(24):
            agent.record("turn_on", now=1100 + i * 10, key=f"d{i}", tier=1)
        self.assertIn("30 actions per 10 min", agent.check("turn_on", now=1400, key="new", tier=1))

    def test_house_json_overrides(self):
        lim = RateLimiter(limits={"tier1": {"per_device": {"max": 2, "window_s": 30}},
                                  "tier2": {"start_gap_s": 0, "house": None},
                                  "agent": {"tier1": {"per_device": None, "house": None}}})
        lim.record("turn_on", now=1000, key="light", tier=1)
        lim.record("turn_on", now=1001, key="light", tier=1)
        self.assertIn("2 actions per 30 s", lim.check("turn_on", now=1002, key="light", tier=1))
        for i in range(20):
            lim.record("start_program", now=1000 + i, key="dishwasher")
        self.assertIsNone(lim.check("start_program", now=1021, key="dishwasher"))   # no limit set
        for i in range(50):
            lim.record("turn_on", now=1000 + i, key="light", tier=1, agent=True)
        self.assertIsNone(lim.check("turn_on", now=1051, key="light", tier=1, agent=True))
        bad = RateLimiter(limits={"tier1": {"per_device": {"max": "lots", "window_s": -1}}})
        self.assertIsNone(bad.check("turn_on", now=1, key="light", tier=1))       # ignored, no crash

    def test_persisted_and_reads_the_old_file(self):
        path = os.path.join(self.tmp, "rl.json")
        with open(path, "w") as f:
            json.dump({"writes": [time.time()] * 9, "last_start": 0}, f)          # the 0.7 format
        a = RateLimiter(path=path)
        self.assertIsNone(a.check("turn_on", key="light", tier=1))
        a.record("start_program", key="dishwasher")
        b = RateLimiter(path=path)
        self.assertIn("between starts", b.check("start_program", key="dishwasher"))
        self.assertNotIn("writes", json.load(open(path)))


def limited(tmp, lim, **kw):
    h = house(tmp, **kw)
    h.limiter = lim
    if h.bridge:
        h.bridge.limiter = lim.for_caller(h.agent)
    return h


class TestHouseRateLimits(Tmp):
    def test_many_light_taps_go_through(self):
        h = limited(self.tmp, RateLimiter(path=os.path.join(self.tmp, "rl.json")))
        for i in range(20):
            r = h.call("set_light", {"device": "living_room_light",
                                     "action": "turn_on" if i % 2 == 0 else "turn_off"})
            self.assertTrue(r["ok"], (i, r))

    def test_limits_come_from_house_json(self):
        h = house(self.tmp)
        h.cfg["rate_limits"] = {"tier1": {"per_device": {"max": 3, "window_s": 60}}}
        h.limiter = RateLimiter(limits=h.cfg["rate_limits"])
        for _ in range(3):
            self.assertTrue(h.call("set_light", {"device": "living_room_light",
                                                 "action": "turn_on"})["ok"])
        r = h.call("set_light", {"device": "living_room_light", "action": "turn_off"})
        self.assertIn("3 actions per minute", r["error"])

    def test_mcp_caller_is_stricter_than_a_person(self):
        lim = RateLimiter()
        agent = limited(self.tmp, lim, source="mcp")
        me = limited(self.tmp, lim, db=agent.db)
        self.assertTrue(agent.agent and not me.agent)
        for i in range(6):
            self.assertTrue(agent.call("set_light", {"device": "living_room_light",
                                                     "action": "turn_on"})["ok"], i)
        r = agent.call("set_light", {"device": "living_room_light", "action": "turn_off"})
        self.assertIn("rate limit", r["error"])
        self.assertTrue(me.call("set_light", {"device": "living_room_light",
                                              "action": "turn_off"})["ok"])

    def test_program_appliances_keep_the_start_gap(self):
        h = limited(self.tmp, RateLimiter(min_start_gap_s=60))
        self.assertTrue(h.call("start_program", {"appliance": "dishwasher", "program": "eco"})["ok"])
        self.assertTrue(h.call("cancel", {"appliance": "dishwasher"})["ok"])
        r = h.call("start_program", {"appliance": "dishwasher", "program": "eco"})
        self.assertIn("between starts", r["error"])


class TestDashboardRateLimit(WebBase):
    cfg_extra = {"rate_limits": {"tier1": {"per_device": {"max": 30, "window_s": 60}}}}

    def setUp(self):
        super().setUp()
        self.httpd.house_kw.pop("limiter")        # the real one, from house.json, on disk

    def test_quick_light_toggling_is_not_blocked(self):
        s = self.sess("Alex", "2468")
        for i in range(12):
            code, r = self.act(s, "living_room_light", "turn_on" if i % 2 == 0 else "turn_off")
            self.assertEqual((code, r.get("ok")), (200, True), (i, r))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "cache", "control-ratelimit.json")))


# ====================================================================== undo a "done"
class TestUndoDb(unittest.TestCase):
    def setUp(self):
        self.now = [1_800_000_000.0]
        self.db = demo_db()
        self.db.clock = lambda: self.now[0]

    def tick(self, s):
        self.now[0] += s

    def test_undo_restores_points_streak_followups_and_rotation(self):
        db = self.db
        empty = db.chore("Empty the dishwasher")
        rot0 = empty["rot_idx"]
        res = db.complete("Dishes", by="Sam", marked_by="Sam")
        iid = res["instance"]["id"]
        self.assertEqual(db.points(7)["Sam"], 2)
        self.assertEqual([f["title"] for f in res["followups"]], ["Empty the dishwasher"])
        self.assertEqual(webview.streak(db, db.person("Sam")["id"], self.now[0]), 1)
        self.tick(30)
        out = db.undo_complete(iid, "Sam")
        self.assertEqual((out["credited"], out["points"]), ("Sam", 2))
        self.assertEqual(out["removed_followups"], ["Empty the dishwasher"])
        self.assertEqual(db.points(7)["Sam"], 0)
        self.assertEqual(webview.streak(db, db.person("Sam")["id"], self.now[0]), 0)
        self.assertEqual(db.instances("open", chore=empty["id"]), [])
        self.assertEqual(db.chore(empty["id"])["rot_idx"], rot0)
        self.assertIsNone(db.chore("Dishes")["last_done"])
        # marking it done again hands the follow-up to the same person as the first time
        again = db.complete("Dishes", by="Sam")
        self.assertEqual(again["followups"][0]["assignee"], res["followups"][0]["assignee"])

    def test_a_scheduled_instance_reopens(self):
        db = self.db
        inst = db.open_instance("Vacuum the living room", assignee="Riley")
        db.complete("Vacuum the living room", by="Riley", instance=inst["id"])
        out = db.undo_complete(inst["id"], "Alex")
        self.assertTrue(out["reopened"])
        back = db.instance(inst["id"])
        self.assertEqual((back["state"], back["completed_by"], back["assignee"]), ("open", None, "Riley"))

    def test_kids_window_and_other_peoples_completions(self):
        db = self.db
        inst = db.open_instance("Vacuum the living room", assignee="Riley")
        db.complete("Vacuum the living room", by="Riley", instance=inst["id"], marked_by="Riley")
        with self.assertRaisesRegex(ChoreError, "only an adult"):
            db.undo_complete(inst["id"], "Jordan")                 # a kid, someone else's
        self.tick(UNDO_WINDOW_S + 1)
        with self.assertRaisesRegex(ChoreError, "ask an adult"):
            db.undo_complete(inst["id"], "Riley")                  # their own, too late
        self.assertTrue(db.undo_complete(inst["id"], "Sam")["reopened"])        # an adult: any time
        # an adult marked it for the kid: the kid can't take it back
        db.complete("Vacuum the living room", by="Riley", instance=inst["id"], marked_by="Alex")
        with self.assertRaisesRegex(ChoreError, "only an adult"):
            db.undo_complete(inst["id"], "Riley")
        with self.assertRaisesRegex(ChoreError, "isn't marked done"):
            db.undo_complete(db.open_instance("Water the lawn")["id"], "Alex")

    def test_pending_approval_is_cancelled(self):
        db = self.db
        res = db.complete("Empty the dishwasher", by="Jordan")
        self.assertEqual(res["instance"]["state"], "pending_approval")
        aid = res["approval"]["id"]
        out = db.undo_complete(res["instance"]["id"], "Jordan")    # their own, in the window
        self.assertEqual(out["was"], "pending_approval")
        self.assertEqual(db.approval(aid)["state"], "cancelled")
        self.assertIsNone(db.instance(res["instance"]["id"]))      # it was ad hoc: gone

    def test_followups_someone_already_did_stay(self):
        db = self.db
        res = db.complete("Dishes", by="Sam")
        f = res["followups"][0]
        db.complete("Empty the dishwasher", by="Sam", instance=f["id"])
        out = db.undo_complete(res["instance"]["id"], "Alex")
        self.assertEqual(out["removed_followups"], [])
        self.assertEqual(db.instance(f["id"])["state"], "done")

    def test_device_verified_and_approved_followups(self):
        db = self.db
        db.record_event("dishwasher", "started", "dishwasher", actor="Jordan")
        db.apply_event("dishwasher", "finished", "dishwasher")
        done = db.instances("done")[0]
        self.assertEqual(db.done_marks([done["id"]])[done["id"]]["by"], None)
        with self.assertRaises(ChoreError):
            db.undo_complete(done["id"], "Jordan")                 # nobody marked it: adults only
        self.assertTrue(db.undo_complete(done["id"], "Alex")["removed_followups"])
        # follow-ups opened when a parent approves are undone too
        res = db.complete("Empty the dishwasher", by="Riley")
        db.decide(res["approval"]["id"], "Alex")
        out = db.undo_complete(res["instance"]["id"], "Alex")
        self.assertEqual(out["was"], "approved")

    def test_bookkeeping_stays_out_of_the_event_feed(self):
        db = self.db
        db.complete("Dishes", by="Sam")
        self.assertEqual([e for e in db.events(50) if e["device"].startswith("chore:")], [])


class TestUndoWeb(WebBase):
    def test_mark_done_then_undo_with_audit(self):
        alex = self.sess("Alex", "2468")
        code, h, _ = self.req("/api/home", sess=alex)
        inst = next(i for i in h["board"]["open"] if i["state"] == "open")
        code, r, _ = self.req(f"/api/chores/{inst['id']}/done", {}, sess=alex)
        self.assertEqual((code, r["undo_s"], r["id"]), (200, UNDO_WINDOW_S, inst["id"]))
        code, h, _ = self.req("/api/home", sess=alex)
        row = next(i for i in h["board"]["done"] + h["board"]["open"] if i["id"] == inst["id"])
        self.assertEqual((row["can_undo"], row["marked_by"], row["undo_until"]), (True, "Alex", None))
        code, r, _ = self.req(f"/api/chores/{inst['id']}/undo", {}, sess=alex)
        self.assertEqual(code, 200, r)
        rows = [x for x in self.audit_rows() if x["tool"] == "undo_chore"]
        self.assertEqual((len(rows), rows[0]["ok"], rows[0]["who"]), (1, True, "Alex"))
        act = webview.activity(rows, ChoreDB(self.dbp))
        self.assertTrue(any(i["text"].startswith("Alex undid") for i in act), act)
        code, r, _ = self.req(f"/api/chores/{inst['id']}/undo", {}, sess=alex)
        self.assertEqual(code, 404)

    def test_kid_can_undo_own_mark_but_not_others(self):
        jordan, alex = self.sess("Jordan", "1111"), self.sess("Alex", "2468")
        db = ChoreDB(self.dbp)
        mine = db.open_instance("Vacuum the living room", assignee="Jordan")
        other = db.open_instance("Water the lawn", assignee="Sam")
        db.close()
        self.assertEqual(self.req(f"/api/chores/{mine['id']}/done", {}, sess=jordan)[0], 200)
        self.assertEqual(self.req(f"/api/chores/{other['id']}/done", {}, sess=alex)[0], 200)
        code, h, _ = self.req("/api/home", sess=jordan)
        rows = {i["id"]: i for i in h["board"]["done"] + h["board"]["open"]}
        self.assertTrue(rows[mine["id"]]["can_undo"])
        self.assertGreater(rows[mine["id"]]["undo_until"], time.time())
        self.assertFalse(rows[other["id"]]["can_undo"])
        code, r, _ = self.req(f"/api/chores/{other['id']}/undo", {}, sess=jordan)
        self.assertEqual(code, 403)
        self.assertIn("only an adult", r["error"])
        denied = [x for x in self.audit_rows() if x["tool"] == "undo_chore"]
        self.assertEqual((denied[-1]["ok"], denied[-1]["outcome"]), (False, "deny"))
        code, r, _ = self.req(f"/api/chores/{mine['id']}/undo", {}, sess=jordan)
        self.assertEqual(code, 200, r)


# ====================================================================== the dashboard
class TestDashboard071(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(UI, "app.js")) as f:
            self.js = f.read()
        with open(os.path.join(UI, "app.css")) as f:
            self.css = f.read()
        with open(os.path.join(UI, "index.html")) as f:
            self.html = f.read()

    def test_palette_everywhere(self):
        self.assertIn('id="ask-open"', self.html)
        self.assertIn("function openPalette", self.js)
        self.assertRegex(self.js, r"e\.metaKey \|\| e\.ctrlKey")
        self.assertIn('"/api/ask"', self.js)                      # one flow for both consoles
        self.assertEqual(self.js.count('api("/api/ask"'), 1)

    def test_phone_hero_and_dock(self):
        phone = "".join(re.findall(r"@media \(max-width: 699px\) \{(.*?)\n\}", self.css, re.S))
        self.assertIn(".kpi-v { font-size: 28px", phone)
        self.assertIn("scroll-padding-bottom", phone)
        self.assertIn("safe-area-inset", phone)

    def test_tile_grids_fill_the_width(self):
        self.assertIn("grid-template-columns: subgrid", self.css)
        self.assertIn("auto-fill, minmax(260px, 1fr)", self.css)
        self.assertNotRegex(self.css, r"(?i)gradient\(.*#0ff|cyan")


if __name__ == "__main__":
    unittest.main()
