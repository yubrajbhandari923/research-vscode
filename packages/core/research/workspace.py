"""Source worktrees and the shared research ledger.

Classic projects keep `.research/` beside the code. Shared-ledger mode (opt-in, see `enable`) keeps ONE ledger in
its own git worktree (branch `research/ledger`) and lets every source worktree of the repository reach it:

    <git common dir>/research/ledger.json     machine-local link: {project_id, ledger_root}   (never tracked)
    <git common dir>/research/workspaces.json machine-local map:  {WS-001: absolute worktree path}
    <worktree git dir>/research-workspace     the worktree's persistent workspace id (survives moves)
    <ledger>/.research/project.json           portable project identity
    <ledger>/.research/workspaces.jsonl       portable append-only workspace records (no machine paths)

Git's common dir is shared by all linked worktrees, so discovery needs no per-worktree file and no `.git`-is-a-file
special case: `git rev-parse` answers for the worktree we are in.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from typing import Any, Dict, List, Optional

from . import gitinfo
from .util import NotFound, ResearchError, now_iso

LEDGER_BRANCH = "research/ledger"
# Derived or machine-local files that are not copied into a new ledger.
_NOT_COPIED = {"research.db", "research.db-wal", "research.db-shm", ".write.lock", ".transaction.json", "bin", "locks"}


# ------------------------------------------------------------------------------------------------ git plumbing
def _git(cwd: str, *args: str, timeout: float = 30.0) -> Optional[str]:
    out = gitinfo._git(cwd, *args, timeout=timeout)
    return out.strip() if out is not None else None


def _git_ok(cwd: str, *args: str, env: Optional[Dict[str, str]] = None) -> str:
    e = {**os.environ, "GIT_AUTHOR_NAME": os.environ.get("GIT_AUTHOR_NAME", "Research OS"),
         "GIT_AUTHOR_EMAIL": os.environ.get("GIT_AUTHOR_EMAIL", "research-os@localhost"),
         "GIT_COMMITTER_NAME": os.environ.get("GIT_COMMITTER_NAME", "Research OS"),
         "GIT_COMMITTER_EMAIL": os.environ.get("GIT_COMMITTER_EMAIL", "research-os@localhost"), **(env or {})}
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=e)
    if p.returncode != 0:
        raise ResearchError(f"git {' '.join(args[:3])} failed: {(p.stderr or p.stdout).strip()[-400:]}")
    return p.stdout.strip()


def common_dir(path: str) -> Optional[str]:
    out = _git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return os.path.realpath(out) if out else None


def git_dir(path: str) -> Optional[str]:
    out = _git(path, "rev-parse", "--path-format=absolute", "--git-dir")
    return os.path.realpath(out) if out else None


def toplevel(path: str) -> Optional[str]:
    out = _git(path, "rev-parse", "--show-toplevel")
    return os.path.realpath(out) if out else None


def repo_identity(path: str) -> Optional[str]:
    """Portable repository identity: the root commit(s) hash. Same for every clone and worktree."""
    out = _git(path, "rev-list", "--max-parents=0", "--topo-order", "HEAD")
    return out.splitlines()[-1] if out else None


def worktrees(path: str) -> List[Dict[str, Any]]:
    out = _git(path, "worktree", "list", "--porcelain") or ""
    res: List[Dict[str, Any]] = []
    cur: Dict[str, Any] = {}
    for line in out.splitlines() + [""]:
        if not line:
            if cur:
                res.append(cur)
            cur = {}
            continue
        k, _, v = line.partition(" ")
        cur[k] = v if v else True
    return [{"path": os.path.realpath(w["worktree"]), "head": w.get("HEAD"),
             "branch": w["branch"][len("refs/heads/"):] if isinstance(w.get("branch"), str) else None,
             "detached": bool(w.get("detached")), "prunable": "prunable" in w} for w in res if "worktree" in w]


# ------------------------------------------------------------------------------------------------ small json files
def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _write_json(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _link_path(common: str) -> str:
    return os.path.join(common, "research", "ledger.json")


def _paths_file(common: str) -> str:
    return os.path.join(common, "research", "workspaces.json")


# ------------------------------------------------------------------------------------------------ location
class Location:
    """Where a research project lives relative to the directory we were started in."""

    def __init__(self, source_root: str, ledger_root: str, linked: bool, common: Optional[str] = None):
        self.source_root = source_root
        self.ledger_root = ledger_root
        self.linked = linked
        self.common = common

    def __repr__(self) -> str:  # pragma: no cover
        return f"Location(source={self.source_root!r}, ledger={self.ledger_root!r}, linked={self.linked})"


def _has_ledger(root: str) -> bool:
    return os.path.isfile(os.path.join(root, ".research", "config.yaml"))


def linked_ledger(path: str) -> Optional[Location]:
    """The shared ledger reachable from `path` through its git repository, or None.

    A directory with its own `.research/` is never linked: classic mode wins, so enabling shared mode on one
    worktree cannot hijack another project that happens to sit beside it.
    """
    top = toplevel(path)
    common = common_dir(path) if top else None
    if not top or not common:
        return None
    link = _read_json(_link_path(common))
    if not link:
        return None
    ledger = link.get("ledger_root")
    if not ledger or not _has_ledger(ledger):
        raise ResearchError(
            f"The shared research ledger recorded for this repository is missing: {ledger}",
            hint="Restore or re-create that directory (git worktree list shows it), or point the link at the new "
                 "location with `research ledger relink <path>`.")
    return Location(top, os.path.realpath(ledger), True, common)


def locate(root: str) -> Optional[Location]:
    root = os.path.abspath(root)
    if _has_ledger(root):
        return Location(root, root, False)
    return linked_ledger(root)


# ------------------------------------------------------------------------------------------------ identity records
def project_identity(rdir: str) -> Optional[Dict[str, Any]]:
    return _read_json(os.path.join(rdir, "project.json"))


def workspace_id_of(path: str) -> Optional[str]:
    gd = git_dir(path)
    if not gd:
        return None
    try:
        with open(os.path.join(gd, "research-workspace"), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _set_workspace_id(path: str, ws: str) -> None:
    gd = git_dir(path)
    if not gd:
        raise ResearchError(f"{path} is not inside a git worktree")
    with open(os.path.join(gd, "research-workspace"), "w", encoding="utf-8") as f:
        f.write(ws + "\n")


# ------------------------------------------------------------------------------------------------ workspace records
def _wfile(p) -> str:
    return p.path("workspaces.jsonl")


def _records(p) -> List[Dict[str, Any]]:
    from .store import read_jsonl_from
    f = _wfile(p)
    return [r for r, _ in read_jsonl_from(f, 0)] if os.path.exists(f) else []


def _fold(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for r in records:
        ws = r.get("workspace")
        if not ws:
            continue
        cur = out.setdefault(ws, {"id": ws, "tasks": []})
        op = r.get("op")
        if op == "create":
            cur.update({k: v for k, v in r.items() if k not in ("op", "workspace")})
            cur["created_at"] = r.get("ts")
            cur["status"] = "active"
        elif op == "link-task":
            if r["task"] not in cur["tasks"]:
                cur["tasks"].append(r["task"])
        elif op == "unlink-task":
            cur["tasks"] = [t for t in cur["tasks"] if t != r["task"]]
        elif op == "assign":
            cur["agent"] = r.get("agent")
        elif op == "set":
            cur.update(r.get("fields") or {})
        elif op == "archive":
            cur["status"] = "archived"
            cur["archived_at"] = r.get("ts")
            cur["archive"] = r.get("archive")
    return out


def _next_ws_id(recs: List[Dict[str, Any]]) -> str:
    n = 0
    for r in recs:
        try:
            n = max(n, int(str(r.get("workspace", "")).split("-")[-1]))
        except ValueError:
            pass
    return f"WS-{n + 1:03d}"


def _append(p, record: Dict[str, Any]) -> None:
    from .store import append_jsonl
    record = {"ts": now_iso(), **record, **{k: v for k, v in p.author.items() if v}}
    append_jsonl(_wfile(p), record)


def _local_paths(common: str) -> Dict[str, str]:
    return (_read_json(_paths_file(common)) or {})


def _remember_path(common: str, ws: str, path: str) -> None:
    m = _local_paths(common)
    m[ws] = path
    _write_json(_paths_file(common), m)


def _path_of(p, ws: str) -> Optional[str]:
    common = common_dir(p.root)
    return _local_paths(common).get(ws) if common else None


# ------------------------------------------------------------------------------------------------ enabling the ledger
def plan_enable(root: str, ledger_path: Optional[str] = None) -> Dict[str, Any]:
    """Dry-run of `enable`: everything that would change, and everything that blocks it. Writes nothing."""
    root = os.path.abspath(root)
    problems: List[str] = []
    notes: List[str] = []
    top = toplevel(root)
    if not top:
        return {"ok": False, "problems": ["Shared-ledger mode needs a git repository (worktrees are a git feature)."],
                "changes": [], "notes": []}
    common = common_dir(top)
    if _read_json(_link_path(common)):
        problems.append("Shared-ledger mode is already enabled for this repository.")
    src = os.path.join(top, ".research")
    if not _has_ledger(top):
        problems.append(f"No classic research project at {top} (no .research/config.yaml) to migrate.")
    if os.path.exists(os.path.join(src, ".transaction.json")):
        problems.append("An unfinished transaction journal exists in .research/; open the project once so it recovers.")
    tracked = _git(top, "ls-files", "--", ".research") or ""
    if tracked:
        problems.append(f"{len(tracked.splitlines())} files under .research/ are tracked by git on this branch. Other branches "
                        "would keep restoring them. Untrack them first (`git rm -r --cached .research`, then commit) and re-run.")
    if _git(top, "status", "--porcelain", "--untracked-files=no", "--", ".research"):
        problems.append("Uncommitted changes in tracked .research/ files.")
    name = os.path.basename(top)
    ledger = os.path.abspath(ledger_path or os.path.join(os.path.dirname(top), f"{name}-ledger"))
    if os.path.exists(ledger):
        problems.append(f"{ledger} already exists; choose another --ledger-path.")
    if _git(top, "rev-parse", "--verify", "-q", f"refs/heads/{LEDGER_BRANCH}"):
        problems.append(f"Branch {LEDGER_BRANCH} already exists.")
    n_files = sum(len(fs) for _, _, fs in os.walk(src)) if os.path.isdir(src) else 0
    changes = [
        f"create branch {LEDGER_BRANCH} (an empty root commit) and a worktree for it at {ledger}",
        f"copy canonical records from {src} to {os.path.join(ledger, '.research')} (derived index, locks and bin/ are not copied)",
        "write .research/project.json (portable project id) in the ledger",
        f"record the machine-local link in {_link_path(common)}",
        f"rename {src} to {src}.pre-ledger (kept as your backup; nothing is deleted)",
        "rebuild the index in the ledger and compare record counts with the original",
    ]
    notes.append("Nothing is committed. Use `research checkpoint create --commit` in the ledger when you want it in git history.")
    notes.append(f"To undo: `git worktree remove {ledger}`, delete {_link_path(common)}, rename {src}.pre-ledger back to .research.")
    return {"ok": not problems, "problems": problems, "changes": changes, "notes": notes, "repo": top,
            "ledger_path": ledger, "files": n_files}


def _counts(p) -> Dict[str, int]:
    tables = ("questions", "experiments", "runs", "findings", "decisions", "checkpoints", "artifacts", "plans", "tasks")
    return {t: p.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def enable(root: str, ledger_path: Optional[str] = None) -> Dict[str, Any]:
    """Migrate a classic project to a shared ledger. The caller is responsible for obtaining approval."""
    from .store import Project
    plan = plan_enable(root, ledger_path)
    if not plan["ok"]:
        raise ResearchError("Cannot enable the shared ledger: " + " ".join(plan["problems"]))
    top, ledger = plan["repo"], plan["ledger_path"]
    common = common_dir(top)
    src = os.path.join(top, ".research")

    with Project(top, sync=True) as before:          # recovers a journal, rebuilds a stale index
        want = _counts(before)
    empty = _git_ok(top, "hash-object", "-w", "-t", "tree", os.devnull)   # makes sure the empty tree exists
    commit = _git_ok(top, "commit-tree", empty, "-m", "research ledger")
    _git_ok(top, "worktree", "add", "-q", "-b", LEDGER_BRANCH, ledger, commit)
    try:
        dst = os.path.join(ledger, ".research")
        shutil.copytree(src, dst, ignore=lambda d, names: [n for n in names if n in _NOT_COPIED and d == src])
        _write_json(os.path.join(dst, "project.json"),
                    {"project_id": str(uuid.uuid4()), "created_at": now_iso(), "schema": 1,
                     "source_repo": repo_identity(top), "ledger_branch": LEDGER_BRANCH})
        with Project(ledger, sync=True) as after:
            got = _counts(after)
        if got != want:
            raise ResearchError(f"Record counts differ after copying (before {want}, after {got}); nothing was changed.")
    except Exception:
        # Roll back everything we created. The original .research/ has not been touched.
        subprocess.run(["git", "worktree", "remove", "--force", ledger], cwd=top, capture_output=True)
        subprocess.run(["git", "branch", "-D", LEDGER_BRANCH], cwd=top, capture_output=True)
        raise
    ident = project_identity(os.path.join(ledger, ".research"))
    _write_json(_link_path(common), {"project_id": ident["project_id"], "ledger_root": ledger, "linked_at": now_iso()})
    backup = src + ".pre-ledger"
    os.rename(src, backup)
    return {"ledger_root": ledger, "project_id": ident["project_id"], "backup": backup, "records": want}


def relink(root: str, ledger_path: str) -> Dict[str, Any]:
    ledger = os.path.abspath(ledger_path)
    if not _has_ledger(ledger):
        raise ResearchError(f"{ledger} has no .research/config.yaml")
    ident = project_identity(os.path.join(ledger, ".research")) or {}
    common = common_dir(root)
    if not common:
        raise ResearchError("Not inside a git repository")
    old = _read_json(_link_path(common)) or {}
    if old.get("project_id") and ident.get("project_id") and old["project_id"] != ident["project_id"]:
        raise ResearchError("That ledger belongs to a different project (project ids differ); refusing to relink.")
    _write_json(_link_path(common), {"project_id": ident.get("project_id"), "ledger_root": ledger, "linked_at": now_iso()})
    return {"ledger_root": ledger}


# ------------------------------------------------------------------------------------------------ source snapshots
def snapshot_tree(root: str, ref_name: str) -> Optional[Dict[str, Any]]:
    """Preserve the exact working tree (tracked and untracked, not ignored) as an unreferenced-by-branches commit.

    The commit is kept alive by `refs/research/snapshots/<ref_name>` so branch deletion and `git gc` cannot lose it.
    Uses a temporary index; the real index, HEAD and working files are untouched.
    """
    import tempfile
    top = toplevel(root)
    head = _git(root, "rev-parse", "--verify", "-q", "HEAD")
    if not top or not head:
        return None
    fd, idx = tempfile.mkstemp(prefix="research-index-")
    os.close(fd)
    os.unlink(idx)   # git wants to create it
    try:
        env = {"GIT_INDEX_FILE": idx}
        _git_ok(top, "read-tree", "HEAD", env=env)
        _git_ok(top, "add", "-A", "--", ".", ":(exclude).research", env=env)
        tree = _git_ok(top, "write-tree", env=env)
        commit = _git_ok(top, "commit-tree", tree, "-p", head, "-m", f"research snapshot {ref_name}", env=env)
        _git_ok(top, "update-ref", f"refs/research/snapshots/{ref_name}", commit)
        base_tree = _git(top, "rev-parse", f"{head}^{{tree}}")
        return {"commit": commit, "tree": tree, "base": head, "ref": f"refs/research/snapshots/{ref_name}",
                "differs_from_head": tree != base_tree}
    finally:
        try:
            os.unlink(idx)
        except OSError:
            pass


# ------------------------------------------------------------------------------------------------ operations
def current(p) -> Optional[Dict[str, Any]]:
    """The workspace record for the source worktree this project handle was opened from (None in classic mode)."""
    ws = workspace_id_of(p.root)
    return _fold(_records(p)).get(ws) if ws else None


def info(p) -> Dict[str, Any]:
    """Identity block for the open project: always safe to call, for any mode."""
    loc = p.location
    ident = project_identity(p.rdir) or {}
    ws = workspace_id_of(p.root) if loc.linked or ident else None
    g = gitinfo.info(p.root)
    return {"mode": "shared-ledger" if loc.linked else "classic", "project_id": ident.get("project_id"),
            "ledger_root": loc.ledger_root, "source_root": p.root, "source_repo": repo_identity(p.root),
            "workspace": ws, "branch": g.get("branch"), "commit": g.get("commit"), "dirty": g.get("dirty")}


def _slug(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def create(p, experiment: str, name: Optional[str] = None, base: Optional[str] = None,
           path: Optional[str] = None, branch: Optional[str] = None) -> Dict[str, Any]:
    """New branch + worktree for an experiment. Never touches existing branches or worktrees."""
    from .schema import normalize_id
    if not p.location.linked:
        raise ResearchError("Experiment workspaces need shared-ledger mode.",
                            hint="Preview with `research ledger enable --dry-run`, then run `research ledger enable --apply`.")
    exp = normalize_id(experiment, "EXP")
    e = p.get("experiment", exp)
    top = toplevel(p.root)
    slug = _slug(name or e["title"])
    br = branch or f"exp/{exp}" + (f"-{slug}" if slug else "")
    base_ref = base or "HEAD"
    if _git(top, "rev-parse", "--verify", "-q", f"refs/heads/{br}"):
        raise ResearchError(f"Branch {br} already exists.", hint="Use `research workspace attach` to adopt an existing branch.")
    wt = os.path.abspath(path or os.path.join(os.path.dirname(top), f"{os.path.basename(top)}-experiments", exp))
    if os.path.exists(wt):
        raise ResearchError(f"{wt} already exists.", hint="Choose another --path.")
    base_rev = _git(top, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
    if not base_rev:
        raise ResearchError(f"Unknown base revision {base_ref!r}")
    _git_ok(top, "worktree", "add", "-q", "-b", br, wt, base_rev)
    return _register(p, wt, br, base_rev, exp)


def attach(p, path: str, experiment: Optional[str] = None) -> Dict[str, Any]:
    """Adopt an existing worktree (and its branch) as a workspace."""
    from .schema import normalize_id
    wt = toplevel(os.path.abspath(path))
    if not wt:
        raise ResearchError(f"{path} is not inside a git worktree")
    if common_dir(wt) != p.location.common:
        raise ResearchError("That worktree belongs to a different repository.")
    existing = workspace_id_of(wt)
    if existing and existing in _fold(_records(p)):
        raise ResearchError(f"{wt} is already workspace {existing}.")
    g = gitinfo.info(wt)
    exp = normalize_id(experiment, "EXP") if experiment else None
    if exp:
        p.get("experiment", exp)
    main_head = _git(wt, "merge-base", g["commit"] or "HEAD", "main") or g["commit"]
    return _register(p, wt, g["branch"], main_head, exp)


def _register(p, wt: str, branch: Optional[str], base_rev: str, exp: Optional[str]) -> Dict[str, Any]:
    with p.tx():
        recs = _records(p)
        ws = _next_ws_id(recs)
        _append(p, {"op": "create", "workspace": ws, "name": os.path.basename(wt), "branch": branch,
                    "base_rev": base_rev, "experiment": exp, "source_repo": repo_identity(wt)})
        p.event("workspace", ws, "created", f"{ws} on branch {branch} (base {base_rev[:8]})" + (f" for {exp}" if exp else ""))
    _set_workspace_id(wt, ws)
    _remember_path(p.location.common, ws, wt)
    return show(p, ws)


def _active_runs(p, ws: str) -> List[Dict[str, Any]]:
    out = []
    for r in p.conn.execute("SELECT id, status, env FROM runs WHERE status IN ('queued','running')"):
        try:
            env = json.loads(r["env"] or "{}")
        except ValueError:
            env = {}
        if (env.get("workspace") or {}).get("id") == ws:
            out.append({"id": r["id"], "status": r["status"]})
    return out


def show(p, ws: str) -> Dict[str, Any]:
    rec = _fold(_records(p)).get(ws)
    if not rec:
        raise NotFound(f"No workspace {ws}")
    path = _path_of(p, ws)
    out = dict(rec)
    out["path"] = path
    out["present"] = bool(path and os.path.isdir(path))
    if out["present"] and rec["status"] == "active":
        g = gitinfo.info(path)
        out.update({"head": g["commit"], "current_branch": g["branch"], "dirty": g["dirty"]})
        untracked = _git(path, "ls-files", "--others", "--exclude-standard") or ""
        out["untracked"] = len(untracked.splitlines())
        if rec.get("base_rev"):
            ahead = _git(path, "rev-list", "--count", f"{rec['base_rev']}..HEAD")
            out["commits_ahead"] = int(ahead) if ahead and ahead.isdigit() else None
    out["active_runs"] = _active_runs(p, ws)
    return out


def list_workspaces(p, include_archived: bool = False) -> List[Dict[str, Any]]:
    recs = _fold(_records(p))
    return [show(p, w) for w in sorted(recs) if include_archived or recs[w]["status"] != "archived"]


def link_task(p, ws: str, task: str) -> Dict[str, Any]:
    from .schema import normalize_id
    t = normalize_id(task, "T")
    p.get("task", t)
    show(p, ws)
    with p.tx():
        _append(p, {"op": "link-task", "workspace": ws, "task": t})
        p.event("workspace", ws, "task_linked", f"{t} linked to {ws}")
    return show(p, ws)


def assign(p, ws: str, agent: Optional[str]) -> Dict[str, Any]:
    show(p, ws)
    with p.tx():
        _append(p, {"op": "assign", "workspace": ws, "agent": agent})
        p.event("workspace", ws, "assigned", f"{ws} agent: {agent or 'none'}")
    return show(p, ws)


def archive_impact(p, ws: str) -> Dict[str, Any]:
    """What archiving would do, and what blocks it. Pure read."""
    s = show(p, ws)
    blockers: List[str] = []
    if s["status"] == "archived":
        blockers.append("Already archived.")
    if s["active_runs"]:
        blockers.append("Active runs depend on this worktree: " + ", ".join(r["id"] for r in s["active_runs"]))
    if s["present"]:
        if s.get("dirty"):
            blockers.append("Uncommitted changes to tracked files would be lost.")
        if s.get("untracked"):
            blockers.append(f"{s['untracked']} untracked files would be lost.")
        unmerged = _git(s["path"], "branch", "--no-merged", "main", "--format=%(refname:short)") or ""
        if s.get("current_branch") in unmerged.split():
            s["unmerged_commits"] = int(_git(s["path"], "rev-list", "--count", "main..HEAD") or 0)
    return {"workspace": ws, "path": s["path"], "branch": s.get("branch"), "blockers": blockers,
            "unmerged_commits": s.get("unmerged_commits", 0),
            "would": ["keep the branch and all its commits (never deleted)",
                      "pin the head commit as refs/research/archive/" + ws, "remove the worktree directory (git worktree remove, no --force)",
                      "keep the workspace record and every run's recorded source snapshot"]}


def archive(p, ws: str, reason: Optional[str] = None) -> Dict[str, Any]:
    """Remove a finished workspace's directory safely. The branch, its commits and the scientific record remain."""
    impact = archive_impact(p, ws)
    if impact["blockers"]:
        raise ResearchError(f"Cannot archive {ws}: " + " ".join(impact["blockers"]),
                            hint="Commit or move the changes, wait for or cancel the runs, then retry. "
                                 "Preview with `research workspace archive --dry-run`.")
    s = show(p, ws)
    head = s.get("head")
    if s["present"]:
        _git_ok(s["path"], "update-ref", f"refs/research/archive/{ws}", head)
        _git_ok(toplevel(p.root), "worktree", "remove", s["path"])   # refuses a dirty tree by itself
    with p.tx():
        _append(p, {"op": "archive", "workspace": ws, "archive": {"head": head, "branch": s.get("branch"), "reason": reason}})
        p.event("workspace", ws, "archived", f"{ws} archived at {str(head)[:8]}" + (f": {reason}" if reason else ""))
    return {"workspace": ws, "archived_head": head, "branch_kept": s.get("branch")}


def run_info(p) -> Dict[str, Any]:
    """Source provenance recorded on each run: which workspace, which base, and a durable snapshot when dirty."""
    ws = workspace_id_of(p.root) if p.location.linked else None
    rec = (_fold(_records(p)).get(ws) if ws else None) or {}
    g = gitinfo.info(p.root)
    out: Dict[str, Any] = {"id": ws, "branch": g.get("branch"), "head": g.get("commit"),
                           "base_rev": rec.get("base_rev"), "experiment": rec.get("experiment"),
                           "source_repo": repo_identity(p.root)}
    return out


def for_task(p, task: str) -> Optional[Dict[str, Any]]:
    """The active workspace a task is linked to (present on this machine), else None."""
    if not p.location.linked:
        return None
    for ws, rec in _fold(_records(p)).items():
        if rec["status"] == "active" and task in rec["tasks"]:
            s = show(p, ws)
            return s if s["present"] else None
    return None
