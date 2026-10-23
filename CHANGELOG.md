# Changelog

## 0.7.1

Dashboard polish, and rate limits that no longer get in a person's way.

- **Ask the house on every view**: a command palette (the top bar button, or Cmd/Ctrl+K)
  opens the same console as a sheet anywhere, with the same `/api/ask` flow, confirm cards
  and policy. The conversation is shared with the console on Home.
- **Phone home**: the hero readouts are a compact 2x2 grid with smaller numerals, so Ask the
  house is above the fold at 390x844, and nothing hides behind the docked input bar (bottom
  space, scroll padding and toasts account for it and for the safe-area insets).
- **Device grid on wide screens**: rooms share one responsive column grid and small rooms pack
  in beside bigger ones, so 1440 px and wider fill cleanly instead of stopping at four tiles;
  the Devices page can use up to 1920 px. Other tile grids use `auto-fill` with a 220 px minimum.
- **Undo a "done"**: right after marking a chore done the toast offers Undo, and finished chores
  have an Undo button. An adult can undo any completion; the person who marked it can undo
  their own within 2 minutes; kids can't undo other people's. Points and streaks go back, a
  pending parent approval is cancelled, untouched follow-ups are removed and their rotation steps
  back. Audited (`undo_chore`) and shown in Activity. `POST /api/chores/<id>/undo`.
- **Rate limits per safety tier**, configurable in `house.json` `"rate_limits"`: tier 1 (lights,
  blinds, speakers...) 30 per minute per device; MCP / external agents 6 per minute per device
  and 30 per 10 min, on a budget of their own; tier 2 (water / heat) and tier 3 (security) stay
  at 6 per 10 min across the house with 60 s between starts of one device. Before, 6 writes per
  10 min were shared by every device, so quick light toggling got blocked.
  [docs/CONTROL.md](docs/CONTROL.md#31-rate-limits).
- **Hermes Agent plugin** (`hermes/chorepilot/`): `hermes plugins install
  WestWaters/ChorePilot/hermes/chorepilot --enable`. Seven `chorepilot_*` tools and a
  `chorepilot:household` skill, as a stdlib client of `chorepilot mcp --http`, so the policy,
  confirmations and tokens are the MCP server's, unchanged. Asks for `CHOREPILOT_URL` and
  `CHOREPILOT_TOKEN` at install; plain HTTP only to this machine or a private network. Passes
  Hermes's own `hermes plugins validate`. [docs/HERMES.md](docs/HERMES.md).

## 0.7.0

The dashboard's second design: a technical, high-performance look, dark first.

- **Visual language**: graphite and near-black surfaces, hairline rules, 2-6 px radii, a
  technical grid behind the hero, uppercase eyebrow labels, tight bold headlines, big tabular
  numerals, and one signal-lime accent (#C8F031 on dark; #4E6600 for text on white). A crisp
  white light theme with the same structure. Top navigation bar on desktop and tablet, bottom tabs
  on phones.
- **Typeface**: IBM Plex Sans (SIL OFL 1.1, variable), self-hosted in `chorepilot/webui/fonts/` with its licence;
  no font CDN.
- **House status hero**: inside temperature, power now (from devices that report it), devices on,
  chores done this week, with count-up numerals and live progress bars for running appliances.
- **Local AI panel** (`GET /api/system`): the model, generation tokens/s, time to first token,
  context size, GPU memory or KV-cache use, a tokens/s sparkline, and the host from
  `chorepilot probe`. Reads llama-server `/props` + `/metrics`, Ollama `/api/ps`, vLLM `/metrics`
  and `/v1/models`, plus ChorePilot's own timings of its last calls; anything missing shows a dash.
- **Ask the house as a console**: a trace of every model turn (time, tool calls, tokens/s, first
  token) and tool call (tool, device, arguments, outcome), crisp confirm cards with the tier.
- **Devices** as spec panels: device id, name, a big live readout, state line, progress bar, a
  lit border and glow on active devices; detail sheets with a 270-degree gauge for setpoints.
- **Chores** as a performance board: ranked leaderboard with weekly rings, progress bars, points,
  streaks and today's status.
- Staggered reveals and number tick-ups, all off under prefers-reduced-motion.
- Activity says "asked to" / "tried to" for actions that were declined or failed.

## 0.6.0

The dashboard, rebuilt as the household app.

- **New design**: a calm, neutral look with one accent colour, light and dark themes (follows the
  device, with a switch), a hand-drawn icon set, real type hierarchy, an 8 px grid, subtle motion
  that respects reduced-motion, keyboard and screen-reader support. Sidebar on desktop, an icon
  rail on tablets, a bottom tab bar on phones. Installable on a phone's home screen (PWA; the
  service worker caches the app shell only, never your data).
- **Home**: a "right now" line, Ask the house, what is running with time left, your chores today,
  the family's week, recent activity, and a setup checklist for the owner.
- **Ask the house** in the browser (`POST /api/ask`): the same agent and policy path as the
  terminal and Telegram; inline Yes / No for water and heat, the typed phrase for strong
  confirmations, PIN re-entry for unlocking, kids' requests to the approvals queue; steps,
  answer and audit shown. Voice input where the browser supports it. Without a model it degrades
  to a one-line hint.
- **Devices**: tiles by room with live state and a one-tap action, and a detail sheet built from
  the device-type registry (stepper, sliders, choices, program picker, progress ring). Direct
  controls go through the policy (`POST /api/devices/<id>/<action>`) with single-use confirm
  tokens.
- **Chores**: per-person week progress, points, streaks, due today, rotations this week and next,
  device-verified badges, reassign and change-this-week for adults.
- **Approvals** with who / what / why, and MCP agent confirmations answerable on the dashboard
  (new `web` confirm channel; `mcp.confirm_channels` can leave it out).
- **Activity**: a readable day-grouped timeline; the raw audit log is still there.
- **Onboarding and settings in the browser** on top of `chorepilot setup`: owner (from this
  machine only), people and PINs, Home Assistant with entity selection, Home Connect device flow
  with a QR code, MQTT, simulated devices, model detection and test, default chores, Telegram
  link QR codes, MCP tokens, remote access.
- **Remote access** without a relay: `chorepilot web --public-url https://...` for Tailscale serve
  or a Cloudflare Tunnel (accepted Host and Origin, Secure cookies, HSTS); `web.public_url` in
  house.json for `chorepilot run`. See docs/REMOTE-ACCESS.md.
- Telegram: `/start <code>` links an account, so a link QR code works in one tap.
- Device types gained dashboard hooks (`room`, `ui_state`, `ui_primary`, `ui_controls`, ...);
  the sprinkler simulator reports its run time.
- Fixed: `chorepilot run --web-port 0` used the configured port instead of a free one.
- Content-Security-Policy tightened (explicit script/style/connect/worker sources, no inline
  code), plus COOP, CORP and Permissions-Policy headers.
