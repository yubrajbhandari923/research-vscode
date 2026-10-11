"""Cost-aware Opus → Sonnet → Haiku orchestration, layered on existing tasks, notes, checks and dispatch.

Routing by difficulty and verifiability (not task label). State is a small per-task ledger in
.research/orchestration/<T-id>.json: frozen acceptance-test hashes, attempts, escalations, parent/children.
Tuning splits live in tuning-<EXP>.json. Nothing here launches a model; `dispatch_task` only prepares a session.

config.yaml → orchestration: {enabled, tiers: {opus|sonnet|haiku: {model, fallback}}, available, limits}
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional

from . import plans
from .schema import normalize_id
from .store import Project, _atomic_write
from .util import NotFound, PolicyBlocked, ResearchError, as_list, now_iso

TIERS = ["haiku", "sonnet", "opus"]
CATEGORIES = ["incorrect_implementation", "ambiguous_specification", "insufficient_test_oracle",
              "environment", "scientific_assumption", "resource_limit"]
_SCIENTIFIC_TYPES = {"synthesis", "research"}
_ROLE_FOR_TIER = {"haiku": "implementer", "sonnet": "implementer", "opus": "analyst"}


def cfg(p: Project) -> Dict[str, Any]:
    return p.config.get("orchestration") or {}


def limits(p: Project) -> Dict[str, Any]:
    return cfg(p).get("limits") or {}


# ----------------------------------------------------------------------------- routing
def resolve(p: Project, tier: str, available: Any = None) -> Dict[str, Any]:
    """Pick a concrete model for a tier. If its models are unavailable, fall back to the next tier up (never silently
    down for opus work); `degraded` is set whenever the tier changed or no model is available."""
    avail = set(as_list(available) if available is not None else as_list(cfg(p).get("available")))
    order = TIERS[TIERS.index(tier):] if tier != "opus" else ["opus"]
    for t in order:
        spec = (cfg(p).get("tiers") or {}).get(t) or {}
        for m in [spec.get("model"), *as_list(spec.get("fallback"))]:
            if m and (not avail or m in avail):
                return {"tier": t, "model": m, "degraded": t != tier,
                        "note": f"{tier} unavailable; using {t}" if t != tier else None}
    return {"tier": tier, "model": None, "degraded": True, "note": f"no available model for {tier}"}


def route(p: Project, task: Dict[str, Any], tier: Optional[str] = None, available: Any = None) -> Dict[str, Any]:
    if tier:
        if tier not in TIERS:
            raise ResearchError(f"tier must be one of {', '.join(TIERS)}")
        why = "explicit"
    else:
        typ, role = task.get("task_type") or "implementation", task.get("assigned_role")
        verifiable = bool(task.get("checks")) and bool(task.get("acceptance_criteria"))
        if typ in _SCIENTIFIC_TYPES or role in ("planner", "analyst"):
            tier, why = "opus", "scientific interpretation / planning"
        elif typ == "verification" or role == "verifier":
            tier, why = "sonnet", "independent verification / integration review"
        elif verifiable:
            tier, why = "haiku", "bounded and checkable (has checks + acceptance criteria)"
        else:
            tier, why = "sonnet", "not fully checkable yet: needs a contract and tests first"
    return {"task": task["id"], "why": why, "role": _ROLE_FOR_TIER[tier], **resolve(p, tier, available)}


# ----------------------------------------------------------------------------- ledger
def _lpath(p: Project, tid: str) -> str:
    return p.path("orchestration", f"{tid}.json")


def ledger(p: Project, tid: str) -> Dict[str, Any]:
    try:
        with open(_lpath(p, tid), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"frozen": {}, "attempts": [], "escalations": 0, "corrections": [], "children": [], "parent": None}


def _save(p: Project, tid: str, led: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(_lpath(p, tid)), exist_ok=True)
    _atomic_write(_lpath(p, tid), json.dumps(led, indent=1, sort_keys=True) + "\n")


def _hash(p: Project, path: str) -> str:
    try:
        with open(os.path.join(p.root, path), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        raise NotFound(f"Cannot freeze {path}: file not found")


def _tid(p: Project, tid: str) -> str:
    tid = normalize_id(tid, "T")
    p.get("task", tid)
    return tid


def freeze(p: Project, tid: str, paths: Any) -> Dict[str, Any]:
    """Freeze the acceptance tests (the oracle). Done by the test author (Sonnet) before Haiku implements."""
    tid = _tid(p, tid)
    led = ledger(p, tid)
    for path in as_list(paths):
        rel = p.rel(os.path.join(p.root, path))
        led["frozen"][rel] = _hash(p, rel)
    led["frozen_at"] = now_iso()
    _save(p, tid, led)
    plans.add_task_note(p, tid, f"acceptance tests frozen: {', '.join(led['frozen'])}")
    return {"task": tid, "frozen": led["frozen"]}


def tampered(p: Project, tid: str) -> List[str]:
    out = []
    for path, h in ledger(p, tid)["frozen"].items():
        try:
            if _hash(p, path) != h:
                out.append(path)
        except NotFound:
            out.append(path)
    return out


def assert_frozen_intact(p: Project, tid: str) -> None:
    """Called from complete_task: a task cannot be completed while its frozen tests differ from what was approved."""
    bad = tampered(p, tid)
    if bad:
        raise PolicyBlocked(f"{tid}: frozen acceptance tests were modified: {', '.join(bad)}",
                            hint="Restore them, or have a Sonnet-level agent run `research orchestrate correct-test` with a reason.")


def correct_test(p: Project, tid: str, path: str, reason: str, tier: str) -> Dict[str, Any]:
    if tier == "haiku":
        raise PolicyBlocked("Haiku may not change frozen tests", hint="Escalate: `research orchestrate attempt … --category insufficient_test_oracle`.")
    if not reason or len(reason.strip()) < 10:
        raise ResearchError("A test correction needs a real reason (what was wrong and why it was necessary)")
    tid = _tid(p, tid)
    led = ledger(p, tid)
    led["corrections"].append({"path": path, "reason": reason, "tier": tier, "ts": now_iso(), "old_hash": led["frozen"].get(path)})
    led["frozen"][p.rel(os.path.join(p.root, path))] = _hash(p, path)
    _save(p, tid, led)
    plans.add_task_note(p, tid, f"frozen test {path} corrected by {tier}: {reason}")
    return {"task": tid, "corrections": len(led["corrections"])}


def record_attempt(p: Project, tid: str, tier: str, outcome: str, category: Optional[str] = None,
                   note: Optional[str] = None) -> Dict[str, Any]:
    """Log an implementation attempt and return the bounded next step: review, retry, escalate, or block."""
    tid = _tid(p, tid)
    if outcome not in ("pass", "fail"):
        raise ResearchError("outcome must be pass or fail")
    if outcome == "fail" and category not in CATEGORIES:
        raise ResearchError(f"a failure needs --category, one of: {', '.join(CATEGORIES)}")
    lim, led = limits(p), ledger(p, tid)
    led["attempts"].append({"tier": tier, "outcome": outcome, "category": category, "note": note, "ts": now_iso()})
    bad = tampered(p, tid)
    nxt: Dict[str, Any]
    if bad:
        nxt = {"action": "restore_tests", "tier": "sonnet", "why": f"frozen tests modified: {', '.join(bad)}"}
    elif outcome == "pass":
        nxt = {"action": "integration_review", "tier": "sonnet"} if tier == "haiku" else {"action": "complete", "tier": tier}
    else:
        fails = sum(1 for a in led["attempts"] if a["tier"] == tier and a["outcome"] == "fail")
        if category in ("scientific_assumption",):
            nxt = {"action": "escalate", "tier": "opus", "why": "scientific assumption problem"}
        elif category in ("environment", "resource_limit"):
            nxt = {"action": "block", "why": f"{category}: needs a human"}
        elif category in ("ambiguous_specification", "insufficient_test_oracle") and tier == "haiku":
            nxt = {"action": "escalate", "tier": "sonnet", "why": category}
        elif category in ("ambiguous_specification", "insufficient_test_oracle"):
            nxt = {"action": "escalate", "tier": "opus", "why": f"{category} not resolvable at sonnet level"}
        elif tier == "haiku" and fails < int(lim.get("haiku_attempts", 3)):
            nxt = {"action": "retry", "tier": "haiku", "remaining": int(lim.get("haiku_attempts", 3)) - fails}
        elif tier == "haiku":
            nxt = {"action": "escalate", "tier": "sonnet", "why": "haiku attempt budget exhausted"}
        elif tier == "sonnet" and fails >= int(lim.get("sonnet_attempts", 2)):
            nxt = {"action": "escalate", "tier": "opus", "why": "sonnet attempt budget exhausted"}
        else:
            nxt = {"action": "retry", "tier": tier}
    if nxt["action"] == "escalate":
        led["escalations"] += 1
        if led["escalations"] > int(lim.get("escalations", 2)):
            nxt = {"action": "block", "why": "escalation budget exhausted"}
    _save(p, tid, led)
    plans.add_task_note(p, tid, f"{tier} attempt {outcome}" + (f" [{category}]" if category else "")
                        + (f": {note}" if note else "") + f" → {nxt['action']}" + (f" {nxt['tier']}" if nxt.get("tier") else ""))
    if nxt["action"] == "block":
        plans.block_task(p, tid, nxt["why"])
    return {"task": tid, **nxt}


def delegate(p: Project, parent: str, title: str, tier: str, goal: Optional[str] = None,
             acceptance: Optional[str] = None, checks: Any = None, **kw: Any) -> Dict[str, Any]:
    """Create a child task under `parent` (same plan). The parent can't finish before the child (depends_on)."""
    parent = _tid(p, parent)
    par = p.get("task", parent)
    child = plans.create_task(p, par["plan_id"], title, goal=goal, task_type="implementation",
                              assigned_role=_ROLE_FOR_TIER[tier], acceptance_criteria=acceptance, checks=checks,
                              inputs=f"Delegated from {parent} ({par['title']}) to a {tier}-tier agent.",
                              related_experiment_id=par.get("related_experiment_id"), skills=par.get("skills"), **kw)
    plans.update_task(p, parent, depends_on=as_list(par.get("depends_on")) + [child["id"]])
    plans.add_task_note(p, parent, f"delegated {child['id']} to {tier}: {title}")
    for tid, field, val in ((child["id"], "parent", parent),):
        led = ledger(p, tid)
        led[field] = val
        _save(p, tid, led)
    pl = ledger(p, parent)
    pl["children"].append(child["id"])
    _save(p, parent, pl)
    return child


# ----------------------------------------------------------------------------- dispatch
def _ponytail(p: Project, name: str) -> Optional[str]:
    from .skills import list_skills
    return name if any(s["name"] == name for s in list_skills(p)) else None


_PROTOCOL = {
    "haiku": ("You are the HAIKU-tier implementer. Implement ONLY what the contract and frozen tests specify. Never edit, "
              "delete or weaken the frozen acceptance tests. Run them, fix, repeat; after each attempt log it: "
              "`research orchestrate attempt {id} --tier haiku --outcome pass|fail [--category …]` and follow the returned "
              "next step. At most {haiku_attempts} attempts. If the spec or test looks wrong, report it, do not 'fix' it. "
              "Tune parameters only on design/validation data."),
    "sonnet": ("You are the SONNET-tier architect/integrator. Write the function contract (inputs, outputs, units, "
               "invariants, edge cases, errors) and an independent test suite BEFORE any implementation; run it to see it "
               "fail; then `research orchestrate freeze {id} <test files>`. Decide whether Haiku can implement it "
               "(`research orchestrate delegate`/`dispatch`) or do it yourself. Review integration; escalate scientific "
               "ambiguity to Opus. Follow the `engineering-loop` skill. Never rewrite the Scientist Guide."),
    "opus": ("You are the OPUS-tier scientist. Own the scientific design, ambiguities, delegation, interpretation and "
             "synthesis. Rarely write ordinary code. After each synthesis, update the Scientist Guide "
             "(`scientist-guide` skill) and run `research guide done`. Propose next questions; do not launch them."),
}


def dispatch_task(p: Project, target: str, tier: Optional[str] = None, available: Any = None,
                  allow_unfrozen: Optional[str] = None) -> Dict[str, Any]:
    """Prepare (not launch) a session for the routed tier. A Haiku implementation requires frozen tests first."""
    from . import agents
    tid = _tid(p, target)
    task = p.get("task", tid)
    r = route(p, task, tier, available)
    if not r["model"]:
        raise ResearchError(r["note"], hint="Set orchestration.tiers / orchestration.available in .research/config.yaml.")
    if r["tier"] == "haiku" and task.get("task_type") in (None, "implementation", "debug") and not ledger(p, tid)["frozen"]:
        if not allow_unfrozen:
            raise PolicyBlocked(f"{tid}: no frozen acceptance tests; Sonnet must write and freeze them before Haiku implements",
                                hint=f"`research orchestrate freeze {tid} <tests>`, or --allow-unfrozen \"<reason>\" (logged).")
        plans.add_task_note(p, tid, f"haiku dispatched WITHOUT frozen tests: {allow_unfrozen}")
    lim = {"haiku_attempts": 3, **limits(p)}
    ins = [_PROTOCOL[r["tier"]].replace("{id}", tid).replace("{haiku_attempts}", str(lim["haiku_attempts"]))]
    if r["tier"] in ("haiku", "sonnet"):
        for nm in ("ponytail", "ponytail-review") if r["tier"] == "sonnet" else ("ponytail",):
            ins.append(f"Before editing code run `research skill show {nm}` and follow it."
                       if _ponytail(p, nm) else f"Optional skill {nm} is not installed; use the built-in engineering "
                       "standards (smallest complete change, tests, no weakened checks) and report it missing.")
        ins.append("Minimal-code preferences never override scientific correctness, tests, provenance or numerical safeguards.")
    ins.append(f"Identify yourself with `--agent claude-{r['tier']} --model {r['model']}` on research commands. Routing: {r['why']}"
               + (f" ({r['note']})" if r.get("note") else ""))
    d = agents.dispatch(p, r["role"], tid, None, None, r["model"], "\n".join(ins))
    return {**d, "tier": r["tier"], "degraded": r["degraded"], "route": r}


def status(p: Project, tid: str) -> Dict[str, Any]:
    tid = _tid(p, tid)
    led = ledger(p, tid)
    return {"task": tid, **led, "tampered": tampered(p, tid), "usage": "unavailable (not recorded by Research OS)"}


# ----------------------------------------------------------------------------- tuning
def _tpath(p: Project, exp: str) -> str:
    return p.path("orchestration", f"tuning-{exp.replace('/', '-')}.json")


def _tload(p: Project, exp: str) -> Dict[str, Any]:
    try:
        with open(_tpath(p, exp), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        raise NotFound(f"No tuning plan for {exp}", hint="`research orchestrate tuning declare` first.")


def _tsave(p: Project, exp: str, t: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(_tpath(p, exp)), exist_ok=True)
    _atomic_write(_tpath(p, exp), json.dumps(t, indent=1, sort_keys=True) + "\n")


def tuning_declare(p: Project, exp: str, objective: str, ranges: Dict[str, Any], strategy: str, selection: str,
                   seed: int = 0, budget: Optional[int] = None) -> Dict[str, Any]:
    exp = normalize_id(exp, "EXP")
    p.get("experiment", exp)
    if not (objective and ranges and strategy and selection):
        raise ResearchError("declare needs objective, ranges, strategy and a selection criterion")
    t = {"experiment": exp, "objective": objective, "ranges": ranges, "strategy": strategy, "selection": selection,
         "seed": seed, "budget": int(budget or limits(p).get("tuning_evaluations", 50)), "trials": [], "selected": None,
         "test": None, "declared_at": now_iso()}
    try:
        _tload(p, exp)
        raise ResearchError(f"A tuning plan for {exp} already exists (frozen); create a new experiment to change it")
    except NotFound:
        pass
    _tsave(p, exp, t)
    p.event("experiment", exp, "tuning_declared", f"tuning declared: {strategy}, budget {t['budget']}, selection: {selection}")
    return t


def tuning_trial(p: Project, exp: str, config: Dict[str, Any], split: str, value: float, run: Optional[str] = None) -> Dict[str, Any]:
    exp = normalize_id(exp, "EXP")
    t = _tload(p, exp)
    if split not in ("design", "validation"):
        raise PolicyBlocked("Trials may use only design or validation data; held-out test is evaluated once, after selection",
                            hint="`research orchestrate tuning test`.")
    if t["selected"]:
        raise PolicyBlocked("Selection is already frozen; no further trials")
    if len(t["trials"]) >= t["budget"]:
        raise PolicyBlocked(f"Tuning evaluation budget ({t['budget']}) reached", hint="Select from the recorded trials.")
    t["trials"].append({"config": config, "split": split, "value": value, "run": run, "ts": now_iso()})
    _tsave(p, exp, t)
    return {"trials": len(t["trials"]), "remaining": t["budget"] - len(t["trials"])}


def tuning_select(p: Project, exp: str, config: Dict[str, Any], rationale: str) -> Dict[str, Any]:
    exp = normalize_id(exp, "EXP")
    t = _tload(p, exp)
    if t["selected"]:
        raise PolicyBlocked("Selection already frozen")
    if not any(x["split"] == "validation" for x in t["trials"]):
        raise ResearchError("Select only after validation trials exist")
    t["selected"] = {"config": config, "rationale": rationale, "ts": now_iso(), "n_trials": len(t["trials"])}
    _tsave(p, exp, t)
    p.event("experiment", exp, "tuning_selected", f"configuration frozen after {len(t['trials'])} trials; test not yet evaluated")
    return t["selected"]


def tuning_test(p: Project, exp: str, value: float, run: Optional[str] = None) -> Dict[str, Any]:
    """Record the single held-out evaluation of the frozen selection. One-shot: cannot be repeated to tune."""
    exp = normalize_id(exp, "EXP")
    t = _tload(p, exp)
    if not t["selected"]:
        raise PolicyBlocked("Held-out test is locked until a configuration is selected")
    if t["test"]:
        raise PolicyBlocked("Held-out test already evaluated; further evaluation would be tuning on the test set")
    t["test"] = {"value": value, "run": run, "ts": now_iso()}
    _tsave(p, exp, t)
    return t["test"]
