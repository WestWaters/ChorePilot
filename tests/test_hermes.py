"""The Hermes Agent plugin (hermes/chorepilot/): a stdlib client of ChorePilot's MCP HTTP server.
Run against the real server in-process with the demo household; no Hermes needed (the plugin's
register() gets a stand-in ctx)."""
import importlib.util
import json
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_phase2 import MCPBase  # noqa: E402

from chorepilot import mcp  # noqa: E402
from chorepilot.chores import ChoreDB  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "hermes", "chorepilot")


def load_plugin():
    """Import hermes/chorepilot as its own package (Hermes namespaces it the same way), so it
    never collides with the chorepilot package itself."""
    spec = importlib.util.spec_from_file_location(
        "cp_hermes_plugin", os.path.join(PLUGIN, "__init__.py"),
        submodule_search_locations=[PLUGIN])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cp_hermes_plugin"] = mod
    spec.loader.exec_module(mod)
    return mod


class Ctx:
    def __init__(self):
        self.tools, self.skills = {}, {}

    def register_tool(self, name, toolset, schema, handler, check_fn=None, emoji="", **kw):
        self.tools[name] = (toolset, schema, handler, check_fn)

    def register_skill(self, name, path):
        self.skills[name] = path


class TestHermesPlugin(MCPBase):
    def setUp(self):
        super().setUp()
        self.p = load_plugin()
        self.p._clients.clear()
        self.tokens = mcp.Tokens(os.path.join(self.tmp, "cfg", "mcp-tokens.json"))
        self.alex = self.tokens.add("Alex", "hermes")
        self.kid = self.tokens.add("Jordan", "hermes-kid")
        self.httpd = mcp.make_http_server(self.server(timeout=1.5), "127.0.0.1", 0, self.tokens,
                                          log=lambda m: None)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/mcp"
        self.env = {k: os.environ.get(k) for k in ("CHOREPILOT_URL", "CHOREPILOT_TOKEN")}
        self.use(self.alex)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def use(self, token, url=None):
        os.environ["CHOREPILOT_URL"] = url or self.url
        os.environ["CHOREPILOT_TOKEN"] = token
        self.p._clients.clear()

    def run_tool(self, ctx, name, args=None):
        return json.loads(ctx.tools[name][2](args or {}))

    def registered(self):
        ctx = Ctx()
        self.p.register(ctx)
        return ctx

    def test_manifest_matches_registrations(self):
        ctx = self.registered()
        with open(os.path.join(PLUGIN, "plugin.yaml")) as f:
            text = f.read()
        declared = [ln.strip()[2:] for ln in text.split("provides_tools:")[1].split("provides_hooks")[0]
                    .splitlines() if ln.strip().startswith("- ")]
        self.assertEqual(sorted(declared), sorted(ctx.tools))
        self.assertEqual(sorted(declared), sorted(self.p.TOOL_NAMES))
        self.assertEqual({t[0] for t in ctx.tools.values()}, {"chorepilot"})
        self.assertIn("household", ctx.skills)
        self.assertIn("version: " + self.p.VERSION, text)
        # the live house went into chorepilot_call's description at load
        self.assertIn("set_light(", ctx.tools["chorepilot_call"][1]["description"])

    def test_loads_when_chorepilot_is_offline(self):
        self.use(self.alex, url="http://127.0.0.1:9/mcp")
        ctx = self.registered()
        self.assertEqual(len(ctx.tools), 7)
        self.assertIn("chorepilot_tools first", ctx.tools["chorepilot_call"][1]["description"])
        out = self.run_tool(ctx, "chorepilot_chores")
        self.assertFalse(out["ok"])
        self.assertIn("can't reach ChorePilot", out["error"])

    def test_reads_and_tier1(self):
        ctx = self.registered()
        me = self.run_tool(ctx, "chorepilot_whoami")
        self.assertEqual((me["person"], me["role"]), ("Alex", "owner"))
        self.assertIn("hermes", me["client"])
        board = self.run_tool(ctx, "chorepilot_chores")
        self.assertTrue(board["ok"], board)
        self.assertEqual(self.run_tool(ctx, "chorepilot_who_has", {"chore": "dishes"})["this_week"],
                         "Jordan")
        self.assertTrue(self.run_tool(ctx, "chorepilot_status", {"appliance": "dishwasher"})["ok"])
        listed = self.run_tool(ctx, "chorepilot_tools")
        self.assertIn("set_light", {t["name"] for t in listed["tools"]})
        out = self.run_tool(ctx, "chorepilot_call", {"tool": "set_light",
                                                     "arguments": {"action": "turn_on"}})
        self.assertTrue(out["ok"], out)
        self.assertEqual((out["policy"], out["tier"]), ("allow", 1))
        out = self.run_tool(ctx, "chorepilot_call", {"tool": "set_light",
                                                     "arguments": '{"action": "turn_off"}'})
        self.assertTrue(out["ok"], out)
        # arguments next to "tool" (what small models send) work too; nested ones win
        out = self.run_tool(ctx, "chorepilot_call", {"tool": "set_light", "action": "turn_on",
                                                     "device": "living_room_light"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["state"], "on")

    def test_tier2_needs_the_persons_own_yes(self):
        ctx = self.registered()
        # nobody answers: a no, whatever the agent puts in the arguments
        out = self.run_tool(ctx, "chorepilot_call", {"tool": "start_program", "arguments": {
            "appliance": "dishwasher", "program": "auto", "confirm": True, "yes": "y"}})
        self.assertFalse(out["ok"], out)
        self.assertEqual(out["confirmation"]["state"], "expired")
        # the person says yes on the dashboard: it runs
        self.answer = lambda db, c: db.answer_confirmation(c["id"], True, "web")
        out = self.run_tool(ctx, "chorepilot_call", {"tool": "start_program", "arguments": {
            "appliance": "dishwasher", "program": "auto"}})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["confirmation"]["via"], "web")
        self.assertIn("confirmed by the person", out["note"])

    def test_tier3_is_refused(self):
        ctx = self.registered()
        self.answer = lambda db, c: db.answer_confirmation(c["id"], True, "telegram")
        for args in ({"action": "unlock"}, {"action": "unlock", "code": "2468"}):
            out = self.run_tool(ctx, "chorepilot_call", {"tool": "door_lock", "arguments": args})
            self.assertFalse(out["ok"], out)
        out = self.run_tool(ctx, "chorepilot_call", {"tool": "door_lock",
                                                     "arguments": {"action": "unlock"}})
        self.assertEqual(out["policy"], "deny")
        self.assertIn("never", out["error"])
        db = ChoreDB(self.dbp)
        self.assertEqual(db.db.execute("SELECT COUNT(*) FROM confirmations").fetchone()[0], 0)
        db.close()

    def test_a_kids_token_goes_to_the_approval_queue(self):
        self.use(self.kid)
        ctx = self.registered()
        out = self.run_tool(ctx, "chorepilot_call", {"tool": "start_program", "arguments": {
            "appliance": "dishwasher", "program": "eco"}})
        self.assertEqual(out.get("policy"), "approval", out)
        self.assertIn("parent", out["note"])

    def test_bad_token_and_bad_input(self):
        self.use("cp_not-a-real-token")
        ctx = self.registered()
        out = self.run_tool(ctx, "chorepilot_chores")
        self.assertFalse(out["ok"])
        self.assertIn("401", out["error"])
        self.use(self.alex)
        out = self.run_tool(ctx, "chorepilot_call", {"tool": "rm_rf"})
        self.assertFalse(out["ok"])
        self.assertIn("unknown tool", out["error"])
        self.assertFalse(self.run_tool(ctx, "chorepilot_call", {})["ok"])
        self.assertFalse(self.run_tool(ctx, "chorepilot_call", {"tool": "set_light",
                                                                "arguments": "[1"})["ok"])

    def test_reconnects_after_the_server_forgets_the_session(self):
        ctx = self.registered()
        self.assertTrue(self.run_tool(ctx, "chorepilot_whoami")["ok"])
        self.httpd.sessions.clear()                       # e.g. ChorePilot restarted
        self.assertTrue(self.run_tool(ctx, "chorepilot_whoami")["ok"])

    def test_plain_http_only_on_private_networks(self):
        c = sys.modules["cp_hermes_plugin.client"]
        ok = c.Client
        for url in ("http://127.0.0.1:8770/mcp", "http://192.168.1.20:8770/mcp",
                    "http://100.101.102.103:8770/mcp", "http://house.local:8770/mcp",
                    "http://pi:8770/mcp", "https://house.example.ts.net/mcp",
                    "https://chores.example.com/mcp"):
            self.assertTrue(c.plain_http_ok(url), url)
        for url in ("http://chores.example.com/mcp", "http://8.8.8.8/mcp", "ftp://x/mcp"):
            self.assertFalse(c.plain_http_ok(url), url)
            with self.assertRaises(c.ChorePilotError):
                ok(url, "cp_x")
        self.assertTrue(ok("http://127.0.0.1:8770", "cp_x").url.endswith("/mcp"))


if __name__ == "__main__":
    unittest.main()
