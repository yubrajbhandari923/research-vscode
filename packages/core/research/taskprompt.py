"""Selective, size-limited context for a supervised agent attempt.

Sections (each tagged with where it came from, each capped): project context files, plan and experiment objectives, the
task prompt and obligations, results of finished dependencies, prior attempts and their diagnostics, workspace, allowed files,
HPC resources and how to report. The whole ledger is never included. The result is persisted per attempt with its hash.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .store import Project

_SECRET = re.compile(r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)[A-Z0-9_]*)\s*[:=]\s*(\S+)")
_KEYS = re.compile(r"\b(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,})")


def redact(text: Optional[str]) -> Optional[str]:
    """Remove credential-looking values from anything the supervisor stores or logs."""
    if not text:
        return text
    return _KEYS.sub("[REDACTED]", _SECRET.sub(lambda m: f"{m.group(1)}=[REDACTED]", text))


def _cap(text: str, n: int, label: str) -> str:
    text = text.strip()
    return text if len(text) <= n else text[:n].rstrip() + f"\n… [{label}: truncated at {n} characters; read the file for the rest]"


def _read(path: str, limit: int) -> Optional[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit * 2)
    except OSError:
        return None


def assemble(p: Project, tid: str, plan_state: Dict[str, Any], entry: Dict[str, Any], kind: str, diagnostics: Optional[List[str]] = None,
             workspace: Optional[Dict[str, Any]] = None, cfg: Optional[Dict[str, Any]] = None) -> Tuple[str, List[Dict[str, str]]]:
    """Return (instructions text, sources). `kind` is initial | repair | handoff | resume."""
    from . import planspec as PS
    cfg = cfg or {}
    budget = int(cfg.get("context_max_chars") or 24000)
    per = max(1500, budget // 8)
    task = p.get("task", tid)
    spec = plan_state.get("spec") or {}
    tspec = (plan_state.get("spec_tasks") or {}).get(next((k for k, v in plan_state["tasks"].items() if v == tid), ""), {})
    sources: List[Dict[str, str]] = []
    parts: List[str] = []

    def add(title: str, body: Optional[str], src: str, cap: Optional[int] = None) -> None:
        if body and body.strip():
            parts.append(f"## {title}  <!-- source: {src} -->\n\n{_cap(redact(body) or '', cap or per, title)}")
            sources.append({"section": title, "source": src})

    parts.append(f"# Supervised task {tid}: {task['title']}\n\nAttempt kind: **{kind}**. Research OS (not you) decides what runs next and "
                 "whether this task counts as done; you cannot mark it verified.")
    # 1. project context
    for rel in plan_state.get("project_context") or []:
        for base in (p.root, os.path.dirname(p.root)):
            text = _read(os.path.join(base, rel), per)
            if text is not None:
                add(f"Project context: {rel}", text, rel)
                break
    # 2. objectives
    add("Plan objective", "\n".join(x for x in (spec.get("objective"), spec.get("success_criteria") and "Success: " + spec["success_criteria"]) if x),
        f"plan {plan_state['plan_key']}")
    eid = task.get("related_experiment_id")
    if eid and p.exists("experiment", eid):
        e = p.get("experiment", eid)
        add(f"Experiment {eid}: {e['title']}", "\n".join(f"{k}: {e[k]}" for k in ("hypothesis", "method", "success_criteria", "parameters") if e.get(k)),
            f"experiment {eid}")
    # 3. the task
    add("Your task", tspec.get("prompt") or task.get("goal"), f"task {tid}", cap=per * 2)
    ob = []
    if task.get("acceptance_criteria"):
        ob.append("Acceptance: " + task["acceptance_criteria"])
    if task.get("inputs"):
        ob.append("Inputs: " + str(task["inputs"]))
    if tspec.get("expected_artifacts"):
        ob.append("Expected artifacts (relative to your workspace): " + ", ".join(tspec["expected_artifacts"]))
    if task.get("checks"):
        from .verification import describe_check, parse_check
        ob.append("Checks Research OS will run to verify you:\n" + "\n".join("  - " + describe_check(parse_check(c)) for c in task["checks"]))
    if tspec.get("allowed_files"):
        ob.append("You may modify only: " + ", ".join(tspec["allowed_files"]) + " (advisory unless a sandbox profile enforces it)")
    add("Acceptance, inputs and checks", "\n".join(ob), f"task {tid}")
    # 4. dependency results
    deps = []
    for d in task.get("depends_on") or []:
        dt = p.get("task", d)
        deps.append(f"- {d} {dt['title']} [{dt['status']}]: {(dt.get('result') or '(no result recorded)')[:600]}")
    add("Finished prerequisite tasks", "\n".join(deps), "task results")
    # 5. history
    if entry.get("attempts"):
        hist = []
        for a in entry["attempts"][-4:]:
            v = a.get("verification") or {}
            hist.append(f"- attempt {a['n']} ({a['kind']}): {a.get('outcome') or 'running'}"
                        + (f"; verification {'passed' if v.get('passed') else 'FAILED'}" if v else "")
                        + (f"; problem category: {a['category']}" if a.get("category") else ""))
        add("Previous attempts on this task", "\n".join(hist), "supervisor attempt ledger")
    if diagnostics:
        add("What failed verification (fix exactly this)", "\n".join("- " + x for x in diagnostics), "verification diagnostics", cap=per * 2)
    notes = [n["text"] for n in __import__("research.plans", fromlist=["x"]).progress_notes(p, tid, 5)]
    add("Progress notes left by earlier attempts", "\n".join("- " + n for n in notes), "task notes")
    # 6. workspace, HPC, reporting
    if workspace:
        add("Workspace", f"Work only in `{workspace.get('path')}` (branch `{workspace.get('branch')}`). Do not switch branches, merge, or push.", "workspace")
    rs = tspec.get("resources") or {}
    if rs:
        hpc = ["Resources requested for computation: " + ", ".join(f"{k}={v}" for k, v in rs.items())]
        if rs.get("backend") == "slurm":
            hpc.append("Submit with `research hpc submit <EXP> --yes -- <command>` (or `research run submit`); never exceed these resources, never "
                       "resubmit a job whose status merely looks unavailable. Outputs only come home if registered; see the HPC contract in your brief.")
        add("Computation", "\n".join(hpc), "task resources")
    add("Reporting (required)", "\n".join([
        "Report through Research OS, one command per call (it is on your PATH):",
        f"  research agent progress {tid} --message \"...\"",
        f"  research agent artifact {tid} --path <relative path>",
        f"  research agent report {tid} --summary \"what you did and found\"",
        f"  research agent problem {tid} --category <incorrect_implementation|ambiguous_specification|insufficient_test_oracle|environment|"
        f"scientific_assumption|resource_limit> --message \"...\"",
        f"  research agent propose {tid} --file patch.yaml --rationale \"...\"   # plan changes need Research OS (and often human) approval",
        f"  research agent finish {tid} --request-verification   # when you believe you are done",
        "You cannot mark the task done or verified; the supervisor verifies after you exit. Do not edit or weaken checks or frozen tests."]), "supervisor")
    text = "\n\n".join(parts)
    if len(text) > budget:
        text = text[:budget].rstrip() + "\n\n… [context truncated to supervisor.context_max_chars]"
    return text, sources


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
