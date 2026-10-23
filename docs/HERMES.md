# ChorePilot in Hermes Agent

[Hermes Agent](https://github.com/NousResearch/hermes-agent) (Nous Research) can run your house
and your chore board through ChorePilot. There are two ways in, and both use the same ChorePilot
MCP server and the same safety rules:

| | What you get | Install |
|---|---|---|
| **The ChorePilot plugin** (recommended) | 7 `chorepilot_*` tools, a bundled `chorepilot:household` skill, and a set-up prompt for the URL and token | `hermes plugins install <owner>/ChorePilot/hermes/chorepilot --enable` |
| **Plain MCP** | every ChorePilot tool, discovered live from your house | `hermes mcp add chorepilot --url http://127.0.0.1:8770/mcp` |

`<owner>` is the GitHub account this repository lives under.

## What Hermes can and can't do

ChorePilot decides, from the household person the token belongs to. Nothing Hermes or its model
sends changes that.

| Tier | Examples | From Hermes |
|---|---|---|
| 0 read, 1 low | status, chore board, lights, vacuum, blinds, AC in the band, locking a door | runs |
| 2 water / heat | dishwasher, washer, sprinkler, coffee, oven, water heater | waits for **that person's own yes**: a Telegram button or the dashboard's Approvals page. 120 s without an answer is a no. Nothing in the tool arguments counts as a yes |
| kid's token | anything a kid needs a parent for | goes to the parents' approval queue (`chorepilot approvals`) |
| 3 security | unlock, open the garage, disarm | **always refused** |

`policy.agents_max_tier` in house.json can lower the ceiling to 0 (read-only) or 1. If Hermes
also has a shell on the ChorePilot machine, set `"mcp": {"confirm_channels": ["telegram"]}` so a
yes can only come from your phone.

## 1. On the ChorePilot machine

```bash
chorepilot mcp token add Alex --client hermes   # one token per agent; shown once, stored hashed
chorepilot run                                  # starts the MCP server on 127.0.0.1:8770 (with Telegram, the dashboard ...)
```

`chorepilot mcp --http` runs only the MCP server. Pick the person carefully: Hermes acts as
them. A kid's token gets a kid's rules. `chorepilot mcp token revoke hermes` takes it away.

### Hermes on another machine

ChorePilot binds to 127.0.0.1 by default. Choose one:

- **Tailscale** (recommended): on the ChorePilot machine, `tailscale serve --bg 8770`. Hermes
  uses `https://<machine>.<tailnet>.ts.net/mcp`. Only your own devices can reach it, over HTTPS.
- **Your own LAN**: `chorepilot mcp --http --lan` (for `chorepilot run`: `"mcp": {"host": "0.0.0.0"}` in
  house.json), and
  Hermes uses `http://<lan-ip-or-name>:8770/mcp`. Plain HTTP, so only on a network you trust.

The plugin refuses plain `http://` to anything that isn't this machine, a private address
(192.168.x, 10.x, Tailscale's 100.64/10) or a `.local` / `.lan` / `.ts.net` name, so a token
never crosses the internet in the clear. Never port-forward 8770.

## 2a. In Hermes: the plugin

```bash
hermes plugins install <owner>/ChorePilot/hermes/chorepilot --enable
```

Hermes downloads only the `hermes/chorepilot` folder, runs its security scan, then asks for:

- `CHOREPILOT_URL`: `http://127.0.0.1:8770/mcp` on the same machine, or one of the addresses above
- `CHOREPILOT_TOKEN`: the `cp_...` token from step 1 (hidden input)

Both are saved in Hermes's `.env` (`~/.hermes/.env`, or the active profile's). Skipped them?
Add the two lines there yourself. Then restart Hermes (`hermes gateway restart` when it runs as a
service; otherwise quit Hermes and open it again) and start a new session: the gateway loads
plugins once, at start.

| Tool | Does |
|---|---|
| `chorepilot_status` | a device, or the whole house |
| `chorepilot_chores` | the chore board (open chores, this week's rotations) |
| `chorepilot_who_has` | who has a chore |
| `chorepilot_done` | mark a chore done |
| `chorepilot_whoami` | which person and role Hermes acts as |
| `chorepilot_tools` | every tool this house offers, with arguments |
| `chorepilot_call` | run any of them: lights, climate, vacuum, blinds, appliances, assign, remind |

The plugin reads your house's tool list when Hermes starts and puts it in `chorepilot_call`'s
description, so the model sees your actual devices. If ChorePilot is down at that moment the
plugin still loads; `chorepilot_tools` fetches the list later. The skill
(`skill_view("chorepilot:household")`) explains the tiers to the agent.

The plugin is stdlib Python, needs no packages, and makes no network calls except to
`CHOREPILOT_URL`. Optional: `CHOREPILOT_TIMEOUT` (seconds a call may wait, default 150, which
leaves room for ChorePilot's 120 s confirmation window).

To update: `hermes plugins update chorepilot`. To remove: `hermes plugins remove chorepilot`.

## 2b. In Hermes: plain MCP

```bash
hermes mcp add chorepilot --url http://127.0.0.1:8770/mcp
```

Answer **yes** to "Does this server require authentication?" and paste the token. Hermes keeps
it in `.env` as `MCP_CHOREPILOT_API_KEY` and writes this to `config.yaml`:

```yaml
mcp_servers:
  chorepilot:
    url: http://127.0.0.1:8770/mcp
    headers:
      Authorization: Bearer ${MCP_CHOREPILOT_API_KEY}
    timeout: 180          # add this: a water/heat confirmation can take up to 120 s
```

Hermes's own default MCP call timeout is 120 s, the same as ChorePilot's confirmation window,
so add `timeout: 180` or a slow yes may arrive after Hermes has stopped waiting.

## Small local models

Hermes defers plugin and MCP tools behind its Tool Search bridge. Big models handle that; a 1-3B
model often doesn't find the tool. For a small model, keep the tools loaded:

```yaml
tools:
  tool_search:
    enabled: off
```

Hermes also needs a 64K context window. With llama.cpp:
`llama-server -m <model>.gguf --jinja -c 65536 -np 1`; a model trained on less (Qwen2.5-1.5B is
32K) needs `model.ollama_num_ctx: 65536` in `config.yaml` to pass Hermes's check.

## Tested

Hermes Agent from GitHub main (commit fab9b01, 2026-10-22), the simulated demo house
(`chorepilot init demo; chorepilot demo seed`), Qwen2.5-1.5B-Instruct Q4_K_S on llama-server:

| Asked | What happened |
|---|---|
| "Who has the dishes chore this week?" | `chorepilot_who_has` -> "Jordan has the Dishes chore this week." |
| "What's on the chore board?" | `chorepilot_chores` -> nothing open today, same as `chorepilot chores list` |
| "Turn on the living room light." | `chorepilot_call set_light` -> on (tier 1, allowed) |
| "Start the dishwasher on the eco program." | the call waited; Alex pressed Yes on the dashboard; the dishwasher started (tier 2, `via: web`) |
| "Start the washer on the cotton program." | Alex pressed No; nothing ran (`declined`) |
| "Unlock the front door." | refused: `an external agent can never do security actions` (tier 3) |
| the same dishwasher request with Jordan's (kid) token | queued as approval #1 for a parent |

`hermes plugins validate hermes/chorepilot` (the plugin catalog's own check) passes, including
its security scan. `tests/test_hermes.py` runs the plugin against the real MCP server.

*Hermes Agent is a project of Nous Research. ChorePilot is an independent project and is not
affiliated with Nous Research.*
