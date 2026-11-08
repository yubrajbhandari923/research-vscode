"""The adapter record and the helpers every provider module shares. Providers import from here, never from the package root."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

LEVELS = ("fast", "balanced", "deep")   # the logical levels every provider's level pack and every strategy speak


@dataclass(frozen=True)
class Adapter:
    """Everything the rest of the code may know about one agent CLI (design D.1). Fields that are None mean "not supported"."""
    name: str
    label: str
    build: Callable[..., List[str]]                  # (prof, model, effort, prompt, cwd, extra_dirs, headless) -> argv
    caps: Dict[str, Any]                             # honest capability table
    levels: Dict[str, Dict[str, Any]]                # level pack: fast|balanced|deep -> {"model", "effort"}
    blurb: str = ""
    profile: Dict[str, Any] = field(default_factory=dict)        # the plain provider profile (agents.profiles.<name>)
    level_extra: Dict[str, Any] = field(default_factory=dict)    # keys every level profile carries besides command/args/model/effort
    usage_parser: Optional[str] = None
    resume_probe: Optional[Callable[[str], bool]] = None         # does the INSTALLED cli prove native resume?
    resume_argv: Optional[Callable[[List[str], str], Optional[List[str]]]] = None
    session_id: Optional[Callable[[str], Optional[str]]] = None
    list_models: Optional[Callable[[str], Optional[List[Dict[str, str]]]]] = None
    mcp_install: Optional[Callable[[str, Optional[str]], Dict[str, Any]]] = None     # (root, command) -> {"provider","where","scope"}
    mcp_registered: Optional[Callable[[str], Optional[bool]]] = None


def run(argv: List[str], cwd: Optional[str] = None, timeout: float = 15.0) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def server_cmd(root: str) -> List[str]:
    if shutil.which("research"):
        return ["research", "mcp"]
    from .. import localbin
    return [localbin.install(root)["path"], "mcp"]


def json_has_research(root: str, rel: str, key: str) -> bool:
    try:
        return "research" in (json.load(open(os.path.join(root, rel), encoding="utf-8")).get(key) or {})
    except (OSError, ValueError):
        return False


def last_json_value(output: str, key: str, reverse: bool = True) -> Optional[str]:
    """First (or last) JSON line of `output` that is an object carrying `key`."""
    lines = (output or "").strip().splitlines()
    for line in (reversed(lines) if reverse else lines):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get(key):
            return str(d[key])
    return None
