#!/bin/sh
# ChorePilot add-on entry point. State lives in /data (kept across updates and backups).
set -e
export CHOREPILOT_CONFIG_DIR=/data/chorepilot CHOREPILOT_CACHE_DIR=/data/cache
export CHOREPILOT_WEB_HOST=0.0.0.0 CHOREPILOT_MCP_HOST=0.0.0.0 CHOREPILOT_NOTIFY_DESKTOP=0
export PYTHONUNBUFFERED=1
# Home Assistant through the Supervisor proxy: no long-lived token needed.
export HA_URL=http://supervisor/core HA_TOKEN="${SUPERVISOR_TOKEN}"

python3 - <<'PY'
import json, os, subprocess, sys
o = json.load(open("/data/options.json"))
from chorepilot.chores import ChoreDB
args = ["chorepilot", "setup", "--non-interactive", "--no-probe",
        "--household", o.get("household") or "Our home", "--unit", o.get("unit") or "C"]
if not ChoreDB().owners():
    if not o.get("owner"):
        sys.exit("ChorePilot: set 'owner' (your name) and 'owner_pin' in the add-on options, then restart")
    args += ["--owner", o["owner"]]
    if o.get("owner_pin"):
        args += ["--owner-pin", str(o["owner_pin"])]
    for m in o.get("members") or []:
        args += ["--member", m]
    if o.get("import_entities", True):
        args += ["--ha-url", "http://supervisor/core", "--ha-token", os.environ.get("HA_TOKEN", "")]
    args += ["--chores", "default" if o.get("default_chores", True) else "none"]
else:
    args += ["--chores", "none"]
args += ["--model-url", o.get("model_url") or "none"]
if o.get("model_name"):
    args += ["--model-name", o["model_name"]]
if o.get("telegram_token"):
    args += ["--telegram-token", o["telegram_token"]]
subprocess.run(args, check=True)
PY

exec chorepilot run
