"""Experiment forking: an experiment starts from another experiment's verified code, pinned, without promotion to `main`.

A fork is one append-only `inherit` record in `workspaces.jsonl` ({experiment, parent, revision, base_rev}) plus a ref
`refs/research/forks/<EXP>` holding the pinned commit, so the code survives worktree archival, branch moves and `git gc`.
`workspace.create` starts a forked experiment's worktree from `base_rev` (never from `main`/HEAD). Nothing here merges, promotes or
rewrites anything; `sync` is the only way the code moves afterwards, and only when asked for.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, Dict, List, Optional

from . import promotion as PR, workspace as W
from .schema import normalize_id
from .util import ResearchError

REF = "refs/research/forks/"


def inherited(p, exp: Optional[str]) -> Optional[Dict[str, Any]]:
    """The experiment's pin. The first record wins: a pin is never changed."""
    return next((r for r in W._records(p) if r.get("op") == "inherit" and r.get("experiment") == exp), None) if exp else None


def _top(p) -> str:
    return W.toplevel(p.root) or p.root


def _live_tasks(p, exp: str) -> List[Dict[str, Any]]:
    return [t for t in p.list("task", "related_experiment_id=?", (exp,)) if not str(t.get("blockers") or "").startswith("retired by plan")]


def _verified_attempts(p, exp: str) -> Dict[str, List[str]]:
    """For each live task of the experiment: the commits of its supervisor-verified, non-voided attempts ('' for a verified attempt that
    changed nothing). A task with no such attempt has no entry. Only the supervisor's own verification counts."""
    from . import supervisor as SV
    ids = {t["id"] for t in _live_tasks(p, exp)}
    out: Dict[str, List[str]] = {}
    for rs in SV.all_runs(p):
        for tid, e in (rs.get("tasks") or {}).items():
            for a in e.get("attempts", []):
                if tid in ids and a.get("state") == "verified" and not a.get("voided"):
                    out.setdefault(tid, []).append(a.get("commit") or "")
    return out


def unverified(p, exp: str) -> Optional[str]:
    """Why the experiment is not (yet) verified: every live task needs a done status AND a supervisor-verified attempt. A task a human
    marked done, or accepted without verification, does not count."""
    live = _live_tasks(p, exp)
    if not live:
        return f"{exp} has no tasks"
    ok = _verified_attempts(p, exp)
    return ", ".join(f"{t['id']} is {t['status']}" if t["status"] != "done" else f"{t['id']} has no verified attempt" for t in live
                     if t["status"] != "done" or t["id"] not in ok) or None


def _verified_commit(p, exp: str) -> str:
    """The tip of the experiment's verified commits. Never a worktree HEAD, never a base revision: if nothing verified was committed
    there is nothing genuine to pin, and the caller must name a commit."""
    shas = [c for cs in _verified_attempts(p, exp).values() for c in cs if c]
    top = _top(p)
    shas = [c for c in shas if W._git(top, "cat-file", "-e", c + "^{commit}") is not None]
    if not shas:
        raise ResearchError(f"{exp} has no verified commit to pin (its verified work changed no files, was not committed "
                            "(supervisor.commit_verified_work), or the commit is gone)",
                            hint="Fork at an exact commit with --at <commit> or --at RUN-NNNN.")
    return W._git_ok(top, "rev-list", "--topo-order", "-n", "1", *shas)


def resolve(p, parent: str, at: str = "verified") -> str:
    """`verified` (the parent's last supervisor-verified commit), a run id (its source snapshot or head), or any git revision."""
    top = _top(p)
    if at == "verified":
        why = unverified(p, parent)
        if why:
            raise ResearchError(f"{parent} is not verified yet ({why})", hint="Wait for it, or fork at an exact commit with --at <commit>.")
        return _verified_commit(p, parent)
    if re.match(r"^RUN-\d+$", at):
        from . import runs
        env = runs.get_run(p, at).get("env") or {}
        ws = (json.loads(env) if isinstance(env, str) else env).get("workspace") or {}
        sha = (ws.get("snapshot") or {}).get("commit") or ws.get("head")
        if not sha:
            raise ResearchError(f"{at} recorded no source revision")
        return sha
    sha = W._git(top, "rev-parse", "--verify", "-q", f"{at}^{{commit}}")
    if not sha:
        raise ResearchError(f"Unknown revision {at!r}")
    return sha


def pin(p, child: str, parent: str, at: str, sha: str, source: str = "cli") -> Dict[str, Any]:
    """Record the pin (idempotent: an existing pin is returned unchanged) and protect the commit with a ref."""
    if (cur := inherited(p, child)):
        return cur
    W._git_ok(_top(p), "update-ref", REF + child, sha)
    with p.tx():
        W._append(p, {"op": "inherit", "experiment": child, "parent": parent, "revision": at, "base_rev": sha, "ref": REF + child, "source": source})
        p.event("experiment", child, "forked", f"{child} forks {parent} at {at} ({sha[:8]})")
    return inherited(p, child) or {}


def fork(p, parent: str, title: str, at: str = "verified", override: Optional[str] = None) -> Dict[str, Any]:
    from . import services
    if not p.location.linked:
        raise ResearchError("Forking needs shared-ledger mode.", hint="Run `research ledger enable --apply` first.")
    parent = str(normalize_id(parent, "EXP"))
    par = p.get("experiment", parent)
    sha = resolve(p, parent, at)
    e = services.create_experiment(p, title, question=par.get("question_id"), parent=parent, override=override)
    rec = pin(p, e["id"], parent, at, sha)
    ws = W.create(p, e["id"], base=sha)
    return {"experiment": e["id"], "parent": parent, "revision": at, "base_rev": sha, "workspace": ws["id"], "branch": ws.get("branch"), "path": ws.get("path"),
            "pin": rec["ref"]}


def sync(p, exp: str, source: str = "main") -> Dict[str, Any]:
    """Explicitly bring another revision (default: main) into a forked experiment's branch: a recorded merge, never a rebase."""
    exp = str(normalize_id(exp, "EXP"))
    ws = next((W.show(p, r["id"]) for r in W._fold(W._records(p)).values() if r.get("experiment") == exp and r["status"] == "active"), None)
    if not ws or not ws["present"]:
        raise ResearchError(f"{exp} has no worktree on this machine")
    if ws["active_runs"]:
        raise ResearchError(f"{exp} has active runs; sync after they finish")
    if ws.get("dirty"):
        raise ResearchError(f"{exp}'s worktree has uncommitted changes; commit or discard them first")
    sha = resolve(p, source, "verified") if re.match(r"^EXP-\d+$", source) else resolve(p, exp, PR._main_branch(p) if source == "main" else source)
    path = ws["path"]
    if PR._is_ancestor(path, sha, "HEAD"):
        return {"experiment": exp, "synced": False, "note": f"already contains {sha[:8]}"}
    env = {**os.environ, "GIT_AUTHOR_NAME": "Research OS", "GIT_AUTHOR_EMAIL": "research-os@localhost",
           "GIT_COMMITTER_NAME": "Research OS", "GIT_COMMITTER_EMAIL": "research-os@localhost"}
    r = subprocess.run(["git", "merge", "--no-edit", sha], cwd=path, capture_output=True, text=True, env=env)
    if r.returncode:
        subprocess.run(["git", "merge", "--abort"], cwd=path, capture_output=True)
        raise ResearchError(f"merging {source} into {exp} conflicts; aborted, nothing changed. Merge it by hand in {path}.")
    with p.tx():
        W._append(p, {"op": "sync", "experiment": exp, "workspace": ws["id"], "source": source, "sha": sha, "head": W._git_ok(path, "rev-parse", "HEAD")})
        p.event("experiment", exp, "synced", f"{exp} merged {source} ({sha[:8]})")
    return {"experiment": exp, "synced": True, "merged": sha, "head": W._git_ok(path, "rev-parse", "HEAD")}


def plan_ref(st: Dict[str, Any], ref: str) -> str:
    """A plan's experiment key or an EXP- id → the experiment id."""
    return st["experiments"].get(ref) or str(normalize_id(ref, "EXP"))


def gate(p, st: Dict[str, Any], tid: str) -> Optional[str]:
    """Scheduler hook for a task whose experiment declares `inherits`: pin the parent's revision once it is available, else say why
    the task must wait. Idempotent across restarts (the pin is in the ledger)."""
    exp = p.get("task", tid).get("related_experiment_id")
    if not exp or inherited(p, exp):
        return None
    spec = next((e for e in st["spec"].get("experiments", []) if st["experiments"].get(e["key"]) == exp), None)
    inh = (spec or {}).get("inherits")
    if not inh:
        return None
    try:
        parent = plan_ref(st, inh["experiment"])
        sha = resolve(p, parent, inh.get("revision", "verified"))
        pin(p, exp, parent, inh.get("revision", "verified"), sha, source=f"plan:{st['plan_key']}")
    except ResearchError as ex:
        return f"waiting to fork {inh['experiment']}: {ex}"
    return None
