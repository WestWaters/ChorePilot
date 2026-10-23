#!/bin/sh
# Copy the ChorePilot package into the add-on's build context (run before building the add-on).
set -e
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
rm -rf "$here/chorepilot/src"
mkdir -p "$here/chorepilot/src"
cp -R "$root/chorepilot" "$root/pyproject.toml" "$root/README.md" "$root/LICENSE" "$here/chorepilot/src/"
find "$here/chorepilot/src" -name __pycache__ -prune -exec rm -rf {} +
echo "synced $root -> $here/chorepilot/src"
