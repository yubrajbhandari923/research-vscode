"""First-run setup and the guided "new experiment" flow. Both are thin sequences over existing services and the settings registry:
they add no storage of their own, validate everything up front, preview before writing, and never run, merge or delete anything.

Setup (`plan` / `apply`) can start minimal and be re-run later; every setting it writes goes through `registry.apply`, so it is
validated, audited and reflected in the generated agent context exactly like a change made in Settings."""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional

from . import gitinfo, isolation, registry, workspace as W
from .util import IncompatibleProject, ResearchError

PROVIDERS = {"claude": "claude", "codex": "codex", "gemini": "gemini"}
STRATEGIES = ("sonnet_first", "opus_first", "haiku_max", "sonnet_heavy")


def environment(root: str) -> Dict[str, Any]:
    """What this machine and folder offer, so the wizard can disable what cannot work and say why."""
    root = os.path.abspath(root)
    top = W.toplevel(root) if os.path.isdir(root) else None
    has_commit = bool(top and gitinfo._git(top, "rev-parse", "--verify", "-q", "HEAD"))
    existing = os.path.isfile(os.path.join(root, ".research", "config.yaml"))
    fmt = None
    if existing:
        try:
            from .store import check_format
            check_format(os.path.join(root, ".research"))
            fmt = 2
        except IncompatibleProject as e:
            fmt = str(e)
    return {"root": root, "folder_exists": os.path.isdir(root), "folder_name": os.path.basename(root),
            "git": {"available": gitinfo.available(), "repo": bool(top), "has_commit": has_commit, "toplevel": top},
            "existing_project": existing, "format_ok": fmt == 2 if existing else None, "format_problem": fmt if existing and fmt != 2 else None,
            "agents": {name: bool(shutil.which(cmd)) for name, cmd in PROVIDERS.items()}, "strategies": list(STRATEGIES),
            "runtimes": [{"runtime": rt.name, "available": rt.detect()["available"], "notes": rt.detect()["notes"]}
                         for rt in isolation.RUNTIMES.values() if rt.name != "native"]}


def _settings(spec: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    ag = spec.get("agent") or {}
    if ag.get("provider"):
        out["agents.default_profile"] = PROVIDERS.get(ag["provider"], ag["provider"])
    if ag.get("strategy"):
        out["orchestration.active_strategy"] = ag["strategy"]
        out["orchestration.enabled"] = True
    iso = spec.get("isolation") or {}
    if iso.get("enabled"):
        out["sandbox.enabled"] = True
        out["sandbox.default_profile"] = iso.get("profile") or "restricted"
    if (spec.get("verification") or {}).get("independent_review") is not None:
        out["verification.independent_reviewer"] = bool(spec["verification"]["independent_review"])
    return out


def plan(root: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    env = environment(root)
    problems: List[str] = []
    actions: List[str] = []
    if env["format_problem"]:
        problems.append(env["format_problem"])
    if not (spec.get("name") or env["existing_project"]):
        problems.append("A project name is required.")
    ag = spec.get("agent") or {}
    if ag.get("provider") and ag["provider"] not in PROVIDERS:
        problems.append(f"Unknown agent provider {ag['provider']!r}.")
    if ag.get("strategy") and (ag["strategy"] not in STRATEGIES or ag.get("provider") not in (None, "claude")):
        problems.append("Model allocation strategies apply to the Claude provider only.")
    if spec.get("git_init") and env["git"]["repo"]:
        problems.append("This folder is already inside a git repository.")
    wants_ledger = bool(spec.get("shared_ledger"))
    if wants_ledger:
        if not env["git"]["available"]:
            problems.append("Git worktrees need git, which was not found.")
        elif not (env["git"]["repo"] or spec.get("git_init")):
            problems.append("The shared ledger needs a git repository. Choose 'create a git repository' or run git init first.")
        elif env["git"]["repo"] and not env["git"]["has_commit"]:
            problems.append("The shared ledger needs at least one commit in the repository (Research OS will not make one for you).")
        elif spec.get("git_init"):
            problems.append("A newly created repository has no commit yet; make your first commit, then enable the shared ledger in Settings.")
    iso = spec.get("isolation") or {}
    if iso.get("enabled"):
        avail = [x for x in env["runtimes"] if x["available"]]
        if not avail:
            problems.append("Job isolation was requested but no runtime (userns, apptainer) is available on this machine; jobs would be refused.")
        for d in iso.get("datasets") or []:
            try:
                isolation.safe_host_path(d, "dataset", [], [])
            except isolation.IsolationError as e:
                problems.append(str(e))
    st = _settings(spec)
    for k, v in st.items():
        ok, _, err = registry.validate(k, v)
        if not ok:
            problems.append(f"{k}: {err}")
    if not env["existing_project"]:
        actions.append(f"Create .research/ for “{spec.get('name')}” (project format 2) and the managed block in AGENTS.md")
    if spec.get("git_init"):
        actions.append("Run `git init` in this folder")
    for k, v in st.items():
        actions.append(f"Set {k} = {v}")
    for d in iso.get("datasets") or []:
        actions.append(f"Allow read-only access to dataset {d} under the restricted profiles")
    if wants_ledger and not problems:
        actions.append("Create the shared research ledger worktree (branch research/ledger); your current .research/ is kept as .research.pre-ledger")
    actions.append("Generate .research/context/policy.md and current.md for agents")
    return {"ok": not problems, "problems": problems, "actions": actions, "environment": env}


def apply(root: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    from .store import Project
    pl = plan(root, spec)
    if not pl["ok"]:
        raise ResearchError("Setup cannot proceed: " + " ".join(pl["problems"]))
    root = os.path.abspath(root)
    os.makedirs(root, exist_ok=True)
    done: List[str] = []
    if spec.get("git_init"):
        subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
        done.append("git init")
    existing = pl["environment"]["existing_project"]
    p = Project.init(root, name=spec.get("name") or None, goal=spec.get("goal") or None) if not existing else Project(root)
    try:
        if not existing:
            done.append("project created")
        st = _settings(spec)
        if st:
            registry.apply(p, st, reason="first-run setup")
            done += [f"{k} = {v}" for k, v in st.items()]
        ds = [os.path.realpath(os.path.expanduser(d)) for d in (spec.get("isolation") or {}).get("datasets") or []]
        if ds:
            for prof in ("restricted", "gpu-research", "custom"):
                p.save_config({f"sandbox.profiles.{prof}.filesystem.datasets": ds})
            p.config = p.load_config()
            p.event("config", "sandbox.datasets", "changed", f"setup: datasets {ds} added to the restricted profiles")
            done.append("datasets: " + ", ".join(ds))
        registry.write_policy(p)
        from . import context as C
        C.write_current_md(p)
    finally:
        p.close()
    if spec.get("shared_ledger"):
        r = W.enable(root, spec.get("ledger_path"))
        done.append(f"shared ledger at {r['ledger_root']}")
    return {"ok": True, "root": root, "done": done,
            "next": ["Open the Control Center and create your first experiment (Research → New experiment).",
                     "Agents read .research/context/policy.md; change settings later under Settings."]}


# ------------------------------------------------------------------------------------------------ new experiment
def new_experiment_plan(p, spec: Dict[str, Any]) -> Dict[str, Any]:
    problems: List[str] = []
    actions: List[str] = []
    if not (spec.get("title") or "").strip():
        problems.append("The experiment needs a title.")
    q = spec.get("question") or {}
    if q.get("id"):
        try:
            p.get("question", q["id"])
        except Exception:
            problems.append(f"Question {q['id']} does not exist.")
    elif q.get("title"):
        actions.append(f"Create question “{q['title']}”")
    actions.append(f"Create experiment “{spec.get('title')}” (status proposed)")
    wt = spec.get("worktree") or {}
    if wt.get("create"):
        if not p.location.linked:
            problems.append("An isolated workspace needs the shared ledger (Settings → Git & workspaces).")
        else:
            actions.append("Create a git branch and worktree for it (your current branch is not touched)")
    tk = spec.get("task") or {}
    if tk.get("create"):
        actions.append(f"Create task “{tk.get('title') or spec.get('title')}” in " + (f"plan {tk['plan']}" if tk.get("plan") else "a new plan"))
    prof = (spec.get("execution") or {}).get("profile")
    if prof:
        try:
            from . import sandbox as SB
            SB.profile(p, prof)
            actions.append(f"Run this experiment's jobs under the {prof} sandbox profile")
        except ResearchError as e:
            problems.append(str(e))
    if (spec.get("agent") or {}).get("dispatch"):
        if not tk.get("create"):
            problems.append("Preparing an agent needs a task to hand over (tick 'Create a task').")
        actions.append("Prepare the agent's brief and command (nothing is started)")
    return {"ok": not problems, "problems": problems, "actions": actions}


def new_experiment(p, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Create everything the user ticked, in order. Records are created individually and kept even if a later step fails;
    the error then says what exists. Runs, merges, and deletions are never part of this flow."""
    from . import agents, plans, services
    pl = new_experiment_plan(p, spec)
    if not pl["ok"]:
        raise ResearchError("Cannot create: " + " ".join(pl["problems"]))
    out: Dict[str, Any] = {}
    try:
        q = spec.get("question") or {}
        qid = q.get("id") or (services.create_question(p, q["title"])["id"] if q.get("title") else None)
        out["question"] = qid
        e = services.create_experiment(p, spec["title"].strip(), question=qid, hypothesis=spec.get("hypothesis") or None,
                                       parameters=spec.get("parameters") or None, planned_runs=spec.get("planned_runs") or None)
        out["experiment"] = e["id"]
        tk = spec.get("task") or {}
        if tk.get("create"):
            plan_id = tk.get("plan") or plans.create_plan(p, f"Work on {e['id']}", objective=spec.get("hypothesis") or spec["title"])["id"]
            t = plans.create_task(p, plan_id, tk.get("title") or spec["title"], goal=spec.get("hypothesis") or None)
            out["plan"], out["task"] = plan_id, t["id"]
        if (spec.get("worktree") or {}).get("create"):
            ws = W.create(p, e["id"], name=(spec["worktree"].get("name") or None))
            out["workspace"] = {"id": ws["id"], "path": ws["path"], "branch": ws["branch"]}
            if out.get("task"):
                W.link_task(p, ws["id"], out["task"])
        prof = (spec.get("execution") or {}).get("profile")
        if prof:
            registry.set_assignment(p, "experiments", e["id"], prof)
            out["sandbox_profile"] = prof
        ag = spec.get("agent") or {}
        if ag.get("dispatch"):
            d = agents.dispatch(p, ag.get("role") or "implementer", out["task"], profile=ag.get("profile") or None)
            out["dispatch"] = {"brief": d["brief"], "cwd": d["cwd"], "command": d["shell"], "available": d["available"]}
        out["next"] = [f"research run exec {e['id']} -- <your command>" + (f"   (run it inside {out['workspace']['path']})" if out.get("workspace") else "")]
    except Exception as err:
        raise ResearchError(f"Stopped part-way: {err}. Already created and kept: " + ", ".join(f"{k} {v if isinstance(v, str) else v.get('id')}" for k, v in out.items() if v),
                            hint="Nothing is rolled back; finish the remaining steps from the Work or Research pages.")
    return out
