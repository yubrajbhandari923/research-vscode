"""Who may create experiments and tasks. Research OS decides; agents propose.

Policy (project `governance:` block, optionally stricter per experiment under `governance.experiment_overrides`):
  experiments.agent_creation   human_only | propose_only (default)
  tasks.agent_creation         human_only | propose_only | bounded_auto (default: operational types, per-experiment cap)
Precedence with the older supervisor settings: governance is the ceiling. `supervisor.auto_apply_operational` and `max_auto_tasks`
can only tighten it (auto-apply needs both to allow it; the smaller cap wins). Enforced in services.create_experiment and
plans.create_task, which the CLI, MCP, RPC, plan YAML and supervisor all call, so there is no path around it.
"""
from __future__ import annotations

import contextlib
from typing import Any, Dict, Iterator, Optional

from .util import PolicyBlocked, ResearchError, deep_merge

DEFAULTS: Dict[str, Any] = {
    "experiments": {"agent_creation": "propose_only"},
    "tasks": {"agent_creation": "bounded_auto", "allowed_auto_types": ["bugfix", "diagnostic", "verification"], "max_auto_per_experiment": 3},
    "scientific_changes": {"require_human_approval": True},
    "approval": {"require_pin": True, "require_rationale": True, "record_rejections": True, "confirm_human_experiment_creation": False},
    "experiment_overrides": {},
}
_STRICT = {"bounded_auto": 0, "propose_only": 1, "human_only": 2}
_GRANTS: list = []   # in-process: set only while a human-approved proposal is being applied


def cfg(p) -> Dict[str, Any]:
    c = deep_merge(DEFAULTS, p.config.get("governance") or {})
    for sect, vals in (("experiments", ("human_only", "propose_only")), ("tasks", tuple(_STRICT))):
        v = c[sect]["agent_creation"]
        if v not in vals:
            raise ResearchError(f"governance.{sect}.agent_creation must be one of {', '.join(vals)}, not {v!r}")
    return c


@contextlib.contextmanager
def approved(reason: str) -> Iterator[None]:
    _GRANTS.append(reason)
    try:
        yield
    finally:
        _GRANTS.pop()


def is_agent(p) -> bool:
    from .approval import agent_environment
    return (p.author or {}).get("author_type") == "agent" or agent_environment()


def task_policy(p, exp_id: Optional[str] = None) -> str:
    """Strictest of the project policy and the experiment's override (overrides can only tighten)."""
    c = cfg(p)
    pol = c["tasks"]["agent_creation"]
    ov = (c["experiment_overrides"].get(exp_id or "") or {}).get("task_creation")
    return ov if ov in _STRICT and _STRICT[ov] > _STRICT[pol] else pol


def check_experiment_create(p) -> None:
    if _GRANTS or not is_agent(p):
        return
    pol = cfg(p)["experiments"]["agent_creation"]
    raise PolicyBlocked(f"Agents may not create experiments (policy: {pol})",
                        hint="Submit an experiment proposal: `research proposal experiment --title … --hypothesis … --rationale … --method … --outputs … --related …`.")


def check_task_create(p, task_type: Optional[str], exp_id: Optional[str]) -> None:
    if _GRANTS or not is_agent(p):
        return
    pol = task_policy(p, exp_id)
    c = cfg(p)["tasks"]
    hint = "Propose it instead: `research plan propose` (a human approves)."
    if pol != "bounded_auto":
        raise PolicyBlocked(f"Agents may not create tasks (policy: {pol})", hint=hint)
    if task_type not in c["allowed_auto_types"]:
        raise PolicyBlocked(f"Agents may auto-create only {', '.join(c['allowed_auto_types'])} tasks, not {task_type or 'untyped'}", hint=hint)
    if not exp_id:
        raise PolicyBlocked("An agent-created task must belong to its assigned experiment (--experiment)", hint=hint)
    import os
    mine = os.environ.get("RESEARCH_EXPERIMENT")
    if mine and mine != exp_id:
        raise PolicyBlocked(f"Agents may create tasks only in their assigned experiment ({mine})", hint=hint)
    n = p.q1("SELECT COUNT(*) AS n FROM tasks WHERE related_experiment_id=? AND author_type='agent'", (exp_id,))["n"]
    if n >= int(c["max_auto_per_experiment"]):
        raise PolicyBlocked(f"{exp_id} already has {n} agent-created tasks (limit {c['max_auto_per_experiment']})", hint=hint)


def auto_apply_allowed(p) -> bool:
    """May a proposal that only adds tasks be applied without a human? Governance first, then the legacy supervisor switch."""
    return cfg(p)["tasks"]["agent_creation"] == "bounded_auto"


def confirm_human_experiment(p, pin: Optional[str] = None) -> None:
    """Optional extra care (off by default): a human creating an experiment interactively needs no PIN unless this is switched on."""
    if is_agent(p) or not cfg(p)["approval"]["confirm_human_experiment_creation"]:
        return
    from . import approval
    approval.confirm(p, "Create an experiment", None, pin, rationale_required=False)
