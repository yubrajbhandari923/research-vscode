"""Project Doctor: one read-only inspection of everything a project depends on, with actionable results.

Levels: `error` (data or an enabled feature is broken), `warn` (needs attention), `info` (an optional capability is not set up; never a
problem by itself), `ok`. Capabilities the user has not enabled (HPC, sandbox, shared ledger) are reported as info, never as failures.

`run` changes nothing. A few generated-file problems carry a `fix` that `apply_fix` performs only when called explicitly; each fix touches
generated files only (context, policy, the local CLI copy, the managed AGENTS.md block), never records, configuration or git.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional

from .util import IncompatibleProject, NotInitialized, ResearchError

SAFE_FIXES = {"context.stale", "agents_md.stale", "bin.stale"}


def normalize_fix(fix: Any) -> Optional[Dict[str, Any]]:
    """The one `fix` shape every consumer can rely on: {text: str, command?: str, safe?: bool}. Accepts a bare string or a command-only dict."""
    if not fix:
        return None
    if isinstance(fix, str):
        fix = {"text": fix}
    out = dict(fix)
    cmd = out.get("command")
    if not out.get("text"):
        out["text"] = f"Run `{cmd}`." if cmd else ""
    return out if out["text"] or cmd else None


class _Report:
    def __init__(self) -> None:
        self.checks: List[Dict[str, Any]] = []

    def add(self, id_: str, area: str, level: str, title: str, detail: str = "", fix: Optional[Dict[str, Any]] = None) -> None:
        self.checks.append({"id": id_, "area": area, "level": level, "title": title, "detail": detail, "fix": normalize_fix(fix)})

    def result(self, root: str) -> Dict[str, Any]:
        n = lambda lv: sum(1 for c in self.checks if c["level"] == lv)
        return {"root": root, "summary": {"errors": n("error"), "warnings": n("warn"), "info": n("info"), "ok": n("ok")},
                "healthy": n("error") == 0, "checks": self.checks}


def _counts(p) -> Dict[str, int]:
    from .workspace import _counts as c
    return c(p)


def run(root: str) -> Dict[str, Any]:
    from . import store, workspace
    r = _Report()
    root = os.path.abspath(root)
    loc = workspace.locate(root)
    if not loc:
        r.add("project.missing", "Project", "error", "No Research OS project here", f"{root} has no .research/config.yaml.",
              {"text": "Create one with `research init`.", "command": "research init"})
        return r.result(root)
    rdir = os.path.join(loc.ledger_root, store.RESEARCH_DIR)
    try:
        store.check_format(rdir)
    except IncompatibleProject as e:
        r.add("format", "Project", "error", "Project format is not v2", str(e), {"text": e.hint})
        return r.result(root)
    r.add("format", "Project", "ok", "Project format 2")
    if os.path.exists(os.path.join(rdir, ".transaction.json")):
        r.add("journal", "Records", "warn", "An interrupted write was left in the journal",
              "Opening the project rolls it back automatically; nothing is lost.", {"text": "Run any `research` command to recover, then re-run the doctor."})
    try:
        p = store.Project(root, sync=False)
    except ResearchError as e:
        r.add("open", "Project", "error", "The project could not be opened", str(e), {"text": e.hint} if e.hint else None)
        return r.result(root)
    try:
        _records(r, p, rdir)
        _ledger(r, p, loc)
        _workspaces(r, p)
        _runs(r, p)
        _artifacts(r, p)
        _agents(r, p)
        _sandbox(r, p)
        _hpc(r, p)
        _context(r, p)
    finally:
        p.close()
    return r.result(root)


# ------------------------------------------------------------------------------------------------ areas
def _records(r: _Report, p, rdir: str) -> None:
    """Rebuild the index from a scratch copy of the canonical records and compare it with the live one."""
    skip = shutil.ignore_patterns("research.db*", "bin", "cache", "locks", "reports", "hpc", "*.log", "outputs", ".write.lock", ".transaction.json")
    tmp = tempfile.mkdtemp(prefix="research-doctor-")
    try:
        copy = os.path.join(tmp, "p")
        os.makedirs(copy)
        shutil.copytree(rdir, os.path.join(copy, ".research"), ignore=skip)
        from .store import Project
        with Project(copy, sync=False) as q:     # no database in the copy: opening it is a full rebuild
            rebuilt = _counts(q)
        live = _counts(p)
        diff = {k: (live[k], rebuilt[k]) for k in live if live[k] != rebuilt[k]}
        if diff:
            r.add("records.rebuild", "Records", "warn", "The index differs from the canonical records",
                  "; ".join(f"{k}: index {a}, records {b}" for k, (a, b) in diff.items()),
                  {"text": "`research sync` imports hand edits; `research rebuild` recreates the index (a backup is kept)."})
        else:
            r.add("records.rebuild", "Records", "ok", "Records rebuild to exactly the live index",
                  ", ".join(f"{v} {k}" for k, v in live.items() if v) or "no records yet")
    except ResearchError as e:
        r.add("records.rebuild", "Records", "error", "The records cannot be rebuilt", str(e),
              {"text": "Fix or restore the file named above; Research OS never overwrites a malformed record."})
    except Exception as e:   # a doctor must report, not crash
        r.add("records.rebuild", "Records", "error", "Rebuild check failed unexpectedly", f"{type(e).__name__}: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _ledger(r: _Report, p, loc) -> None:
    from . import workspace as W
    if not loc.linked:
        r.add("ledger", "Git & workspaces", "info", "Single-folder project",
              "Experiment worktrees need a shared ledger. Optional: `research ledger enable` previews the change.")
        return
    problems = []
    if not os.path.isdir(loc.ledger_root):
        problems.append(f"ledger directory {loc.ledger_root} is missing")
    ident = W.project_identity(p.rdir)
    if not ident:
        problems.append("project.json (portable project id) is missing")
    branch = W._git(loc.ledger_root, "rev-parse", "--abbrev-ref", "HEAD") if os.path.isdir(loc.ledger_root) else None
    if branch != W.LEDGER_BRANCH:
        problems.append(f"the ledger worktree is on {branch!r}, expected {W.LEDGER_BRANCH}")
    if problems:
        r.add("ledger", "Git & workspaces", "error", "Shared ledger problem", "; ".join(problems),
              {"text": "Restore the ledger directory, or point this repository at it with `research ledger relink <path>`."})
    else:
        r.add("ledger", "Git & workspaces", "ok", "Shared ledger connected", f"{loc.ledger_root} (project {ident['project_id'][:8]})")
    stale = [w["path"] for w in W.worktrees(loc.source_root) if w["prunable"]]
    if stale:
        r.add("worktrees.prunable", "Git & workspaces", "warn", f"{len(stale)} git worktree(s) point at missing directories",
              ", ".join(stale), {"text": "Review with `git worktree list`; `git worktree prune` removes only the stale entries."})


def _workspaces(r: _Report, p) -> None:
    from . import workspace as W
    if not p.location.linked:
        return
    for w in W.list_workspaces(p):
        if not w["present"]:
            r.add(f"workspace.{w['id']}", "Git & workspaces", "warn", f"{w['id']} has no directory on this machine",
                  f"branch {w.get('branch')}; its commits are safe in git.", {"text": "Re-create the worktree with `git worktree add`, or archive the record."})
        elif w.get("dirty") or w.get("untracked"):
            r.add(f"workspace.{w['id']}", "Git & workspaces", "warn", f"{w['id']} has uncommitted work",
                  f"{'tracked changes' if w.get('dirty') else ''}{' and ' if w.get('dirty') and w.get('untracked') else ''}"
                  f"{str(w.get('untracked')) + ' untracked file(s)' if w.get('untracked') else ''} in {w['path']}. Archiving is blocked until committed.")
    n = len(W.list_workspaces(p))
    if n:
        r.add("workspaces", "Git & workspaces", "ok", f"{n} active workspace(s)")


def _runs(r: _Report, p) -> None:
    import socket
    from .backends.local import pid_alive
    stale = []
    for row in p.q("SELECT id, status, pid, hostname, backend FROM runs WHERE status IN ('queued','running') AND backend='local'"):
        if row["pid"] and row["hostname"] in (None, socket.gethostname()) and not pid_alive(row["pid"]):
            stale.append(row["id"])
    if stale:
        r.add("runs.stale", "Runs", "warn", f"{len(stale)} run(s) look interrupted", ", ".join(stale) + " — marked running but the process is gone.",
              {"text": "`research sync` reconciles them from their run directories.", "command": "research sync"})
    if p.q1("SELECT COUNT(*) AS n FROM runs WHERE status IN ('queued','running') AND backend='slurm'")["n"]:
        r.add("runs.slurm", "Runs", "info", "SLURM runs are outstanding", "`research hpc sync` or `research run list` shows their current state.")


def _artifacts(r: _Report, p) -> None:
    missing = []
    for a in p.q("SELECT id, path FROM artifacts"):
        ap = p.abspath(a["path"])
        if ap and not os.path.exists(ap):
            missing.append(a["id"])
    if missing:
        r.add("artifacts.missing", "Records", "warn", f"{len(missing)} registered artifact file(s) are missing",
              ", ".join(missing[:8]) + (" …" if len(missing) > 8 else "") + ". The records are kept; the files may be on another machine or deleted.",
              {"text": "Restore or re-mount the storage; `research evidence verify` reports which findings depended on them."})


def _agents(r: _Report, p) -> None:
    ag = p.config.get("agents") or {}
    name = ag.get("default_profile")
    prof = (ag.get("profiles") or {}).get(name) or {}
    cmd = prof.get("command") or name
    if cmd and not shutil.which(cmd):
        r.add("agents.cli", "Agents", "info", f"Agent CLI `{cmd}` is not on PATH",
              "Only needed to dispatch agents from Research OS; you can still run agents yourself and they use `research` normally.")
    elif cmd:
        r.add("agents.cli", "Agents", "ok", f"Agent CLI `{cmd}` found")
    try:
        from . import skills as SK
        miss = SK.required_for(p)["missing"]
        if miss:
            r.add("skills.required", "Agents", "error", "Required skills are missing", ", ".join(miss),
                  {"text": "Restore them with `research skills add …`; dispatch is blocked until then."})
        else:
            r.add("skills", "Agents", "ok", f"{len(SK.list_skills(p))} skills installed")
    except Exception as e:
        r.add("skills", "Agents", "warn", "Skills could not be inspected", str(e))
    from . import mcp as MCP
    st = MCP.status(p.root)
    reg = [c["client"] for c in st["clients"] if c["registered"]]
    if reg:
        r.add("mcp", "Agents", "ok", "MCP server registered", ", ".join(reg))
    else:
        r.add("mcp", "Agents", "info", "MCP server not registered in any client", "Optional: `research mcp install` lets agents call Research OS as tools.")


def _sandbox(r: _Report, p) -> None:
    from . import isolation as I, sandbox as SB
    c = SB.cfg(p)
    in_use = set()
    if c.get("enabled"):
        in_use.add(c.get("default_profile") or "native")
    in_use |= set((c.get("assign") or {}).get("experiments", {}).values()) | set((c.get("assign") or {}).get("tasks", {}).values())
    in_use.discard("native")
    if not in_use:
        avail = [rt.name for rt in I.RUNTIMES.values() if rt.name != "native" and rt.detect()["available"]]
        r.add("sandbox", "Execution", "info", "Job isolation is not enabled",
              "Jobs run natively. Available if you want it: " + (", ".join(avail) if avail else "no runtime found on this machine") + ".")
        return
    for name in sorted(in_use):
        try:
            res = SB.verify(p, name)
        except ResearchError as e:
            r.add(f"sandbox.{name}", "Execution", "error", f"Sandbox profile {name} is invalid", str(e))
            continue
        if res["allowed"]:
            states = {x["state"] for x in res["properties"]}
            r.add(f"sandbox.{name}", "Execution", "ok" if states == {"verified"} else "warn", f"Profile {name} can be enforced on {res['runtime']}",
                  "every requested restriction was verified by a self-test" if states == {"verified"} else "some restrictions are enforceable but unverified: " + ", ".join(sorted(states)))
        else:
            r.add(f"sandbox.{name}", "Execution", "error", f"Profile {name} cannot be enforced here: jobs will be refused",
                  res.get("refusal") or "", {"text": "`research sandbox doctor` shows what each runtime needs. Jobs never fall back to native."})
    if (c.get("agent") or {}).get("mode") == "advisory":
        r.add("sandbox.agent", "Execution", "info", "Agent boundary is advisory", "It is written into briefs; Research OS does not enforce it for agents.")


def _hpc(r: _Report, p) -> None:
    cl = ((p.config.get("hpc") or {}).get("clusters")) or {}
    if not cl:
        return   # not enabled: nothing to report
    r.add("hpc", "Execution", "info", f"{len(cl)} HPC cluster(s) configured: {', '.join(sorted(cl))}",
          "The doctor does not open SSH connections. Test one with `research hpc doctor`.", {"command": "research hpc doctor"})


def _context(r: _Report, p) -> None:
    from . import agentsmd, context as C, registry
    stale = []
    if not os.path.exists(p.path("context", "current.md")):
        stale.append("current.md is missing")
    pol = p.path("context", "policy.md")
    if not os.path.exists(pol) or f"`{registry.fingerprint(p)}`" not in open(pol, encoding="utf-8").read():
        stale.append("policy.md does not match the current settings")
    if stale:
        r.add("context.stale", "Agent context", "warn", "Generated agent context is out of date", "; ".join(stale),
              {"text": "Regenerates only .research/context/*.md.", "safe": True})
    else:
        r.add("context", "Agent context", "ok", "Agent context matches the current settings")
    st = agentsmd.state(p)
    if st.get("problem"):
        r.add("agents_md.problem", "Agent context", "error", "AGENTS.md markers are ambiguous", str(st["problem"]),
              {"text": "Fix the `research:begin` / `research:end` markers by hand; nothing was changed."})
    elif not st.get("up_to_date", True):
        r.add("agents_md.stale", "Agent context", "warn", "AGENTS.md's managed block is out of date",
              "Only the text between the research markers is rewritten; everything else is yours.", {"text": "`research agents-md --diff` previews it.", "safe": True})
    else:
        r.add("agents_md", "Agent context", "ok", "AGENTS.md managed block is current")
    from . import localbin
    b = localbin.status(p.root)
    if b["exists"] is False:
        r.add("bin.stale", "Agent context", "info", ".research/bin/research is not installed",
              "Agents use it to run the CLI without installing anything.", {"text": "`research bin install`", "safe": True})


# ------------------------------------------------------------------------------------------------ fixes
def apply_fix(root: str, check_id: str) -> Dict[str, Any]:
    """Run one safe fix. Only generated files are touched; the caller must have been shown what it does."""
    if check_id not in SAFE_FIXES:
        raise ResearchError(f"{check_id} has no automatic fix", hint="Follow the instructions shown with the check.")
    from . import agentsmd, context as C, localbin, registry, store
    with store.Project(root) as p:
        if check_id == "context.stale":
            C.write_current_md(p)
            registry.write_policy(p)
            return {"fixed": check_id, "wrote": [".research/context/current.md", ".research/context/policy.md"]}
        if check_id == "agents_md.stale":
            agentsmd.apply(p)
            return {"fixed": check_id, "wrote": ["AGENTS.md (managed block only)"]}
        localbin.install(root)
        return {"fixed": check_id, "wrote": [".research/bin/"]}
