# ChorePilot: design

ChorePilot grew out of Dishsoaker3000. The first half of this document is the house and chore
layer that ChorePilot added. The second half (from "Goals") is the original Dishsoaker design:
running models on any device, the memory and speed models, the RPC cluster, and the
program-appliance bridge. ChorePilot still uses all of it unchanged.

## ChorePilot architecture

```
 CLI (ask / chores / devices / approvals / confirm)   Telegram bot (telegram.py)   MCP server (mcp.py)
            |  identity: --as <person> | linked Telegram user id | MCP bearer token -> person + role
            |  source:   cli            | telegram                 | mcp (tier 3 never; tier 2 -> confirm.py)
            v
   +-------------------------------- House (house.py) --------------------------------+
   |  tool router: which groups a request needs (chores | program | <device types>)   |
   |  agent loop (agent.py): local LLM via any OpenAI-compatible URL                  |
   |  POLICY ENGINE (policy.py): role x tier x source -> allow / confirm /            |
   |     strong_confirm / approval queue / second factor / deny                       |
   |  audit log: ~/.config/chorepilot/audit.log (append-only JSONL, 0600)             |
   +--------------+------------------------------+-----------------------------------+
                  |                              |                                    |
     device-type plugins (devtypes/)    program appliances                  chore core (chores.py)
     light climate valve coffee_maker   dishwasher washer dryer             SQLite: people, chores,
     oven fridge vacuum lock ...        -> the Dishsoaker Bridge            schedules, rotations,
            |                              (control.py, unchanged)          instances, events,
     adapters (adapters/):                 -> appliance.py backends          approvals, points
     simulated ha matter mqtt homeconnect                                          ^
                  \_____________ events.py: status diffs -> device events ______/
                                  "dishwasher finished" => complete "Dishes" (credit the starter),
                                  open "Empty the dishwasher" for next(rotation)
```

### Device-type plugins

Each type lives in a small module (`chorepilot/devtypes/<type>.py`) that subclasses `DeviceType`:

- `actions`: each has typed `Param`s (with limits) and a **tier**. `tier_for()` can depend on the
  parameters: a thermostat setpoint inside the comfort band is tier 1, outside it tier 2.
- `confirm="strong"` + `phrase` for actions that need a typed confirmation (oven preheat:
  `PREHEAT`).
- `validate()` does strict checks and normalisation:
  - unit sense: "72" in a Celsius house is read as 72 F, and the result says so;
  - hard limits;
  - food-safe fridge ranges;
  - sprinkler duration caps;
  - codes and PINs in the model's arguments are rejected.
- `interlock()` is what the device itself would refuse: door open, already running, remote start
  not armed, local controls in use, tank empty, jammed.
- `events(prev, cur)` turns a status change into device events (`started`, `finished`,
  `door_alarm`, `locked` ...).
- Adapter hooks: `ha_status`/`ha_ops`, `mqtt_state_topics`/`mqtt_status`/`mqtt_ops`,
  `hc_status`/`hc_ops`, and `sim_init`/`sim_apply`/`sim_tick`/`sim_status`.
- `tool_schema()` makes one OpenAI tool per type, with `device` (an enum of nicknames) and
  `action` (an enum).

The dishwasher, washer and dryer types (`program.py`) carry tiers, keywords and events only. Their
programs and exact Bosch profiles stay in `machines.py`, and the House sends their tools through
the original Bridge. A user plugin in `~/.config/chorepilot/devtypes/` is loaded at start-up.

### Adapters

- `simulated`: one JSON file per device, with time compressed (`CHOREPILOT_SIM_SPEED`, 600x).
- `ha`: Home Assistant REST: `GET /api/states/<entity>` and `POST /api/services/<domain>/<service>`.
- `matter`: the HA adapter. Matter devices are commissioned into HA's Matter server and appear as
  HA entities.
- `mqtt`: ESPHome topics, using the stdlib MiniMQTT client.
- `homeconnect`: reuses the program appliances' Home Connect OAuth and appliance discovery.

Writes are always two-phase. `plan()` returns the exact requests, which is what `--dry-run`
prints, and `apply()` sends them. A step with `delay_s` (closing the sprinkler valve after N
minutes) goes to a `DeferredStore`, and a detached `chorepilot devices deferred <id>` sends it.
The close is therefore scheduled at the moment the valve opens, even if the CLI exits.

### A call through the House

1. Validate the call with the type plugin.
2. Read the device status and check its interlocks. Nobody is asked to approve something that
   would fail.
3. Work out the tier and ask the **policy engine** for a decision, made from the *authenticated*
   person's role and the source (`cli` / `mcp` / `schedule`).
4. On `deny`, return an error. On `approval`, queue the request for a parent (a kid asking for
   tier 2).
5. Plan the requests. On a dry run, stop here and print them.
6. Check the rate limit.
7. Ask for the confirmation the decision needs: y/N, the typed phrase, or the owner's PIN/TOTP.
   `--yes` covers only a y/N.
8. Apply the requests and read the status again.
9. Turn the status change into events. `started` is recorded *with the actor*, and anything else
   goes straight to the chore engine. Then update the snapshot and write the audit line.

Program-appliance calls get steps 3-4 in the House. The rest happens in the Bridge (interlocks,
dry run, rate limit), whose y/N goes through the House's confirmation, so `--yes`, approvals and
the terminal rules are the same everywhere. Small models fill in every option of every
appliance ("temperature": "cold" for a dishwasher). The House drops options the target appliance
doesn't have before the Bridge's strict validation.

### Tool router

`House.tool_groups(request)` is deterministic keyword routing. Each type declares its own words,
and nicknames and aliases count too. A question about the board ("who / whose / which ... ?" plus
a chore word) or a completion report ("I emptied ...") gets **only the chore tools**. A device
request gets only that type's tool, plus `get_status`. Anything else gets everything. This keeps
a 1.5B model's tool list short, and it removes the tools that could do harm with a question
("who has the dishes?" can't start the dishwasher).

### Chore core

- **People** have a role (`owner`/`adult`/`kid`/`guest`/`staff`), chat handles, quiet hours, and
  an optional PIN (a PBKDF2 hash) or TOTP secret (RFC 6238, stdlib).
- **Chores** have a schedule, a rotation and strategy, points, an optional `verify` rule
  (`<device|type>.<event>`), follow-ups and `needs_approval`.
- **Schedules** come from a deterministic parser (`schedule.py`). `materialise()` opens today's
  instances and marks older open ones `missed`.
- **Rotations** are `round_robin`, `least_points` (points in the last 7 days), `fixed`, `random`
  or `claimable`, applied per instance or per week. A week's assignee can be overridden by
  `assign --week` and `swap`.
- **`apply_event(device, event)`** records the event. Then, for every chore whose `verify` rule
  matches, it completes the open instance (or an ad-hoc one assigned to the week's person). The
  credit goes to the person who started the device, which comes from the last `started` event
  with an actor since the previous finish, and the result says "covering for" when that person
  isn't the assignee. Finally it opens the follow-ups for the next person in *their* rotation,
  without stacking duplicates.
- **Approvals:**
  - a kid's self-report on a `needs_approval` chore becomes `pending_approval`;
  - a kid's tier-2 device request is queued;
  - an adult's `approve` runs it as the approver, with the approval standing in for the y/N;
  - if it can't run yet (rate limit, interlock), it goes back to pending.

### Event ingestion

`events.poll_once()` compares each device with the snapshot in
`~/.cache/chorepilot/device-states.json` and calls `apply_event` for each event. The watcher
behind `notify_when_done` runs one poll when the program ends. Push sources (below) trigger
`events.check_device()` for one device and go into the same `apply_event`.

### Push events (push.py)

`chorepilot events push` runs one thread per source and one consumer:

- **Home Assistant**: a stdlib RFC 6455 websocket client to `/api/websocket`: `auth`, then
  `subscribe_events` for `state_changed`. Events for entities we own (a plugin device's entity
  and extra entities, a program appliance's operation_state / door / remote_start ... entities)
  mark that device changed.
- **Home Connect**: the account-wide SSE stream `GET /api/homeappliances/events`. KEEP-ALIVE
  arrives every ~55 s, so a 90 s read timeout means the stream is dead. Only keys that can
  change a chore (OperationState, DoorState, ProgramFinished/Aborted, RemoteControlStartAllowed,
  connection events ...) count, so progress ticks don't spend the 1000-a-day request quota.
  A 401 drops the cached token, and the next try refreshes it from the token file.
- **MQTT**: one persistent subscription per broker with PINGREQ keepalive. The retained value
  on subscribe is the baseline, not a change.

Every source reconnects with exponential backoff (1 s doubling to 5 min, with ±20% jitter),
which resets once a connection has lasted a minute. The consumer runs in a single thread,
because SQLite connections stay in one thread. It debounces pushes per device for 2 s, then
calls `events.check_device`: read the device, diff it against the snapshot, and apply the
type's events to the chore rules. A push and a poll therefore always mean the same thing.
Uncovered devices are polled every `-i` seconds, and covered ones get a safety poll every
`--safety` seconds.

### Front-ends and outside agents

- **Telegram** (`telegram.py`): Bot API long polling (outbound only).
  - *Identity.* An account is linked by a one-time code (8 characters, 10 minutes, single
    use, stored as a sha256 hash, 5 wrong tries an hour). After that, `people.handles.telegram_id`
    maps the sender's Telegram user id to a person. Exact match only.
  - *Requests.* Each one runs in a worker thread with its own House (source `telegram`), whose
    confirm functions are Telegram prompts:
    - an inline y/N, answerable only by the sender;
    - the typed phrase;
    - the PIN/TOTP, private chats only, with the message deleted on receipt and never logged or
      sent to the model.
  - *Background loop.* The main loop also pushes MCP confirmations, new approvals (Approve /
    Deny for parents), the `outbox` table (chore lines, reminders, "Approved: ..." messages
    that any process writes through `House.notify`) and an optional daily reminder.
- **MCP** (`mcp.py`): JSON-RPC 2.0. The protocol versions are 2025-11-25, 2025-06-18,
  2025-03-26 and 2024-11-05.
  - *Transports.* stdio, and streamable HTTP: POST `/mcp` with JSON responses and
    `Mcp-Session-Id` sessions bound to the token; GET returns 405 and DELETE ends the session.
  - *Security.* There is a bearer token on every request, Origin validation, a 127.0.0.1
    default, and `--lan` prints a warning.
  - *Tools.* The House's own OpenAI tool schemas become MCP tools, with `readOnlyHint`
    annotations, plus `whoami`. Every call gets a fresh House with source `mcp` and a
    `confirm.Broker`.
  - *Policy.* `policy.decide` denies tier 3 to external sources unconditionally and clamps
    `agents_max_tier` to 2.
- **Confirmations** (`confirm.py`, the `confirmations` table). The Broker writes the question,
  pushes it to the desktop, stderr and Telegram, and polls for the answer. A timeout is a no.
  Answers come only from the person's linked Telegram account or from `chorepilot confirm`
  at a TTY, and `mcp.confirm_channels` can narrow that to Telegram.

### Not done yet (phases 3-5 of the plan)

- Discord, SMS/Signal, an HA Assist conversation agent.
- Power-curve / door-sensor / NFC verification.
- Presence-aware rotation (skip-if-away).
- Photo-and-approve verification.
- Sync to HA todo lists, Donetick or Grocy, over their APIs only.
- Covers, garage doors and alarm panels. They will follow the lock's tier-3 path.

## Dishsoaker3000 design (models on any device, appliance bridge)

## Goals

1. **Any device.** Laptops, Macs, Pis, Termux phones, RK3588 boards, and a board in the
   dishwasher. The tool is stdlib-only Python 3.9+, so `pip install` works on a Pi Zero.
   The only runtime dependency is llama.cpp (`llama-server`, plus `ggml-rpc-server` for
   cluster mode).
2. **Bring your own model.** Read what the user already has: the llama.cpp cache, the HF
   hub cache, LM Studio, Ollama blobs (resolved through their manifests), Pollard's
   workspace, `~/models`, `~/Downloads`, and `--models-dir`. There is an optional online
   catalog: the PollardWeights GGUF rungs.
3. **Never crash the host or the appliance.** Every launch is planned against *usable*
   RAM and runs under a watchdog.
4. **Honest numbers.** Estimates come from memory-bandwidth and latency math with published
   constants (docs/RESEARCH.md). `plan` shows the arithmetic, and verdicts say STREAM when
   something technically runs but too slowly to use.

## Modules

| Module | Job |
|---|---|
| `gguf.py` | header-only GGUF reader (local file, or any `read()`-able object such as HTTP ranges); geometry, params, expert bytes, per-layer bytes, fork-only tensor types |
| `profiles.py` | `Device` dataclass + presets (dishwasher-1g, pi-*, esp32-s3, smart-appliance-module, ...), Wi-Fi link table, user JSON profiles |
| `probe.py` | live detection: total/available RAM, P-cores, Metal/CUDA, disk, LAN IP/link, optional memcpy bandwidth bench |
| `models.py` | scan the usual model dirs + Ollama manifests; skip mmproj / vocab-only / non-first shards |
| `catalog.py` | PollardWeights listing via HF API (`?blobs=true` sizes) + GGUF header via HTTP Range, cached in `~/.cache/chorepilot/catalog.json` |
| `fit.py` | the math: `fit_local`, `best_ctx`, `best_rung`, `plan_cluster`, `decode_tok_s` |
| `recommend.py` | verdicts (RUNS / TIGHT / REQUANT / CLUSTER / STREAM / NO) + table |
| `guard.py` | memory floor + `Watchdog` thread (SIGTERM, then SIGKILL) |
| `serve.py` | local / worker / host launchers, UDP beacon + scan, device-order-aware `--tensor-split` |
| `wash.py` | the narrated front door (`--prompt` = load, answer, unload; a positional request = appliance control) |
| `machines.py` | device types (dishwasher/washer/dryer) and exact model profiles (Bosch SMV6ZCX42E, WAT286H9SG, WTW876H8SG): programs, options, Home Connect keys, run times, remote-start rules; strict option validation |
| `appliance.py` | the fleet (nicknames -> one backend per appliance) and control backends: simulated, Home Assistant REST, MQTT (stdlib 3.1.1 client), Home Connect API (+ OAuth device flow); `plan()` = the exact requests, `apply()` = send them |
| `env.py` | `CHOREPILOT_*` then legacy `DISHSOAKER_*` settings; config/cache dirs; the one-time `~/.config/dishsoaker` -> `~/.config/chorepilot` copy |
| `housecfg.py` | `house.json`, presets (demo, bosch), control.json fallback, the program-appliance fleet config |
| `house.py` | the House: tool router, policy in front of every call, generic device calls, chore tools, approvals, audit, system prompt |
| `policy.py` | role x tier x source -> decision |
| `chores.py` | the SQLite chore core (people, chores, rotations, instances, events, approvals, PIN/TOTP) |
| `schedule.py` | the deterministic schedule parser + next-due |
| `events.py` | status diffs -> device events -> chore rules |
| `agent.py` | the chat loop over the House |
| `push.py` | push events: HA websocket, Home Connect SSE, MQTT subscriptions; reconnect with backoff |
| `confirm.py` | confirmations a human answers on their own channel (MCP tier 2) |
| `mcp.py` | the MCP server (stdio + streamable HTTP), tokens |
| `telegram.py` | the Telegram bot (linking, buttons, PIN prompts, approvals, announcements) |
| `commands.py` | `init`, `people` (incl. `transfer-owner`), `chores`, `approvals`, `events`, `devices`, `ask`, `confirm`, `demo` (+ the `mcp`, `telegram`, `setup`, `run`, `service`, `web` parsers) |
| `onboard.py` | `chorepilot setup`: the first-run wizard (owner + PIN/TOTP, members, model detection, HA / Home Connect / MQTT discovery, Telegram, MCP tokens, default chores), re-runnable per section, `--non-interactive` |
| `qr.py` | a stdlib QR encoder (byte mode, levels L/M, versions 1-10) for TOTP enrolment: terminal, SVG, PNG |
| `runner.py` | `chorepilot run` (events + Telegram + MCP + dashboard in one supervised process) and `chorepilot service` (systemd user unit / launchd agent) |
| `web.py` | `chorepilot web`: the dashboard server (stdlib http.server): static app shell, JSON API, PIN sign-in, CSRF, Host/Origin checks, CSP, `--public-url`, device actions through the policy with single-use confirm tokens |
| `webview.py` | what the dashboard shows: device view models from the device-type registry (rooms, tile action, status line, controls, progress), the "right now" facts, the chore board (week progress, streaks, rotations), the readable activity timeline |
| `webask.py` | "Ask the house" from the dashboard: one worker thread per request running `agent.run`; the House's confirm / phrase / PIN callbacks become questions only the asking session can answer |
| `websetup.py` | onboarding and settings in the browser on top of `onboard.Setup` (owner from this machine only, members, HA / Home Connect / MQTT, model detect + test, chores, Telegram link QR, MCP tokens, remote-access state) |
| `webllm.py` | the Local AI panel: model-server metrics (llama-server /props + /metrics, Ollama /api/ps, vLLM /metrics, /v1/models), ChorePilot's own call timings, the host from `probe` |
| `webui/` | the app: `index.html`, `app.css`, `app.js` (no framework, no build), `sw.js` (caches the shell only, never `/api/`), `manifest.webmanifest`, icons drawn by `scripts/make_icons.py` |
| `devtypes/` | device-type plugins |
| `adapters/` | simulated / ha / matter / mqtt / homeconnect for plugin devices, deferred steps |
| `control.py` | OpenAI tool schemas, the `Bridge` (validation, interlocks, confirmation, rate limit, dry-run, audit), notifier/watcher, the tool-calling agent loop, `chorepilot control` |

## Memory model

```
need = weights_file_bytes
     + kv_per_token(f16|q8_0|q4_0) * ctx      # sum over KV-bearing layers only
     + compute_buffer(n_vocab, n_embd, ubatch, ctx)
     + runtime (~60 MB)
usable = available_now - margin               # probed device
       = total - reserve                      # preset: 15% of RAM, 0.25..4 GB
fits  <=> need <= usable
```

- **KV cache.** It is computed per layer from `head_count_kv`, which can be a per-layer
  list, so SSM/linear layers count as 0 and only every Nth layer counts on hybrids with
  `full_attention_interval`. MLA models use `kv_lora_rank + rope_dim`. Sliding-window
  layers are counted as full, which over-estimates on purpose.
- **Compute buffer.** It is dominated by the logits tensor, `n_vocab * ubatch * 4`. On
  boards with 2 GB or less Dishsoaker uses `-ub 128 -b 128`: for a 152k vocabulary that
  is 78 MB instead of 311 MB, and that saving is what lets a 0.5B fit in 1 GB.
- **Metal.** Apple GPUs can only wire about two thirds of unified RAM on small Macs, so
  a model larger than that runs on the CPU (`-ngl 0`).
- **mmap streaming.** If the weights don't fit but KV plus compute do, the verdict is
  STREAM: speed ~ 1 / (missed bytes / disk read + bytes / memory bandwidth). It is labelled
  as impractical.

## Speed model

Single-stream decode reads every active weight once per token:

```
tok/s ~= eff * BW / (active_bytes + kv_bytes(ctx/2))
eff = 0.8 of the preset's sustained (STREAM-like) bandwidth for CPU,
      0.7 of nameplate bandwidth for GPU
```

For MoE models, `active_bytes = dense + expert_bytes * used / count`. Calibration checks:
Pi 5 runs an 8B Q4_K_M at about 1.5 tok/s and a 3B Q4 at about 3.5-5 tok/s (published
figures: 4.7-6.9 for a 3B), so the model is on the conservative side.

## Cluster model (llama.cpp RPC)

llama.cpp's RPC backend makes a *pipeline*: each node holds whole layers, plus those
layers' KV cache. For each token the host sends the activation, `n_embd * 4 bytes`
(20 KB for a 27B), to each worker in turn and gets it back. Activations between two
workers relay through the host. After the first token the graph is cached
(`GRAPH_RECOMPUTE`), so each hop costs about 2 round trips.

```
t_token = sum_i(shard_bytes_i / (eff_i * BW_i))       # compute: the shards run in sequence
        + sum_workers(2 * RTT + 2 * act_bytes / net)  # network: latency dominates
first load = sum_workers(shard_bytes / net)           # tensors are uploaded once; -c caches them
```

Two consequences shape the UX:

- **Splitting adds capacity, not speed.** Two Pi 5s are not faster than one; they can
  hold a model that one can't. `--strategy fastest` (the default) fills the
  highest-bandwidth nodes first and leaves slow nodes, often the dishwasher, idle unless
  they are needed. `--strategy proportional` gives every node a share, which is the
  "the dishwasher is helping" mode.
- **Wi-Fi bandwidth hardly matters for decode; RTT does.** It matters a great deal for
  the first load: 5 GB over a 40 Mbps Wi-Fi 4 link takes about 17 minutes. That is why
  workers get `--cache`.

**Memory guard in a cluster.** Current `ggml-rpc-server` has no memory cap of its own.
The *host* sizes each worker's share from that worker's own beacon:
`usable - compute - runtime`, giving `layers_i`. Each worker also runs the watchdog
locally, so a misbehaving host can't push the appliance into OOM: the worker's RPC
server is stopped instead.

**Device mapping.** `--tensor-split` indexes llama.cpp's device list, which mixes RPC
devices with local GPUs. `serve_host` calls `llama-server --rpc ... --list-devices` and
maps each RPC entry to its endpoint, so the split lines up whatever the order. When the
host has no GPU, its layers stay on the CPU and `-ngl` counts only the offloaded layers.

**Device mapping detail (verified).** llama.cpp moves RPC devices to the *front* of the
model's device list, ahead of the local GPUs (`src/llama.cpp`, "add RPC servers at the
front of the list"). `--list-devices` shows a different order: `MTL0, BLAS, RPC0`. That
is why the host passes an explicit `--device RPC0,...,MTL0`. Also, `--rpc` has to come
*before* `--list-devices` on the command line, because `--list-devices` prints and exits
as soon as it is parsed.

**Failure semantics.** If a worker disappears mid-session (its guard tripped, it lost
power, it was unplugged), the llama.cpp host aborts with "Remote RPC server crashed".
The appliance stays up; the host process dies. Re-run `serve --scan` to re-plan without
that worker. Wrappers trap SIGTERM/SIGHUP and take their llama.cpp child down with them,
so no orphaned server is left holding RAM or a port. A port pre-flight refuses to start
when the port is already in use.

## Discovery

The beacon is a UDP request/response on 50053: `DISHSOAKER?` gets back
`{"rpc": "ip:port", "device": {...live probe...}}`. `chorepilot scan` broadcasts, or asks
specific hosts. There is no auth (same trust model as llama.cpp RPC), and the beacon
binds wherever the worker binds, so it is loopback-only without `--lan`.

## What is deliberately not done (yet)

- Running on the appliance's own Wi-Fi MCU: no llama.cpp, signed firmware, a 9600-baud
  link to the mainboard. Instead the model runs elsewhere and *controls* the appliance:
  `chorepilot control` / `chorepilot wash "<request>"` (docs/CONTROL.md).
- Injecting commands on the BSH internal D-Bus. The ESPHome `bshdbus` component is
  receive-only and the community send stack is experimental; Dishsoaker's MQTT backend will
  publish whatever command topics you configure, but the documented path for *starting* a BSH
  machine is the Home Connect API.

## Control design

- **Two-phase writes.** Each backend's `plan(action)` returns the concrete operations
  (HTTP method/URL/body, or MQTT topic/payload); `apply()` sends them. `--dry-run` is just
  "plan, print, don't apply", so dry-run output is exactly what would hit the wire.
- **Status first.** Every write re-reads status and enforces interlocks (offline, already
  running, door open, remote start disarmed, nothing to pause/resume/cancel) before asking
  for confirmation, so the user is never asked to approve something that would fail.
- **Errors are tool results.** The bridge never raises into the agent loop; the model gets
  `{"ok": false, "error": ...}` and explains it. The model sees outcomes, not request URLs.
- **The model gets help with numbers.** Status carries `remaining: "2 h 29 min"` next to
  `remaining_s`; a 1.5B model misread raw seconds as hours.
- **notify_when_done** detaches `chorepilot control wait` (own session, logs to
  `~/.cache/chorepilot/watch.log`) so the CLI returns immediately. "finished" or idle near
  the expected end = done; anything else = "stopped before finishing".
- Tensor parallelism over Wi-Fi (distributed-llama style). It needs about 2 syncs per
  layer per token, which is 0.6 s+ of pure latency for 64 layers at 5 ms RTT. Pipeline
  is the right default on Wi-Fi.
- An automatic download and re-quantize step. `recommend` names the rung; the user runs
  `hf download` or `llama-quantize`.
- MoE expert offload (`--n-cpu-moe`) and NVMe n-gram tables are noted in RESEARCH.md
  but not planned for yet.


## The dashboard (0.6)

The dashboard is a single-page app served by `web.py` from `chorepilot/webui/`. The server
does all decisions; the page only renders and collects a human's answers.

- **One path for every action.** A tile tap, a sheet control and a request typed into Ask the
  house all end in `House.call` with source `web`, so the policy tiers, interlocks, rate limits,
  event credit and the audit log are the same as at the terminal or in Telegram.
- **Confirmations.** For a direct control the server first works out the decision itself
  (`policy.decide` on the validated action). `confirm` returns a question plus a token bound to
  (session, device, tool, arguments), valid once for two minutes; the page asks, and only a
  repeat with that token runs. `strong_confirm` needs the typed phrase; `second_factor` needs the
  PIN / TOTP code, checked and rate-limited by `House._second_factor`. Kids' tier-2 requests are
  queued by the House as before. For Ask the house the House's confirm callbacks block the worker
  thread until the same session answers (`webask.Job`), or two minutes pass (a no).
- **Registry-driven UI.** `DeviceType` gained dashboard hooks: `room`, `icon`, `ui_state`,
  `ui_active`, `ui_attention`, `ui_primary` (the one-tap action, generic from pairs like
  turn_on/turn_off, lock/unlock, open/close) and `ui_controls` (actions with typed, limited
  parameters). The page picks widgets from the parameter types: a big stepper for a reported
  target, sliders for 0-100 values, segmented choices, switches, steppers.
- **Remote access.** No relay of ours. `--public-url https://...` (Tailscale serve or a
  Cloudflare Tunnel) adds that host to the accepted Host and Origin set and turns on `Secure`
  cookies and HSTS for requests that arrive through it. The first owner can only be created by a
  loopback client with a loopback Host and no forwarding headers.
- **PWA.** The service worker caches the shell (HTML, CSS, JS, icons) network-first and never
  touches `/api/`; the page always shows live state or says it can't reach home.

### 0.7: the look

Dark first (`theme.js` applies the saved theme before first paint; dark unless the person picks
light or "match this device"). Tokens live on `:root[data-theme]`: graphite surfaces
(#050505 / #111 / #171717), hairlines (#262626), radii of 2-6 px, one accent (#C8F031; text on
white uses #4E6600). IBM Plex Sans (OFL, variable) is self-hosted under `webui/fonts/`, so the CSP still allows
only 'self'. Motion is CSS keyframes plus a small requestAnimationFrame count-up, all disabled by
prefers-reduced-motion. `GET /api/system` feeds the Local AI panel and the status pill in the top
bar; it never blocks (0.8 s per probe, cached 3 s) and shows a dash for anything a server doesn't
expose.

### 0.7.1: polish

- **Ask the house from anywhere.** The console is one component (`consoleHtml` /
  `wireConsole` / `updateConsole` in `app.js`), shown inline on Home and as a palette sheet on
  every view: the top bar button or Cmd/Ctrl+K. Both share one conversation and the same
  `/api/ask` job, confirm cards and policy; nothing changed on the server.
- **Phone home.** The four readouts are a compact 2x2 grid (28 px numerals) so Ask the house
  sits above the fold. While the input is docked above the tab bar, the page reserves that space
  (bottom padding, `scroll-padding-bottom`, toasts lifted), including the safe-area insets.
- **Device grid.** Rooms share one column grid (CSS subgrid, `auto-fill` of 240-260 px
  columns): each room spans as many columns as it has devices, up to the row, and smaller rooms
  pack in beside bigger ones, so a wide screen fills instead of leaving a column empty. The
  Devices page may use up to 1920 px.
- **Undo a "done".** `ChoreDB.complete` records who pressed done and what the completion opened
  (`chore_done` event under `chore:<id>`, kept out of the event feed). `undo_complete` reopens the
  instance (or removes an ad-hoc one), so points and streaks, which are derived from completed
  instances, go back; it cancels a pending parent approval, removes follow-ups nobody has touched
  and steps their rotation back. An owner or adult may undo any completion; anyone else only
  their own, for `UNDO_WINDOW_S` (2 min). `POST /api/chores/<id>/undo`, audited as `undo_chore`.
- **Rate limits per tier** (`control.RateLimiter`): tier 1 per device and generous, with a
  stricter budget of its own for MCP / external callers; tiers 2 and 3 house-wide with a gap
  between starts. `house.json` `"rate_limits"`; see [CONTROL.md](CONTROL.md#31-rate-limits).
