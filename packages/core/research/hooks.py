"""Executable harness hooks: project-defined commands Research OS runs at fixed workflow stages.

    harness:
      hooks:
        oracle: {stage: post_implementation, command: "pytest tests/oracle -q", timeout: 120, required: true}
      hooks_approved: {oracle: <hash>}        # written by `research hook approve` (a human); never by an agent

Stages: preflight (before a task is dispatched) · post_implementation (after a worker's change) ·
pre_experiment (before a run is created) · post_experiment (after runs finish, in `experiment check`).

Safeguards: nothing runs until a *human* approves that exact command (approval is a hash of stage+command+timeout, so
any edit revokes it); every hook has a timeout and its process group is killed on expiry; output is truncated; every
execution is appended to .research/hooks/results.jsonl. An unapproved hook is reported as skipped, never as passed.
Hooks are deterministic checks: they replace model tokens spent re-checking what a program can verify.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from typing import Any, Dict, List, Optional

from .store import append_jsonl
from .processes import shell_check
from .util import PolicyBlocked, ResearchError, now_iso

STAGES = ["preflight", "post_implementation", "pre_experiment", "post_experiment"]
_NAME = re.compile(r"^[A-Za-z][\w-]*$")


def _h(p) -> Dict[str, Any]:
    return p.config.get("harness") or {}


def specs(p) -> Dict[str, Dict[str, Any]]:
    return {n: s for n, s in (_h(p).get("hooks") or {}).items() if isinstance(s, dict)}


def timeout_for(p, spec: Dict[str, Any]) -> float:
    value = spec.get("timeout", _h(p).get("hooks_timeout_seconds", 300))
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        raise ResearchError("Hook timeout must be a positive finite number")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ResearchError("Hook timeout must be a positive finite number")
    return timeout


def digest(p, spec: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps([spec.get("stage"), spec.get("command"), timeout_for(p, spec)]).encode()).hexdigest()[:16]


def is_approved(p, name: str) -> bool:
    s = specs(p).get(name)
    return bool(s) and (_h(p).get("hooks_approved") or {}).get(name) == digest(p, s)


def add(p, name: str, stage: str, command: str, timeout: Optional[float] = None, required: bool = True,
        description: Optional[str] = None) -> Dict[str, Any]:
    """Register (or change) a hook. It stays unapproved — and so does not run — until a human approves it."""
    if not _NAME.match(name or ""):
        raise ResearchError(f"Bad hook name {name!r}", hint="Letters, digits, '_' and '-', starting with a letter.")
    if stage not in STAGES:
        raise ResearchError(f"stage must be one of {', '.join(STAGES)}")
    if not (command or "").strip():
        raise ResearchError("A hook needs a command")
    spec = {"stage": stage, "command": command.strip(), "required": bool(required), "enabled": True}
    if timeout is not None:
        spec["timeout"] = timeout_for(p, {"timeout": timeout})
    if description:
        spec["description"] = description
    p.save_config({f"harness.hooks.{name}": spec})
    p.config = p.load_config()
    p.event("project", None, "hook_registered", f"harness hook {name} ({stage}) registered; awaiting human approval")
    return status_one(p, name)


# The hook catalog. `tier`: cheap (seconds, reads files and records, never runs project code) or costly (runs project code or toolchains).
# `stacks`: the project kinds a hook is meant for (None = every project). `default`: installed (and approved by a human) in new projects;
# the rest are offered by `recommended()` and installed on request. Commands use the checks module (see checks.py) and $RESEARCH_PYTHON.
_PY = '"$RESEARCH_PYTHON" -m research.checks '
CATALOG: Dict[str, Dict[str, Any]] = {
    "syntax-check": {"stage": "post_implementation", "tier": "cheap", "stacks": None, "default": True, "timeout": 120, "group": "code",
                     "command": "python3 -m compileall -q -x '(\\.venv|node_modules|\\.git|experiments)' .",
                     "description": "Python files compile after an agent's change (catches syntax errors before anything runs)."},
    "whitespace-check": {"stage": "post_implementation", "tier": "cheap", "stacks": None, "default": True, "timeout": 60, "group": "git",
                         "command": "git diff --check", "description": "No conflict markers or trailing-whitespace errors in the change."},
    "git-integrity": {"stage": "post_implementation", "tier": "cheap", "stacks": None, "default": True, "timeout": 120, "group": "git",
                      "command": _PY + "git-integrity", "description": "The repository is healthy: no damaged objects, unresolved conflicts or half-finished merge/rebase."},
    "secrets": {"stage": "post_implementation", "tier": "cheap", "stacks": None, "default": True, "timeout": 60, "group": "git",
                "command": _PY + "secrets", "description": "No private keys or well-known token formats in changed or new files (values are never printed)."},
    "task-scope": {"stage": "post_implementation", "tier": "cheap", "stacks": None, "default": True, "timeout": 60, "group": "task",
                   "command": _PY + "task-scope", "description": "A task changes only the files its contract allows."},
    "frozen-tests": {"stage": "post_implementation", "tier": "cheap", "stacks": None, "default": True, "timeout": 60, "group": "task",
                     "command": _PY + "frozen-tests", "description": "A task's frozen acceptance tests are byte-identical to what was approved."},
    "experiment-config": {"stage": "post_experiment", "tier": "cheap", "stacks": None, "default": True, "timeout": 60, "group": "experiment",
                          "command": _PY + "experiment-config", "description": "Runs record the code revision, a clean tree and the command; a seed is flagged (an error with harness.require_seed)."},
    "numeric-invariants": {"stage": "post_experiment", "tier": "cheap", "stacks": None, "default": True, "timeout": 60, "group": "science",
                           "command": _PY + "numeric-invariants", "description": "No NaN/Inf metric values; optional bounds from harness.invariants (e.g. rmse: {min: 0})."},
    "artifact-integrity": {"stage": "post_experiment", "tier": "cheap", "stacks": None, "default": True, "timeout": 120, "group": "science",
                           "command": _PY + "artifact-integrity", "description": "Registered artifacts exist and still match their recorded hash."},
    "lint": {"stage": "post_implementation", "tier": "cheap", "stacks": ["python"], "default": False, "timeout": 120, "group": "code",
             "command": _PY + "lint", "description": "ruff (or pyflakes) on the changed Python files; skipped, and says so, when neither is installed."},
    "format": {"stage": "post_implementation", "tier": "cheap", "stacks": ["python"], "default": False, "timeout": 120, "group": "code",
               "command": _PY + "format", "description": "ruff format / black --check on the changed Python files."},
    "tests": {"stage": "post_implementation", "tier": "costly", "stacks": ["python"], "default": False, "timeout": 900, "group": "code",
              "command": _PY + "tests", "description": "Runs the project's pytest suite. It executes project code, so it is offered, never automatic."},
    "tsc": {"stage": "post_implementation", "tier": "costly", "stacks": ["typescript"], "default": False, "timeout": 900, "group": "code",
            "command": _PY + "tsc", "description": "TypeScript type check with the project's own compiler (never downloads one)."},
}
STARTERS = {n: c for n, c in CATALOG.items() if c["default"]}


def detect_stacks(root: str) -> List[str]:
    """What kind of project this is, from marker files and a bounded look at dependency files. Never imports or runs project code."""
    stacks: List[str] = []
    names = set(os.listdir(root)) if os.path.isdir(root) else set()
    txt = ""
    for f in ("pyproject.toml", "requirements.txt", "environment.yml", "setup.py", "setup.cfg", "Pipfile"):
        if f in names:
            try:
                with open(os.path.join(root, f), encoding="utf-8", errors="replace") as fh:
                    txt += fh.read(60000).lower() + "\n"
            except OSError:
                pass
    if names & {"pyproject.toml", "requirements.txt", "setup.py", "Pipfile", "environment.yml"} or any(n.endswith(".py") for n in names):
        stacks.append("python")
    if "tsconfig.json" in names or ("package.json" in names and any(n.endswith((".ts", ".tsx")) for n in names)):
        stacks.append("typescript")
    elif "package.json" in names:
        stacks.append("javascript")
    if any(n.endswith(".ipynb") for n in names) or "notebooks" in names:
        stacks.append("notebooks")
    if re.search(r"\b(numpy|scipy|pandas|sympy|xarray|matplotlib|astropy|jax)\b", txt):
        stacks.append("scientific")
    if re.search(r"\b(torch|pytorch|tensorflow|jax|scikit-learn|sklearn|lightning|transformers|keras)\b", txt):
        stacks.append("ml")
    if re.search(r"\b(pydicom|nibabel|monai|simpleitk)\b", txt):
        stacks.append("medical-imaging")
    if re.search(r"\b(qiskit|cirq|pennylane)\b", txt):
        stacks.append("quantum")
    if "slurm" in names or any(n.endswith((".sbatch", ".slurm")) for n in names):
        stacks.append("hpc")
    return stacks


def recommended(p) -> Dict[str, Any]:
    """The catalog in the light of this project: what applies, what is installed and approved. Read-only."""
    stacks = detect_stacks(p.root)
    have = specs(p)
    rows = []
    for n, c in CATALOG.items():
        applies = c["stacks"] is None or bool(set(c["stacks"]) & set(stacks))
        st = status_one(p, n) if n in have else None
        rows.append({"name": n, "stage": c["stage"], "command": c["command"], "description": c["description"], "tier": c["tier"], "group": c["group"],
                     "stacks": c["stacks"], "default": c["default"], "applies": applies, "installed": n in have,
                     "approved": bool(st and st["approved"]), "enabled": bool(st and st["enabled"]), "last": st["last"] if st else None,
                     "timeout": c["timeout"]})
    return {"stacks": stacks, "hooks": rows}


def install(p, names: Any = None, approve_now: bool = False) -> Dict[str, Any]:
    """Register catalog hooks that are not there yet (never overwrites a hook of the same name). With approve_now (a human acting through
    the wizard or the Agents page, after seeing the commands) they are approved too. With no names: the project-aware set, that is every
    cheap hook that applies to this project's stacks."""
    if names is None:
        r = recommended(p)
        names = [h["name"] for h in r["hooks"] if h["applies"] and h["tier"] == "cheap" and not h["installed"]]
    added = []
    for n in names:
        c = CATALOG.get(n)
        if not c:
            raise ResearchError(f"No catalog hook named {n!r}", hint="`research hook recommend` lists them.")
        if n not in specs(p):
            add(p, n, c["stage"], c["command"], c.get("timeout"), False, c["description"])
            added.append(n)
    approved = approve(p, added)["approved"] if approve_now and added else []
    return {"added": added, "approved": approved}


def install_starters(p, approve_now: bool = False) -> Dict[str, Any]:
    """The universal, cheap, default set (what a new project gets)."""
    return install(p, list(STARTERS), approve_now)


def set_enabled(p, name: str, enabled: bool) -> Dict[str, Any]:
    if name not in specs(p):
        raise ResearchError(f"No hook named {name!r}", hint="`research hook list`")
    p.save_config({f"harness.hooks.{name}.enabled": bool(enabled)})
    p.config = p.load_config()
    return status_one(p, name)


def approve(p, names: Any = None) -> Dict[str, Any]:
    """A human approves the exact commands now configured. Agents cannot (exit code 3)."""
    if (p.author or {}).get("author_type") == "agent":
        raise PolicyBlocked("Only a human can approve hooks: an approved hook executes a shell command on this machine",
                            hint="Ask the researcher to run `research hook approve` (or use the Agent Setup page).")
    todo = [n for n in (names or specs(p)) if n in specs(p)]
    if not todo:
        raise ResearchError("No hooks to approve", hint="Register one with `research hook add`.")
    cur = dict(_h(p).get("hooks_approved") or {})
    for n in todo:
        cur[n] = digest(p, specs(p)[n])
    p.save_config({"harness.hooks_approved": cur})
    p.config = p.load_config()
    p.event("project", None, "hook_approved", f"hooks approved: {', '.join(todo)}")
    return {"approved": todo}


def revoke(p, name: str) -> Dict[str, Any]:
    cur = {k: v for k, v in (_h(p).get("hooks_approved") or {}).items() if k != name}
    p.save_config({"harness.hooks_approved": cur})
    p.config = p.load_config()
    return status_one(p, name)


def _results_path(p) -> str:
    return p.path("hooks", "results.jsonl")


def results(p, name: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    try:
        with open(_results_path(p), encoding="utf-8") as f:
            rows = [json.loads(x) for x in f.readlines()[-2000:] if x.strip()]
    except (OSError, ValueError):
        return []
    return [r for r in rows if not name or r["hook"] == name][-limit:][::-1]


def status_one(p, name: str) -> Dict[str, Any]:
    s = specs(p)[name]
    last = (results(p, name, 1) or [None])[0]
    return {"name": name, "stage": s.get("stage"), "command": s.get("command"), "timeout": timeout_for(p, s),
            "required": s.get("required", True), "enabled": s.get("enabled", True), "description": s.get("description"),
            "approved": is_approved(p, name), "last": last}


def listing(p) -> List[Dict[str, Any]]:
    return [status_one(p, n) for n in sorted(specs(p))]


def _exec(p, name: str, spec: Dict[str, Any], env_extra: Dict[str, str]) -> Dict[str, Any]:
    import sys
    pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # the directory holding the `research` package, so `python -m research.checks` works from any interpreter
    result = shell_check(spec["command"], cwd=p.root, timeout=timeout_for(p, spec),
                         env={**os.environ, "RESEARCH_ROOT": p.root, "RESEARCH_HOOK": name, "RESEARCH_HOOK_STAGE": spec["stage"],
                              "RESEARCH_PYTHON": sys.executable, "PYTHONPATH": pkg + os.pathsep + os.environ.get("PYTHONPATH", ""), **env_extra})
    return {"ts": now_iso(), "hook": name, "stage": spec["stage"], **result,
            "required": spec.get("required", True), "digest": digest(p, spec)}


def run(p, stage: str, task: Optional[str] = None, experiment: Optional[str] = None, run_id: Optional[str] = None,
        names: Any = None) -> Dict[str, Any]:
    """Run every enabled, approved hook of `stage`. Unapproved or disabled hooks are listed as skipped, never as passed."""
    if stage not in STAGES:
        raise ResearchError(f"stage must be one of {', '.join(STAGES)}")
    env = {k: v for k, v in (("RESEARCH_TASK", task), ("RESEARCH_EXPERIMENT", experiment), ("RESEARCH_RUN", run_id)) if v}
    ran, skipped = [], []
    for n, s in sorted(specs(p).items()):
        if s.get("stage") != stage or (names and n not in names):
            continue
        if not s.get("enabled", True):
            skipped.append({"hook": n, "reason": "disabled"})
        elif not is_approved(p, n):
            skipped.append({"hook": n, "required": s.get("required", True),
                            "reason": "not approved (a human must run `research hook approve`)"})
        else:
            r = {**_exec(p, n, s, env), "task": task, "experiment": experiment}
            os.makedirs(os.path.dirname(_results_path(p)), exist_ok=True)
            append_jsonl(_results_path(p), r)
            p.event("project", None, "hook_run", f"hook {n} ({stage}): " + ("passed" if r["ok"] else "FAILED" + (" (timeout)" if r["timed_out"] else "")))
            ran.append(r)
    failed = [r for r in ran if not r["ok"]]
    blocked = [r for r in skipped if r.get("required")]
    return {"stage": stage, "ran": ran, "skipped": skipped, "failed": failed, "blocked": blocked,
            "ok": not blocked and not any(r["required"] for r in failed)}


def format_failures(res: Dict[str, Any]) -> List[str]:
    return [f"hook {r['hook']} {'timed out' if r['timed_out'] else 'exited ' + str(r['exit_code'])}:\n{r['output'].strip()}" for r in res["failed"]]


def gate(p, stage: str, **kw: Any) -> Dict[str, Any]:
    """Run a stage and raise PolicyBlocked (exit 3) when a required hook fails."""
    res = run(p, stage, **kw)
    if not res["ok"]:
        names = [r["hook"] for r in res["failed"] if r["required"]] + [r["hook"] for r in res["blocked"]]
        raise PolicyBlocked(f"{stage} hook blocked: " + "; ".join(names),
                            hint="\n".join(format_failures(res) + [r["reason"] for r in res["blocked"]])[:1500])
    return res
