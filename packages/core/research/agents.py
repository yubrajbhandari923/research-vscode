"""Agent roles, profiles and dispatch (human-in-the-loop orchestration).

config.yaml:
    agents:
      default_profile: claude
      roles: {planner: null, implementer: codex, verifier: claude, analyst: null}   # null → default_profile
      profiles:
        claude: {command: claude, args: [], model: null, model_flag: --model, prompt_flag: null}
        codex:  {command: codex,  args: [], model: null, model_flag: -m,      prompt_flag: null}

`dispatch(role, target)` writes a brief to .research/cache/dispatch/ and returns the argv + env that start the
profile's agent CLI on it. The CLI runs it in the current terminal; VS Code opens a terminal. The env marks the
session (RESEARCH_AGENT=<profile>, RESEARCH_AGENT_ROLE=<role>), so everything it records is attributed to e.g.
`claude/verifier`, and an agent can't review its own findings under the same role.
"""
from __future__ import annotations

import os
import shlex
import shutil
import time
from typing import Any, Dict, List, Optional

from .schema import normalize_id, type_of
from .store import Project, _atomic_write
from .util import ResearchError, as_list

ROLES = ["planner", "implementer", "verifier", "analyst"]

_ROLE_GUIDE = {
    "planner": """You are the PLANNER. Turn the objective into a plan of small, verifiable tasks. Do not implement or run anything.

1. Read the project state (below, or `research current`) and search for prior work: `research search "…"`.
2. Create the plan: `research plan create "<title>" -o "<objective>" --success "<how we know it worked>" [-q Q-…]`.
3. Add tasks in order, each one small enough for one agent session:
   `research task create PLAN-… "<title>" -g "<goal>" --type <implementation|experiment|analysis|verification|…> \\
      --acceptance "<observable criteria>" --depends T-… --check "cmd:pytest -q" --check "file:…" --check "metric:EXP-…:name<=x" \\
      --skill <relevant project skill>`
   Give every task checks where possible: they gate completion.
4. Register the experiments the plan needs (`research experiment create …`) and link them to tasks (`-e EXP-…`).
5. Stop. Summarise the plan in a progress note on its first task (`research task note T-… "…"`).""",
    "implementer": """You are the IMPLEMENTER for exactly one task. Stay inside its scope.

1. Claim it: `research task start {id}` (fails if another agent holds it — then stop and say so).
2. Load the skills listed below with `research skill show NAME` before starting.
3. Do the work. Every computation is a run of an experiment: `research run exec EXP-… -- <command>`;
   log metrics/artifacts from code with `research.log_metric(...)` / `research.register_artifact(...)`.
4. Leave a trail as you go: `research task note {id} "what I did / found / what is left"`.
5. Record reusable results as findings with evidence (`research finding create "…" --supports EXP-… RUN-… A-…`).
   They stay preliminary until someone else reviews them; do not try to mark them supported.
6. Finish: `research task done {id} -r "<result>"` — this runs the task's checks; fix failures until they pass.
   If you can't finish: `research task block {id} "<why>"` or `research task release {id} "<handoff note>"`.""",
    "verifier": """You are the VERIFIER. Your job is to check work against its acceptance criteria, not to produce new results.

1. Run the task's checks: `research task verify {id}`.
2. Inspect its outputs against the acceptance criteria and the verification notes below (open files, plots, logs).
3. Record what you checked: `research task note {id} "…"`.
4. If it holds up: `research task done {id} -r "verified: …"`; otherwise
   `research task update {id} --status running --blockers "<what is wrong>"`.
Review any findings the task produced the same way (`research finding review F-… --verdict …`).
Do not edit the work you are reviewing.""",
    "verifier:finding": """You are the VERIFIER. Your job is to check a claim against its evidence, not to produce new results.

1. Open the evidence: `research show {id}`, then every supporting run, artifact and plot it cites.
2. Ask: does the evidence actually support the statement? Is the interpretation overreaching? What confounders,
   missing controls or alternative explanations remain? Is the confidence level justified?
3. Record ONE verdict with your reasoning:
   `research finding review {id} --verdict supported|contradicted|needs_work --notes "<reasoning>" [--confidence low|medium|high]`
Do not edit the finding or its evidence.""",
    "analyst": """You are the ANALYST. Turn finished runs into understanding.

1. Read the experiment (`research experiment show {id}`), its runs, metrics and plots; compare runs with
   `research run compare RUN-… RUN-…` where useful.
2. Synthesize: `research experiment synthesize {id} --happened … --worked … --failed … --interpretation … --next …`.
3. Promote only reusable conclusions to findings, each with evidence (runs, artifacts, plots) and limitations.
   Record dead ends as `--kind failure`. Findings stay preliminary until reviewed.
4. Suggest (don't launch) the next experiment in the synthesis.""",
}


def config(p: Project) -> Dict[str, Any]:
    return p.config.get("agents") or {}


def profile_for(p: Project, role: Optional[str] = None, profile: Optional[str] = None) -> (str, Dict[str, Any]):
    cfg = config(p)
    name = profile or (cfg.get("roles") or {}).get(role or "") or cfg.get("default_profile") or "claude"
    prof = (cfg.get("profiles") or {}).get(name)
    if not prof:
        raise ResearchError(f"Unknown agent profile {name!r}",
                            hint="Profiles are defined in .research/config.yaml under agents.profiles.")
    return name, prof


def list_profiles(p: Project) -> Dict[str, Any]:
    cfg = config(p)
    profiles = []
    for name, prof in (cfg.get("profiles") or {}).items():
        cmd = (prof or {}).get("command") or name
        profiles.append({"name": name, "command": cmd, "model": (prof or {}).get("model"),
                         "available": bool(shutil.which(cmd))})
    roles = {r: (cfg.get("roles") or {}).get(r) or cfg.get("default_profile") for r in ROLES}
    return {"default_profile": cfg.get("default_profile"), "roles": roles, "profiles": profiles}


def default_role(p: Project, target: Optional[str]) -> str:
    if not target:
        return "planner"
    t = type_of(target)
    if t == "finding":
        return "verifier"
    if t == "experiment":
        return "analyst"
    if t == "plan":
        return "planner"
    if t == "task":
        task = p.get("task", normalize_id(target, "T"))
        if task["status"] == "verify" or task.get("task_type") == "verification":
            return "verifier"
        if task.get("assigned_role") in ROLES:
            return task["assigned_role"]
        return "implementer"
    return "analyst"


def task_brief_md(c: Dict[str, Any]) -> str:
    t = c["task"]
    L = [f"## Task {t['id']} · {t['title']}  [{t['status']}{', READY' if c['is_ready'] else ''}]", ""]
    for k, label in (("task_type", "Type"), ("assigned_role", "Role")):
        if t.get(k):
            L.append(f"- **{label}:** {t[k]}")
    if c.get("claimed_by"):
        L.append(f"- **Claimed by:** {c['claimed_by']}")
    if c["plan"]:
        pl = c["plan"]
        L += ["", f"### Plan {pl['id']} · {pl['title']}", ""]
        for k, label in (("objective", "Objective"), ("success_criteria", "Success criteria"), ("context", "Context")):
            if pl.get(k):
                L.append(f"**{label}:** {pl[k]}")
    for k, label in (("goal", "Goal"), ("inputs", "Inputs"), ("expected_outputs", "Expected outputs"),
                     ("acceptance_criteria", "Acceptance criteria"), ("verification", "Verification"),
                     ("result", "Result so far"), ("blockers", "Blockers"), ("notes", "Notes")):
        if t.get(k):
            L += ["", f"### {label}", "", str(t[k])]
    if c.get("checks"):
        L += ["", "### Checks (run by `research task done` / `research task verify`)", ""]
        L += [f"- {x}" for x in c["checks"]]
        if c.get("last_verification"):
            v = c["last_verification"]
            L.append(f"- last run {v['id']}: **{v['verdict']}** — {v['summary']}")
    if c["dependencies"]:
        L += ["", "### Dependencies", ""]
        L += [f"- {d['id']} {d['title']} [{d['status']}]" + (f" — result: {d['result']}" if d.get("result") else "")
              for d in c["dependencies"]]
    if c.get("question"):
        q = c["question"]
        L += ["", f"### Question {q['id']} · {q['title']} [{q['status']}]"] + (["", q["description"]] if q.get("description") else [])
    if c.get("experiment"):
        e = c["experiment"]
        s = e["state"]
        L += ["", f"### Experiment {e['id']} · {e['title']} [{e['status']}]", ""]
        for k in ("hypothesis", "parameters", "success_criteria", "stop_conditions"):
            if e.get(k):
                L.append(f"- **{k.replace('_', ' ')}:** {e[k]}")
        L.append(f"- **runs:** {s['runs']} ({s['unsynthesized']} unsynthesized"
                 + (", RUN BUDGET REACHED — synthesize first" if s["over_run_budget"] else "") + ")")
        syn = e.get("latest_synthesis")
        if syn:
            L.append(f"- **last synthesis:** {syn.get('interpretation') or ''}" + (f" · next: {syn['next_experiment']}" if syn.get("next_experiment") else ""))
    if c.get("related_findings"):
        L += ["", "### Related findings", ""] + [f"- {f['id']} [{f['status']}] {f['title']}" for f in c["related_findings"]]
    if c.get("skills"):
        L += ["", "### Skills to load (`research skill show NAME`)", ""]
        L += [f"- **{s['name']}** ({s['why']}){' — ' + s['description'][:160] if s.get('description') else ''}"
              + (" — NOT INSTALLED" if s.get("missing") else "") for s in c["skills"]]
    if c.get("progress"):
        L += ["", "### Progress notes (newest first)", ""]
        L += [f"- {n['ts'][:16]} {n.get('author_name') or n.get('author_type')}: {n['text']}" for n in c["progress"]]
    pol = c["policy"]
    L += ["", "### Rules", "",
          f"- Run budget: synthesize after {pol.get('max_runs_without_synthesis')} runs; review after "
          f"{pol.get('max_failed_runs_without_review')} failed runs (exit code 3 = blocked by policy).",
          "- Findings stay preliminary until an independent review. Never rewrite history; supersede instead."]
    return "\n".join(L)


def build_brief(p: Project, role: str, target: Optional[str] = None, objective: Optional[str] = None,
                instructions: Optional[str] = None) -> str:
    from . import context as C
    from . import plans as P
    from .views import show
    tid = None
    L = [f"# Research brief — {role}", "",
         f"Project: **{p.name}** ({p.root}). Use the `research` CLI (or the `research` MCP tools) for every "
         "record you create; read AGENTS.md if anything is unclear.", ""]
    if target:
        tid = normalize_id(target)
    guide = _ROLE_GUIDE.get(f"{role}:{type_of(tid)}" if tid else role) or _ROLE_GUIDE[role]
    L += ["## Your role", "", guide.replace("{id}", tid or "<ID>"), ""]
    if objective:
        L += ["## Objective", "", objective.strip(), ""]
    if instructions:
        L += ["## Extra instructions from the human", "", instructions.strip(), ""]
    if tid and type_of(tid) == "task":
        L += [task_brief_md(P.task_context(p, tid)), ""]
    elif tid:
        d = show(p, tid)
        L += [f"## Target {tid} · {d.get('title') or d.get('name') or ''}", "",
              f"Inspect it with `research show {tid}`" + (" (and `research experiment show`)" if tid.startswith("EXP") else "") + ".", ""]
        if tid.startswith("F-"):
            L += [f"- **Statement:** {d.get('statement')}", f"- **Status:** {d.get('status')} · **confidence:** {d.get('confidence')}",
                  f"- **Author:** {d.get('author_name') or d.get('author_type')}",
                  "- **Supporting evidence:** " + (", ".join(b["id"] for b in d["evidence"]["supports"]) or "none"),
                  "- **Contradicting evidence:** " + (", ".join(b["id"] for b in d["evidence"]["contradicts"]) or "none"),
                  f"- **Limitations:** {d.get('limitations') or '—'}", ""]
    L += ["## Current project state", "", C.render_current_md(p)]
    return "\n".join(L)


def dispatch(p: Project, role: Optional[str] = None, target: Optional[str] = None, objective: Optional[str] = None,
             profile: Optional[str] = None, model: Optional[str] = None, instructions: Optional[str] = None) -> Dict[str, Any]:
    """Prepare an agent session: write the brief, build argv + env. Does not start anything."""
    role = role or default_role(p, target)
    if role not in ROLES:
        raise ResearchError(f"role must be one of {', '.join(ROLES)}")
    if role == "planner" and not (objective or target):
        raise ResearchError("The planner needs an objective (--objective \"…\") or a plan id")
    name, prof = profile_for(p, role, profile)
    brief = build_brief(p, role, target, objective, instructions)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    label = normalize_id(target) if target else role
    path = p.path("cache", "dispatch", f"{label.replace('/', '-')}-{role}-{stamp}.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _atomic_write(path, brief)
    rel = p.rel(path)
    prompt = (f"You are the {role} on this research project. Read the brief at {rel} and follow it exactly. "
              "Use the `research` CLI for every record.")
    cmd = prof.get("command") or name
    argv: List[str] = [cmd, *[str(a) for a in as_list(prof.get("args"))]]
    mdl = model or prof.get("model")
    if mdl and prof.get("model_flag"):
        argv += [prof["model_flag"], str(mdl)]
    if prof.get("prompt_flag"):
        argv.append(prof["prompt_flag"])
    argv.append(prompt)
    env = {"RESEARCH_AGENT": name, "RESEARCH_AGENT_ROLE": role, "RESEARCH_ROOT": p.root, "RESEARCH_BRIEF": rel}
    if mdl:
        env["RESEARCH_AGENT_MODEL"] = str(mdl)
    p.event(type_of(label) if target else "agent", label if target else None, "dispatched",
            f"Dispatched {name}/{role}" + (f" on {label}" if target else "") + (f": {objective[:120]}" if objective else ""))
    return {"role": role, "profile": name, "model": mdl, "argv": argv, "env": env, "brief": rel,
            "available": bool(shutil.which(cmd)),
            "shell": " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items()) + " " + " ".join(shlex.quote(a) for a in argv)}
