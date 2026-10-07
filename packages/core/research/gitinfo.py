"""Git metadata capture, plus one opt-in write: `snapshot()` commits .research/ only (never pushes, never touches
other paths or the index of other files). Everything else is read-only."""
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


def diff_commits(root: str, a: str, b: str, max_bytes: int = 200 * 1024) -> Optional[Dict[str, str]]:
    """Code changes between two commits (excluding .research/): {stat, patch}. None if unavailable."""
    stat = _git(root, "diff", "--stat", a, b, "--", ".", _EXCLUDE, timeout=30.0)
    if stat is None:
        return None
    patch = _git(root, "diff", a, b, "--", ".", _EXCLUDE, timeout=30.0) or ""
    if len(patch.encode("utf-8", "replace")) > max_bytes:
        patch = patch.encode("utf-8", "replace")[:max_bytes].decode("utf-8", "ignore") + "\n# … truncated\n"
    return {"stat": stat, "patch": patch}


def snapshot(root: str, message: str) -> Optional[str]:
    """Commit the current state of .research/ (and nothing else). Returns the new commit hash, or None if there was
    nothing to commit. Uses `git commit --only -- .research`, so anything else you have staged stays staged."""
    from .util import ResearchError
    if not is_repo(root):
        raise ResearchError("Not a git repository", hint="`git init` first, or create the checkpoint without --commit.")
    p = subprocess.run(["git", "add", "--", ".research"], cwd=root, capture_output=True, text=True)
    if p.returncode != 0:
        raise ResearchError(f"git add failed: {p.stderr.strip()[-300:]}")
    pending = _git(root, "diff", "--cached", "--name-only", "--", ".research")
    if not (pending and pending.strip()):
        return None
    p = subprocess.run(["git", "commit", "--only", "-m", message, "--", ".research"], cwd=root,
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise ResearchError(f"git commit failed: {(p.stderr or p.stdout).strip()[-400:]}",
                            hint="Check `git config user.name/user.email` and any commit hooks.")
    return (_git(root, "rev-parse", "HEAD") or "").strip() or None
