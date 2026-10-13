"""Named model-allocation strategies: which model does which part of the work, and when work escalates.

A *profile* (agents.profiles) is an executable + model. A *strategy* (orchestration.strategies.<name>) refers to
profiles only through logical levels (fast / balanced / deep — names are the strategy's own), so a future model
replaces a profile, not the routing. A *harness* (see harness.py) is how a project prepares and verifies work; it is
independent of the strategy. Nothing here launches a model, classifies with an LLM, or knows a vendor name.

config.yaml:
    orchestration:
      active_strategy: sonnet_first        # null/absent → legacy tier routing, byte-for-byte as before
      strategies:
        mine: {extends: sonnet_first, attempts: {fast: 2}}     # dicts merge key by key; lists/scalars replace

Layers (existing config machinery): built-in default < machine file < project file; a name defined in several layers
is merged, the highest layer winning per key. A one-dispatch override (`--strategy`, `--level`, `--profile`,
`--model`) beats all of them. Every dispatch freezes a snapshot of what was resolved; history never re-reads config.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from typing import Any, Dict, List, Optional

from .util import ResearchError, as_list, deep_merge as _merge

CLASSES = ["planning", "scientific", "verification", "checked", "unchecked", "mechanical"]
_ROLE_FOR_CLASS = {"planning": "planner", "scientific": "analyst", "verification": "verifier"}
_SCIENTIFIC_TYPES = {"synthesis", "research"}
_MECHANICAL_TYPES = {"mechanical", "chore"}
HUMAN = "human"
_LEVELS = {"fast": "claude-fast", "balanced": "claude-balanced", "deep": "claude-deep"}


def _s(desc: str, route: Dict[str, str], attempts: Dict[str, int], escalate: Dict[str, str], sci: str) -> Dict[str, Any]:
    return {"description": desc, "extends": None, "levels": dict(_LEVELS), "coordinator": "balanced", "guide": "deep",
            "route": {"planning": "balanced", "scientific": "deep", "verification": "balanced", "checked": "fast",
                      "unchecked": "balanced", "mechanical": "fast", **route},
            "attempts": attempts, "escalate": escalate, "on_scientific_assumption": sci, "max_escalations": 2}


# Built-ins are plain config defaults (merged into DEFAULT_CONFIG), so every one can be overridden per key.
BUILTIN: Dict[str, Dict[str, Any]] = {
    "sonnet_first": _s("Balanced model coordinates; fast model implements checkable work; deep model only for scientific "
                       "synthesis. Preferred for new projects.", {}, {"fast": 3, "balanced": 2},
                       {"fast": "balanced", "balanced": HUMAN, "deep": HUMAN}, "deep"),
    "opus_first": _s("Deep model plans and interprets; balanced translates to contracts and tests; fast implements.",
                     {"planning": "deep"}, {"fast": 3, "balanced": 2},
                     {"fast": "balanced", "balanced": "deep", "deep": HUMAN}, "deep"),
    "haiku_max": _s("Maximise verified fast-model execution; balanced model is orchestrator and escalation target; deep "
                    "model only on explicit request.", {"scientific": "balanced"}, {"fast": 4, "balanced": 2},
                    {"fast": "balanced", "balanced": HUMAN, "deep": HUMAN}, HUMAN),
    "sonnet_heavy": _s("Balanced model coordinates and implements; fast model only for mechanical work; deep model for "
                       "milestone synthesis. Fallback when tasks lack mature contracts or verifiers.",
                       {"checked": "balanced"}, {"fast": 2, "balanced": 2},
                       {"fast": "balanced", "balanced": HUMAN, "deep": HUMAN}, "deep"),
}


# ----------------------------------------------------------------------------- resolution
def _orch(p) -> Dict[str, Any]:
    return p.config.get("orchestration") or {}


def active_name(p, override: Optional[str] = None) -> Optional[str]:
    return override or _orch(p).get("active_strategy") or None


def _chain(defs: Dict[str, Any], name: Any) -> List[str]:
    seen: List[str] = []
    while name:
        if name in seen:
            raise ResearchError(f"strategy inheritance cycle: {' → '.join(seen + [name])}")
        if name not in defs:
            raise ResearchError(f"Unknown strategy {name!r}", hint=f"Known: {', '.join(sorted(defs)) or 'none'}. "
                                "`research strategy list` shows them; define one under orchestration.strategies.")
        seen.append(name)
        name = (defs[name] or {}).get("extends")
    return seen


def origin(p, name: str) -> str:
    """Where the highest layer defining `name` lives: project / global (machine file) / built-in."""
    from . import configfile as CF
    if name in ((p.raw_config.get("orchestration") or {}).get("strategies") or {}):
        return "project"
    machine, _ = CF.machine_config()
    return "global" if name in ((machine.get("orchestration") or {}).get("strategies") or {}) else "built-in"


def definition(p, name: str) -> Dict[str, Any]:
    """The effective strategy: its `extends` chain merged parent → child."""
    defs = _orch(p).get("strategies") or {}
    merged: Dict[str, Any] = {}
    for n in reversed(_chain(defs, name)):
        merged = _merge(merged, defs[n] or {})
    merged.pop("extends", None)
    return merged


def fingerprint(p, name: str) -> str:
    """Stable id of the resolved policy *and* the profiles it points at (model swaps change it)."""
    sd = definition(p, name)
    profs = {pn: (p.config.get("agents", {}).get("profiles") or {}).get(pn) for pn in sd.get("levels", {}).values()}
    return hashlib.sha256(json.dumps([sd, profs], sort_keys=True, default=str).encode()).hexdigest()[:12]


def validate(p, name: str) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    try:
        sd = definition(p, name)
    except ResearchError as e:
        return {"strategy": name, "ok": False, "errors": [str(e)], "warnings": []}
    levels = sd.get("levels") or {}
    profiles = (p.config.get("agents") or {}).get("profiles") or {}
    if not levels:
        errors.append("no levels defined")
    for lv, pn in levels.items():
        prof = profiles.get(pn)
        if not prof:
            errors.append(f"level {lv}: profile {pn!r} is not defined (agents.profiles)")
        elif not shutil.which(prof.get("command") or pn):
            warnings.append(f"level {lv}: command {prof.get('command') or pn!r} is not on PATH here")
        elif not prof.get("model"):
            warnings.append(f"level {lv}: profile {pn!r} sets no model; the CLI default will be used")

    def need(where: str, lv: Any, extra: tuple = ()) -> None:
        if lv not in levels and lv not in extra:
            errors.append(f"{where}: {lv!r} is not a level ({', '.join(levels)})")
    for c, lv in (sd.get("route") or {}).items():
        if c not in CLASSES:
            warnings.append(f"route.{c}: unknown task class (known: {', '.join(CLASSES)})")
        need(f"route.{c}", lv)
    for c in CLASSES:
        if c not in (sd.get("route") or {}):
            errors.append(f"route.{c}: missing")
    need("coordinator", sd.get("coordinator"))
    need("guide", sd.get("guide"))
    need("on_scientific_assumption", sd.get("on_scientific_assumption"), (HUMAN,))
    for lv in sd.get("attempts") or {}:
        need(f"attempts.{lv}", lv)
    esc = sd.get("escalate") or {}
    for lv, to in esc.items():
        need(f"escalate.{lv} (from)", lv)
        need(f"escalate.{lv}", to, (HUMAN,))
    for lv in levels:  # the chain must end at a human, never loop
        seen, cur = [], lv
        while cur in esc and esc[cur] != HUMAN:
            if cur in seen:
                errors.append(f"escalation cycle: {' → '.join(seen + [cur])}")
                break
            seen.append(cur)
            cur = esc[cur]
    return {"strategy": name, "ok": not errors, "errors": errors, "warnings": warnings}


def listing(p) -> List[Dict[str, Any]]:
    act = active_name(p)
    return [{"name": n, "origin": origin(p, n), "active": n == act,
             "description": ((_orch(p).get("strategies") or {}).get(n) or {}).get("description")}
            for n in sorted(_orch(p).get("strategies") or {})]


def profile_for_level(p, sd: Dict[str, Any], level: str) -> Dict[str, Any]:
    pn = (sd.get("levels") or {}).get(level)
    if not pn:
        raise ResearchError(f"Unknown level {level!r}", hint=f"Levels: {', '.join(sd.get('levels') or {})}.")
    prof = ((p.config.get("agents") or {}).get("profiles") or {}).get(pn)
    if not prof:
        raise ResearchError(f"Level {level} uses profile {pn!r}, which is not defined",
                            hint="Define it under agents.profiles in .research/config.yaml.")
    avail = as_list(_orch(p).get("available"))
    if avail and prof.get("model") and prof["model"] not in avail:
        raise ResearchError(f"Level {level}: model {prof['model']} (profile {pn}) is not in orchestration.available",
                            hint="Fix the profile's model or orchestration.available; no other model is substituted.")
    return {"profile": pn, "command": prof.get("command") or pn, "model": prof.get("model")}


# ----------------------------------------------------------------------------- routing
def task_class(task: Dict[str, Any]) -> tuple:
    typ, role = task.get("task_type") or "implementation", task.get("assigned_role")
    if role == "planner":
        return "planning", "planner role"
    if typ in _SCIENTIFIC_TYPES or role == "analyst":
        return "scientific", "scientific interpretation"
    if typ == "verification" or role == "verifier":
        return "verification", "independent verification / integration review"
    if typ in _MECHANICAL_TYPES:
        return "mechanical", "mechanical work"
    if task.get("checks") and task.get("acceptance_criteria"):
        return "checked", "bounded and checkable (checks + acceptance criteria)"
    return "unchecked", "not fully checkable yet: needs a contract and tests first"


def _implementers(led: Dict[str, Any]) -> set:
    return {(d["profile"], d.get("model")) for d in led.get("dispatches", []) if d.get("role") == "implementer"}


def select(p, role: Optional[str] = None, cls: Optional[str] = None, why: Optional[str] = None, level: Optional[str] = None,
           strategy: Optional[str] = None, profile: Optional[str] = None, model: Optional[str] = None,
           led: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The single routing decision every dispatch path uses. Precedence: explicit profile/model > explicit level >
    strategy routing of the class (strategy itself: per-dispatch name > project > global > built-in)."""
    name = active_name(p, strategy)
    if not name:
        raise ResearchError("No active strategy", hint="`research strategy use NAME` (see `research strategy list`).")
    sd = definition(p, name)
    bad = validate(p, name)["errors"]
    if bad:
        raise ResearchError(f"Strategy {name} is invalid: {bad[0]}" + (f" (+{len(bad) - 1} more)" if len(bad) > 1 else ""),
                            hint="`research strategy validate` lists every problem.")
    cls = cls or {"planner": "planning", "analyst": "scientific", "verifier": "verification"}.get(role or "", "unchecked")
    role = role or _ROLE_FOR_CLASS.get(cls, "implementer")
    tov = (led or {}).get("override") if role == "implementer" and not (level or profile or model) else None
    if tov:   # per-task pin: below per-dispatch flags, above the strategy's routing
        level, profile, model = tov.get("level"), tov.get("profile"), tov.get("model")
    lv, reason, overrides = (level, "task override" if tov else "explicit level", {"level": level}) if level else (sd["route"][cls], why or cls, {})
    if tov:
        overrides["task_override"] = tov
    independent: Optional[bool] = None
    if role == "verifier" and led is not None:
        used = _implementers(led)
        order = [lv] if level else [lv, *[x for x in sd["levels"] if x != lv]]
        for cand in order:  # a reviewer that is the implementer's own profile+model is not independent
            pr = profile_for_level(p, sd, cand)
            if (pr["profile"], pr["model"]) not in used:
                if cand != lv:
                    reason += f"; {lv} implemented this, so verification moved to {cand}"
                lv, independent = cand, True
                break
        else:
            independent = False if used else None
    out = {"strategy": name, "origin": origin(p, name), "fingerprint": fingerprint(p, name), "class": cls, "level": lv,
           "role": role, "why": reason, "overrides": overrides, "independent": independent, "coordinator": sd["coordinator"],
           **profile_for_level(p, sd, lv)}
    if profile:
        prof = ((p.config.get("agents") or {}).get("profiles") or {}).get(profile)
        if not prof:
            raise ResearchError(f"Unknown agent profile {profile!r}", hint="Profiles are defined under agents.profiles.")
        out.update(profile=profile, command=prof.get("command") or profile, model=prof.get("model"))
        out["overrides"]["profile"] = profile
    if model:
        out["model"] = model
        out["overrides"]["model"] = model
    if strategy:
        out["overrides"]["strategy"] = strategy
    if independent is False:
        out["warning"] = "no level differs from the implementer's profile+model: this review is NOT independent"
    return out


def route_task(p, task: Dict[str, Any], led: Optional[Dict[str, Any]] = None, **kw: Any) -> Dict[str, Any]:
    cls, why = task_class(task)
    return {"task": task["id"], **select(p, None, cls, why, led=led, **kw)}


def requires_frozen(r: Dict[str, Any], task: Dict[str, Any]) -> bool:
    """Frozen tests are required whenever the implementer is not the level that authors the tests (the coordinator)."""
    return (r["role"] == "implementer" and (task.get("task_type") in (None, "implementation", "debug"))
            and r["level"] != r["coordinator"])


def next_step(p, name: str, level: str, outcome: str, category: Optional[str], fails: int) -> Dict[str, Any]:
    """Bounded next action after an attempt. 'escalate' to HUMAN becomes 'block'; never silently picks another model."""
    sd = definition(p, name)
    esc = sd.get("escalate") or {}

    def up(why: str) -> Dict[str, Any]:
        to = esc.get(level, HUMAN)
        return {"action": "block", "why": f"{why}: needs a human"} if to == HUMAN else {"action": "escalate", "tier": to, "why": why}
    if outcome == "pass":
        co = sd["coordinator"]
        return {"action": "integration_review", "tier": co} if level == sd["route"]["checked"] and level != co else {"action": "complete", "tier": level}
    if category == "scientific_assumption":
        to = sd.get("on_scientific_assumption", HUMAN)
        return {"action": "block", "why": "scientific assumption problem: needs a human"} if to == HUMAN \
            else {"action": "escalate", "tier": to, "why": "scientific assumption problem"}
    if category in ("environment", "resource_limit"):
        return {"action": "block", "why": f"{category}: needs a human"}
    if category in ("ambiguous_specification", "insufficient_test_oracle"):
        return up(category)
    cap = int((sd.get("attempts") or {}).get(level, 2))
    return {"action": "retry", "tier": level, "remaining": cap - fails} if fails < cap else up(f"{level} attempt budget exhausted")


def show(p, name: Optional[str] = None) -> Dict[str, Any]:
    name = active_name(p, name)
    if not name:
        return {"active": None, "legacy": "no active strategy: tier routing (orchestration.tiers) applies unchanged"}
    sd = definition(p, name)
    res = {lv: profile_for_level(p, sd, lv) if validate(p, name)["ok"] else {"profile": pn} for lv, pn in sd["levels"].items()}
    return {"name": name, "origin": origin(p, name), "active": name == active_name(p), "fingerprint": fingerprint(p, name),
            "extends": ((_orch(p).get("strategies") or {}).get(name) or {}).get("extends"), "definition": sd,
            "resolved_levels": res, "validation": validate(p, name)}


# ----------------------------------------------------------------------------- generated agent files
def agent_role(sd: Dict[str, Any], level: str) -> str:
    """worker / coordinator / scientist for a level, derived from the strategy rather than from a model name."""
    rt = sd["route"]
    if level == sd["coordinator"]:
        return "coordinator"
    if level in (rt["scientific"], rt["planning"], sd["guide"]) and level != rt["checked"]:
        return "scientist"
    return "worker"


def agent_doc(p, name: str, level: str, marker: str) -> str:
    """Claude Code sub-agent file for one level of the active strategy. References commands for live project data
    (hooks, contracts) instead of copying it, so it only changes when the strategy does."""
    sd = definition(p, name)
    prof = profile_for_level(p, sd, level)
    role = agent_role(sd, level)
    lv = sd["levels"]
    table = "\n".join(f"- **{k}**: {profile_for_level(p, sd, k)['model'] or 'CLI default'}" + ("  ← you" if k == level else "") for k in lv)
    esc = sd.get("escalate") or {}
    to = esc.get(level, HUMAN)
    me = {
        "coordinator": ("You are the COORDINATOR. Turn approved objectives into tasks. For each delegated task write the contract "
                        "(`research orchestrate contract T-… --objective … --allowed-file … --constraint …`) and independent tests, run them to see "
                        "them fail, then `research orchestrate freeze T-… <tests>`. Delegate with `research orchestrate delegate` or "
                        "`dispatch`; the active strategy picks the worker, so never repeat a model id. Review integration yourself."),
        "worker": ("You are a bounded WORKER. Satisfy the task contract and frozen tests, touching only the files it allows. After each "
                   "attempt run `research orchestrate verify T-… --tier " + level + " --record`; it returns the deterministic diagnostics and "
                   "the bounded next step. Never edit, delete or weaken frozen tests. If the spec or a test looks wrong, report it."),
        "scientist": ("You are the SCIENTIST. Own scientific design, ambiguity, interpretation and synthesis; rarely write routine code. "
                      "Deterministic verification is done by programs (`research experiment check`), not by you. After a synthesis update the "
                      "Scientist Guide and run `research guide done`."),
    }[role]
    return f"""---
name: research-{level}
description: Research OS {level}-level agent ({role}) under the {name} strategy. {sd.get('description') or ''}
model: {prof['model'] or 'inherit'}
tools: Read, Grep, Glob, Edit, Write, Bash
---
<!-- {marker} — written by `research setup agents` for strategy {name}@{fingerprint(p, name)}; delete this line to take ownership -->
{me}

## Strategy {name}
Levels in this project:
{table}

- Attempts before escalation: {(sd.get('attempts') or {}).get(level, 'n/a')}; next after that: {'a human (block the task and explain)' if to == HUMAN else to}. At most {sd.get('max_escalations', 2)} escalations per task.
- Scientific-assumption problems go to: {'a human' if sd.get('on_scientific_assumption') == HUMAN else sd.get('on_scientific_assumption')}.
- Delegation uses `research orchestrate delegate` / `dispatch` (add `--tier LEVEL` only to override); a task may be pinned to a model with `research orchestrate pin`.
- Verifiers must differ from the implementer's profile and model; a review that cannot is marked not independent.

## Project guidance (read live, not copied here)
- `research task context T-…` — the task brief and its contract; `research strategy show` — the effective policy.
- `research hook list` — project hooks (preflight, post-implementation, experiment validation). Hooks run only after a human approves them; you may propose one with `research hook add`.
- Identify yourself with `--agent {prof['profile']} --model {prof['model']}` on research commands.
"""
