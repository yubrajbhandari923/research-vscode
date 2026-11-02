"""`Research: Initialize Project` — create the whole shared-ledger layout from an empty folder.

    <root>/main/            git checkout of branch `main`  (its files are the repository root on GitHub)
    <root>/ledger/          worktree of branch `research/ledger` holding .research/
    <root>/experiments/     one worktree per experiment, created on demand
    <root>/<name>.code-workspace

Everything is previewed first (`plan`) and nothing outside `root` is touched. The first commit contains only files this
module wrote (.gitignore, README.md, AGENTS.md, CLAUDE.md); it never runs `git add -A`, so pre-existing files in the folder are
never committed. Nothing is pushed. Provider MCP registration is limited to project files, except Codex, whose only
registration is the per-user `codex mcp add`; that step is listed in the preview and only runs if Codex is enabled.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional

from . import gitinfo, providers
from . import workspace as W
from .util import ResearchError

DEFAULT_LAYOUT = {"main_dir": "main", "ledger_dir": "ledger", "experiments_dir": "experiments"}
PROVIDER_PROFILES = {"claude": "claude", "codex": "codex", "agy": "agy"}
STRATEGIES = ("sonnet_first", "opus_first", "haiku_max", "sonnet_heavy")


def _git(cwd: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _identity_env(cwd: str) -> Dict[str, str]:
    """Commit identity: the user's own git config when set, a neutral placeholder only when it is not."""
    env = dict(os.environ)
    if not _git(cwd, "config", "user.name").stdout.strip():
        env.setdefault("GIT_AUTHOR_NAME", "Research OS")
        env.setdefault("GIT_COMMITTER_NAME", "Research OS")
    if not _git(cwd, "config", "user.email").stdout.strip():
        env.setdefault("GIT_AUTHOR_EMAIL", "research-os@localhost")
        env.setdefault("GIT_COMMITTER_EMAIL", "research-os@localhost")
    return env


def ssh_hosts() -> List[str]:
    """Concrete `Host` aliases from ~/.ssh/config (wildcards skipped). Read-only; nothing is contacted."""
    path = os.path.expanduser("~/.ssh/config")
    hosts: List[str] = []
    try:
        for line in open(path, encoding="utf-8", errors="replace"):
            m = re.match(r"\s*Host\s+(.+)", line, re.I)
            if m:
                hosts += [h for h in m.group(1).split() if not re.search(r"[*?!]", h)]
    except OSError:
        pass
    return sorted(set(hosts))


def environment(root: str) -> Dict[str, Any]:
    from .store import DEFAULT_CONFIG
    root = os.path.abspath(root)
    entries = sorted(e for e in os.listdir(root) if e not in (".vscode", ".DS_Store")) if os.path.isdir(root) else []   # editor folders do not make a folder non-empty
    profs = DEFAULT_CONFIG["agents"]["profiles"]
    provs = []
    for name, key in PROVIDER_PROFILES.items():
        r = providers.readiness(profs[key], key)
        r["models"] = providers.available_models(r["provider"], r["command"], profs)
        provs.append(r)
    return {"root": root, "folder_name": os.path.basename(root), "folder_exists": os.path.isdir(root),
            "entries": entries, "empty": not entries, "layout": DEFAULT_LAYOUT,
            "existing_project": os.path.isfile(os.path.join(root, ".research", "config.yaml"))
            or any(os.path.isfile(os.path.join(root, d, ".research", "config.yaml")) for d in ("ledger", "main")),
            "git": {"available": gitinfo.available(), "version": providers.version("git"),
                    "user_configured": bool(_git(root if os.path.isdir(root) else ".", "config", "user.name").stdout.strip())},
            "providers": provs, "strategies": list(STRATEGIES), "ssh_hosts": ssh_hosts(),
            "defaults": {"provider": "claude", "strategy": "sonnet_first", "create_workspace": True, "local_execution": True,
                         "independent_review": True, "hpc": False}}


def _layout(root: str, spec: Dict[str, Any]) -> Dict[str, str]:
    lay = {**DEFAULT_LAYOUT, **{k: v for k, v in (spec.get("layout") or {}).items() if v}}
    abs_ = {k: (v if os.path.isabs(v) else os.path.join(root, v)) for k, v in lay.items()}
    return {**{k: v for k, v in lay.items()}, **{k.replace("_dir", "_path"): os.path.abspath(v) for k, v in abs_.items()}}


def _workspace_name(spec: Dict[str, Any], root: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", spec.get("name") or os.path.basename(root)).strip("-") or "project"


def plan(root: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    root = os.path.abspath(root)
    env = environment(root)
    lay = _layout(root, spec)
    problems: List[str] = []
    actions: List[str] = []
    notes: List[str] = []
    if not spec.get("name"):
        problems.append("A project name is required.")
    if not env["git"]["available"]:
        problems.append("Git was not found. Research OS stores each experiment as a git worktree, so git is required.")
    if env["existing_project"]:
        problems.append("This folder already contains a Research OS project. Use Settings to change it, or `research ledger enable` "
                        "to migrate a classic project.")
    for key in ("main_path", "ledger_path"):
        if os.path.exists(lay[key]) and os.listdir(lay[key]):
            problems.append(f"{lay[key]} already exists and is not empty; choose another folder name.")
    if os.path.exists(lay["experiments_path"]) and os.listdir(lay["experiments_path"]):
        problems.append(f"{lay['experiments_path']} is not empty; experiments/ must start empty.")
    paths = [lay["main_path"], lay["ledger_path"], lay["experiments_path"]]
    if len({os.path.realpath(x) for x in paths}) != 3:
        problems.append("The main checkout, ledger and experiments folders must be three different folders.")
    for a in paths:
        for b in paths:
            if a != b and (os.path.realpath(a) + os.sep).startswith(os.path.realpath(b) + os.sep):
                problems.append(f"{a} is inside {b}; keep the three folders side by side.")
    ag = spec.get("agents") or {}
    enabled = ag.get("enabled") or ["claude"]
    default = ag.get("default") or enabled[0]
    unknown = [x for x in enabled + [default] if x not in PROVIDER_PROFILES]
    if unknown:
        problems.append(f"Unknown agent(s): {', '.join(unknown)}.")
    if default not in enabled:
        problems.append("The default agent must be one of the enabled agents.")
    if ag.get("strategy") and ag["strategy"] not in STRATEGIES:
        problems.append(f"Unknown allocation strategy {ag['strategy']!r}.")
    missing = [x for x in enabled if x in PROVIDER_PROFILES and not next(p for p in env["providers"] if p["name"] == x)["installed"]]
    if missing:
        notes.append(f"{', '.join(missing)} not installed on this machine: configured now, but cannot run tasks until installed.")
    hpc = spec.get("hpc") or {}
    if hpc.get("enabled") and not (hpc.get("ssh_host") and hpc.get("workspace_root")):
        problems.append("HPC deployment needs an SSH host and a remote workspace directory (or turn HPC off).")
    remote = (spec.get("git") or {}).get("remote_url")
    if remote and not re.match(r"^(https://|git@|ssh://)[^\s]+$", remote):
        problems.append("The GitHub remote URL must start with https://, git@ or ssh://.")
    if not problems:
        rel = lambda x: os.path.relpath(x, root)  # noqa: E731
        actions += [f"Create {rel(lay['main_path'])}/ and run `git init -b main` there",
                    "Write .gitignore and README.md and make the first commit containing only those files (nothing else is added)",
                    f"Create the research ledger worktree at {rel(lay['ledger_path'])}/ on branch research/ledger",
                    f"Create {rel(lay['experiments_path'])}/ (empty; each experiment gets {rel(lay['experiments_path'])}/EXP-NNN/ when you create it)",
                    "Write AGENTS.md (the one set of project rules every agent reads) and CLAUDE.md that imports it, and commit those two files",
                    f"Save layout, default agent ({default}), allocation ({ag.get('strategy') or 'sonnet_first'}) and review policy in the ledger config"]
        for x in enabled:
            actions.append({"claude": "Register the Research MCP server for Claude Code in main/.mcp.json",
                            "agy": "Register the Research MCP server for Antigravity in main/.agents/mcp_config.json",
                            "codex": "Register the Research MCP server for Codex with `codex mcp add research` (your user-level Codex config)"}[x])
        if spec.get("create_workspace", True):
            actions.append(f"Write {_workspace_name(spec, root)}.code-workspace and open it in VS Code")
        if hpc.get("enabled"):
            actions.append(f"Save HPC cluster profile for {hpc['ssh_host']} (no connection is made while initializing; test it later with `research hpc doctor`)")
        if remote:
            actions.append(f"Add git remote origin = {remote} (nothing is pushed)")
        if env["entries"]:
            notes.append(f"{len(env['entries'])} existing item(s) in this folder are left alone and not committed.")
    tree = [f"{os.path.basename(root)}/", "├── main/        # git checkout of `main`; GitHub's root", "├── ledger/      # branch research/ledger, holds .research/",
            "├── experiments/ # empty now; EXP-001/, EXP-002/ appear as you create experiments", f"└── {_workspace_name(spec, root)}.code-workspace"]
    return {"ok": not problems, "problems": problems, "actions": actions, "notes": notes, "tree": tree, "layout": lay, "environment": env}


def workspace_file(root: str, spec: Dict[str, Any], lay: Dict[str, str]) -> Dict[str, Any]:
    folders = [{"name": "main (code)", "path": lay["main_dir"]}, {"name": "ledger (research records)", "path": lay["ledger_dir"]},
               {"name": "experiments (one worktree each)", "path": lay.get("experiments_dir", "experiments")}]
    return {"folders": folders,
            "settings": {"research.projectRoot": ".", "git.autoRepositoryDetection": "subFolders",
                         "git.repositoryScanMaxDepth": 3, "git.countBadge": "tracked", "files.exclude": {"**/.research/cache": True},
                         "git.repositoryScanIgnoredFolders": []},
            "extensions": {"recommendations": ["research-os.research-panel"]}}


def apply(root: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    from .store import Project
    pl = plan(root, spec)
    if not pl["ok"]:
        raise ResearchError("Cannot initialize: " + " ".join(pl["problems"]))
    root = os.path.abspath(root)
    lay = pl["layout"]
    main, ledger, exps = lay["main_path"], lay["ledger_path"], lay["experiments_path"]
    name, goal = spec["name"], spec.get("goal") or ""
    ag = spec.get("agents") or {}
    enabled = ag.get("enabled") or ["claude"]
    default = ag.get("default") or enabled[0]
    done: List[str] = []
    created: List[str] = []
    try:
        os.makedirs(main, exist_ok=True)
        created.append(main)
        r = _git(main, "init", "-q", "-b", "main")
        if r.returncode:
            raise ResearchError("git init failed: " + r.stderr.strip())
        done.append("git init (branch main)")
        for fname, text in ((".gitignore", ".research/\n.research.pre-ledger/\n.venv/\n__pycache__/\n*.pyc\n"),
                            ("README.md", f"# {name}\n\n{goal}\n\nManaged with [Research OS](https://github.com/yubrajbhandari923/research-vscode): "
                                          "see AGENTS.md for the project rules agents follow.\n")):
            fp = os.path.join(main, fname)
            if not os.path.exists(fp):
                open(fp, "w", encoding="utf-8").write(text)
        env = _identity_env(main)
        for cmd in (["add", "--", ".gitignore", "README.md"], ["commit", "-q", "-m", "Initialize project"]):
            r = subprocess.run(["git", *cmd], cwd=main, capture_output=True, text=True, env=env)
            if r.returncode:
                raise ResearchError(f"git {cmd[0]} failed: {r.stderr.strip()}")
        done.append("first commit (.gitignore, README.md only)")
        Project.init(main, name=name, goal=goal).close()
        res = W.enable(main, ledger)
        created.append(ledger)
        shutil.rmtree(main + os.sep + ".research.pre-ledger", ignore_errors=True)   # the project was created seconds ago; this is its empty seed
        done.append(f"shared ledger at {res['ledger_root']}")
        os.makedirs(exps, exist_ok=True)
        done.append("experiments/ created (empty)")

        p = Project(main)
        try:
            cfg: Dict[str, Any] = {"layout.main_dir": lay["main_dir"], "layout.ledger_dir": lay["ledger_dir"],
                                   "layout.experiments_dir": lay["experiments_dir"],
                                   "agents.default_profile": PROVIDER_PROFILES[default],
                                   "orchestration.enabled": bool(ag.get("strategy", "sonnet_first")),
                                   "orchestration.active_strategy": ag.get("strategy", "sonnet_first"),
                                   "verification.independent_reviewer": bool((spec.get("verification") or {}).get("independent_review", True))}
            if (spec.get("hpc") or {}).get("enabled"):
                h = spec["hpc"]
                name_ = h.get("name") or h["ssh_host"]
                cfg[f"hpc.clusters.{name_}.ssh_host"] = h["ssh_host"]
                for k in ("workspace_root", "shared_data", "projects", "scratch"):
                    if h.get(k):
                        cfg[f"hpc.clusters.{name_}.paths.{k}"] = h[k]
                if h.get("partition"):
                    cfg[f"hpc.clusters.{name_}.slurm.partition"] = h["partition"]
                cfg["hpc.default_cluster"] = name_
            p.save_config(cfg)
            p.config = p.load_config()
            from . import registry
            registry.write_policy(p)
        finally:
            p.close()
        providers.sync_instructions(main)
        tracked = ["AGENTS.md", "CLAUDE.md"]
        for x in enabled:
            try:
                mi = providers.mcp_install(PROVIDER_PROFILES[x], main)
                done.append(f"MCP registered for {x}: {mi['where']}")
                if mi["scope"] == "project":
                    tracked.append(mi["where"])
            except ResearchError as e:
                done.append(f"MCP for {x} NOT registered: {e}")
        existing = [t for t in tracked if os.path.exists(os.path.join(main, t))]
        subprocess.run(["git", "add", "--", *existing], cwd=main, capture_output=True, env=env)
        subprocess.run(["git", "commit", "-q", "-m", "Add agent instructions and MCP configuration"], cwd=main, capture_output=True, env=env)
        done.append("committed " + ", ".join(existing))
        if (spec.get("git") or {}).get("remote_url"):
            _git(main, "remote", "add", "origin", spec["git"]["remote_url"])
            done.append("remote origin added (not pushed)")
        wsf = None
        if spec.get("create_workspace", True):
            wsf = os.path.join(root, _workspace_name(spec, root) + ".code-workspace")
            with open(wsf, "w", encoding="utf-8") as f:
                json.dump(workspace_file(root, spec, lay), f, indent=2)
                f.write("\n")
            done.append(f"workspace file {os.path.basename(wsf)}")
    except BaseException:
        # Roll back only what this run created, never anything that was there before.
        if os.path.isdir(main) and os.path.isdir(ledger):
            subprocess.run(["git", "worktree", "remove", "--force", ledger], cwd=main, capture_output=True)
        for d in reversed(created):
            shutil.rmtree(d, ignore_errors=True)
        raise
    return {"ok": True, "root": root, "main": main, "ledger": ledger, "experiments": exps, "workspace_file": wsf, "done": done,
            "next": ["Open the workspace (it opens automatically).", "Research → New experiment: describe what you want to test.",
                     "Open the task, choose an agent and press Preview, then Start."]}
