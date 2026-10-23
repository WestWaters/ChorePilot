#!/usr/bin/env bash
# A fresh install, end to end, in a throwaway HOME: `chorepilot setup --non-interactive` makes
# whoever runs it the owner, then a local model drives the simulated house.
#
#   chorepilot serve --model ~/models/<an instruct model>.gguf --port 8092 --ctx 8192 &
#   scripts/fresh_install_demo.sh http://127.0.0.1:8092
#
# Nothing outside the temp HOME is written; no real device is touched.
set -u
URL=${1:-http://127.0.0.1:8092}
REPO=$(cd "$(dirname "$0")/.." && pwd)
export HOME=${DEMO_HOME:-$(mktemp -d)}
unset CHOREPILOT_CONFIG_DIR CHOREPILOT_CACHE_DIR CHOREPILOT_USER
export CHOREPILOT_NOTIFY_DESKTOP=0 CHOREPILOT_SIM_SPEED=${CHOREPILOT_SIM_SPEED:-600}
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
cd "$HOME"
cp_() { python3 -m chorepilot "$@"; }
say() { printf '\n==== %s\n' "$*"; }
yes_tty() { python3 "$REPO/scripts/answer.py" "$1" "$2" -- python3 -m chorepilot "${@:3}"; }

say "0. a brand-new machine: nobody exists yet"
cp_ people list

say "1. chorepilot setup --non-interactive: the installer (Alex) becomes the owner"
cp_ setup --non-interactive --household "Our home" --unit F \
    --owner Alex --owner-pin 2468 --totp \
    --member Sam:adult:1357 --member Jordan:kid:1111 --member Riley:kid \
    --model-url "$URL" --simulated --mcp-client claude-desktop --chores default
cp_ setup status
ls -l "$HOME/.config/chorepilot"

say "2. Alex (no --as needed: the person who set it up): \"wash my dishes\""
yes_tty "[y/N]" y ask "wash my dishes" --no-watch

say "3. Sam: \"who has the dishwasher this week?\""
cp_ ask "who has the dishwasher this week?" --as Sam

say "4. the dishwasher finishes -> 'Run the dishwasher' verified, 'Empty the dishwasher' opens"
for _ in $(seq 1 60); do
  st=$(cp_ control status --appliance dishwasher 2>/dev/null)
  case "$st" in *running*|*delayed*) sleep 1 ;; *) break ;; esac
done
cp_ events poll
cp_ chores list

say "5. tier 3: Alex opens the garage (owner + PIN at the prompt); Jordan closes it (tier 1)"
yes_tty "TOTP code:" 2468 ask "open the garage door"
cp_ ask "close the garage door" --as Jordan

say "6. Jordan (a kid): \"charge the car\" -> queued for a parent"
cp_ ask "charge the car" --as Jordan
cp_ approvals list

say "7. Sam approves it on the web dashboard (PIN sign-in, CSRF token)"
cp_ web --port 18781 2>/dev/null &
WEB=$!
sleep 1.5
J=$(mktemp)
LOGIN=$(curl -s -c "$J" -H 'Content-Type: application/json' -d '{"name":"Sam","pin":"1357"}' http://127.0.0.1:18781/api/login)
echo "login: $LOGIN" | sed -E 's/"csrf": "[^"]+"/"csrf": "..."/'
CSRF=$(printf '%s' "$LOGIN" | python3 -c 'import json,sys; print(json.load(sys.stdin)["csrf"])')
curl -s -b "$J" -X POST -H "X-ChorePilot-CSRF: $CSRF" -H 'Content-Type: application/json' \
     -d '{"approve": true}' http://127.0.0.1:18781/api/approvals/1 | python3 -c '
import json, sys; r = json.load(sys.stdin); print("approve #1:", r.get("state"), (r.get("result") or {}).get("status"))'
curl -s -b "$J" http://127.0.0.1:18781/api/board | python3 -c '
import json, sys; b = json.load(sys.stdin)
print("board:", ", ".join("%s %s pts/%s open" % (p["name"], p["points"], p["open"]) for p in b["people"]))
print("open:", "; ".join("%s -> %s" % (i["chore"], i["who"]) for i in b["open"]))'
kill $WEB 2>/dev/null

say "8. Riley (a kid) arms the alarm (tier 1); Sam tries to disarm it (owner only)"
cp_ ask "arm the alarm" --as Riley
cp_ ask "disarm the alarm" --as Sam

say "9. Alex: \"set the water heater to 120\" (heat: y/N)"
yes_tty "[y/N]" y ask "set the water heater to 120"

say "10. Riley: \"turn the music up to 100\" (the speaker is capped at 80)"
cp_ ask "turn the music up to 100" --as Riley

say "11. chorepilot run --check (what \`chorepilot run\` would start)"
cp_ run --check

say "audit log (last 12)"
tail -n 12 "$HOME/.config/chorepilot/audit.log"
echo "(demo HOME: $HOME)"
