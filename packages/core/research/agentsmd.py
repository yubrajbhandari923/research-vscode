"""The Research OS-managed block in AGENTS.md: render, locate, preview, diff, apply.

One owned region, delimited by the markers below. Everything outside it belongs to the project and is
never read, rewritten or reasoned about — bytes outside the region are preserved exactly, including
line endings and a missing trailing newline.

Deliberately a human-reviewed workflow: no merge heuristics, no three-way merge. If the markers are
missing we propose adding the block; if they are malformed or duplicated we refuse and say how to fix it.

The `*_for_root` functions take a path so `research upgrade --dry-run` can preview AGENTS.md without
opening (and therefore migrating) the project database.
"""
from __future__ import annotations

import difflib
import os
from typing import Any, Dict, List, Optional, Tuple

from .store import TEMPLATES, Project, _atomic_write
from .util import ResearchError

BEGIN = "<!-- research:begin"      # the real marker carries a trailing comment; match the prefix
END = "<!-- research:end -->"


def block() -> str:
    """The managed block as the currently installed Research OS generates it."""
    return (TEMPLATES / "AGENTS.block.md").read_text(encoding="utf-8").strip()


def path_for_root(root: str) -> str:
    return os.path.join(root, "AGENTS.md")


def path(p: Project) -> str:
    return path_for_root(p.root)


def _read(file: str) -> Optional[str]:
    try:
        with open(file, encoding="utf-8", newline="") as f:  # newline='' → keep CRLF exactly as found
            return f.read()
    except FileNotFoundError:
        return None


def _marker_lines(text: str, prefix: str) -> List[int]:
    """Offsets of lines that *start* with `prefix` (ignoring indentation).

    Deliberately line-anchored: prose that merely mentions a marker — in the block itself, or in the
    user's own notes about it — must not be mistaken for the marker and corrupt the file.
    """
    out, pos = [], 0
    for line in text.split("\n"):
        if line.lstrip().startswith(prefix):
            out.append(pos + (len(line) - len(line.lstrip())))
        pos += len(line) + 1
    return out


def locate(text: str) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """Find the managed region. Returns (start, end_exclusive, problem).

    `problem` is set when the file cannot be edited safely; the caller must then refuse.
    """
    begins = _marker_lines(text, BEGIN)
    ends = _marker_lines(text, END)
    if not begins and not ends:
        return None, None, None
    if len(begins) != 1 or len(ends) != 1:
        return None, None, (f"found {len(begins)} begin marker(s) and {len(ends)} end marker(s); "
                            "exactly one of each is required")
    if ends[0] < begins[0]:
        return None, None, "the end marker appears before the begin marker"
    nl = text.find("\n", begins[0])
    line = text[begins[0]:(nl if nl != -1 else len(text))]
    if not line.rstrip().endswith("-->"):
        return None, None, "the begin marker line is not a complete HTML comment"
    return begins[0], ends[0] + len(END), None


def _problem(msg: str) -> ResearchError:
    return ResearchError(
        f"AGENTS.md managed block is ambiguous: {msg}",
        hint="Fix it by hand — keep exactly one `<!-- research:begin … -->` line and one "
             "`<!-- research:end -->` line, with the managed text between them. Nothing was changed.")


def state_for_root(root: str, project_name: Optional[str] = None) -> Dict[str, Any]:
    """What we would do to AGENTS.md, without touching it."""
    file = path_for_root(root)
    text = _read(file)
    new = block()
    base = {"path": file, "new": new, "project_name": project_name or os.path.basename(root)}
    if text is None:
        return {**base, "exists": False, "action": "create", "problem": None, "current": None,
                "up_to_date": False}
    start, end, problem = locate(text)
    if problem:
        return {**base, "exists": True, "action": "refuse", "problem": problem, "current": None,
                "up_to_date": False}
    if start is None:
        return {**base, "exists": True, "action": "add_block", "problem": None, "current": None,
                "up_to_date": False}
    current = text[start:end]
    return {**base, "exists": True, "action": "replace" if current != new else "none", "problem": None,
            "current": current, "up_to_date": current == new}


def render_for_root(root: str, project_name: Optional[str] = None) -> str:
    """The file contents after applying the managed block. Bytes outside the region are untouched."""
    st = state_for_root(root, project_name)
    if st["problem"]:
        raise _problem(st["problem"])
    new = st["new"]
    if st["action"] == "create":
        return f"# Agent instructions — {st['project_name']}\n\n{new}\n"
    text = _read(st["path"]) or ""
    if st["action"] == "add_block":
        sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        return text + sep + new + "\n"
    start, end, _ = locate(text)
    return text[:start] + new + text[end:]


def diff_for_root(root: str, project_name: Optional[str] = None, context: int = 3) -> str:
    """Unified diff of AGENTS.md as it is vs. as it would be. Empty when nothing would change."""
    st = state_for_root(root, project_name)
    if st["problem"]:
        raise _problem(st["problem"])
    old = _read(st["path"]) or ""
    new = render_for_root(root, project_name)
    if old == new:
        return ""
    return "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                        fromfile="a/AGENTS.md", tofile="b/AGENTS.md", n=context))


def apply_for_root(root: str, project_name: Optional[str] = None) -> Dict[str, Any]:
    """Write the managed region. Returns what happened; a no-op when already current."""
    st = state_for_root(root, project_name)
    if st["problem"]:
        raise _problem(st["problem"])
    if st["action"] == "none":
        return {"path": st["path"], "action": "none", "changed": False}
    _atomic_write(st["path"], render_for_root(root, project_name))
    return {"path": st["path"], "action": st["action"], "changed": True}


def outside_bytes_for_root(root: str) -> List[bytes]:
    """The byte ranges the project owns (everything but the managed region)."""
    text = _read(path_for_root(root))
    if text is None:
        return []
    start, end, problem = locate(text)
    if problem or start is None:
        return [text.encode("utf-8")]
    return [text[:start].encode("utf-8"), text[end:].encode("utf-8")]


# ------------------------------------------------------------------ Project-flavoured wrappers

def state(p: Project) -> Dict[str, Any]:
    return state_for_root(p.root, p.name)


def render(p: Project) -> str:
    return render_for_root(p.root, p.name)


def diff(p: Project, context: int = 3) -> str:
    return diff_for_root(p.root, p.name, context)


def apply(p: Project) -> Dict[str, Any]:
    return apply_for_root(p.root, p.name)


def outside_bytes(p: Project) -> List[bytes]:
    return outside_bytes_for_root(p.root)
