# ChorePilot add-on

ChorePilot is your smart-home AI agent assistant: a local model runs your appliances through
Home Assistant and keeps the household's chores fair (who has the dishes this week, verified by
the dishwasher itself).

## First start

1. Set **owner** (your name) and **owner_pin** (4-12 digits) in the options. You become the
   household's owner: only you can unlock doors, open the garage or disarm the alarm, and only
   with that PIN.
2. Add **members** like `Sam:adult` or `Jordan:kid`.
3. Point **model_url** at a model server on your network (llama-server, Ollama, LM Studio, or
   any OpenAI-compatible URL). ChorePilot never sends your data to a cloud model.
4. Start the add-on. On the first start it maps your Home Assistant entities (lights, climate,
   locks, covers, alarm panel, media players, water heaters, humidifiers, purifiers, EV
   charger switches, valves) and writes a set of default chores.
5. Open the dashboard on port 8771 and sign in with your name and PIN.

Telegram: create your own bot with @BotFather, paste its token into **telegram_token**, restart,
then link each person with a one-time code from the add-on log (or `chorepilot telegram link`).

The MCP port (8770) is off until you map it; agents need a token (`chorepilot setup mcp`).

## Safety

Tier 0 reads are free; tier 1 (lights, blinds, media) is allowed for adults (and kids if you
allow it); tier 2 (water, heat, EV charging) needs an adult's yes; tier 3 (unlock, open the
garage, disarm) needs the owner's PIN and is never available to outside agents or schedules.
