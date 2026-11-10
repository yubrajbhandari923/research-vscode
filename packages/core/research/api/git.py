"""Git health and the reviewed commit (githygiene.py): read-only report, preflight, managed ignores, and a scoped commit that needs confirmation."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import githygiene as GH
from . import op


@op()
def git_health(p) -> Dict[str, Any]:
    """Main repository, experiment worktrees, pending promotions and the ledger, kept apart; every uncommitted file with what it is and why it matters."""
    return GH.report(p)


@op()
def git_preflight(p, purpose: str = "plan") -> Dict[str, Any]:
    return GH.preflight(p, purpose)


@op()
def git_commit_preview(p, paths: Optional[List[str]] = None, scaffold: bool = False, message: Optional[str] = None) -> Dict[str, Any]:
    """The staged diff of exactly the files a reviewed commit would contain. Commits nothing and leaves the real index alone."""
    return GH.preview_commit(p, paths, scaffold, message)


@op(mutates=True)
def git_commit(p, paths: Optional[List[str]] = None, scaffold: bool = False, message: Optional[str] = None, confirm: bool = False) -> Dict[str, Any]:
    """Human-only scoped commit on main of the files named (or the generated scaffolding). Never pushes; without confirm it only previews."""
    return GH.commit(p, paths, scaffold, message, confirm)


@op(mutates=True)
def git_ignore(p, apply: bool = False, patterns: Optional[List[str]] = None) -> Dict[str, Any]:
    """Preview or write the managed .gitignore block for generated and internal files. A pattern that would hide an important file is refused."""
    return GH.ignore_apply(p, patterns) if apply else GH.ignore_plan(p, patterns)
