"""Environment, config and cache locations, with the Dishsoaker3000 names kept working.

ChorePilot started life as Dishsoaker3000. Every setting is read as CHOREPILOT_<NAME> first and
DISHSOAKER_<NAME> second, so old shells, launchd plists and scripts keep working. The config
directory moved from ~/.config/dishsoaker to ~/.config/chorepilot; the first time ChorePilot
needs it and only the old one exists, the old files are copied across (permissions kept, the
old directory left in place) and a MIGRATED-FROM-DISHSOAKER.txt note is written.
"""
from __future__ import annotations

import os
import shutil

NEW, OLD = "CHOREPILOT_", "DISHSOAKER_"


def getenv(name, default=None):
    """$CHOREPILOT_<name>, else the legacy $DISHSOAKER_<name>, else `default`."""
    v = os.environ.get(NEW + name)
    if v is None:
        v = os.environ.get(OLD + name)
    return default if v is None else v


def _migrate(old, new):
    """Copy ~/.config/dishsoaker -> ~/.config/chorepilot once. Never overwrites, never deletes."""
    if os.path.isdir(new) or not os.path.isdir(old):
        return False
    shutil.copytree(old, new, copy_function=shutil.copy2)
    os.chmod(new, 0o700)
    with open(os.path.join(new, "MIGRATED-FROM-DISHSOAKER.txt"), "w") as f:
        f.write(f"Copied from {old} by ChorePilot. The old directory was left as it was; "
                "delete it once you are happy.\n")
    return True


def config_dir():
    explicit = getenv("CONFIG_DIR")
    if explicit:
        return explicit
    new = os.path.expanduser("~/.config/chorepilot")
    _migrate(os.path.expanduser("~/.config/dishsoaker"), new)
    return new


def cache_dir():
    return getenv("CACHE_DIR") or os.path.expanduser("~/.cache/chorepilot")


# ------------------------------------------------------------------ secrets
SECRETS_FILE = "secrets.json"


def secrets_path():
    return os.path.join(config_dir(), SECRETS_FILE)


def _read_secrets():
    import json
    try:
        with open(secrets_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def secret(key, envvar=None, default=None):
    """A credential: $envvar first (12-factor, Docker, the HA add-on), else the 0600
    ~/.config/chorepilot/secrets.json that `chorepilot setup` writes. Never in house.json."""
    if envvar and os.environ.get(envvar):
        return os.environ[envvar]
    v = _read_secrets().get(key)
    return default if v in (None, "") else v


def set_secret(key, value):
    """Store (or with value=None, remove) one credential in secrets.json, mode 0600."""
    import json
    d = _read_secrets()
    if value is None:
        d.pop(key, None)
    else:
        d[key] = value
    path = secrets_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(d, f, indent=2)
    os.chmod(path, 0o600)
    return path
