"""Git metadata capture. Never commits, never modifies the repo."""
from __future__ import annotations

import os
import subprocess
from typing import Dict, Optional

_EXCLUDE = ":(exclude).research"


def _git(root: str, *args: str, timeout: float = 10.0) -> Optional[str]:
    try:
        p = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    return p.stdout


def is_repo(root: str) -> bool:
    out = _git(root, "rev-parse", "--is-inside-work-tree")
    return bool(out and out.strip() == "true")


def info(root: str) -> Dict[str, Optional[object]]:
    """{branch, commit, dirty, remote}. All None outside a git repo.

    Changes under .research/ do not count as dirty (they are bookkeeping, not code).
    """
    if not is_repo(root):
        return {"branch": None, "commit": None, "dirty": None, "remote": None}
    branch = (_git(root, "rev-parse", "--abbrev-ref", "HEAD") or "").strip() or None
    commit = (_git(root, "rev-parse", "HEAD") or "").strip() or None
    status = _git(root, "status", "--porcelain", "--untracked-files=no", "--", ".", _EXCLUDE)
    dirty = bool(status and status.strip()) if status is not None else None
    remote = (_git(root, "config", "--get", "remote.origin.url") or "").strip() or None
    return {"branch": branch, "commit": commit, "dirty": dirty, "remote": remote}


def diff(root: str, max_bytes: int = 2 * 1024 * 1024) -> Optional[str]:
    """Uncommitted diff vs HEAD (tracked files, excluding .research/), truncated to max_bytes."""
    out = _git(root, "diff", "HEAD", "--", ".", _EXCLUDE, timeout=30.0)
    if out is None:
        return None
    if len(out.encode("utf-8", "replace")) > max_bytes:
        out = out.encode("utf-8", "replace")[:max_bytes].decode("utf-8", "ignore") + "\n# … truncated\n"
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "--", ".", _EXCLUDE)
    if untracked and untracked.strip():
        out += "\n# Untracked files (not included):\n" + "".join(f"#   {l}\n" for l in untracked.splitlines())
    return out


def short(commit: Optional[str]) -> str:
    return commit[:8] if commit else "—"


def available() -> bool:
    return any(os.access(os.path.join(p, "git"), os.X_OK) for p in os.environ.get("PATH", "").split(os.pathsep))
