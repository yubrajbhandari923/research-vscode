"""Declarative YAML research plans: validate, export, diff and apply (schema_version 1).

The YAML states the *desired* work. Runtime history (attempts, runs, results, status) stays in the ledger; applying a
plan never rewrites it. Stable user keys map to internal ids in `.research/supervisor/specs/<plan-key>.json`, which also
holds the revision counter, the last applied spec and plan-change history.

Rules enforced here (see docs/SUPERVISOR.md):
  * unknown fields and newer schema versions are rejected (keys starting `x_` are kept and ignored);
  * a partial document never implies deletion; only `scope: full` retires unstarted tasks that are missing;
  * running tasks keep their active snapshot: requested changes are deferred to the next attempt;
  * done tasks are never changed or retired; a stale `revision` is a conflict.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from . import yamlio
from .store import Project, _atomic_write
from .util import ResearchError, now_iso

SCHEMA_VERSION = 1
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

PLAN_FIELDS = {"key", "id", "title", "objective", "success_criteria", "context", "question", "execution", "experiments",
               "tasks", "scope", "revision", "skills"}
EXEC_FIELDS = {"mode", "max_parallel_agents", "max_attempts_per_task", "max_total_attempts", "max_wall_minutes", "on_failure",
               "require_approval_for_scientific_changes", "default_profile", "max_hpc_resubmissions", "auto_promotion", "escalation"}
EXP_FIELDS = {"key", "id", "title", "hypothesis", "motivation", "method", "success_criteria", "expected_outcome", "stop_conditions",
              "parameters", "metrics", "expected_artifacts", "planned_runs", "question", "tasks", "notes", "tags"}
TASK_FIELDS = {"key", "id", "title", "description", "prompt", "depends_on", "agent", "inputs", "expected_artifacts", "acceptance",
               "checks", "resources", "execution", "task_type", "skills", "datasets", "allowed_files", "notes"}
AGENT_FIELDS = {"role", "profile", "model", "provider", "session_group", "level"}
RES_FIELDS = {"backend", "cpus", "time", "mem", "gpus", "partition", "account", "cluster"}
TASK_EXEC_FIELDS = {"max_attempts", "requires_approval", "workspace", "timeout_minutes", "on_failure"}
ROLES = ("planner", "implementer", "verifier", "analyst", "human")
ON_FAILURE = ("diagnose_and_repair", "pause", "stop")
MODES = ("bounded_autonomous", "manual")

# Fields whose change alters the scientific meaning of already-defined work (see supervisor.classify_change).
SCIENTIFIC_PLAN = {"objective", "success_criteria"}
SCIENTIFIC_EXP = {"hypothesis", "method", "success_criteria", "expected_outcome", "stop_conditions", "parameters", "metrics", "question"}
SCIENTIFIC_TASK = {"acceptance", "checks", "expected_artifacts", "datasets", "inputs", "depends_on"}


class PlanSpecError(ResearchError):
    """The document is invalid; `errors` lists every problem found (nothing is applied)."""

    def __init__(self, errors: List[str]):
        super().__init__("Invalid research plan:\n  - " + "\n  - ".join(errors), hint="Fix the listed problems; nothing was changed.")
        self.errors = errors


# ----------------------------------------------------------------------------------------------- storage
def spec_dir(p: Project) -> str:
    d = p.path("supervisor", "specs")
    os.makedirs(d, exist_ok=True)
    return d


def _spec_path(p: Project, plan_key: str) -> str:
    return os.path.join(spec_dir(p), plan_key + ".json")


def load_state(p: Project, plan_key: str) -> Optional[Dict[str, Any]]:
    try:
        with open(_spec_path(p, plan_key), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def save_state(p: Project, st: Dict[str, Any]) -> None:
    _atomic_write(_spec_path(p, st["plan_key"]), json.dumps(st, indent=1, sort_keys=True, default=str) + "\n")


def all_states(p: Project) -> List[Dict[str, Any]]:
    d = spec_dir(p)
    return [json.load(open(os.path.join(d, n), encoding="utf-8")) for n in sorted(os.listdir(d)) if n.endswith(".json")]


def find_state_for_plan(p: Project, plan_id: str) -> Optional[Dict[str, Any]]:
    return next((s for s in all_states(p) if s.get("plan_id") == plan_id), None)


def find_task_key(p: Project, task_id: str) -> Optional[Tuple[Dict[str, Any], str]]:
    for s in all_states(p):
        for k, v in (s.get("tasks") or {}).items():
            if v == task_id:
                return s, k
    return None


def _hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ----------------------------------------------------------------------------------------------- parsing & validation
def parse(text: str) -> Dict[str, Any]:
    try:
        doc = yamlio.load(text)
    except Exception as e:  # the vendored loader raises its own error types
        raise PlanSpecError([f"not valid YAML: {str(e).splitlines()[0]}"])
    if not isinstance(doc, dict):
        raise PlanSpecError(["the document must be a mapping with schema_version and plans"])
    return doc


def _unknown(d: Dict[str, Any], allowed: set, where: str, errs: List[str]) -> None:
    for k in d:
        if k not in allowed and not str(k).startswith("x_"):
            errs.append(f"{where}: unknown field {k!r}")


def _list(v: Any) -> List[Any]:
    return [] if v is None else v if isinstance(v, list) else [v]


def normalize(doc: Dict[str, Any], known: Optional[Any] = None) -> Dict[str, Any]:
    """Validate and return a normalized copy. Raises PlanSpecError listing every problem."""
    errs: List[str] = []
    ver = doc.get("schema_version")
    if ver is None:
        errs.append("schema_version is required (use 1)")
    elif not isinstance(ver, int) or ver > SCHEMA_VERSION:
        errs.append(f"schema_version {ver!r} is not supported by this Research OS (supports up to {SCHEMA_VERSION}); upgrade, do not edit it down")
    elif ver < 1:
        errs.append("schema_version must be >= 1")
    _unknown(doc, {"schema_version", "project", "plans", "generated_by"}, "document", errs)
    proj = doc.get("project") or {}
    if not isinstance(proj, dict):
        errs.append("project must be a mapping")
        proj = {}
    _unknown(proj, {"context", "instructions"}, "project", errs)
    out: Dict[str, Any] = {"schema_version": SCHEMA_VERSION, "project": {"context": [str(x) for x in _list(proj.get("context"))],
                                                                       "instructions": proj.get("instructions")}, "plans": []}
    plans = doc.get("plans")
    if not isinstance(plans, list) or not plans:
        errs.append("plans must be a non-empty list")
        plans = []
    seen_plans = set()
    for pi, pl in enumerate(plans):
        where = f"plans[{pi}]"
        if not isinstance(pl, dict):
            errs.append(f"{where}: must be a mapping")
            continue
        _unknown(pl, PLAN_FIELDS, where, errs)
        key = pl.get("key")
        if not isinstance(key, str) or not KEY_RE.match(key):
            errs.append(f"{where}: key is required (letters, digits, _ . -)")
            continue
        where = f"plan {key}"
        if key in seen_plans:
            errs.append(f"{where}: duplicate plan key")
        seen_plans.add(key)
        if not pl.get("title"):
            errs.append(f"{where}: title is required")
        ex = pl.get("execution") or {}
        _unknown(ex, EXEC_FIELDS, where + ".execution", errs)
        if ex.get("mode", "bounded_autonomous") not in MODES:
            errs.append(f"{where}.execution.mode must be one of {', '.join(MODES)}")
        if ex.get("on_failure", "diagnose_and_repair") not in ON_FAILURE:
            errs.append(f"{where}.execution.on_failure must be one of {', '.join(ON_FAILURE)}")
        for k in ("max_parallel_agents", "max_attempts_per_task", "max_total_attempts", "max_wall_minutes", "max_hpc_resubmissions"):
            if k in ex and (not isinstance(ex[k], int) or isinstance(ex[k], bool) or ex[k] < (0 if k == "max_hpc_resubmissions" else 1)):
                errs.append(f"{where}.execution.{k} must be a positive integer")
        esc = ex.get("escalation")
        if esc is not None:
            if not isinstance(esc, dict) or set(esc) - {"on", "model", "effort", "profile", "max_escalated_attempts"}:
                errs.append(f"{where}.execution.escalation must be a mapping with only on, model, effort, profile, max_escalated_attempts")
            elif not any(esc.get(k) for k in ("model", "effort", "profile")):
                errs.append(f"{where}.execution.escalation needs at least one of model, effort, profile")
            elif esc.get("on") is not None and (not isinstance(esc["on"], list) or any(x not in ("incomplete_deliverable", "incorrect_implementation", "ambiguous_specification", "insufficient_test_oracle", "agent_process_failure") for x in esc["on"])):
                errs.append(f"{where}.execution.escalation.on must be a list of repairable failure categories")
        if "auto_promotion" in ex and not isinstance(ex["auto_promotion"], bool):
            errs.append(f"{where}.execution.auto_promotion must be true or false (it only declares intent; a human still authorizes the run)")
        scope = pl.get("scope", "partial")
        if scope not in ("full", "partial"):
            errs.append(f"{where}.scope must be full or partial")
        norm = {"key": key, "id": pl.get("id"), "title": pl.get("title"), "objective": pl.get("objective"),
                "success_criteria": pl.get("success_criteria"), "context": pl.get("context"), "question": pl.get("question"),
                "skills": _list(pl.get("skills")), "execution": dict(ex), "scope": scope, "revision": pl.get("revision"),
                "experiments": [], "tasks": []}
        task_keys: Dict[str, str] = {}
        def add_task(t: Any, wh: str, exp_key: Optional[str]) -> None:
            if not isinstance(t, dict):
                errs.append(f"{wh}: must be a mapping")
                return
            _unknown(t, TASK_FIELDS, wh, errs)
            tk = t.get("key")
            if not isinstance(tk, str) or not KEY_RE.match(tk):
                errs.append(f"{wh}: key is required")
                return
            wh = f"task {key}/{tk}"
            if tk in task_keys:
                errs.append(f"{wh}: duplicate task key (task keys are unique within a plan)")
            task_keys[tk] = exp_key or ""
            if not t.get("title"):
                errs.append(f"{wh}: title is required")
            if not (t.get("prompt") or t.get("description")):
                errs.append(f"{wh}: prompt (what the agent must do) is required")
            ag = t.get("agent") or {}
            _unknown(ag, AGENT_FIELDS, wh + ".agent", errs)
            if ag.get("role", "implementer") not in ROLES:
                errs.append(f"{wh}.agent.role must be one of {', '.join(ROLES)}")
            rs = t.get("resources") or {}
            _unknown(rs, RES_FIELDS, wh + ".resources", errs)
            te = t.get("execution") or {}
            _unknown(te, TASK_EXEC_FIELDS, wh + ".execution", errs)
            if te.get("workspace", "experiment") not in ("experiment", "main"):
                errs.append(f"{wh}.execution.workspace must be experiment or main")
            for c in _list(t.get("checks")):
                try:
                    from .verification import parse_check
                    parse_check(c)
                except ResearchError as e:
                    errs.append(f"{wh}: bad check {c!r}: {e}")
            for a in _list(t.get("expected_artifacts")):
                if not isinstance(a, str) or os.path.isabs(a) or ".." in a.replace("\\", "/").split("/"):
                    errs.append(f"{wh}: expected_artifacts must be relative paths without '..' (got {a!r})")
            norm["tasks"].append({"key": tk, "id": t.get("id"), "experiment": exp_key, "title": t.get("title"),
                                  "prompt": (t.get("prompt") or t.get("description") or "").strip() or None,
                                  "depends_on": [str(x) for x in _list(t.get("depends_on"))],
                                  "agent": {**{"role": "implementer"}, **ag}, "inputs": t.get("inputs"),
                                  "expected_artifacts": [str(x) for x in _list(t.get("expected_artifacts"))],
                                  "acceptance": t.get("acceptance"), "checks": [c for c in _list(t.get("checks"))],
                                  "resources": dict(rs), "execution": dict(te), "task_type": t.get("task_type"),
                                  "skills": _list(t.get("skills")), "datasets": _list(t.get("datasets")),
                                  "allowed_files": [str(x) for x in _list(t.get("allowed_files"))], "notes": t.get("notes")})
        exp_keys = set()
        for ei, e in enumerate(_list(pl.get("experiments"))):
            ew = f"{where}.experiments[{ei}]"
            if not isinstance(e, dict):
                errs.append(f"{ew}: must be a mapping")
                continue
            _unknown(e, EXP_FIELDS, ew, errs)
            ek = e.get("key")
            if not isinstance(ek, str) or not KEY_RE.match(ek):
                errs.append(f"{ew}: key is required")
                continue
            if ek in exp_keys:
                errs.append(f"{where}: duplicate experiment key {ek}")
            exp_keys.add(ek)
            if not e.get("title"):
                errs.append(f"experiment {key}/{ek}: title is required")
            norm["experiments"].append({**{k: e.get(k) for k in EXP_FIELDS if k not in ("tasks",) and k in e}, "key": ek})
            for t in _list(e.get("tasks")):
                add_task(t, f"{ew}.tasks", ek)
        for t in _list(pl.get("tasks")):
            add_task(t, f"{where}.tasks", None)
        # dependency references and cycles (references may be a task key of this plan or an existing T- id)
        deps = {t["key"]: t["depends_on"] for t in norm["tasks"]}
        for tk, ds in deps.items():
            for d in ds:
                if d not in deps and not re.match(r"^T-\d+$", d) and not (known and d in known(key)):
                    errs.append(f"task {key}/{tk}: depends_on {d!r} is neither a task key of this plan nor an existing T- id")
                if d == tk:
                    errs.append(f"task {key}/{tk}: depends on itself")
        state, stack = {}, []
        def visit(n: str) -> None:
            if state.get(n) == 2:
                return
            if state.get(n) == 1:
                errs.append(f"plan {key}: dependency cycle: {' -> '.join(stack[stack.index(n):] + [n])}")
                return
            state[n] = 1
            stack.append(n)
            for d in deps.get(n, []):
                if d in deps:
                    visit(d)
            stack.pop()
            state[n] = 2
        for n in deps:
            visit(n)
        out["plans"].append(norm)
    if errs:
        raise PlanSpecError(errs)
    return out


# ----------------------------------------------------------------------------------------------- template
TEMPLATE = """# Research OS plan (schema_version 1). Edit freely, then:
#   research plan yaml validate research.yaml
#   research plan yaml diff research.yaml        # preview
#   research plan yaml apply research.yaml
#   research plan run <PLAN-ID>                  # let the supervisor execute it
schema_version: 1
project:
  context:                # files every agent prompt draws from (selected, size-limited, never the whole ledger)
    - RESEARCH.md
    - AGENTS.md
plans:
  - key: my-plan
    title: Short plan title
    objective: What this plan should establish.
    execution:
      mode: bounded_autonomous
      max_parallel_agents: 1
      max_attempts_per_task: 3
      on_failure: diagnose_and_repair      # or: pause, stop
      require_approval_for_scientific_changes: true
    experiments:
      - key: first-experiment
        title: What is compared
        hypothesis: >
          State a falsifiable expectation.
        tasks:
          - key: implement
            title: Implement the method
            agent: {role: implementer, profile: claude-balanced, session_group: main}
            prompt: |
              Say exactly what to build and where.
            checks:
              - "cmd:python3 -m pytest -q"
          - key: run
            title: Run it on the cluster
            depends_on: [implement]
            agent: {role: implementer, session_group: main}
            prompt: |
              Submit the runs with `research hpc submit` and register outputs.
            resources: {backend: slurm, cpus: 1, time: "00:05:00"}
            expected_artifacts: [results/metrics.json]
          - key: analyze
            title: Analyze and plot
            depends_on: [run]
            agent: {role: analyst, profile: claude-deep}
            prompt: |
              Compare results, plot them, report anomalies and limitations.
            expected_artifacts: [results/comparison.png]
"""


# ----------------------------------------------------------------------------------------------- export
def export(p: Project, plan_ref: str, scope: str = "full", experiment: Optional[str] = None, task: Optional[str] = None) -> str:
    """YAML for a plan registered through this engine. An experiment/task filter makes the document partial: applying it
    can never retire anything that is not in it."""
    st = _state_for(p, plan_ref)
    spec = st["spec"]
    flat = {t["key"]: t for t in spec.get("tasks_flat", [])}

    def out_task(t: Dict[str, Any]) -> Dict[str, Any]:
        d = {k: v for k, v in t.items() if k != "experiment"}
        d["id"] = st["tasks"].get(t["key"])
        if t["key"] in (st.get("retired") or {}):
            d["x_retired"] = True
        return d
    exps = []
    for e in spec.get("experiments", []):
        if experiment and e["key"] != experiment:
            continue
        ts = [out_task(t) for t in flat.values() if t.get("experiment") == e["key"] and (not task or t["key"] == task)]
        if task and not ts:
            continue
        exps.append({**e, "id": st["experiments"].get(e["key"]), "tasks": ts})
    if experiment or task:
        scope = "partial"
    pl = {k: v for k, v in spec.items() if k not in ("tasks_flat", "experiments", "tasks")}
    pl.update(id=st.get("plan_id"), revision=st["revision"], scope=scope, experiments=exps,
              tasks=[out_task(t) for t in flat.values() if not t.get("experiment") and (not experiment) and (not task or t["key"] == task)])
    doc = {"schema_version": SCHEMA_VERSION, "project": {"context": st.get("project_context") or []}, "plans": [_clean(pl)]}
    head = f"# Research OS plan {st['plan_key']} ({st.get('plan_id')}), revision {st['revision']}, exported {now_iso()}\n"
    return head + yamlio.dump(doc, block=True)


def _clean(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items() if v not in (None, [], {}) and not str(k).startswith("_")}
    if isinstance(obj, list):
        return [_clean(x) for x in obj]
    return obj


def _state_for(p: Project, ref: str) -> Dict[str, Any]:
    st = load_state(p, ref) if KEY_RE.match(ref or "") and not ref.upper().startswith("PLAN-") else None
    if st is None:
        st = find_state_for_plan(p, ref.upper() if ref and ref.upper().startswith("PLAN-") else ref)
    if st is None:
        raise ResearchError(f"{ref} is not a plan managed through YAML",
                            hint="Import one first: `research plan yaml apply plan.yaml`. Plans created by hand can be adopted by "
                                 "giving the YAML plan an `id: PLAN-NNN`.")
    return st


# ----------------------------------------------------------------------------------------------- diff / apply
def _plan_changes(p: Project, norm_plan: Dict[str, Any], st: Optional[Dict[str, Any]], exp_ids: Dict[str, str]) -> Dict[str, Any]:
    """The ordered operations needed to make the ledger match `norm_plan`, plus conflicts and warnings."""
    ops: List[Dict[str, Any]] = []
    conflicts: List[str] = []
    warnings: List[str] = []
    key = norm_plan["key"]
    old = (st or {}).get("spec") or {}
    if st is None:
        if norm_plan.get("id"):
            if not p.exists("plan", norm_plan["id"]):
                conflicts.append(f"plan {key}: id {norm_plan['id']} does not exist")
            ops.append({"op": "adopt_plan", "key": key, "id": norm_plan["id"]})
        else:
            ops.append({"op": "create_plan", "key": key, "title": norm_plan["title"]})
    else:
        rev = norm_plan.get("revision")
        if rev is not None and rev != st["revision"]:
            conflicts.append(f"plan {key}: the YAML is based on revision {rev} but the ledger is at revision {st['revision']} "
                             "(someone or the supervisor changed the plan). Re-export, merge your edits, and apply again.")
        changed = {f: norm_plan.get(f) for f in ("title", "objective", "success_criteria", "context", "skills")
                   if norm_plan.get(f) not in (None, []) and norm_plan.get(f) != old.get(f)}
        if changed:
            ops.append({"op": "update_plan", "key": key, "fields": changed})
        new_ex, old_ex = norm_plan.get("execution") or {}, old.get("execution") or {}
        ex_changed = sorted(k for k in set(new_ex) | (set(old_ex) if norm_plan.get("scope") == "full" else set()) if new_ex.get(k) != old_ex.get(k))
        # a partial patch changes only the keys it states (one without `execution` changes nothing); a full document also removes keys it no longer lists
        if ex_changed:     # concurrency, failure policy, escalation, auto_promotion: stored in the plan spec, not the ledger row
            ops.append({"op": "update_execution", "key": key, "fields": ex_changed})
    old_exps = {e["key"]: e for e in old.get("experiments", [])}
    new_exps = {e["key"]: e for e in norm_plan["experiments"]}
    for ek, e in new_exps.items():
        if ek not in old_exps:
            ops.append({"op": "create_experiment" if not e.get("id") else "adopt_experiment", "key": ek, "title": e.get("title"), "id": e.get("id")})
        else:
            changed = {f: v for f, v in e.items() if f not in ("key", "id") and v != old_exps[ek].get(f)}
            if changed:
                ops.append({"op": "update_experiment", "key": ek, "fields": changed})
    old_tasks = {t["key"]: t for t in old.get("tasks_flat", [])}
    new_tasks = {t["key"]: t for t in norm_plan["tasks"]}
    tstate = (st or {}).get("tasks") or {}
    retired = (st or {}).get("retired") or {}
    for tk, t in new_tasks.items():
        tid = tstate.get(tk) or t.get("id")
        cur = p.get("task", tid) if tid and p.exists("task", tid) else None
        if tk in retired and cur:
            ops.append({"op": "unretire_task", "key": tk, "id": tid})
        if cur is None and tk not in tstate:
            if tid:
                conflicts.append(f"task {key}/{tk}: id {tid} does not exist")
            ops.append({"op": "create_task" if not t.get("id") else "adopt_task", "key": tk, "id": t.get("id"), "title": t["title"]})
            continue
        o = old_tasks.get(tk, {})
        changed = {f: v for f, v in t.items() if f not in ("key", "id") and v != o.get(f)}
        if not changed:
            continue
        status = cur["status"] if cur else "todo"
        if status == "done":
            conflicts.append(f"task {key}/{tk} ({tid}) is done and verified: its definition cannot change. Add a new task for follow-up work.")
        elif status in ("running", "verify"):
            ops.append({"op": "defer_task", "key": tk, "id": tid, "fields": changed})
            warnings.append(f"task {key}/{tk} ({tid}) is {status}: changes apply to its NEXT attempt only; the active attempt keeps its snapshot")
        else:
            ops.append({"op": "update_task", "key": tk, "id": tid, "fields": changed})
    if norm_plan.get("scope") == "full":
        for tk, tid in tstate.items():
            if tk in new_tasks or tk in retired:
                continue
            cur = p.get("task", tid) if p.exists("task", tid) else None
            if cur is None:
                continue
            if cur["status"] in ("done", "running", "verify"):
                conflicts.append(f"task {key}/{tk} ({tid}) is {cur['status']} but missing from a full-scope YAML: completed or running work is never "
                                 "removed. Keep it in the file (it is not re-run).")
            else:
                dependents = [k for k, ds in ((t["key"], t["depends_on"]) for t in new_tasks.values()) if tk in ds]
                if dependents:
                    conflicts.append(f"task {key}/{tk} is removed but {', '.join(dependents)} still depend on it")
                else:
                    ops.append({"op": "retire_task", "key": tk, "id": tid})
    # dependency edits on tasks that already started
    return {"ops": ops, "conflicts": conflicts, "warnings": warnings}


def _known(p: Optional[Project]):
    """Task keys already registered for a plan: a partial document may depend on them without repeating them."""
    def f(plan_key: str) -> set:
        st = load_state(p, plan_key) if p else None
        return set((st or {}).get("tasks") or {})
    return f if p else None


def deliverables(t: Dict[str, Any]) -> List[str]:
    """Files a task is expected to produce: its `file:` checks, `expected_artifacts`, and an `Outputs: a; b` line in its acceptance text."""
    from .verification import parse_check
    out = [str(x) for x in (t.get("expected_artifacts") or [])]
    for c in t.get("checks") or []:
        try:
            pc = parse_check(c)
        except ResearchError:
            continue
        if pc.get("type") == "file":
            out.append(pc["path"])
    m = re.search(r"Outputs?:\s*([^\n]+)", str(t.get("acceptance") or ""))
    if m:
        out += [x.strip().rstrip(".") for x in m.group(1).split(";") if x.strip().rstrip(".")]
    return sorted({x for x in out if x})


def authored_by_other(tasks: Dict[str, Dict[str, Any]], key: str) -> set:
    """Deliverable paths of the tasks `key` depends on, transitively: work that exists before this task starts."""
    seen, todo, paths = set(), list(tasks[key].get("depends_on") or []), set()
    while todo:
        d = todo.pop()
        if d in seen or d not in tasks:
            continue
        seen.add(d)
        paths |= set(deliverables(tasks[d]))
        todo += tasks[d].get("depends_on") or []
    return paths


def role_warnings(plan: Dict[str, Any]) -> List[str]:
    """A verifier checks work; it does not author it. Flag verifier tasks whose own checks/outputs are files nobody upstream produces.
    Advice only: the plan is never rewritten and no review requirement is weakened."""
    tasks = {t["key"]: t for t in plan["tasks"]}
    out = []
    for k, t in tasks.items():
        if (t.get("agent") or {}).get("role") != "verifier":
            continue
        own = [d for d in deliverables(t) if d not in authored_by_other(tasks, k)]
        if own:
            out.append(f"task {plan['key']}/{k} is a verifier but must itself create {', '.join(own)}. Verifiers check work and are told not to produce new results, "
                       f"so this task will fail its own checks. Change its role to analyst or implementer (it authors the report), then add a separate verifier task "
                       f"that depends on it and checks the report; or drop those file checks from the verifier.")
    return out


def diff(p: Project, text: str) -> Dict[str, Any]:
    """Preview: what apply would do. Reads nothing but the ledger."""
    doc = normalize(parse(text), _known(p))
    plans_out, conflicts, warnings, nops = [], [], [], 0
    for pl in doc["plans"]:
        st = load_state(p, pl["key"])
        ch = _plan_changes(p, pl, st, (st or {}).get("experiments") or {})
        pw = ch["warnings"] + role_warnings(pl)
        plans_out.append({"plan": pl["key"], "revision": (st or {}).get("revision"), "ops": ch["ops"], "warnings": pw})
        conflicts += ch["conflicts"]
        warnings += pw
        nops += len(ch["ops"])
    return {"ok": not conflicts, "plans": plans_out, "conflicts": conflicts, "warnings": warnings, "operations": nops,
            "unchanged": nops == 0 and not conflicts}


def validate(text: str, p: Optional[Project] = None) -> Dict[str, Any]:
    doc = normalize(parse(text), _known(p))
    n_tasks = sum(len(p["tasks"]) for p in doc["plans"])
    return {"ok": True, "plans": [p["key"] for p in doc["plans"]], "tasks": n_tasks,
            "experiments": sum(len(p["experiments"]) for p in doc["plans"]), "warnings": [w for p in doc["plans"] for w in role_warnings(p)]}


def _id_resolver(st: Dict[str, Any]):
    def ref(k: str) -> Optional[str]:
        return k if re.match(r"^T-\d+$", k) else st["tasks"].get(k)
    return ref


def apply(p: Project, text: str, dry_run: bool = False, source: str = "yaml", author_note: Optional[str] = None) -> Dict[str, Any]:
    """Reconcile the ledger with the document. Validates fully first; applies idempotently, saving the key map after every
    entity so an interrupted apply is finished by re-running it."""
    from . import plans as P
    from . import services
    doc = normalize(parse(text), _known(p))
    pre = diff(p, text)
    if pre["conflicts"]:
        raise PlanSpecError(pre["conflicts"])
    if dry_run:
        return {**pre, "dry_run": True}
    result: Dict[str, Any] = {"plans": [], "operations": 0, "dry_run": False}
    for pl in doc["plans"]:
        st = load_state(p, pl["key"])
        fresh = st is None
        if fresh:
            st = {"plan_key": pl["key"], "plan_id": None, "revision": 0, "experiments": {}, "tasks": {}, "retired": {}, "deferred": {},
                  "history": [], "spec": {}, "project_context": doc["project"]["context"], "created_at": now_iso()}
        ch = _plan_changes(p, pl, None if fresh else st, st["experiments"])
        if not ch["ops"] and not fresh:
            result["plans"].append({"plan": pl["key"], "id": st["plan_id"], "revision": st["revision"], "changed": False})
            continue
        author = {"author_note": author_note} if author_note else {}
        ex = pl["execution"]
        for op in ch["ops"]:
            k = op["op"]
            if k in ("create_plan", "adopt_plan"):
                if k == "adopt_plan":
                    st["plan_id"] = op["id"]
                else:
                    q = None
                    pq = pl.get("question")
                    if pq and isinstance(pq, str) and re.match(r"^Q-\d+$", pq):
                        q = pq
                    elif pq:
                        q = services.create_question(p, str(pq))["id"]
                    st["plan_id"] = P.create_plan(p, pl["title"], objective=pl.get("objective"), success_criteria=pl.get("success_criteria"),
                                                  context=pl.get("context"), root_question_id=q, skills=pl.get("skills") or None)["id"]
            elif k == "update_plan":
                P.update_plan(p, st["plan_id"], **op["fields"])
            elif k == "update_execution":
                pass     # carried by the spec saved below; listed so the change is visible in diff and counted as a change
            elif k in ("create_experiment", "adopt_experiment"):
                e = next(e for e in pl["experiments"] if e["key"] == op["key"])
                if k == "adopt_experiment":
                    st["experiments"][op["key"]] = e["id"]
                else:
                    q = e.get("question")
                    if q and not re.match(r"^Q-\d+$", str(q)):
                        q = services.create_question(p, str(q))["id"]
                    kw = {f: e.get(f) for f in ("hypothesis", "motivation", "method", "success_criteria", "expected_outcome", "stop_conditions",
                                                  "parameters", "metrics", "expected_artifacts", "planned_runs", "notes", "tags") if e.get(f) is not None}
                    st["experiments"][op["key"]] = services.create_experiment(p, e["title"], question=q, **kw)["id"]
            elif k == "update_experiment":
                f = dict(op["fields"])
                f.pop("question", None)
                if f:
                    services.update_experiment(p, st["experiments"][op["key"]], **f)
            elif k in ("create_task", "adopt_task"):
                t = next(t for t in pl["tasks"] if t["key"] == op["key"])
                if k == "adopt_task":
                    st["tasks"][op["key"]] = t["id"]
                else:
                    st["tasks"][op["key"]] = P.create_task(p, st["plan_id"], t["title"], **_task_kwargs(p, st, t, None))["id"]
            elif k in ("update_task", "unretire_task"):
                t = next(t for t in pl["tasks"] if t["key"] == op["key"])
                P._update_task(p, op["id"], **_task_kwargs(p, st, t, op["id"]))
                st["retired"].pop(op["key"], None)
            elif k == "defer_task":
                st["deferred"][op["key"]] = {"fields": op["fields"], "revision": st["revision"] + 1, "ts": now_iso()}
            elif k == "retire_task":
                P._update_task(p, op["id"], status="blocked", blockers=f"retired by plan {pl['key']} revision {st['revision'] + 1}")
                st["retired"][op["key"]] = {"id": op["id"], "revision": st["revision"] + 1, "ts": now_iso()}
            st["tasks"] = st["tasks"]
            save_state(p, st)            # after every entity: an interrupted apply resumes where it stopped
            result["operations"] += 1
        # second pass: dependencies need every task id to exist
        for t in pl["tasks"]:
            tid = st["tasks"].get(t["key"])
            if tid and t["key"] not in st["retired"]:
                want = [_id_resolver(st)(d) for d in t["depends_on"]]
                if None in want:
                    raise PlanSpecError([f"task {pl['key']}/{t['key']}: unresolved dependency"])
                cur = p.get("task", tid)
                if (cur.get("depends_on") or []) != want and cur["status"] not in ("done",) and t["key"] not in st["deferred"]:
                    P._update_task(p, tid, depends_on=want or None)
        # A partial document (an agent's patch, a scoped export) adds to what is registered; only a full-scope one replaces it.
        old_spec = st.get("spec") or {}
        old_flat = {t["key"]: t for t in old_spec.get("tasks_flat", [])}
        old_exps = {e["key"]: e for e in old_spec.get("experiments", [])}
        full = pl.get("scope") == "full"
        keep_t = {k: v for k, v in old_flat.items() if k in (st.get("retired") or {})} if full else old_flat
        keep_e = {} if full else old_exps
        spec = {k: v for k, v in pl.items() if k not in ("revision", "id")}
        if not full:
            spec = {**old_spec, **{k: v for k, v in spec.items() if v not in (None, [], {})}}
        merged_t = {**keep_t, **{t["key"]: t for t in pl["tasks"]}}
        spec["experiments"] = list({**keep_e, **{e["key"]: e for e in pl["experiments"]}}.values())
        spec["tasks_flat"] = list(merged_t.values())
        spec["tasks"] = []
        st["spec"] = spec
        st["spec_tasks"] = merged_t
        st["project_context"] = doc["project"]["context"]
        st["revision"] += 1
        st["history"].append({"revision": st["revision"], "ts": now_iso(), "hash": _hash(pl), "source": source, "operations": len(ch["ops"])})
        save_state(p, st)
        snap = os.path.join(spec_dir(p), pl["key"] + ".revisions")
        os.makedirs(snap, exist_ok=True)
        _atomic_write(os.path.join(snap, f"{st['revision']:04d}.json"), json.dumps(pl, indent=1, sort_keys=True, default=str) + "\n")
        p.event("plan", st["plan_id"], "plan_yaml_applied", f"{pl['key']} revision {st['revision']}: {len(ch['ops'])} operation(s) ({source})")
        result["plans"].append({"plan": pl["key"], "id": st["plan_id"], "revision": st["revision"], "changed": True,
                                "operations": len(ch["ops"]), "tasks": st["tasks"], "experiments": st["experiments"], "warnings": ch["warnings"]})
    if source in ("yaml", "ui"):      # not for agent proposals: their project root is an experiment worktree
        _write_context_template(p)
    return result


def _task_kwargs(p: Project, st: Dict[str, Any], t: Dict[str, Any], tid: Optional[str]) -> Dict[str, Any]:
    checks = list(t["checks"])
    for a in t["expected_artifacts"]:
        c = f"file:{a}"
        if c not in checks:
            checks.append(c)
    deps = []
    for d in t["depends_on"]:
        r = _id_resolver(st)(d)
        if r:
            deps.append(r)       # unresolved ones are connected in the second pass
    kw: Dict[str, Any] = {
        "goal": t["prompt"], "task_type": t.get("task_type") or ("analysis" if t["agent"].get("role") == "analyst" else "implementation"),
        "assigned_role": t["agent"].get("role"), "inputs": _s(t.get("inputs")), "expected_outputs": ", ".join(t["expected_artifacts"]) or None,
        "acceptance_criteria": _s(t.get("acceptance")), "checks": checks or None, "notes": t.get("notes"), "skills": t.get("skills") or None,
    }
    if tid is None:
        kw["depends_on"] = deps or None
        if t.get("experiment"):
            kw["related_experiment_id"] = st["experiments"].get(t["experiment"])
    else:
        kw["title"] = t["title"]
    return {k: v for k, v in kw.items() if v is not None or tid is not None}


def _s(v: Any) -> Optional[str]:
    if v is None:
        return None
    return v if isinstance(v, str) else "\n".join(str(x) for x in v) if isinstance(v, list) else str(v)


def _write_context_template(p: Project) -> None:
    """RESEARCH.md: project scientific context for agents. Created once; never overwritten."""
    main = p.root
    path = os.path.join(main, "RESEARCH.md")
    if os.path.exists(path):
        return
    try:
        _atomic_write(path, RESEARCH_MD)
    except OSError:
        pass


RESEARCH_MD = """# RESEARCH.md: scientific context for agents

Research OS puts selected parts of this file in every agent prompt. Keep it short and factual; edit it as you learn.

## Question and why it matters
(What are we trying to find out, and what decision depends on it?)

## Assumptions and numerical conventions
(Equations, units, discretisation, data conventions, anything an implementer must not guess.)

## Data and environment
(Where datasets live, cluster modules/venv, resource limits.)

## Known pitfalls
(Bugs found, approaches that failed, traps.)

## Definition of done
(What a convincing result looks like: metrics, figures, tolerances.)
"""
