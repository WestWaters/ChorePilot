#!/usr/bin/env bash
# ChorePilot end-to-end demo: a local model drives the simulated house and the chore board.
#
#   chorepilot serve --model ~/models/<an instruct model>.gguf --port 8091 --ctx 8192 &
#   scripts/demo.sh http://127.0.0.1:8091
#
# Everything lives in a throwaway config/cache directory; no real device is touched.
# scripts/answer.py plays the person typing "y" at the confirmation prompt (a real pty).
set -u
URL=${1:-http://127.0.0.1:8091}
ROOT=${DEMO_DIR:-$(mktemp -d)}
export CHOREPILOT_CONFIG_DIR="$ROOT/cfg" CHOREPILOT_CACHE_DIR="$ROOT/cache" CHOREPILOT_NOTIFY_DESKTOP=0
cd "$(dirname "$0")/.."
cp_() { python3 -m chorepilot "$@"; }
say() { printf '\n==== %s\n' "$*"; }
yes_tty() { python3 scripts/answer.py "$1" "$2" -- python3 -m chorepilot "${@:3}"; }

say "setup: simulated demo house + demo household (Alex owner, Sam adult, Jordan and Riley kids)"
cp_ init demo --force
cp_ demo seed --pin 2468 --force
cp_ events poll >/dev/null

say "1. Alex: \"wash my dishes\" (water + heat -> Alex confirms with y)"
yes_tty "[y/N]" y ask "wash my dishes" --as Alex --url "$URL" --no-watch

say "2. Sam: \"who has dishes this week?\""
cp_ ask "who has dishes this week?" --as Sam --url "$URL"

say "3. the dishwasher finishes -> 'Dishes' verified, 'Empty the dishwasher' -> next in rotation"
for _ in $(seq 1 40); do
  st=$(cp_ control status --appliance dishwasher 2>/dev/null)
  case "$st" in *running*|*delayed*) sleep 2 ;; *) break ;; esac
done
cp_ control status --appliance dishwasher
cp_ events poll
cp_ chores list

say "4. Alex: \"set the AC to 72\" (inside the comfort band: tier 1, no prompt)"
cp_ ask "set the AC to 72" --as Alex --url "$URL"

say "5a. Alex: \"water the lawn for 10 minutes\" with nobody at a terminal -> not confirmed"
cp_ ask "water the lawn for 10 minutes" --as Alex --url "$URL" </dev/null

say "5b. the same, and Alex types y at the confirmation"
yes_tty "[y/N]" y ask "water the lawn for 10 minutes" --as Alex --url "$URL"

say "5c. Jordan (a kid) asks for it -> queued for a parent"
cp_ devices do lawn_sprinkler stop --as Alex >/dev/null
cp_ ask "water the lawn for 10 minutes" --as Jordan --url "$URL"
cp_ approvals list

say "6. Alex approves Jordan's request (the approval is the adult's yes)"
echo "(waiting 61 s: the rate limit allows one start per device per minute, and 5b just started it)"
sleep 61
cp_ approvals approve 1 --as Alex

say "7. tier 3: Sam asks to unlock the front door (denied), then Alex (owner + PIN at the prompt)"
cp_ ask "unlock the front door" --as Sam --url "$URL"
yes_tty "TOTP code:" 2468 ask "unlock the front door" --as Alex --url "$URL"

say "audit log (last 10)"
tail -n 10 "$CHOREPILOT_CONFIG_DIR/audit.log"
echo "(demo files: $ROOT)"
