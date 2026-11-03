"""Typed configuration registry: what each setting is, how to validate it, how to explain it, and how to change it.

The YAML layers stay canonical (defaults < machine file < project file; see configfile.py). This module adds the
metadata the editor and the CLI need, so validation, help text and approval rules live in ONE place:

    describe(p)        every option with its effective value, default, source layer and help
    preview(p, ...)    validate a change and show the exact YAML diff, without writing
    apply(p, ...)      validate, enforce approval rules, write surgically, audit, regenerate agent context
    reset(p, ...)      remove an override so the inherited value applies again

Defaults are NOT repeated here: they are read from `store.DEFAULT_CONFIG`, so a default can never disagree with the code.
Anything the registry does not describe keeps working and stays editable in YAML; it is simply not offered as a form.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from . import configfile, yamlio
from .util import ResearchError, now_iso

CATEGORIES = [
    ("project", "Project"), ("policy", "Research policy"), ("verification", "Verification"),
    ("agents", "Agents & orchestration"), ("hooks", "Hooks & skills"), ("git", "Git & workspaces"), ("environment", "Python environment"), ("supervisor", "Supervisor"),
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
        self.choices, self.min, self.max, self.nullable = choices, min_, max_, nullable
        self.affects = affects or []
        self.scopes, self.restart, self.depends_on, self.doc, self.unit = scopes, restart, depends_on, doc, unit
        self.advanced = advanced

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
    _o("agents.default_profile", "str", "agents", "Default agent CLI", "Which agent profile (claude, codex, gemini, or one you define) `research dispatch` starts when a role names none.",
       why="Lets you hand work to your usual agent without choosing each time.", effect="Only affects new dispatches. The CLI must be installed on the machine that runs it."),
    _o("orchestration.enabled", "bool", "agents", "Model routing", "Route work to cheaper or stronger models by level (fast, balanced, deep) instead of one model for everything.",
       why="Spends the expensive model only where it matters.", effect="Off: dispatch uses the default profile. Run `research setup agents --apply` to refresh generated agent files.",
       affects=[".claude/agents/"]),
    _o("orchestration.active_strategy", "str", "agents", "Allocation strategy", "Which named strategy maps task levels to models (for example sonnet_first or opus_first). Empty uses the legacy tier routing.",
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
    _o("skills.link", "list", "hooks", "Expose skills to", "Which agent CLIs see the project's skills (`claude` → .claude/skills, `agents` → .agents/skills).", choices=["claude", "agents"],
       why="Skills only help agents that can find them.", effect="Changing it creates or removes links on the next skills refresh.", affects=[".claude/skills", ".agents/skills"]),

    _o("git.commit_checkpoints", "bool", "git", "Commit every checkpoint", "Creating a checkpoint also commits `.research/` to git (never pushes, and only `.research/`).",
       why="Puts research history into git history automatically.", effect="Off: checkpoints are files only; commit them yourself or use `checkpoint create --commit`.",
       affects=["git history"]),

    _o("environment.python", "str", "environment", "Project Python", "The Python used on this machine by agents, task checks and `{python}` in check commands: a venv folder or an interpreter path (relative paths are under the project folder). Empty uses whatever `python3` is on PATH.",
       nullable=True, why="Without it, an agent's `python3` and your checks may use an interpreter that lacks your packages.", effect="Takes effect for the next agent session and check run.", affects=[".research/bin/py"]),
    _o("environment.packages", "list", "environment", "Packages the project needs", "Importable package names that `research env check` verifies (for example numpy, matplotlib).",
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
    _o("supervisor.on_failure", "enum", "supervisor", "When verification fails", "diagnose_and_repair dispatches a bounded repair attempt; pause stops the plan for you to look; stop ends the run.",
       choices=["diagnose_and_repair", "pause", "stop"], why="Engineering failures are usually fixable without you; scientific ones never are automatic.", effect="Applies to failures after the change.", risk="policy"),
    _o("supervisor.enforce_completion", "bool", "supervisor", "Only the supervisor completes supervised tasks", "While a plan runs, only the supervisor's verification (or a human `supervisor accept`) can mark its tasks done, by any route: CLI, MCP, UI or a hand-edited file.",
       why="An agent saying it is finished is not evidence.", effect="Off lets any actor complete supervised tasks through the ordinary gates.", risk="policy"),
    _o("supervisor.auto_apply_operational", "bool", "supervisor", "Auto-apply operational plan changes", "Agent proposals that only add new tasks (diagnostics, follow-ups) are applied without asking, up to the limit below.",
       why="Lets agents add a missing diagnostic step without stalling the plan.", effect="Off: every proposal waits for you.", risk="policy"),
    _o("supervisor.max_auto_tasks", "int", "supervisor", "Auto-added task limit", "Most tasks agents may add to a plan through auto-applied proposals.", why="Bounds uncontrolled plan growth.",
       effect="Further proposals wait for approval.", min_=0, max_=200, risk="policy"),
    _o("supervisor.require_approval_for_scientific_changes", "bool", "supervisor", "Approve scientific changes", "Proposals that change hypotheses, methods, checks, acceptance, parameters or existing task definitions always wait for you.",
       why="Science is the researcher's decision.", effect="Turning it off lets proposals of any kind apply automatically (not recommended).", risk="policy"),
    _o("supervisor.session_resume", "enum", "supervisor", "Continue the agent's own session", "auto continues the provider's native conversation for repairs when the installed CLI proves it can; never always starts fresh with a generated hand-off.",
       choices=["auto", "never"], why="Native sessions keep context cheaply; hand-offs work everywhere.", effect="Applies to later repair attempts.", advanced=True),
    _o("supervisor.context_max_chars", "int", "supervisor", "Prompt context size", "Upper bound for the context the supervisor assembles for an agent attempt (project docs, objectives, prerequisites, history).",
       why="Selective context beats dumping the ledger into every prompt.", effect="Larger limits cost more tokens per attempt.", min_=2000, max_=400000, unit="characters", advanced=True),
    _o("supervisor.commit_verified_work", "bool", "supervisor", "Commit verified work on the experiment branch", "When a task verifies, its changes are committed on the experiment's own branch (never main, never pushed) so they can be promoted and traced to the task.",
       why="Promotion works on commits; agents often leave their work uncommitted.", effect="Off: you commit yourself before preparing a promotion.", risk="policy"),
    _o("supervisor.commit_exclude", "list", "supervisor", "Paths kept out of those commits", "Folders (relative to the worktree) the supervisor never commits, such as bulky results.",
       why="Code history should not carry data and figures.", effect="Applies to later verifications.", advanced=True),
    _o("supervisor.hpc_poll_seconds", "int", "supervisor", "HPC polling interval", "How often waiting computational runs are reconciled with the cluster. Unavailable status never causes a resubmission.",
       why="Be gentle with login nodes.", effect="Applies on the next cycle.", min_=5, max_=3600, unit="seconds", advanced=True),
    _o("supervisor.max_hpc_resubmissions", "int", "supervisor", "Computational resubmissions", "How many extra runs an agent may submit per task after failures before the task is blocked.",
       why="Failed jobs cost allocation; repeats must be bounded.", effect="Further failures block the task.", min_=0, max_=20, risk="policy"),

    _o("promotion.main_branch", "str", "git", "Main branch", "The branch that promotions are prepared from and merged into.",
       why="Candidates start from the current tip of this branch so reviewed code is exactly what lands.", effect="Changing it affects only promotions prepared afterwards.", risk="policy"),
    _o("promotion.test_command", "str", "git", "Promotion test command", "The command run in a promotion candidate (for example `pytest -q`). Its result is bound to the candidate's exact commit; without it nothing can be verified or approved.",
       nullable=True, why="Integration should only receive code that passes the project's own checks.", effect="Runs a shell command with your permissions: set only commands you would run yourself.", risk="policy"),
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
          remove: bool = False, expected: Optional[str] = None) -> Dict[str, Any]:
    plan = _plan(p, changes, scope, remove, expected)
    if not plan["ok"]:
        raise ResearchError("; ".join(f"{k}: {e}" for k, e in plan["errors"].items()))
    if plan["policy_keys"] and _is_agent(p):
        raise ResearchError("Agents cannot change project policy settings: " + ", ".join(plan["policy_keys"]),
                            hint="Ask the human to change it (Settings in the panel, or `research config set`).")
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
