"""Orchestration layered on tasks, notes, checks and dispatch: routing, frozen tests, bounded attempts, delegation, contracts, verification, pins.

With an active allocation strategy (strategy.py) every decision comes from its single resolver and levels are strategy-defined; with none, the
legacy Opus → Sonnet → Haiku tier logic below applies unchanged.

Legacy routing is by difficulty and verifiability (not task label). State is a small per-task ledger in
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
from . import strategy as S
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


def route(p: Project, task: Dict[str, Any], tier: Optional[str] = None, available: Any = None,
          strategy: Optional[str] = None, profile: Optional[str] = None, model: Optional[str] = None) -> Dict[str, Any]:
    if S.active_name(p, strategy):  # `tier` is a strategy level here; `available` is superseded by orchestration.available
        r = S.route_task(p, task, ledger(p, task["id"]), level=tier, strategy=strategy, profile=profile, model=model)
        return {**r, "tier": r["level"], "degraded": False, "note": None}
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


def record_dispatch(p: Project, tid: str, snap: Dict[str, Any]) -> None:
    """Append the resolved-allocation snapshot to the task's ledger. History never re-reads the active config."""
    led = ledger(p, tid)
    keep = ("ts", "role", "strategy", "fingerprint", "level", "profile", "provider", "model", "why", "overrides",
            "independent", "revision", "status")
    led.setdefault("dispatches", []).append({**{k: snap[k] for k in keep if k in snap},
                                             "harness": (snap.get("harness") or {}).get("fingerprint")})
    _save(p, tid, led)


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


def _fast_level(p: Project) -> Optional[str]:
    try:
        return S.definition(p, S.active_name(p))["route"]["checked"]
    except (ResearchError, KeyError):
        return None


def correct_test(p: Project, tid: str, path: str, reason: str, tier: str) -> Dict[str, Any]:
    implementers = {d["level"] for d in ledger(p, normalize_id(tid, "T")).get("dispatches", []) if d.get("role") == "implementer"}
    if tier == "haiku" or (S.active_name(p) and tier in implementers | {_fast_level(p)}):  # the implementer never edits its oracle
        raise PolicyBlocked(f"{tier} may not change frozen tests (it implements, or can implement, this task)",
                            hint="Escalate: `research orchestrate attempt … --category insufficient_test_oracle`.")
    if not reason or len(reason.strip()) < 10:
        raise ResearchError("A test correction needs a real reason (what was wrong and why it was necessary)")
    tid = _tid(p, tid)
    led = ledger(p, tid)
    led["corrections"].append({"path": path, "reason": reason, "tier": tier, "ts": now_iso(), "old_hash": led["frozen"].get(path)})
    led["frozen"][p.rel(os.path.join(p.root, path))] = _hash(p, path)
    _save(p, tid, led)
    plans.add_task_note(p, tid, f"frozen test {path} corrected by {tier}: {reason}")
    return {"task": tid, "corrections": len(led["corrections"])}


def _model_of(p: Project, strat: Optional[str], level: str) -> Optional[str]:
    try:
        return S.profile_for_level(p, S.definition(p, strat), level)["model"] if strat else None
    except ResearchError:
        return None


def record_attempt(p: Project, tid: str, tier: str, outcome: str, category: Optional[str] = None,
                   note: Optional[str] = None, usage: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Log an implementation attempt and return the bounded next step: review, retry, escalate, or block."""
    tid = _tid(p, tid)
    if outcome not in ("pass", "fail"):
        raise ResearchError("outcome must be pass or fail")
    if outcome == "fail" and category not in CATEGORIES:
        raise ResearchError(f"a failure needs --category, one of: {', '.join(CATEGORIES)}")
    lim, led = limits(p), ledger(p, tid)
    strat = S.active_name(p)
    if strat and tier not in S.definition(p, strat)["levels"]:
        raise ResearchError(f"--tier {tier!r} is not a level of strategy {strat} ({', '.join(S.definition(p, strat)['levels'])})")
    att = {"tier": tier, "outcome": outcome, "category": category, "note": note, "ts": now_iso()}
    if strat:
        att["strategy"] = {"name": strat, "fingerprint": S.fingerprint(p, strat)}
    if usage:  # only what the caller actually has; absent stays absent (never 0)
        att["usage"] = usage
        from . import usage as U
        U.record(p, "manual", usage, task=tid, level=tier, strategy=strat, model=_model_of(p, strat, tier))
    led["attempts"].append(att)
    bad = tampered(p, tid)
    nxt: Dict[str, Any]
    if bad:
        nxt = {"action": "restore_tests", "tier": S.definition(p, strat)["coordinator"] if strat else "sonnet",
               "why": f"frozen tests modified: {', '.join(bad)}"}
    elif strat:
        fails = sum(1 for a in led["attempts"] if a["tier"] == tier and a["outcome"] == "fail")
        nxt = S.next_step(p, strat, tier, outcome, category, fails)
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
        cap = S.definition(p, strat).get("max_escalations", 2) if strat else lim.get("escalations", 2)
        if led["escalations"] > int(cap):
            nxt = {"action": "block", "why": "escalation budget exhausted"}
    _save(p, tid, led)
    plans.add_task_note(p, tid, f"{tier} attempt {outcome}" + (f" [{category}]" if category else "")
                        + (f": {note}" if note else "") + f" → {nxt['action']}" + (f" {nxt['tier']}" if nxt.get("tier") else ""))
    if nxt["action"] == "block":
        plans.block_task(p, tid, nxt["why"])
    return {"task": tid, **nxt}


def delegate(p: Project, parent: str, title: str, tier: Optional[str] = None, goal: Optional[str] = None,
             acceptance: Optional[str] = None, checks: Any = None, **kw: Any) -> Dict[str, Any]:
    """Create a child task under `parent` (same plan). The parent can't finish before the child (depends_on).
    Under an active strategy, `tier` (a level) defaults to the strategy's routing of the child as specified."""
    parent = _tid(p, parent)
    par = p.get("task", parent)
    if S.active_name(p):
        r = S.route_task(p, {"id": parent, "task_type": "implementation", "checks": as_list(checks), "acceptance_criteria": acceptance},
                         level=tier)
        tier, role = r["level"], r["role"]
    else:
        tier = tier or "haiku"
        role = _ROLE_FOR_TIER[tier]
    child = plans.create_task(p, par["plan_id"], title, goal=goal, task_type="implementation",
                              assigned_role=role, acceptance_criteria=acceptance, checks=checks,
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


# ----------------------------------------------------------------------------- contracts, overrides, verification
CONTRACT_FIELDS = ["objective", "context", "expected_outputs", "allowed_files", "constraints", "verification"]
_LISTS = {"expected_outputs", "allowed_files", "constraints", "verification"}


def set_contract(p: Project, tid: str, **fields: Any) -> Dict[str, Any]:
    """The coordinator's delegation contract, kept in the task's ledger (no new task columns). Merges field by field;
    pass an empty value to clear one. Acceptance criteria and checks stay on the task itself."""
    tid = _tid(p, tid)
    led = ledger(p, tid)
    c = dict(led.get("contract") or {})
    for k, v in fields.items():
        if k not in CONTRACT_FIELDS:
            raise ResearchError(f"unknown contract field {k!r} (one of {', '.join(CONTRACT_FIELDS)})")
        if v in (None, "", []):
            c.pop(k, None)
        else:
            c[k] = as_list(v) if k in _LISTS else str(v)
    led["contract"] = c
    _save(p, tid, led)
    plans.add_task_note(p, tid, "delegation contract updated: " + ", ".join(sorted(fields)))
    return {"task": tid, "contract": c}


def contract_md(p: Project, tid: str) -> str:
    task, c = p.get("task", tid), ledger(p, tid).get("contract") or {}
    L = ["## Contract (set by the coordinator; satisfy it, do not renegotiate it)", ""]
    for k, label in (("objective", "Objective"), ("context", "Context")):
        L.append(f"- **{label}:** {c[k]}" if c.get(k) else (f"- **{label}:** {task.get('goal')}" if k == "objective" and task.get("goal") else ""))
    L += [f"- **Expected outputs:** {', '.join(c['expected_outputs'])}" if c.get("expected_outputs") else "",
          f"- **You may modify only:** {', '.join(c['allowed_files'])} (anything else is reported as a violation)" if c.get("allowed_files") else "",
          f"- **Acceptance criteria:** {task['acceptance_criteria']}" if task.get("acceptance_criteria") else ""]
    L += [f"- **Constraint:** {x}" for x in c.get("constraints", [])] + [f"- **Required verification:** {x}" for x in c.get("verification", [])]
    L.append(f"- **Check your work:** `research orchestrate verify {tid} --tier <your level> --record` runs the frozen tests, task checks, "
             "approved project hooks and the file boundary, and returns the diagnostics. Fix and repeat within the attempt limit.")
    return "\n".join(x for x in L if x)


def changed_files(p: Project) -> List[str]:
    from . import gitinfo
    out = gitinfo._git(p.root, "status", "--porcelain", "-uall", "--", ".", ":(exclude).research") or ""
    return sorted({ln[3:].split(" -> ")[-1].strip().strip('"') for ln in out.splitlines() if len(ln) > 3})


def set_override(p: Project, tid: str, level: Optional[str] = None, profile: Optional[str] = None,
                 model: Optional[str] = None, clear: bool = False) -> Dict[str, Any]:
    """Pin the model for one task's implementer dispatches; beats the strategy, loses to per-dispatch flags."""
    tid = _tid(p, tid)
    led = ledger(p, tid)
    if clear:
        led.pop("override", None)
        msg = "model override cleared"
    else:
        name = S.active_name(p)
        if not name:
            raise ResearchError("Per-task overrides need an active strategy", hint="`research strategy use NAME`")
        sd = S.definition(p, name)
        if not (level or profile or model):
            raise ResearchError("give --level, --profile and/or --agent-model (or --clear)")
        if level and level not in sd["levels"]:
            raise ResearchError(f"{level!r} is not a level of {name} ({', '.join(sd['levels'])})")
        if profile and profile not in ((p.config.get("agents") or {}).get("profiles") or {}):
            raise ResearchError(f"Unknown agent profile {profile!r}")
        led["override"] = {k: v for k, v in (("level", level), ("profile", profile), ("model", model)) if v}
        msg = f"model override set: {led['override']}"
    _save(p, tid, led)
    plans.add_task_note(p, tid, msg)
    p.event("task", tid, "model_override", msg)
    return {"task": tid, "override": led.get("override")}


def verify_work(p: Project, tid: str, tier: Optional[str] = None, record: bool = False) -> Dict[str, Any]:
    """Deterministic verification of a worker's change, returning diagnostics the worker can act on.

    Runs: frozen-test integrity, the contract's file boundary, approved post_implementation hooks, the task's checks.
    Passing needs at least one deterministic verifier to have actually run: no verifier is never success.
    With record=True the result is logged as an attempt and the bounded next step (retry/escalate/block) is returned."""
    from . import hooks, verification
    tid = _tid(p, tid)
    task, led = p.get("task", tid), ledger(p, tid)
    problems: List[str] = []
    ran = 0
    bad = tampered(p, tid)
    if bad:
        problems.append("frozen acceptance tests were modified (restore them): " + ", ".join(bad))
    allowed = (led.get("contract") or {}).get("allowed_files") or []
    if allowed:
        import fnmatch
        extra = [f for f in changed_files(p) if f not in set(led.get("baseline") or []) and not any(fnmatch.fnmatch(f, a) or f.startswith(a.rstrip("/") + "/") for a in allowed)]
        if extra:
            problems.append("files changed outside the contract's allowed_files: " + ", ".join(extra))
    hr = hooks.run(p, "post_implementation", task=tid)
    ran += len(hr["ran"])
    problems += hooks.format_failures(hr)
    checks = None
    if task.get("checks"):
        checks = verification.verify_task(p, tid, transition=False)
        ran += len(checks["results"])
        problems += [f"check {r['check']} failed ({r['detail']})" + (f":\n{r['output'].strip()}" if r.get("output") else "") for r in checks["results"] if not r["ok"]]
    if not ran and not problems:
        problems.append("no deterministic verifier ran (no task checks and no approved post_implementation hooks): this is not a pass")
    passed = not problems
    out: Dict[str, Any] = {"task": tid, "passed": passed, "diagnostics": problems, "hooks_skipped": hr["skipped"], "verifiers_run": ran}
    if record:
        if not tier:
            raise ResearchError("--record needs --tier (the level that made this attempt)")
        out["next"] = record_attempt(p, tid, tier, "pass" if passed else "fail", None if passed else "incorrect_implementation",
                                     note=None if passed else problems[0][:200])
    return out


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


_STRAT_PROTOCOL = {
    "worker": ("You are a bounded IMPLEMENTER ({level}). Implement ONLY what the contract and frozen tests specify. Never edit, "
               "delete or weaken the frozen acceptance tests. Run them, fix, repeat; after each attempt log it: "
               "`research orchestrate attempt {id} --tier {level} --outcome pass|fail [--category …]` and follow the returned "
               "next step. At most {attempts} attempts. If the spec or test looks wrong, report it, do not 'fix' it. "
               "Tune parameters only on design/validation data."),
    "coordinator": ("You are the COORDINATOR/integrator ({level}). Write the function contract (inputs, outputs, units, "
                    "invariants, edge cases, errors) and an independent test suite BEFORE any implementation; run it to see it "
                    "fail; then `research orchestrate freeze {id} <test files>`. Delegate bounded work with `research "
                    "orchestrate delegate` (the active strategy picks the worker) or do it yourself. Review integration. Follow "
                    "the `engineering-loop` skill. Never rewrite the Scientist Guide."),
    "scientist": ("You are the SCIENTIST ({level}). Own the scientific design, ambiguities, interpretation and synthesis. "
                  "Rarely write ordinary code. After each synthesis, update the Scientist Guide (`scientist-guide` skill) "
                  "and run `research guide done`. Propose next questions; do not launch them."),
    "verifier": ("You are an independent VERIFIER ({level}). Judge the work against the task's acceptance criteria and its "
                 "frozen tests; do not edit what you review."),
}


def _dispatch_strategy(p: Project, tid: str, task: Dict[str, Any], level, strategy, profile, model, allow_unfrozen, headless=False) -> Dict[str, Any]:
    from . import agents
    from . import hooks
    pre = hooks.gate(p, "preflight", task=tid)   # approved project preflight; a required failure blocks the dispatch
    led = ledger(p, tid)
    r = S.route_task(p, task, led, level=level, strategy=strategy, profile=profile, model=model)
    if S.requires_frozen(r, task) and not led["frozen"]:
        if not allow_unfrozen:
            raise PolicyBlocked(f"{tid}: no frozen acceptance tests; the coordinator ({r['coordinator']}) must write and freeze "
                                f"them before {r['level']} implements",
                                hint=f"`research orchestrate freeze {tid} <tests>`, or --allow-unfrozen \"<reason>\" (logged).")
        plans.add_task_note(p, tid, f"{r['level']} dispatched WITHOUT frozen tests: {allow_unfrozen}")
    sd = S.definition(p, r["strategy"])
    kind = ("verifier" if r["role"] == "verifier" else "scientist" if r["role"] in ("analyst", "planner")
            else "coordinator" if r["level"] == sd["coordinator"] else "worker")
    ins = [_STRAT_PROTOCOL[kind].replace("{id}", tid).replace("{level}", r["level"])
           .replace("{attempts}", str((sd.get("attempts") or {}).get(r["level"], 2)))]
    if kind in ("worker", "coordinator"):
        for nm in ("ponytail", "ponytail-review") if kind == "coordinator" else ("ponytail",):
            ins.append(f"Before editing code run `research skill show {nm}` and follow it."
                       if _ponytail(p, nm) else f"Optional skill {nm} is not installed; use the built-in engineering "
                       "standards (smallest complete change, tests, no weakened checks) and report it missing.")
        ins.append("Minimal-code preferences never override scientific correctness, tests, provenance or numerical safeguards.")
    if r["independent"] is False:
        ins.append("WARNING: " + r["warning"] + ". State this in your review; a human must confirm it.")
    ins.append(f"Identify yourself with `--agent {r['profile']} --model {r['model']}` on research commands. "
               f"Strategy {r['strategy']}@{r['fingerprint']}, routing: {r['why']}")
    if r["role"] == "implementer":
        led = ledger(p, tid)
        led["baseline"] = changed_files(p)   # so the file boundary only judges what this attempt changed
        _save(p, tid, led)
        ins.append(contract_md(p, tid))
    d = agents.dispatch(p, r["role"], tid, None, None, None, "\n".join(ins), policy=r, headless=headless)
    return {**d, "tier": r["level"], "degraded": False, "route": r, "preflight": {"ran": [x["hook"] for x in pre["ran"]], "skipped": pre["skipped"]}}


def dispatch_task(p: Project, target: str, tier: Optional[str] = None, available: Any = None,
                  allow_unfrozen: Optional[str] = None, strategy: Optional[str] = None, profile: Optional[str] = None,
                  model: Optional[str] = None, headless: bool = False) -> Dict[str, Any]:
    """Prepare (not launch) a session for the routed tier. A Haiku implementation requires frozen tests first."""
    from . import agents
    tid = _tid(p, target)
    task = p.get("task", tid)
    if S.active_name(p, strategy):
        return _dispatch_strategy(p, tid, task, tier, strategy, profile, model, allow_unfrozen, headless)
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
    u = [a["usage"] for a in led["attempts"] if a.get("usage")]
    usage: Any = "unavailable (not recorded by Research OS)"
    if u:  # sum only fields that were actually reported; counts say how many attempts reported
        keys = {k for x in u for k in x if isinstance(x[k], (int, float))}
        usage = {"attempts_reporting": len(u), "attempts": len(led["attempts"]), **{k: sum(x[k] for x in u if k in x) for k in keys}}
    return {"task": tid, **led, "tampered": tampered(p, tid), "usage": usage}


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
