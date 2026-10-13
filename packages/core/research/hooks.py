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
    result = shell_check(spec["command"], cwd=p.root, timeout=timeout_for(p, spec),
                         env={**os.environ, "RESEARCH_ROOT": p.root, "RESEARCH_HOOK": name,
                              "RESEARCH_HOOK_STAGE": spec["stage"], **env_extra})
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
