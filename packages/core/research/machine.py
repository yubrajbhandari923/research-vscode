"""Machine-level registry of known projects (`<config dir>/projects.json`), so `self-update` can find supervisors to restart.

Written by `init` (via `localbin.install`), RPC startup and `supervisor start`. Atomic, 0600, and tolerant: a missing or
corrupt file reads as empty and is rewritten on the next `remember`.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

from . import durability

MAX_PROJECTS = 200


def path() -> str:
    from .configfile import machine_path
    return os.path.join(os.path.dirname(machine_path() or os.path.expanduser("~/.config/research-os/config.yaml")), "projects.json")


def _load() -> Dict[str, Any]:
    try:
        with open(path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get("projects"), dict) else {"projects": {}}
    except (OSError, ValueError):
        return {"projects": {}}


def remember(root: str) -> None:
    """Record `root` (absolute, symlinks resolved). Never raises: the registry is a convenience."""
    try:
        root = os.path.realpath(root)
        p = path()
        with durability.lock(p + ".lock"):
            d = _load()
            d["projects"][root] = {"last_seen": time.time()}
            if len(d["projects"]) > MAX_PROJECTS:     # drop the least recently seen
                keep = sorted(d["projects"].items(), key=lambda kv: kv[1].get("last_seen", 0), reverse=True)[:MAX_PROJECTS]
                d["projects"] = dict(keep)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            durability.replace_bytes(p, (json.dumps(d, indent=1, sort_keys=True) + "\n").encode("utf-8"), 0o600)
    except Exception:
        pass


def known_projects() -> List[str]:
    """Registered roots that still look like projects, most recently seen first."""
    items = sorted(_load()["projects"].items(), key=lambda kv: (kv[1] or {}).get("last_seen", 0) if isinstance(kv[1], dict) else 0, reverse=True)
    from . import workspace
    return [r for r, _ in items if os.path.isdir(os.path.join(r, ".research")) or workspace.locate(r) is not None]   # shared-ledger roots keep .research elsewhere
