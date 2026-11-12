"""Typed configuration registry: what each setting is, how to validate it, how to explain it, and how to change it.

The YAML layers stay canonical (defaults < machine file < project file; see configfile.py). This module adds the
metadata the editor and the CLI need, so validation, help text and approval rules live in ONE place:

    describe(p)        every option with its effective value, default, source layer and help
    preview(p, ...)    validate a change and show the exact YAML diff, without writing
    apply(p, ...)      validate, enforce approval rules, write surgically, audit, regenerate agent context
    reset(p, ...)      remove an override so the inherited value applies again

This module OWNS every default: `defaults()` builds the nested dict (`store.DEFAULT_CONFIG` is that dict), `section(p, name)` is the
merged view modules read, and `default(key)` one value. No other module keeps a default of its own.
Anything the registry does not describe keeps working and stays editable in YAML; it is simply not offered as a form.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import copy

from . import configfile, providers as _providers, strategy as _strategy, yamlio
from .util import ResearchError, deep_merge, now_iso

# ------------------------------------------------------------------------------------------------ defaults (the only place)
SUPERVISOR: Dict[str, Any] = {
    "poll_seconds": 5, "max_parallel_agents": 1, "max_attempts_per_task": 3, "max_total_attempts": 20, "max_wall_minutes": 480,
    "attempt_timeout_minutes": 90, "on_failure": "diagnose_and_repair", "enforce_completion": True, "auto_apply_operational": True,
    "max_auto_tasks": 5, "require_approval_for_scientific_changes": True, "session_resume": "auto", "context_max_chars": 24000,
    "hpc_poll_seconds": 30, "max_hpc_resubmissions": 1, "launch_grace_seconds": 120, "auto_restart": True,
    "recovery": {"enabled": True, "collection_wait_minutes": 120, "max_reverifications": 5, "max_syncs": 100},   # late-evidence recovery (recovery.py); never relaxes a check or submits a job
    "investigator": {"auto": False, "max_per_plan": 3, "budget_usd": 2.0},   # on-demand investigator agent (investigator.py); auto = also on unclassified failures
    "usage_limit": {"on_limit": "halt", "wait_buffer_seconds": 120, "max_wait_hours": 8,      # H.10: what a provider usage/rate limit does (supervisor.py)
                    "unknown_reset_backoff_minutes": [15, 30, 60], "short_backoff_minutes": [1, 2, 5]},
    "escalation": {},     # {on: [incomplete_deliverable], model, effort, profile, max_escalated_attempts: 1}; empty = never escalate
    "commit_verified_work": True, "commit_exclude": ["outputs/", "results/", "patch*.yaml", "*.pyc", "__pycache__/", ".venv/"],
    "commit_keep": ["*.png", "*.svg", "*.pdf", "*.md", "*.qmd", "*.csv"], "commit_max_file_mb": 5,      # small figures/reports inside the excluded folders still go on the branch
}
GOVERNANCE: Dict[str, Any] = {
    "experiments": {"agent_creation": "propose_only"},
    "tasks": {"agent_creation": "bounded_auto", "allowed_auto_types": ["bugfix", "diagnostic", "verification"], "max_auto_per_experiment": 3},
    "scientific_changes": {"require_human_approval": True},
    "approval": {"require_pin": True, "require_rationale": True, "record_rejections": True, "confirm_human_experiment_creation": False,
                 "chat": {"enabled": False}, "chat_allow": []},
    "experiment_overrides": {},
}
AUTO_PROMOTION: Dict[str, Any] = {
    "test_command": None, "numerical_command": None, "reviewer_profile": None, "require_distinct_reviewer": True,
    "review_timeout_minutes": 120, "max_file_mb": 5,
    "auto_approve_verified_code": False,              # standing authorization: see autopromote.py (never bypasses tests, independent review, provenance or any other gate)
    "auto_retry_attempts": 3, "auto_retry_minutes": [1, 5, 15],
    "protected_test_globs": ["tests/*", "*/tests/*", "test_*.py", "*/test_*.py", "*_test.py", "conftest.py", "*/conftest.py"],
}
NOTIFICATIONS: Dict[str, Any] = {
    "halt_grace_seconds": 120, "auto_debug": False, "level": "medium", "summary": "facts", "telegram": {"enabled": False},
    "discord": {"enabled": False, "thread_per_task": True, "mention": True},
    "alerts": {"on": ["halt", "task_failed", "approval_needed", "promotion_ready", "plan_finished", "run_failed", "budget", "supervisor_down", "decision", "provider_limit",
                      "plan_started", "experiment_started", "experiment_finished", "run_finished", "task_started", "task_verified", "recovery", "promotion_done", "job"],
               "cooldown_minutes": 30, "escalate_after": 3, "approval_expiry_hours": 24, "max_per_hour": 30},
    "digest": {"at": None, "silent_if_unchanged": True},
}

_CONFIG: Dict[str, Any] = {
    "project": {
        "name": "", "description": "", "goal": "", "status": "active",
        "created_at": None, "git_repo": None, "default_backend": "local",
    },
    # Where things live for projects made by `Initialize Project`; relative paths are under the container folder (the parent of
    # the main checkout). null = legacy placement (<name>-ledger / <name>-experiments beside the checkout).
    "layout": {"main_dir": None, "ledger_dir": None, "experiments_dir": None, "workspace_file": None},
    "agent_policy": {
        "max_runs_without_synthesis": 5,
        "max_failed_runs_without_review": 3,
        "require_experiment_registration": True,
        "require_synthesis_before_new_experiment": True,
        "enforce_for_humans": False,
    },
    "backends": {
        "slurm": {
            "partition": None, "account": None, "time": "01:00:00", "cpus": None, "mem": None,
            "gpus": None, "extra_args": [],
            "setup": [],
        },
    },
    "artifacts": {"hash_max_bytes": 64 * 1024 * 1024},
    # Remote HPC execution. Opt-in: with no clusters configured, every `research hpc` command says so and
    # nothing else changes. Never put passwords, keys or tokens here — OpenSSH (~/.ssh/config) owns auth.
    "hpc": {
        "default_cluster": None,
        "clusters": {},                      # name -> see hpc.CLUSTER_DEFAULTS
        "bundle": {
            # Dirty/untracked sources are packaged (a commit alone is not a snapshot), minus these.
            "exclude": [".git", ".research", "__pycache__", "*.pyc", ".venv", "venv", "node_modules",
                        ".mypy_cache", ".pytest_cache", ".DS_Store", "*.egg-info", ".ipynb_checkpoints"],
            "max_file_bytes": 8 * 1024 * 1024,     # a bigger source file must be named in `include` explicitly
            "max_total_bytes": 256 * 1024 * 1024,
            "include": [],                          # extra paths to package (datasets stay on the cluster)
        },
        "collect": {
            "max_artifact_bytes": 32 * 1024 * 1024,  # bigger outputs stay remote and are referenced
        },
    },
    "resume": {"stale_checkpoint_days": 14},
    # Who does what. A profile is an agent CLI; roles map to profiles (null → default_profile).
    "agents": {
        "default_profile": "claude",
        # Cross-provider continuation (supervisor.continue_with / automatic fallback). Fallback is opt-in and bounded by the supervisor's attempt limits.
        "handoff": {"enabled": False, "fallback_order": list(_providers.KNOWN), "on_provider_unavailable": "pause", "on_unknown_execution_state": "pause"},
        "roles": {"planner": None, "implementer": None, "verifier": None, "analyst": None},
        # Generated from the provider registry: one plain profile per provider, `<provider>-fast|balanced|deep` model profiles
        # (the strategies' levels point at these; swap a model here, not in routing) and `gemini`. A new provider needs no edit here.
        "profiles": _providers.default_profiles(),
    },
    "verification": {
        "require_review_for_supported": True,   # agents can't mark findings supported; a review must
        "independent_reviewer": True,           # the reviewer must not be the finding's author
        "require_evidence_for_supported": True,
        "checks_gate_task_completion": True,    # `task done` runs the task's checks; failures block agents
        "check_timeout_seconds": 600,
    },
    "coordination": {"stale_task_hours": 4},
    "context": {"max_chars": 12000},             # budget for context/current.md (~3k tokens)
    "skills": {"link": ["claude", "agents"], "packs": ["core-research"], "disabled": []},    # expose .research/skills to .claude/skills and .agents/skills
    # The Scientist Guide is a Quarto book on the main branch (docs/scientist-guide). `highlights` decides which findings count as
    # important results; `draft.*` bounds what an agent may write into it (see guide.py).
    "guide": {"enabled": True, "dir": "docs/scientist-guide", "profile": None, "model": "claude-opus-5-5",
              "auto_log": True, "highlights": "supported",
              "draft": {"trigger": "result", "max_words": 150, "max_sections": 3, "max_figures": 1},
              "publish": {"workflow": True}, "site": {"auto_include": False}},
    # Opt-in Opus → Sonnet → Haiku routing (`research setup agents`). `available`: [] = assume all configured models exist.
    "ask": {"profile": None, "model": None, "timeout_seconds": 300, "allow_unenforced": False},
    "orchestration": {
        "enabled": False,
        "tiers": {
            "opus": {"model": "claude-opus-5-5", "fallback": ["opus"]},
            "sonnet": {"model": "claude-sonnet-5-5", "fallback": ["sonnet"]},
            "haiku": {"model": "claude-haiku-5-5", "fallback": ["haiku"]},
        },
        "available": [],
        "active_strategy": None,    # null → legacy tier routing above; else a name from `strategies` (research strategy use)
        "strategies": _strategy.BUILTIN,
        "limits": {"haiku_attempts": 3, "sonnet_attempts": 2, "escalations": 2, "max_turns": None,
                   "tuning_evaluations": 50},
    },
    # Project-owned references carried into agent briefs (see harness.py); independent of the strategy.
    "harness": {"name": None, "skills": [], "context": [], "instructions": None, "checks": [],
                "hooks": {}, "hooks_approved": {}, "hooks_timeout_seconds": 300, "require_seed": False},   # hooks: see hooks.py (human approval required)
    "git": {"commit_checkpoints": False,         # true → every checkpoint commits .research/ (never pushes)
            "preflight": "warn"},                # githygiene.py: warn (report what is uncommitted and why it matters) | block | off
    # Optional job isolation (see sandbox.py / isolation.py). Native execution stays the default; nothing is restricted unless a
    # profile other than `native` applies, and a restrictive profile never silently falls back to native.
    # Selective promotion of experiment code into main (promotion.py). Nothing merges without a human.
    "environment": {"python": None, "packages": []},
    "supervisor": SUPERVISOR,
    "governance": GOVERNANCE,   # who may create experiments/tasks; PIN approvals (governance.py)
    "promotion": {"main_branch": "main", "test_command": None, "test_timeout_seconds": 1800, "require_supported_finding": True},
    # Opt-in unattended promotion (autopromote.py), supervisor interventions (intervene.py) and halt notifications (notify.py). Secrets (Telegram) are NOT here.
    "auto_promotion": AUTO_PROMOTION,
    "supervisor_assistant": {"profile": None, "model": None, "timeout_seconds": 900, "self_heal": False, "max_repairs": 2, "max_repairs_per_plan": 6, "repair_instructions": None},
    "intervention": {"require_approval": False},
    "notifications": NOTIFICATIONS,
    "sandbox": {
        "enabled": False,                         # true → `default_profile` applies to jobs that name no profile
        "default_profile": "native",
        "approved_roots": [],                     # when set, datasets and extra mounts must live under one of these
        "agent": {"mode": "none"},                # none | advisory (written boundaries in briefs; NOT enforced)
        "assign": {"experiments": {}, "tasks": {}},
        "profiles": {
            "native": {"runtime": "native"},
            "restricted": {"runtime": "auto", "network": {"mode": "disabled"}},
            "gpu-research": {"runtime": "auto", "gpu": True, "network": {"mode": "disabled"}},
            "custom": {"runtime": "auto"},
        },
    },
}


def defaults() -> Dict[str, Any]:
    """A fresh copy of the full default configuration (callers may edit it)."""
    return copy.deepcopy(_CONFIG)


def default(key: str) -> Any:
    """One default by dotted key, e.g. `default("context.max_chars")`."""
    present, v = configfile._get(_CONFIG, key)
    if not present:
        raise KeyError(key)
    return copy.deepcopy(v)


def section(p, name: str) -> Dict[str, Any]:
    """The merged view of one top-level section: its defaults under whatever `p.config` holds (which may be a partial dict)."""
    return deep_merge(_CONFIG[name], p.config.get(name) or {})


CATEGORIES = [
    ("project", "Project"), ("policy", "Research policy"), ("verification", "Verification"),
    ("agents", "Agents & orchestration"), ("hooks", "Hooks & skills"), ("git", "Git & workspaces"), ("environment", "Python environment"), ("supervisor", "Supervisor"), ("governance", "Governance & approval"),
    ("sandbox", "Execution & sandbox"), ("hpc", "HPC & SLURM"), ("context", "Context"), ("guide", "Scientist Guide"), ("data", "Data & artifacts"),
]


class Option:
    """One configurable setting."""

    def __init__(self, key: str, type_: str, category: str, label: str, help_: str, *, why: str = "", effect: str = "",
                 risk: str = "normal", choices: Optional[List[Any]] = None, min_: Optional[float] = None,
                 max_: Optional[float] = None, nullable: bool = False, affects: Optional[List[str]] = None,
                 scopes: Tuple[str, ...] = ("project", "machine"), restart: bool = False,
                 depends_on: Optional[str] = None, doc: Optional[str] = None, unit: Optional[str] = None, advanced: bool = False):
        self.key, self.type, self.category, self.label, self.help = key, type_, category, label, help_
        self.why, self.effect, self.risk = why, effect, risk
        self._choices, self.min, self.max, self.nullable = choices, min_, max_, nullable
        self.affects = affects or []
        self.scopes, self.restart, self.depends_on, self.doc, self.unit = scopes, restart, depends_on, doc, unit
        self.advanced = advanced

    @property
    def choices(self) -> Optional[List[Any]]:
        return self._choices() if callable(self._choices) else self._choices   # callable: resolved late (packs import the store)

    def meta(self) -> Dict[str, Any]:
        return {"key": self.key, "type": self.type, "category": self.category, "label": self.label, "help": self.help,
                "why": self.why, "effect": self.effect, "risk": self.risk, "choices": self.choices, "min": self.min,
                "max": self.max, "nullable": self.nullable, "affects": self.affects, "scopes": list(self.scopes),
                "restart": self.restart, "depends_on": self.depends_on, "doc": self.doc, "unit": self.unit, "advanced": self.advanced,
                "approval": self.risk != "normal"}


def _o(*a, **k) -> Option:
    return Option(*a, **k)


# `risk`: normal = anyone may change it; policy = a human only (agents are refused, so an agent cannot loosen its own
# rules); risky = a human only, and only with explicit confirmation because it can discard or expose work.
OPTIONS: List[Option] = [
    _o("project.name", "str", "project", "Project name", "The name shown in the panel, reports and generated agent context.",
       why="Humans and agents see it in every summary.", effect="Cosmetic. Identity records use ids, not the name.",
       affects=[".research/config.yaml"]),
    _o("project.goal", "str", "project", "Research goal",
       "One or two sentences describing what the project is trying to learn. It opens every agent brief and checkpoint.",
       why="It keeps agents and future you pointed at the same question.", effect="Regenerates the agent context; no records change.",
       affects=[".research/context/current.md"]),
    _o("project.default_backend", "enum", "project", "Default run backend",
       "Where `research run` executes when you do not say: on this machine, or through SLURM.", choices=["local", "slurm"],
       why="Saves choosing a backend for every run.", effect="Only changes the default; each run can still pick its own."),

    _o("agent_policy.max_runs_without_synthesis", "int", "policy", "Maximum runs without synthesis",
       "How many finished, unsynthesized runs an agent may accumulate for one experiment. Beyond this limit further runs "
       "are blocked (exit code 3) until the results are synthesized or an override with a reason is used.",
       why="Stops agents from producing large amounts of output nobody has interpreted.",
       effect="Lower = more frequent synthesis; higher = longer unattended sweeps. Default 5.",
       risk="policy", min_=1, max_=1000, affects=[".research/context/policy.md"]),
    _o("agent_policy.max_failed_runs_without_review", "int", "policy", "Maximum failed runs without review",
       "How many failed runs may pile up on an experiment before agents must stop and have the failures reviewed.",
       why="Repeated failures usually mean a wrong assumption, not bad luck.",
       effect="Reaching the limit blocks new runs for that experiment until someone reviews. Default 3.",
       risk="policy", min_=1, max_=1000),
    _o("agent_policy.require_experiment_registration", "bool", "policy", "Require registered experiments",
       "Every run must belong to a registered experiment (question, hypothesis, parameters) before it executes.",
       why="Keeps intent on record before results exist, which is what makes the results interpretable.",
       effect="Off: runs may be created without an experiment. Unregistered work is invisible to synthesis.", risk="policy"),
    _o("agent_policy.require_synthesis_before_new_experiment", "bool", "policy", "Synthesize before the next experiment",
       "Agents must synthesize a finished experiment before starting a new one on the same question.",
       why="Prevents running ahead of what has actually been learned.", effect="Off allows back-to-back experiments.", risk="policy"),
    _o("agent_policy.enforce_for_humans", "bool", "policy", "Apply agent limits to humans too",
       "By default the run limits above bind agents only. Turn this on to make them bind you as well.",
       why="Useful for shared projects where everyone should follow the same discipline.",
       effect="Humans hit the same blocks and need an override reason to go past them.", risk="policy"),

    _o("verification.require_review_for_supported", "bool", "verification", "Require review for 'supported' findings",
       "A finding cannot be marked supported until an independent review approves it.",
       why="Agents are fluent and can be confidently wrong; review is the check.", effect="Off lets findings be marked supported without review.",
       risk="policy"),
    _o("verification.independent_reviewer", "bool", "verification", "Reviewer must be independent",
       "The reviewer may not be the finding's author.", why="Self-review does not catch the author's own blind spots.",
       effect="Off allows self-review.", risk="policy", depends_on="verification.require_review_for_supported"),
    _o("verification.require_evidence_for_supported", "bool", "verification", "Require evidence for 'supported' findings",
       "A supported finding must cite runs, metrics or artifacts. Numbers bound to records must verify.",
       why="Keeps conclusions traceable to recorded data.", effect="Off allows supported findings without recorded evidence.",
       risk="policy"),
    _o("verification.checks_gate_task_completion", "bool", "verification", "Task checks gate completion",
       "`task done` runs the task's checks first; failures block agents from completing it.",
       why="'Done' should mean the acceptance checks passed.", effect="Off lets agents complete tasks without passing checks.",
       risk="policy"),
    _o("verification.check_timeout_seconds", "int", "verification", "Check timeout", "How long one task check may run before it is stopped and counted as failed.",
       why="A hung check must not block everyone.", effect="Too low fails slow-but-correct checks.", min_=1, max_=86400, unit="seconds"),

    _o("layout.experiments_dir", "str", "git", "Experiment worktree folder", "Where new experiment worktrees are created. Relative paths are under the project folder that holds main/ and ledger/. Empty keeps the legacy <name>-experiments folder beside the checkout.",
       why="Keeps every experiment checkout in one place you chose.", effect="Only affects experiments created afterwards; existing worktrees stay where they are.", nullable=True),
    _o("layout.main_dir", "str", "git", "Main checkout folder", "Folder name of the main git checkout (informational; set by Initialize Project).", nullable=True,
       why="Documents the project layout for people and tools.", effect="Does not move anything on disk."),
    _o("layout.ledger_dir", "str", "git", "Ledger folder", "Folder name of the shared-ledger worktree (informational; set by Initialize Project).", nullable=True,
       why="Documents the project layout for people and tools.", effect="Does not move anything on disk."),
    _o("agents.default_profile", "str", "agents", "Default agent CLI", "Which agent profile (" + ", ".join(_providers.KNOWN) + ", gemini, or one you define) `research agent dispatch` starts when a role names none.",
       why="Lets you hand work to your usual agent without choosing each time.", effect="Only affects new dispatches. The CLI must be installed on the machine that runs it."),
    _o("orchestration.enabled", "bool", "agents", "Model routing", "Route work to cheaper or stronger models by level (fast, balanced, deep) instead of one model for everything.",
       why="Spends the expensive model only where it matters.", effect="Off: dispatch uses the default profile. Run `research agent setup --apply` to refresh generated agent files.",
       affects=[".claude/agents/"]),
    _o("orchestration.active_strategy", "str", "agents", "Allocation strategy", "Which named strategy maps task levels to models (for example balanced_first or deep_first). Empty uses the legacy tier routing.",
       why="Strategies make cost/quality trade-offs explicit and auditable.", effect="Changes which model each new dispatch uses. Running sessions keep the policy recorded at dispatch.",
       nullable=True, affects=[".claude/agents/"]),
    _o("orchestration.limits.haiku_attempts", "int", "agents", "Fast-model attempts", "How many times the fast model may retry a task before work escalates.", min_=1, max_=20,
       why="Bounds time spent on a model that is not getting there.", effect="Higher = more retries before paying for a stronger model."),
    _o("orchestration.limits.sonnet_attempts", "int", "agents", "Balanced-model attempts", "How many times the balanced model may retry before escalating.", min_=1, max_=20,
       why="Bounds retries on the mid tier.", effect="Higher = more retries before escalation."),
    _o("orchestration.limits.escalations", "int", "agents", "Maximum escalations", "How many times a task may move up to a stronger model.", min_=0, max_=10,
       why="Caps cost on a task that keeps failing.", effect="0 means never escalate; the task fails instead."),

    _o("harness.hooks_timeout_seconds", "int", "hooks", "Hook timeout", "How long an approved hook command may run before it is stopped.", min_=1, max_=86400, unit="seconds",
       why="Hooks run shell commands; a hung one must not stall dispatch.", effect="Applies to every hook without its own timeout."),
    _o("skills.packs", "list", "hooks", "Skill packs", "Which skill packs are on. Core Research is always on; the others add skills for a field (Scientific Python, Machine Learning, Physics, Medical Imaging, Quantum, HPC, Frontend, Scientific Writing). Put it in the machine file to choose the default for every new project.",
       choices=lambda: [x["id"] for x in __import__("research.packs", fromlist=["x"]).pack_defs()], why="Skills only help when they fit the work; more is not better.",
       effect="Enabling a pack installs its skills (yours are never overwritten). Disabling stops it being on.", affects=[".research/skills", ".research/skills.yaml"]),
    _o("skills.disabled", "list", "hooks", "Disabled skills", "Skills that stay on disk but are not suggested to agents or linked into .claude/skills and .agents/skills.",
       why="Switch a skill off without deleting it or its history.", effect="Takes effect on the next skills refresh.", affects=[".claude/skills", ".agents/skills"]),
    _o("harness.require_seed", "bool", "hooks", "Require a seed in every run", "The experiment-config hook fails (instead of warning) when a run records no seed among its parameters.",
       why="An unseeded stochastic run cannot be reproduced.", effect="Only affects the experiment-config hook, once approved."),
    _o("skills.link", "list", "hooks", "Expose skills to", "Which agent CLIs see the project's skills (`claude` → .claude/skills, `agents` → .agents/skills).", choices=["claude", "agents"],
       why="Skills only help agents that can find them.", effect="Changing it creates or removes links on the next skills refresh.", affects=[".claude/skills", ".agents/skills"]),

    _o("git.commit_checkpoints", "bool", "git", "Commit every checkpoint", "Creating a checkpoint also commits `.research/` to git (never pushes, and only `.research/`).",
       why="Puts research history into git history automatically.", effect="Off: checkpoints are files only; commit them yourself or use `checkpoint create --commit`.",
       affects=["git history"]),

    _o("environment.python", "str", "environment", "Project Python", "The Python used on this machine by agents, task checks and `{python}` in check commands: a venv folder or an interpreter path (relative paths are under the project folder). Empty uses whatever `python3` is on PATH.",
       nullable=True, why="Without it, an agent's `python3` and your checks may use an interpreter that lacks your packages.", effect="Takes effect for the next agent session and check run.", affects=[".research/bin/py"]),
    _o("environment.packages", "list", "environment", "Packages the project needs", "Packages that `research env check` verifies, written as on PyPI (for example numpy, PyYAML, scikit-learn>=1.4); each is checked by its import name (yaml, sklearn).",
       why="Finds a missing package before an agent session does.", effect="Only used by the check."),
    _o("supervisor.poll_seconds", "int", "supervisor", "Polling interval", "How often the supervisor looks for ready tasks, finished agents and HPC jobs.",
       why="Short intervals react faster; long ones are gentler on shared filesystems.", effect="Takes effect on the daemon's next cycle.", min_=1, max_=3600, unit="seconds"),
    _o("supervisor.max_parallel_agents", "int", "supervisor", "Agents at once", "How many agents may work at the same time across a plan. Tasks of one experiment never share a worktree concurrently.",
       why="Parallel agents finish faster and cost more; one is the safe default.", effect="Applies from the next scheduling pass. A plan's own `execution.max_parallel_agents` overrides it.", min_=1, max_=32, risk="policy"),
    _o("supervisor.max_attempts_per_task", "int", "supervisor", "Attempts per task", "How many agent attempts one task may use (the first try plus repairs) before it is blocked for a human.",
       why="Bounds automatic repair so a stuck task cannot loop.", effect="Reaching it blocks the task with the last diagnostics.", min_=1, max_=20, risk="policy"),
    _o("supervisor.max_total_attempts", "int", "supervisor", "Attempts per plan", "Total agent attempts a plan run may start.", why="A hard ceiling on cost.",
       effect="Reaching it blocks remaining tasks.", min_=1, max_=500, risk="policy"),
    _o("supervisor.max_wall_minutes", "int", "supervisor", "Plan wall-time budget", "Minutes a plan may stay in the running state before the supervisor pauses it.",
       why="Stops unattended runs from going on indefinitely.", effect="The plan pauses; resume it to continue.", min_=1, max_=100000, unit="minutes", risk="policy"),
    _o("supervisor.attempt_timeout_minutes", "int", "supervisor", "Agent attempt timeout", "A single agent process that runs longer than this is asked to stop. Computation on HPC is not counted.",
       why="A hung agent should not hold the plan forever.", effect="A timed-out attempt is verified as it stands, then repaired or blocked.", min_=1, max_=10000, unit="minutes"),
    _o("supervisor.auto_restart", "bool", "supervisor", "Restart a stopped supervisor", "When the supervisor process has died while a plan is running, the chat gateway starts it again (at most 3 times an hour) and only tells you if that fails.",
       why="A crashed supervisor at night should not wait for you.", effect="Applies at the gateway's next check (every 30 s)."),
    _o("supervisor.on_failure", "enum", "supervisor", "When verification fails", "diagnose_and_repair dispatches a bounded repair attempt; pause stops the plan for you to look; stop ends the run.",
       choices=["diagnose_and_repair", "pause", "stop"], why="Engineering failures are usually fixable without you; scientific ones never are automatic.", effect="Applies to failures after the change.", risk="policy"),
    _o("supervisor.enforce_completion", "bool", "supervisor", "Only the supervisor completes supervised tasks", "While a plan runs, only the supervisor's verification (or a human `supervisor accept`) can mark its tasks done, by any route: CLI, MCP, UI or a hand-edited file.",
       why="An agent saying it is finished is not evidence.", effect="Off lets any actor complete supervised tasks through the ordinary gates.", risk="policy"),
    _o("supervisor.auto_apply_operational", "bool", "supervisor", "Auto-apply operational plan changes", "Agent proposals that only add new tasks (diagnostics, follow-ups) are applied without asking, up to the limit below.",
       why="Lets agents add a missing diagnostic step without stalling the plan.", effect="Off: every proposal waits for you.", risk="policy"),
    _o("supervisor.usage_limit.on_limit", "enum", "supervisor", "When a provider's usage limit is hit", "halt: stop the task for you (today's behaviour). wait: pause only the tasks that use that provider until it resets, then relaunch them by themselves. fallback_then_wait: first try the next provider in agents.handoff.fallback_order (only where strategy, roles and orchestration.available allow it), else wait.",
       choices=["halt", "wait", "fallback_then_wait"], why="A 5-hour or weekly limit ends by itself; nobody needs to be there to press Retry.", effect="Applies to the next limit. Attempts refused by a limit never count against the budgets; approvals and governance are untouched.", risk="policy"),
    _o("supervisor.usage_limit.wait_buffer_seconds", "int", "supervisor", "Extra wait after the reset", "Seconds added after the provider's stated reset time before the task is relaunched.", why="Clocks differ by a minute or two.", effect="Applies to the next limit.", min_=0, max_=3600, unit="seconds"),
    _o("supervisor.usage_limit.max_wait_hours", "float", "supervisor", "Longest unattended wait", "A reset further away than this (a weekly limit, say) is not waited out silently: the supervisor asks you (wait, switch provider, or stop).", why="Never an unattended multi-day wait unless you raise it.", effect="Applies to the next limit.", min_=0, max_=720, unit="hours", risk="policy"),
    _o("supervisor.usage_limit.unknown_reset_backoff_minutes", "list", "supervisor", "Retry intervals when the reset time is unknown", "Minutes between relaunches when the provider did not say when the limit ends; the last value repeats. A refused relaunch costs nothing.", why="The relaunch is the probe; no extra paid call is made.", effect="Applies to the next limit.", unit="minutes"),
    _o("supervisor.usage_limit.short_backoff_minutes", "list", "supervisor", "Retry intervals for rate and overload errors", "Minutes between relaunches after a short-lived rate-limit or overload error with no reset time; the last value repeats.", why="These clear in minutes, not hours.", effect="Applies to the next limit.", unit="minutes"),
    _o("supervisor.investigator.auto", "bool", "supervisor", "Investigate failures automatically", "When a task hits a failure the supervisor cannot classify, run the investigator agent once (read-mostly, never approves). Off: it only runs when you ask (`research investigate`, or a free-text answer to a decision request).",
       why="Novel failures need judgment the rules do not have.", effect="Spends model budget; capped by the two settings below. Protected changes still need your PIN.", risk="policy"),
    _o("supervisor.investigator.max_per_plan", "int", "supervisor", "Investigator runs per plan", "Most investigator runs for one plan; after that the supervisor just asks you in plain text.", why="Bounds spend.", effect="Further questions wait for you.", min_=0, max_=50),
    _o("supervisor.investigator.budget_usd", "float", "supervisor", "Investigator budget (USD)", "Total provider-reported cost the investigator may use for this project; runs with no reported cost count toward the per-plan limit only.", why="Bounds spend.", effect="Beyond it the supervisor asks you in plain text.", min_=0, max_=1000),
    _o("supervisor.max_auto_tasks", "int", "supervisor", "Auto-added task limit", "Most tasks agents may add to a plan through auto-applied proposals.", why="Bounds uncontrolled plan growth.",
       effect="Further proposals wait for approval.", min_=0, max_=200, risk="policy"),
    _o("supervisor.require_approval_for_scientific_changes", "bool", "supervisor", "Approve scientific changes", "Proposals that change hypotheses, methods, checks, acceptance, parameters or existing task definitions always wait for you.",
       why="Science is the researcher's decision.", effect="Turning it off lets proposals of any kind apply automatically (not recommended).", risk="policy"),
    _o("governance.experiments.agent_creation", "enum", "governance", "Agents creating experiments", "human_only: agents cannot even propose. propose_only: agents submit a proposal; you approve it with your PIN. Agents never create experiments directly.",
       choices=["propose_only", "human_only"], why="Experiments are scientific decisions.", effect="Applies at once, to every creation path.", risk="policy"),
    _o("governance.tasks.agent_creation", "enum", "governance", "Agents creating tasks", "human_only, propose_only, or bounded_auto (agents add operational tasks of the allowed types inside their own experiment, up to the limit).",
       choices=["bounded_auto", "propose_only", "human_only"], why="Keeps the plan yours while letting agents add a missing diagnostic.", effect="Applies at once. The older supervisor switches below can only tighten it.", risk="policy"),
    _o("governance.tasks.allowed_auto_types", "list", "governance", "Task types agents may add themselves", "Under bounded_auto, agents may add tasks of these types themselves; any other type needs a proposal.", why="Operational work only.", effect="Other types need a proposal.", risk="policy"),
    _o("governance.tasks.max_auto_per_experiment", "int", "governance", "Agent-created tasks per experiment", "Under bounded_auto, the most tasks agents may add to one experiment; further tasks need a proposal.", why="Bounds plan growth.", effect="Further tasks need a proposal.", min_=0, max_=200, risk="policy"),
    _o("governance.scientific_changes.require_human_approval", "bool", "governance", "Scientific changes need approval", "Changes to hypotheses, methods, checks or acceptance always wait for you.", why="Science is the researcher's decision.", effect="Off (with the supervisor switch) lets them apply automatically.", risk="policy"),
    _o("governance.approval.require_pin", "bool", "governance", "Ask for the PIN", "Protected actions (approving a proposal, changing policy, promoting to main) ask for your PIN once one is set.", why="Stops accidental autonomous changes. Not a defence against a hostile agent.", effect="Off: protected actions need only a human actor.", risk="policy"),
    _o("governance.approval.require_rationale", "bool", "governance", "Ask for a reason", "Approving a proposal needs a written reason, recorded in the audit trail.", why="The audit trail explains itself.", effect="Off: the note is optional.", risk="policy"),
    _o("governance.approval.record_rejections", "bool", "governance", "Record rejections", "Rejected and revision-requested proposals are written to the audit trail.", why="Rejected ideas stay visible.", effect="Off: only the proposal file records it."),
    _o("governance.approval.chat.enabled", "bool", "governance", "Approve from chat with an authenticator code", "Lets an approver on a paired chat account approve some PIN-protected proposals by sending a 6-digit authenticator (TOTP) code. Also needs `research approval chat-enroll` on this machine. Frozen tests, scientific plan or hypothesis edits, governance/approval settings, the PIN/authenticator and the chat allow-list always stay computer-only.",
       why="Approving from a phone when you are away. As strong as your chat account AND your authenticator device.", effect="Off: chat can only ask you to approve at the computer.", risk="policy"),
    _o("governance.approval.chat_allow", "list", "governance", "What chat may approve", "Narrows what chat may approve: experiment_proposal, plan_add_tasks, budget_increase, retry, role_change, promotion, resume, decision_option. Empty means all of these (never the computer-only ones).",
       why="Least privilege for the phone.", effect="Anything not listed needs the PIN at the computer.", risk="policy"),
    _o("governance.approval.confirm_human_experiment_creation", "bool", "governance", "Confirm my own experiments", "Ask for the PIN when you create an experiment yourself. Off by default: creating one interactively needs no PIN.", why="Optional extra care.", effect="Applies to the CLI and the panel."),
    _o("agents.handoff.enabled", "bool", "agents", "Automatic provider fallback", "When a provider is unavailable or hits its limit, continue the same task with the next provider in the fallback order. Off: the task pauses for you (you can still press Continue with another agent).",
       why="Keeps long plans moving when one provider runs out of credit.", effect="Bounded by the attempt limits; never used when an attempt's state is unknown.", risk="policy"),
    _o("agents.handoff.fallback_order", "list", "agents", "Fallback order", "Providers to try, in order: " + ", ".join(_providers.KNOWN) + ". Each must have a profile with that provider and be installed.", why="Explicit order, no surprises.", effect="Used only when automatic fallback is on.", choices=list(_providers.KNOWN)),
    _o("agents.handoff.on_provider_unavailable", "enum", "agents", "When a provider is unavailable", "try_next continues with the next provider; pause stops the task for you.", choices=["pause", "try_next"], why="Opt-in.", effect="Needs automatic fallback on."),
    _o("agents.handoff.on_unknown_execution_state", "enum", "agents", "When an attempt's state is unknown", "Always pause: a task is never relaunched while its previous process might still be running.", choices=["pause"], why="Prevents two agents editing one worktree.", effect="Fixed."),
    _o("supervisor.session_resume", "enum", "supervisor", "Continue the agent's own session", "auto continues the provider's native conversation for repairs when the installed CLI proves it can; never always starts fresh with a generated hand-off.",
       choices=["auto", "never"], why="Native sessions keep context cheaply; hand-offs work everywhere.", effect="Applies to later repair attempts.", advanced=True),
    _o("supervisor.context_max_chars", "int", "supervisor", "Prompt context size", "Upper bound for the context the supervisor assembles for an agent attempt (project docs, objectives, prerequisites, history).",
       why="Selective context beats dumping the ledger into every prompt.", effect="Larger limits cost more tokens per attempt.", min_=2000, max_=400000, unit="characters", advanced=True),
    _o("supervisor.commit_verified_work", "bool", "supervisor", "Commit verified work on the experiment branch", "When a task verifies, its changes are committed on the experiment's own branch (never main, never pushed) so they can be promoted and traced to the task.",
       why="Promotion works on commits; agents often leave their work uncommitted.", effect="Off: you commit yourself before preparing a promotion.", risk="policy"),
    _o("supervisor.commit_exclude", "list", "supervisor", "Paths kept out of those commits", "Folders (relative to the worktree) the supervisor never commits, such as bulky results.",
       why="Code history should not carry data and figures.", effect="Applies to later verifications.", advanced=True),
    _o("supervisor.commit_keep", "list", "supervisor", "Small figures and reports kept despite those folders", "File patterns that are still committed with verified work when they sit in an excluded folder (outputs/, results/) and are small, so figures and reports stay with the code that made them.",
       why="A finding's figure and report should be versioned with its code; bulky data should not.", effect="Applies to later verifications. Secrets, caches and compiled files are never committed whatever this says.", advanced=True),
    _o("supervisor.commit_max_file_mb", "int", "supervisor", "Largest file committed with verified work", "Files bigger than this are left out of the experiment-branch commit and listed in the attempt record.",
       why="Git history should not carry large data.", effect="Applies to later verifications.", min_=1, max_=500, unit="MB", advanced=True),
    _o("git.preflight", "enum", "git", "Git preflight before plans and promotion", "Before a plan starts or resumes, or promotion is authorized: warn = report uncommitted files and why they matter, then continue; block = refuse until you have reviewed and committed what matters; off = do not check.",
       choices=["warn", "block", "off"], why="Experiment worktrees start from the last commit, so uncommitted plans and code are invisible to agents and not reproducible.", effect="Applies to the next plan run, resume or promotion authorization.", risk="policy"),
    _o("supervisor.hpc_poll_seconds", "int", "supervisor", "HPC polling interval", "How often waiting computational runs are reconciled with the cluster. Unavailable status never causes a resubmission.",
       why="Be gentle with login nodes.", effect="Applies on the next cycle.", min_=5, max_=3600, unit="seconds", advanced=True),
    _o("supervisor.recovery.enabled", "bool", "supervisor", "Recover from late evidence automatically", "Wait for a finished Slurm job's results to be collected before verifying, and re-verify a failed task unchanged when its missing results arrive. It never relaxes a check, changes the science, raises a budget or submits a job.",
       why="A job can finish before its metrics are home; judging it then is a false failure.", effect="Off: a task is verified the moment its run is terminal, and a late result needs `research supervisor resolve`.", risk="policy"),
    _o("supervisor.recovery.collection_wait_minutes", "int", "supervisor", "Wait for results collection", "How long a task whose Slurm job finished waits for its results to be collected and ingested before it is verified on what exists.",
       why="Bounds the wait when the cluster or transfer is down.", effect="Past it a missing metric is reported as evidence_pending (and still re-verified automatically if the results arrive).", min_=0, max_=10080, unit="minutes"),
    _o("supervisor.recovery.max_reverifications", "int", "supervisor", "Automatic re-verifications per attempt", "Most times one attempt is re-verified automatically because its evidence changed.", why="Bounds automatic work.", effect="Further changes need `research supervisor resolve TASK --completed`.", min_=0, max_=50, advanced=True),
    _o("supervisor.recovery.max_syncs", "int", "supervisor", "Recovery syncs per task", "Most cluster syncs the recovery step runs for one stopped task while it waits for results.", why="Be gentle with login nodes.", effect="After it the task waits for a manual `research hpc collect`.", min_=0, max_=10000, advanced=True),
    _o("supervisor.max_hpc_resubmissions", "int", "supervisor", "Computational resubmissions", "How many extra runs an agent may submit per task after failures before the task is blocked.",
       why="Failed jobs cost allocation; repeats must be bounded.", effect="Further failures block the task.", min_=0, max_=20, risk="policy"),

    _o("promotion.main_branch", "str", "git", "Main branch", "The branch that promotions are prepared from and merged into.",
       why="Candidates start from the current tip of this branch so reviewed code is exactly what lands.", effect="Changing it affects only promotions prepared afterwards.", risk="policy"),
    _o("promotion.test_command", "str", "git", "Promotion test command", "The command run in a promotion candidate (for example `pytest -q`). Its result is bound to the candidate's exact commit; without it nothing can be verified or approved.",
       nullable=True, why="Integration should only receive code that passes the project's own checks.", effect="Runs a shell command with your permissions: set only commands you would run yourself.", risk="policy"),
    _o("auto_promotion.numerical_command", "str", "git", "Auto-promotion numerical check", "Run after the promotion test command in an automatic-promotion candidate (for example `pytest -q -m oracle`). Optional; recorded as not configured when empty.",
       nullable=True, why="Numerical oracles catch code that passes unit tests but computes the wrong numbers.", effect="Runs a shell command with your permissions. Changing it suspends an existing authorization until a human re-authorizes.", risk="policy"),
    _o("auto_promotion.reviewer_profile", "str", "git", "Auto-promotion reviewer profile", "The agent profile that independently reviews automatic-promotion candidates. It must not be a profile that implemented the work (unless require_distinct_reviewer is off).",
       nullable=True, why="A separate reviewer prevents self-approval of code.", effect="Changing it suspends an existing authorization until a human re-authorizes.", risk="policy"),
    _o("auto_promotion.require_distinct_reviewer", "bool", "git", "Require a different reviewer profile", "Refuse a reviewer profile that also implemented the work being promoted.",
       why="Keeps review independent.", effect="Turning it off allows the same provider to review its own work.", risk="policy"),
    _o("auto_promotion.auto_approve_verified_code", "bool", "git", "Standing authorization for verified code", "On: the supervisor authorizes, approves and integrates eligible code promotions on its own, across plans and restarts, with no PIN prompt each time. Every gate still applies: the promotion tests, the independent reviewer, scientific-content provenance, frozen tests and fast-forward-only merging. It never approves a scientific claim.",
       why="Overnight runs should not stop at a promotion that you would approve anyway; the checks, not a person, decide whether code is good.", effect="Enabling is one human act (this setting needs your PIN). It then applies to every plan of this project. Changing the verification policy (test command, reviewer) through Settings keeps it; an edit that bypasses Settings suspends it until you re-save this setting.", risk="policy", scopes=("project",)),
    _o("auto_promotion.auto_retry_attempts", "int", "git", "Automatic promotion retries", "How many times a transient promotion failure (main moved, a git lock, a briefly dirty main worktree) is retried by itself before it asks you. Only with the standing authorization on.",
       why="A failure that clears by itself should not wake you.", effect="After this many, the promotion pauses and you are interrupted. Failed tests, a changes-requested review and failed provenance are never retried.", min_=0, max_=10, risk="policy"),
    _o("auto_promotion.auto_retry_minutes", "list", "git", "Delays between promotion retries", "Minutes to wait before each automatic retry; the last value repeats.",
       why="Gives a git lock or a human edit time to clear.", effect="Applies to the next retry.", unit="minutes"),
    _o("supervisor_assistant.self_heal", "bool", "supervisor", "Automatically diagnose and repair recoverable failures", "On: when a promotion gate or a task fails for a recoverable engineering reason (failing tests, a reviewer's requested change, an executable cell in a report), the debug agent's diagnosis acts. Deterministic diagnosis first; then a bounded repair task runs in the experiment's own worktree, is verified by the supervisor, the promotion candidate is rebuilt, re-tested and independently re-reviewed, and integration is retried. Repairs never edit or weaken tests, acceptance criteria, hypotheses, evidence or governance; those stop and ask you.",
       why="Overnight, a fixable formatting or code problem should not wait for you.", effect="Capped per failure and per plan; the same failure twice stops it. You are told about each recovery and interrupted only for decisions, permissions, exhausted budgets, unsafe changes or failures it cannot fix.", risk="policy", scopes=("project",)),
    _o("supervisor_assistant.max_repairs", "int", "supervisor", "Repairs per failure", "Most repair attempts for ONE failure before it asks you.", why="A repair that keeps failing is a decision for a person.", effect="Applies to the next failure.", min_=1, max_=5, risk="policy"),
    _o("supervisor_assistant.max_repairs_per_plan", "int", "supervisor", "Repairs per plan", "Most repair tasks self-healing may add to one plan run.", why="Bounds the total autonomous work and cost.", effect="After it, failures ask you.", min_=1, max_=50, risk="policy"),
    _o("supervisor_assistant.repair_instructions", "str", "supervisor", "Extra repair instructions", "Project conventions appended to every repair task (for example how reports are built). They cannot relax the hard rules.",
       nullable=True, why="Lets a project teach the repair agent its house rules.", effect="Applies to the next repair task.", risk="policy"),
    _o("supervisor_assistant.profile", "str", "supervisor", "Assistant agent profile", "The agent profile used for the read-only planning agent (interventions) and the debug agent (halts). Empty uses `claude`.",
       nullable=True, why="These agents read logs and plans and propose; a capable model avoids bad advice.", effect="Applies to the next intervention or diagnosis.", risk="policy"),
    _o("supervisor_assistant.model", "str", "supervisor", "Assistant model", "Model for the planning and debug agents. Empty uses the strongest Claude (claude-opus-5-5) when the profile is Claude, otherwise the profile's own model.",
       nullable=True, why="Diagnosing a stuck research plan is worth the strongest model.", effect="Applies to the next intervention or diagnosis; costs follow the model.", risk="policy"),
    _o("intervention.require_approval", "bool", "supervisor", "Always approve interventions", "Ask for approval even when an intervention changes nothing protected (unblock, retry, guidance). Protected changes always need approval and the PIN.",
       why="Lets a cautious team review every instruction before it runs.", effect="Operational interventions wait in 'proposed' until approved."),
    _o("notifications.telegram.enabled", "bool", "supervisor", "Telegram notifications", "Message you on Telegram when a plan halts and answer simple commands. The bot token and chat id are stored on this machine only (Control Center → Plans → Chat, or `research chat setup`).",
       why="An overnight run that stops should reach you.", effect="The supervisor contacts api.telegram.org while enabled."),
    _o("notifications.discord.enabled", "bool", "supervisor", "Discord notifications", "Alerts, slash commands and decision buttons in Discord, through the one chat gateway (needs the optional discord extra; `research chat setup discord`). The bot token is stored on this machine only.",
       why="Answer a halted run from your phone with buttons and threads.", effect="The chat gateway keeps an outbound connection to discord.com while enabled."),
    _o("notifications.level", "enum", "supervisor", "Push notification level", "How much the project pushes to your phone (Telegram and Discord): off = nothing automatic (commands still work); low = plan started/finished and failures; medium (default) = plus each experiment starting and finishing with the results it recorded, and anything waiting for you; high = plus every run and task. Change it from chat with /notify.",
       choices=["off", "low", "medium", "high"], why="A long run should tell you what you need to know, not everything.", effect="Applies to the next alert; events skipped by a lower level are not replayed when you raise it."),
    _o("notifications.summary", "enum", "supervisor", "Experiment summary written by", "facts = a template filled from recorded facts only (free, instant, no agent). agent = a read-only agent (ask.profile / ask.model) writes a short plain-language summary from the same facts; it is read-only, may cost money, and falls back to the facts if it fails.",
       choices=["facts", "agent"], why="A quick 'what happened and what should I know' on your phone.", effect="Applies to the next experiment that finishes."),
    _o("ask.profile", "str", "agents", "Agent for /ask and summaries", "The agent profile (claude, codex, agy, opencode or your own) that answers /ask and writes agent summaries. Empty uses the default profile. Only claude and codex can be restricted to read-only, so others are refused unless ask.allow_unenforced is on. In chat: /ai PROFILE MODEL.", nullable=True,
       why="Pick the provider and model you want for quick questions, separate from the one doing the work.", effect="Applies to the next /ask or summary.", scopes=("project", "machine")),
    _o("ask.model", "str", "agents", "Model for /ask and summaries", "The model the profile above uses for these questions. Empty uses the profile's own default.", nullable=True,
       why="A cheaper or faster model is usually enough for questions.", effect="Applies to the next /ask or summary."),
    _o("ask.timeout_seconds", "int", "agents", "/ask time limit", "How long the agent may work on one question or summary before it is stopped.", min_=30, max_=3600, unit="seconds",
       why="A question should not run away.", effect="Applies to the next /ask or summary."),
    _o("ask.allow_unenforced", "bool", "agents", "Allow agents that cannot be made read-only", "agy, opencode and custom agents cannot be restricted to read-only from here; allowing them means only the written instruction stops them from changing files.", risk="policy",
       why="Read-only is a safety rule, not a preference.", effect="Applies to the next /ask or summary.", depends_on="ask.profile"),
    _o("notifications.discord.mention", "bool", "supervisor", "Ping me on Discord", "Mention the paired operators on Discord alerts that need a human (warnings and errors), so Discord sends a push notification even when the server only notifies for @mentions.",
       why="Discord's default setting hides ordinary messages from your phone.", effect="Applies to the next alert.", depends_on="notifications.discord.enabled"),
    _o("notifications.discord.thread_per_task", "bool", "supervisor", "One Discord thread per task", "Alerts about a task go to a thread named after it, so the conversation is the task's log; the channel gets a one-line pointer for errors and approvals.",
       why="Keeps a busy project channel readable.", effect="Applies to the next alert; existing threads are kept."),
    _o("notifications.alerts.on", "list", "supervisor", "Alert kinds", "Which events reach chat: halt, task_failed, approval_needed, promotion_ready, plan_finished, run_failed, budget, supervisor_down, decision, provider_limit.",
       why="Silence the kinds you do not want on your phone.", effect="Alerts of other kinds are not queued."),
    _o("notifications.alerts.cooldown_minutes", "int", "supervisor", "Alert cooldown", "The same alert is not repeated inside this time; after it, one reminder ('still: ...').", min_=1, max_=10080, unit="minutes",
       why="An unattended run must not repeat itself.", effect="Applies to the next alert."),
    _o("notifications.alerts.max_per_hour", "int", "supervisor", "Most messages per hour", "A hard ceiling on what this project sends to one chat platform in an hour. Past it, one notice says so and the rest are held back; replies, decisions and approvals always come through.",
       min_=5, max_=1000, why="No bug upstream may turn into hundreds of bot messages (that is how a bot token gets revoked).", effect="Applies to the next message."),
    _o("notifications.alerts.escalate_after", "int", "supervisor", "Escalate after", "After this many failures in a row for one task the alert suggests a next step (continue with another agent, pause the plan).", min_=1, max_=100,
       why="Repeated failure needs a decision, not more retries.", effect="Applies to the next alert."),
    _o("notifications.alerts.approval_expiry_hours", "int", "supervisor", "Approval buttons expire after", "Buttons on approval cards disappear after this time. The proposal stays pending; nothing is approved or denied by waiting.", min_=1, max_=720, unit="hours",
       why="Old buttons on a phone should not be tappable for ever.", effect="Applies to cards sent afterwards."),
    _o("notifications.digest.at", "str", "supervisor", "Digest time", "Local time (HH:MM) for a daily summary of what changed. Empty turns the digest off.", nullable=True,
       why="The morning question 'what happened overnight'.", effect="The chat gateway sends it to the home chat once a day."),
    _o("notifications.digest.silent_if_unchanged", "bool", "supervisor", "Digest only when something changed", "Skip the digest when nothing happened since the last one.",
       why="No noise on quiet days.", effect="Applies to the next digest."),
    _o("notifications.halt_grace_seconds", "int", "supervisor", "Halt grace period", "How long a plan must stay halted before it is reported and diagnosed.", min_=0, max_=86400, unit="seconds",
       why="Avoids alerts for stops that resolve themselves.", effect="Takes effect on the next scheduling pass."),
    _o("notifications.auto_debug", "bool", "supervisor", "Diagnose halts automatically", "Run the debug agent when a plan halts and send its summary and options (Telegram, if enabled, and Control Center). Off (the default) sends only the halt notice; you can still ask for a diagnosis any time.",
       why="Gives you a likely cause and a few fixes instead of just an alert.", effect="Each new halt costs one agent run."),
    _o("promotion.require_supported_finding", "bool", "git", "Require a supported finding", "A promotion can be approved only when its experiment has at least one supported (reviewed) finding.",
       why="Keeps unreviewed science out of main.", effect="Off allows promoting code from experiments without a supported finding; the gate still reports the missing evidence.", risk="policy"),
    _o("coordination.stale_task_hours", "int", "policy", "Stale task after", "A running task that has had no progress note for this long is flagged stale.", min_=1, max_=8760, unit="hours",
       why="Surfaces tasks an agent abandoned.", effect="Flags only; nothing is released automatically."),

    _o("backends.slurm.partition", "str", "hpc", "SLURM partition", "The SLURM partition (queue) that runs submitted from this machine use unless one is named.", nullable=True,
       why="Avoids repeating `--partition` on every submission.", effect="Used when a submission does not name one. Reservation approval rules still apply."),
    _o("backends.slurm.account", "str", "hpc", "SLURM account", "The SLURM account that jobs submitted from this machine are charged to.", nullable=True, why="Many clusters require it.", effect="Used when a submission does not name one."),
    _o("backends.slurm.time", "str", "hpc", "SLURM wall time", "The wall-time limit given to SLURM jobs unless a submission sets one, as HH:MM:SS.", why="A realistic limit avoids both killed jobs and wasted reservation.",
       effect="Shown in the reservation preview before any submission.", risk="normal"),
    _o("hpc.default_cluster", "str", "hpc", "Default cluster", "Which configured cluster `research hpc` uses when none is named.", nullable=True,
       why="Avoids naming the cluster every time.", effect="No effect on submission approval: you still approve the resources."),
    _o("hpc.bundle.max_total_bytes", "int", "hpc", "Maximum bundle size", "Largest source bundle that may be packaged for a cluster.", min_=1024, unit="bytes",
       why="Stops a stray data directory from being uploaded.", effect="Packaging fails with a list of the largest files when exceeded."),

    _o("sandbox.enabled", "bool", "sandbox", "Apply the default sandbox profile",
       "When on, jobs that name no profile run under the default sandbox profile. Off leaves jobs native unless a run, task, experiment or cluster names a profile.",
       why="Lets a project make isolation the norm instead of something each run must remember.",
       effect="A restrictive profile is mandatory once it applies: jobs are refused, never run unrestricted, if the runtime cannot enforce it.",
       risk="policy", affects=[".research/context/policy.md"]),
    _o("sandbox.default_profile", "str", "sandbox", "Default sandbox profile", "The profile used when sandboxing is enabled and a job names none (native, restricted, gpu-research, custom or your own).",
       why="Choose the isolation level once for the project.", effect="Only matters while 'Apply the default sandbox profile' is on.", risk="policy",
       depends_on="sandbox.enabled"),
    _o("sandbox.approved_roots", "list", "sandbox", "Approved data roots", "Directories that datasets and extra mounts must live under. Empty means any path that passes the safety checks.",
       why="Stops a typo from exposing an unrelated directory, such as a shared scratch root.", effect="A mount outside every approved root is refused when the job is prepared.",
       risk="policy"),
    _o("sandbox.agent.mode", "enum", "sandbox", "Agent filesystem boundary", "none, or advisory: writes the job boundaries into agent briefs. Neither mode restricts the agent's own shell; Research OS does not sandbox agents.",
       choices=["none", "advisory"], why="Makes expectations explicit without pretending to enforce them.", effect="advisory changes brief text only.", risk="policy"),
    _o("context.max_chars", "int", "context", "Context size budget", "Character budget for the generated agent context (`current.md`), about four characters per token.", min_=2000, max_=200000, unit="characters",
       why="Agents read this first; too long wastes their context window.", effect="Lower = terser summaries and more omitted detail."),
    _o("resume.stale_checkpoint_days", "int", "context", "Stale checkpoint after", "A checkpoint older than this is flagged in the resume view.", min_=1, max_=3650, unit="days",
       why="Reminds you to record what you now understand.", effect="Flag only."),
    _o("guide.enabled", "bool", "guide", "Scientist Guide", "Keep a Quarto book of the science on the main branch (docs/scientist-guide) and flag what needs writing.",
       why="Records and prose drift apart without a freshness signal.", effect="Off hides guide status and prompts; existing guide files are untouched."),
    _o("guide.highlights", "enum", "guide", "What counts as an important result", "supported: findings an independent review marked supported. starred: only findings you star. all: every finding that is not superseded.",
       choices=["supported", "starred", "all"], why="The guide communicates the main results, not every run. Starred findings are always included.",
       effect="Changes which findings are logged into the guide's Key results chapter and listed on the Guide page.", depends_on="guide.enabled"),
    _o("guide.auto_log", "bool", "guide", "Log important results automatically", "When a finding becomes an important result, add a short entry (claim, evidence link, one plot) to the Key results chapter. No agent is involved and nothing is ever rewritten.",
       why="You start from a skeleton that is already tied to the evidence.", effect="Off: add entries yourself with `research guide log`.", depends_on="guide.enabled"),
    _o("guide.draft.trigger", "enum", "guide", "When to suggest an agent draft", "manual: never prompt. result: Home asks as soon as an important result has no draft. checkpoint: only after you record a checkpoint, so drafts are batched.",
       choices=["manual", "result", "checkpoint"], why="Drafting costs agent time; you decide how eagerly to be asked.",
       effect="Only controls the reminder. An agent never writes into the guide until you start a draft.", depends_on="guide.enabled"),
    _o("guide.draft.max_words", "int", "guide", "Words an agent may add per section", "Upper bound for one draft in one chapter or result entry. A short first draft with the main idea; you add the context.",
       min_=30, max_=2000, unit="words", why="Keeps the guide yours: the agent proposes, you write.", effect="Written into the agent brief and checked afterwards (`research guide draft check`).", depends_on="guide.enabled"),
    _o("guide.draft.max_sections", "int", "guide", "Sections an agent may touch per draft", "How many chapters or result entries one draft may change.",
       min_=1, max_=20, unit="sections", why="Bounds the size of a review.", effect="Written into the agent brief and checked afterwards.", depends_on="guide.enabled"),
    _o("guide.draft.max_figures", "int", "guide", "Figures an agent may add per section", "How many figures one draft may add to a section. 0 means the agent writes text only.",
       min_=0, max_=10, unit="figures", why="Figures need captions and axes you will want to choose.", effect="Written into the agent brief and checked afterwards.", depends_on="guide.enabled"),
    _o("guide.publish.workflow", "bool", "guide", "GitHub Pages workflow", "Keep .github/workflows/publish-guide.yml, which builds the guide's scientific edition and publishes it as a website on every push to main.",
       why="One push publishes the current text.", effect="Creation only: turning it off never deletes an existing workflow.", depends_on="guide.enabled"),
    _o("guide.site.auto_include", "bool", "guide", "Add verified experiments to the website", "When a finding becomes supported, publish every verified experiment that has committed report files under reports/ (and was not unpublished) as a draft page on the research website. Adds only; nothing is pushed.",
       why="Verified work shows up in the local site without a command; you still edit it and decide when to push.", effect="Checked when a finding becomes supported, and by `research site sync`.", depends_on="guide.enabled"),
    _o("guide.model", "str", "guide", "Guide writing model", "The model profile the guide draft dispatch asks for.", why="Short drafts need a careful model more than a long one.", effect="Used on the next draft only.", depends_on="guide.enabled"),

    _o("artifacts.hash_max_bytes", "int", "data", "Hash artifacts up to", "Artifacts at or below this size are content-hashed when registered; larger ones record size only until pinned.", min_=0, unit="bytes",
       why="Hashes prove an artifact is unchanged; hashing huge files is slow.", effect="Larger values give stronger integrity evidence for more files at the cost of registration time."),
]

_BY_KEY: Dict[str, Option] = {o.key: o for o in OPTIONS}

# Tuning knobs and infrastructure detail. The editor hides these until asked, so a researcher running a simple experiment
# is not shown model escalation limits, hook timeouts or mount policy.
for _k in ("orchestration.limits.haiku_attempts", "orchestration.limits.sonnet_attempts", "orchestration.limits.escalations",
           "harness.hooks_timeout_seconds", "hpc.bundle.max_total_bytes", "artifacts.hash_max_bytes", "context.max_chars",
           "resume.stale_checkpoint_days", "coordination.stale_task_hours", "verification.check_timeout_seconds",
           "sandbox.approved_roots", "sandbox.agent.mode", "skills.link", "backends.slurm.time", "promotion.require_supported_finding"):
    _BY_KEY[_k].advanced = True


def register(option: Option) -> None:
    """Add (or replace) an option; later phases register their own."""
    if option.key in _BY_KEY:
        OPTIONS[:] = [o for o in OPTIONS if o.key != option.key]
    OPTIONS.append(option)
    _BY_KEY[option.key] = option


def get(key: str) -> Option:
    if key not in _BY_KEY:
        raise ResearchError(f"Unknown setting {key!r}", hint="`research config options` lists the settings that have forms; "
                                                              "other YAML keys remain editable in config.yaml.")
    return _BY_KEY[key]


# ------------------------------------------------------------------------------------------------ validation
_TRUE = {"true", "yes", "on", "1"}
_FALSE = {"false", "no", "off", "0"}


def validate(key: str, value: Any) -> Tuple[bool, Any, Optional[str]]:
    """(ok, normalized value, error). Accepts strings as typed in a CLI or form."""
    o = get(key)
    nullword = isinstance(value, str) and value.strip().lower() in ("", "null", "~")
    if value is None or (nullword and o.nullable):
        return (True, None, None) if o.nullable else (False, None, "A value is required.")
    if isinstance(value, str) and not value.strip() and o.type in ("int", "float", "bool", "enum"):
        return False, None, "A value is required."
    t = o.type
    try:
        if t == "bool":
            if isinstance(value, bool):
                v = value
            elif str(value).strip().lower() in _TRUE:
                v = True
            elif str(value).strip().lower() in _FALSE:
                v = False
            else:
                return False, None, "Use true or false."
        elif t in ("int", "float"):
            if isinstance(value, bool):
                return False, None, "Expected a number."
            v = int(str(value).strip()) if t == "int" and not isinstance(value, float) else float(value)
            if t == "int" and isinstance(value, float) and value != int(value):
                return False, None, "Expected a whole number."
            if t == "int":
                v = int(v)
            if o.min is not None and v < o.min:
                return False, None, f"Must be at least {o.min:g}."
            if o.max is not None and v > o.max:
                return False, None, f"Must be at most {o.max:g}."
        elif t in ("str", "path"):
            v = str(value)
            if "\x00" in v:
                return False, None, "Contains an invalid character."
            if t == "path" and ".." in v.replace("\\", "/").split("/"):
                return False, None, "Path may not contain '..'."
        elif t == "enum":
            v = str(value)
            if v not in (o.choices or []):
                return False, None, "Choose one of: " + ", ".join(map(str, o.choices or [])) + "."
        elif t == "list":
            if isinstance(value, (list, tuple)):
                v = [str(x) for x in value]
            else:
                s = str(value).strip()
                v = json.loads(s) if s.startswith("[") else [x.strip() for x in s.split(",") if x.strip()]
                v = [str(x) for x in v]
            if o.choices:
                bad = [x for x in v if x not in o.choices]
                if bad:
                    return False, None, f"Not allowed: {', '.join(bad)}. Choose from: {', '.join(o.choices)}."
        else:
            return False, None, f"Unsupported option type {t}."
    except (ValueError, TypeError):
        return False, None, "Expected a " + ("whole number." if t == "int" else "number." if t == "float" else "valid value.")
    if (o.key.startswith("supervisor.usage_limit.") or o.key == "auto_promotion.auto_retry_minutes") and o.type == "list":
        try:
            nums = [float(x) for x in v]
        except ValueError:
            nums = []
        if not nums or any(n <= 0 for n in nums):
            return False, None, "Give one or more positive numbers, for example 15,30,60."
    if o.key == "backends.slurm.time" and not re.fullmatch(r"(\d+-)?\d{1,3}:\d{2}:\d{2}|\d+", str(v)):
        return False, None, "Use HH:MM:SS (or D-HH:MM:SS)."
    return True, v, None


# ------------------------------------------------------------------------------------------------ reading
def _layer_text(p, scope: str) -> Tuple[str, str]:
    if scope == "project":
        with open(p.config_path, encoding="utf-8", newline="") as f:
            return f.read(), p.config_path
    path = configfile.machine_path()
    if not path:
        raise ResearchError("No machine config location is available on this system.")
    return (open(path, encoding="utf-8", newline="").read() if os.path.isfile(path) else ""), path


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def file_digests(p) -> Dict[str, str]:
    """Content digests of the editable layers, so a save can detect an edit made behind the editor's back."""
    out = {}
    for scope in ("project", "machine"):
        try:
            out[scope] = _digest(_layer_text(p, scope)[0])
        except (ResearchError, OSError):
            out[scope] = ""
    return out


def _defaults() -> Dict[str, Any]:
    from .store import DEFAULT_CONFIG
    return DEFAULT_CONFIG


def _default_of(key: str) -> Any:
    present, v = configfile._get(_defaults(), key)
    return v if present else None


def describe(p, category: Optional[str] = None, search: Optional[str] = None) -> Dict[str, Any]:
    from .store import DEFAULT_CONFIG
    src = configfile.sources(p.raw_config, DEFAULT_CONFIG)
    needle = (search or "").lower().strip()
    out = []
    for o in OPTIONS:
        if category and o.category != category:
            continue
        if needle and needle not in " ".join([o.key, o.label, o.help, o.why, o.category]).lower():
            continue
        s = src.get(o.key)
        present, value = configfile._get(p.config, o.key)
        origin = s["source"] if s else "default"
        errs = []
        if present and value is not None:
            ok, _, err = validate(o.key, value)
            if not ok:
                errs.append(err)
        d = _default_of(o.key)
        meta = o.meta()
        dyn = _dynamic_choices(p, o.key)
        if dyn is not None:   # names that exist in this project (profiles, strategies) are offered as a dropdown
            meta.update(type="enum", choices=dyn)
        out.append({**meta, "value": value if present else d, "default": d, "source": origin,
                    "inherited": origin != "project",
                    "file": {"project": p.rel(p.config_path), "machine": configfile.machine_path()}.get(origin) if origin != "default" else None,
                    "overridden_in": (s or {}).get("defined_in", []), "errors": errs})
    return {"categories": [{"id": c, "label": l} for c, l in CATEGORIES], "options": out,
            "machine_config": configfile.machine_path(), "project_config": p.config_path,
            "project_config_rel": p.rel(p.config_path), "digests": file_digests(p),
            "layers": ["default", "machine", "project"], "fingerprint": fingerprint(p)}


def _dynamic_choices(p, key: str) -> Optional[List[str]]:
    if key == "sandbox.default_profile":
        return sorted((p.config.get("sandbox") or {}).get("profiles") or {})
    if key == "agents.default_profile":
        return sorted(((p.config.get("agents") or {}).get("profiles")) or {})
    if key == "orchestration.active_strategy":
        return sorted((p.config.get("orchestration") or {}).get("strategies") or {})
    return None


def explain(p, key: str) -> Dict[str, Any]:
    d = describe(p)
    for x in d["options"]:
        if x["key"] == key:
            return x
    get(key)
    raise ResearchError(f"Unknown setting {key!r}")


def fingerprint(p) -> str:
    """Short hash of every registered option's effective value: changes when a policy a dispatch relied on changes."""
    vals = {}
    for o in OPTIONS:
        present, v = configfile._get(p.config, o.key)
        vals[o.key] = v if present else _default_of(o.key)
    return hashlib.sha256(json.dumps(vals, sort_keys=True, default=str).encode()).hexdigest()[:12]


# ------------------------------------------------------------------------------------------------ change planning
def _is_agent(p) -> bool:
    return (p.author or {}).get("author_type") == "agent"


def remove_key(text: str, dotted: str) -> str:
    """Delete one live key (and the lines of its value) from YAML text, leaving everything else byte-identical."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    idx, indent = configfile._find_key(lines, dotted)
    if idx is None:
        return text
    del lines[idx:configfile._block_end(lines, idx, indent)]
    # A parent left with no children would parse as null and wipe the inherited block, so remove it too.
    parts = dotted.split(".")[:-1]
    while parts:
        pidx, pindent = configfile._find_key(lines, ".".join(parts))
        if pidx is None or configfile._block_end(lines, pidx, pindent) != pidx + 1 \
                or configfile._KEY_RE.match(lines[pidx]).group("rest").strip() not in ("", "{}"):
            break
        del lines[pidx]
        parts.pop()
    return newline.join(lines)


def _plan(p, changes: Dict[str, Any], scope: str, remove: bool = False, expected: Optional[str] = None) -> Dict[str, Any]:
    if scope not in ("project", "machine"):
        raise ResearchError("scope must be 'project' or 'machine'")
    errors: Dict[str, str] = {}
    norm: Dict[str, Any] = {}
    opts: Dict[str, Option] = {}
    for k, v in changes.items():
        o = get(k)
        opts[k] = o
        if scope not in o.scopes:
            errors[k] = f"{o.label} cannot be set at the {scope} level."
            continue
        if remove:
            norm[k] = None
            continue
        ok, nv, err = validate(k, v)
        if ok:
            norm[k] = nv
        else:
            errors[k] = err or "Invalid value."
    text, path = _layer_text(p, scope)
    if expected and _digest(text) != expected:
        raise ResearchError(f"{os.path.basename(path)} changed on disk since the editor loaded it.",
                            hint="Reload the settings to see the current file, then repeat the change. Nothing was written.")
    new = text
    if not errors:
        if remove:
            for k in norm:
                new = remove_key(new, k)
        elif not configfile.editable(text) and scope == "project":
            raise ResearchError("config.yaml uses legacy flow-style YAML, which cannot be edited line by line.",
                                hint="Rewrite config.yaml in block style (one `key: value` per line), then retry.")
        else:
            new = configfile.set_keys(text, norm) if text else configfile.set_keys("", norm)
    diff = "".join(difflib.unified_diff(text.splitlines(True), new.splitlines(True), "current", "proposed")) if new != text else ""
    before = {k: describe_value(p, k) for k in changes}
    risky = [k for k, o in opts.items() if o.risk == "risky"]
    policy = [k for k, o in opts.items() if o.risk in ("policy", "risky")]
    return {"ok": not errors, "errors": errors, "values": norm, "scope": scope, "path": path, "diff": diff,
            "changed": new != text, "before": before, "needs_confirmation": bool(risky), "policy_keys": policy, "risky_keys": risky,
            "restart": any(o.restart for o in opts.values()),
            "affects": sorted({a for o in opts.values() for a in o.affects} | {".research/context/policy.md"}),
            "_old": text, "_new": new}


def describe_value(p, key: str) -> Any:
    present, v = configfile._get(p.config, key)
    return v if present else _default_of(key)


def preview(p, changes: Dict[str, Any], scope: str = "project", remove: bool = False, expected: Optional[str] = None) -> Dict[str, Any]:
    r = _plan(p, changes, scope, remove, expected)
    r.pop("_old"), r.pop("_new")
    if _is_agent(p) and r["policy_keys"]:
        r["blocked_for_agents"] = r["policy_keys"]
    return r


def apply(p, changes: Dict[str, Any], scope: str = "project", reason: Optional[str] = None, confirm: bool = False,
          remove: bool = False, expected: Optional[str] = None, pin: Optional[str] = None) -> Dict[str, Any]:
    plan = _plan(p, changes, scope, remove, expected)
    if not plan["ok"]:
        raise ResearchError("; ".join(f"{k}: {e}" for k, e in plan["errors"].items()))
    if plan["policy_keys"] and _is_agent(p):
        raise ResearchError("Agents cannot change project policy settings: " + ", ".join(plan["policy_keys"]),
                            hint="Ask the human to change it (Settings in the panel, or `research config set`).")
    if plan["policy_keys"] and plan["changed"]:
        from . import approval
        approval.confirm(p, "Change policy " + ", ".join(plan["policy_keys"]), reason, pin, rationale_required=False)
    if plan["risky_keys"] and not confirm:
        raise ResearchError("Confirmation required for: " + ", ".join(plan["risky_keys"]),
                            hint="Review `research config set --dry-run`, then repeat with --confirm.")
    if not plan["changed"]:
        return {"changed": False, "values": plan["values"], "scope": scope}
    from .store import _atomic_write
    with p.tx():
        _atomic_write(plan["path"], plan["_new"]) if os.path.isdir(os.path.dirname(plan["path"])) else _write_new(plan["path"], plan["_new"])
        verb = "reset" if remove else "set"
        for k, v in plan["values"].items():
            old = plan["before"].get(k)
            p.event("config", k, "changed", f"{k} {verb}: {old!r} → "
                    + ("inherited default" if remove else repr(v)) + f" ({scope})" + (f" — {reason}" if reason else ""))
    p.config = p.load_config()
    write_policy(p)
    from . import autopromote as _AP
    _AP.on_settings_changed(p, set(plan["values"]))      # the standing authorization follows a human's change made here, never an edit made around it
    return {"changed": True, "values": plan["values"], "scope": scope, "diff": plan["diff"], "path": plan["path"],
            "policy": ".research/context/policy.md", "fingerprint": fingerprint(p), "digests": file_digests(p)}


def _write_new(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def set_assignment(p, kind: str, ident: str, profile: str) -> None:
    """Assign a sandbox profile to one experiment or task (`sandbox.assign.<kind>.<id>`). A policy change: humans only, audited."""
    if (p.author or {}).get("author_type") == "agent":
        raise ResearchError("Agents cannot assign sandbox profiles", hint="Ask the human.")
    from . import sandbox as SB
    SB.profile(p, profile)
    p.save_config({f"sandbox.assign.{kind}.{ident}": profile})
    p.config = p.load_config()
    p.event("config", f"sandbox.assign.{kind}.{ident}", "changed", f"{kind[:-1]} {ident} → sandbox profile {profile}")
    write_policy(p)


def reset(p, keys: List[str], scope: str = "project", reason: Optional[str] = None) -> Dict[str, Any]:
    return apply(p, {k: None for k in keys}, scope=scope, reason=reason, remove=True)


# ------------------------------------------------------------------------------------------------ agent-readable policy
def _fmt(v: Any) -> str:
    return "—" if v is None else ("on" if v is True else "off" if v is False else json.dumps(v) if isinstance(v, (list, dict)) else str(v))


def render_policy(p) -> str:
    from . import gitinfo, workspace
    d = describe(p)
    L = ["# Effective policy",
         "",
         "> Generated by Research OS from the project configuration. Do not edit; change settings with `research config set` "
         "or the Settings page. Agents cannot change policy settings.",
         "",
         f"Settings fingerprint: `{d['fingerprint']}` (a dispatch records the fingerprint it started under; a later change does "
         "not alter a running session's recorded policy).",
         ""]
    by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for x in d["options"]:
        by_cat.setdefault(x["category"], []).append(x)
    for cid, label in CATEGORIES:
        rows = by_cat.get(cid)
        if not rows or cid in ("hpc", "data"):
            continue
        L += [f"## {label}", ""]
        for x in rows:
            mark = "" if x["source"] == "default" else f" _(set at {x['source']} level)_"
            L.append(f"- **{x['label']}** (`{x['key']}`): {_fmt(x['value'])}{mark} — {x['help']}")
        L.append("")
    from . import governance
    gv = governance.cfg(p)
    L += ["## Restrictions on you (agents)", "",
          f"- You may NOT create experiments (policy: {gv['experiments']['agent_creation']}). Submit one with `research proposal experiment --title … --hypothesis … --rationale … --method … --expected-outputs … --related …`.",
          f"- Tasks: policy {gv['tasks']['agent_creation']}. Under bounded_auto you may add only {', '.join(gv['tasks']['allowed_auto_types'])} tasks, inside your own experiment, at most {gv['tasks']['max_auto_per_experiment']}. Anything else: `research agent propose`.",
          "- You may NOT approve proposals, change policy, or promote work to main. These need the researcher's PIN; do not ask for it, do not try `--as human`.",
          "- If a command is refused by policy, submit a proposal and carry on with what remains.", ""]
    info = workspace.info(p)
    g = gitinfo.info(p.root)
    L += ["## Verified now", "",
          "These are facts observed when this file was generated, not settings.", "",
          f"- Project layout: {info['mode']}" + (f" (ledger at `{info['ledger_root']}`)" if info["mode"] != "classic" else ""),
          f"- Git: {'available' if gitinfo.available() else 'not found'}; branch {g.get('branch') or '—'}"
          + (f"; workspace {info['workspace']}" if info.get("workspace") else ""),
          ]
    try:
        from . import hooks
        hl = hooks.listing(p)
        L.append(f"- Hooks: {sum(1 for h in hl if h.get('approved'))} approved of {len(hl)} configured")
    except Exception:
        pass
    L += ["", "Anything not listed here has no special project rule beyond `AGENTS.md`.", ""]
    return "\n".join(L)


def write_policy(p) -> str:
    path = p.path("context", "policy.md")
    text = render_policy(p)
    old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
    strip = lambda s: "\n".join(l for l in (s or "").splitlines() if not l.startswith("- Git: "))
    if strip(old) != strip(text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        from .store import _atomic_write
        _atomic_write(path, text)
    return path
