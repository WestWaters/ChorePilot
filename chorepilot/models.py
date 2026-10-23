"""Find the models the user already has on disk (bring-your-own-model).

Looks in the usual places llama.cpp, Hugging Face, LM Studio, Ollama and Pollard put
GGUFs, plus anything in $CHOREPILOT_MODELS or --models-dir. Only reads headers.
"""
from __future__ import annotations

import json
import os

from .gguf import GGUFInfo, is_gguf, read_gguf
from .env import getenv

DEFAULT_DIRS = [
    "~/models", "~/Models", "~/.cache/llama.cpp", "~/.cache/huggingface/hub",
    "~/.lmstudio/models", "~/.cache/lm-studio/models", "~/pollard", "~/Downloads",
]
OLLAMA_DIR = "~/.ollama/models"
MAX_DEPTH = 6


def _walk(root, depth=MAX_DEPTH):
    root = os.path.expanduser(root)
    if not os.path.isdir(root):
        return
    base = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        if dirpath.count(os.sep) - base >= depth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if not d.startswith(".") or d in (".cache",)]
        for fn in filenames:
            if fn.endswith(".gguf"):
                yield os.path.join(dirpath, fn)


def _ollama_models():
    """Ollama stores GGUFs as sha256 blobs; manifests map name:tag -> blob."""
    base = os.path.expanduser(OLLAMA_DIR)
    man = os.path.join(base, "manifests")
    out = {}
    if not os.path.isdir(man):
        return out
    for dirpath, _, files in os.walk(man):
        for fn in files:
            try:
                with open(os.path.join(dirpath, fn)) as f:
                    m = json.load(f)
            except (OSError, ValueError):
                continue
            for layer in m.get("layers", []):
                if layer.get("mediaType") == "application/vnd.ollama.image.model":
                    blob = os.path.join(base, "blobs", layer["digest"].replace(":", "-"))
                    if os.path.exists(blob):
                        name = os.path.relpath(os.path.join(dirpath, fn), man).split(os.sep)
                        out[blob] = "ollama:" + ":".join(name[-2:])
    return out


def _is_main_model(info: GGUFInfo) -> bool:
    if info.n_params < 10_000:          # vocab-only files
        return False
    if info.meta.get("general.type") in ("mmproj", "adapter", "imatrix"):
        return False
    if info.arch in ("clip", "mmproj"):
        return False
    return True


def scan(extra_dirs=None, include_defaults=True, include_ollama=True):
    """Return a list of GGUFInfo for every runnable model found (first shard only)."""
    dirs = list(extra_dirs or [])
    env = getenv("MODELS")
    if env:
        dirs += env.split(os.pathsep)
    if include_defaults:
        dirs += DEFAULT_DIRS
    seen, found = set(), []
    candidates = []
    for d in dirs:
        if os.path.isfile(os.path.expanduser(d)):
            candidates.append((os.path.expanduser(d), None))
            continue
        candidates += [(p, None) for p in _walk(d)]
    if include_ollama:
        candidates += list(_ollama_models().items())
    for path, label in candidates:
        # skip non-first shards of split GGUFs
        if "-of-" in path and "-00001-of-" not in path:
            continue
        try:
            real = os.path.realpath(path)
        except OSError:
            continue
        if real in seen or not is_gguf(real):
            continue
        seen.add(real)
        try:
            info = read_gguf(real)
        except (OSError, ValueError, UnicodeDecodeError, MemoryError):
            continue
        if not _is_main_model(info):
            continue
        info.path = path
        if label:
            info.meta["chorepilot.label"] = label
        found.append(info)
    return found
