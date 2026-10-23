# ChorePilot is installed

1. On the ChorePilot machine, make a token for Hermes (shown once):
   `chorepilot mcp token add <person> --client hermes`
2. Keep ChorePilot's MCP server running there: `chorepilot run` (or `chorepilot mcp --http`;
   add `--lan` for another machine on your network, or put `tailscale serve --bg 8770` in front).
3. If you skipped the questions, set `CHOREPILOT_URL` and `CHOREPILOT_TOKEN` in Hermes's `.env`.
4. `hermes plugins enable chorepilot` (if you didn't pass `--enable`), then start a new session
   and ask "what's on the chore board?"

Water and heat actions wait for that person's yes in Telegram or the ChorePilot dashboard.
Unlocking is never available to Hermes.
