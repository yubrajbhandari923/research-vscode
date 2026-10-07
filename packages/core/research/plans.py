"""Plan and Task CRUD operations.

Plans are high-level research objectives that decompose into Tasks.
Tasks represent units of work with dependencies, goals, acceptance criteria, and verification.

The 'ready' state is derived: a task is ready if status='todo' AND all dependencies are done.
This avoids storing derived state in the database.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .schema import STATUSES, TASK_TYPES, TASK_ROLES, normalize_id
from .store import Project
from .util import NotFound, PolicyBlocked, ResearchError, as_list, now_iso, parse_iso


def author_label(project: Project) -> str:
    """Who is acting, as a short string: agent name (e.g. claude/implementer) or the human's name."""
    a = project.author
    return a.get("author_name") or a.get("author_type") or "human"


def _skill_names(values: Any) -> Optional[List[str]]:
    names = [str(v).strip() for v in as_list(values) if str(v).strip()]
    return names or None


def _checks(values: Any) -> Optional[List[Dict[str, Any]]]:
    from .verification import parse_check
    out = [parse_check(v) for v in as_list(values) if v not in (None, "")]
    return out or None


def create_plan(
    project: Project,
    title: str,
    objective: str = None,
    status: str = "active",
    root_question_id: str = None,
    success_criteria: str = None,
    context: str = None,
    question: str = None,
    skills: Any = None,
) -> Dict[str, Any]:
    """Create a new plan. `question` is an alias for `root_question_id` (matches create_experiment)."""
    status = status or "active"
    if status not in STATUSES["plan"]:
        raise ResearchError(f"Invalid plan status: {status}. Valid: {STATUSES['plan']}")

    # Validate question exists if provided
    root_question_id = root_question_id or question
    qid = None
    if root_question_id:
        qid = normalize_id(root_question_id, "Q")
        if not project.exists("question", qid):
            raise NotFound(f"Question {qid} not found")

    values = {
        "title": title,
        "status": status,
        "root_question_id": qid,
        "objective": objective,
        "success_criteria": success_criteria,
        "context": context,
        "skills": _skill_names(skills),
    }

    return project.insert_entity("plan", values, summary=f"Plan created: {title}")


def create_task(
    project: Project,
    plan_id: str,
    title: str,
    goal: str = None,
    task_type: str = None,
    status: str = "todo",
    assigned_role: str = None,
    depends_on: List[str] = None,
    inputs: str = None,
    expected_outputs: str = None,
    acceptance_criteria: str = None,
    verification: str = None,
    related_question_id: str = None,
    related_experiment_id: str = None,
    notes: str = None,
    skills: Any = None,
    checks: Any = None,
) -> Dict[str, Any]:
    """Create a new task within a plan. `checks` are verification checks (see verification.parse_check)."""
    # Validate status
    if status not in STATUSES["task"]:
        raise ResearchError(f"Invalid task status: {status}. Valid: {STATUSES['task']}")

    # Note: task_type and assigned_role are extensible, so we only warn if non-standard
    # This allows custom types without breaking

    # Validate plan exists
    pid = normalize_id(plan_id, "PLAN")
    if not project.exists("plan", pid):
        raise NotFound(f"Plan {pid} not found")

    # Validate and normalize dependencies
    dep_ids = []
    if depends_on:
        for dep in depends_on:
            tid = normalize_id(dep, "T")
            if not project.exists("task", tid):
                raise NotFound(f"Task dependency {tid} not found")
            dep_ids.append(tid)

    # Validate related entities
    qid = None
    if related_question_id:
        qid = normalize_id(related_question_id, "Q")
        if not project.exists("question", qid):
            raise NotFound(f"Question {qid} not found")

    eid = None
    if related_experiment_id:
        eid = normalize_id(related_experiment_id, "EXP")
        if not project.exists("experiment", eid):
            raise NotFound(f"Experiment {eid} not found")

    values = {
        "plan_id": pid,
        "title": title,
        "goal": goal,
        "task_type": task_type,
        "status": status,
        "assigned_role": assigned_role,
        "depends_on": dep_ids or None,
        "inputs": inputs,
        "expected_outputs": expected_outputs,
        "acceptance_criteria": acceptance_criteria,
        "verification": verification,
        "related_question_id": qid,
        "related_experiment_id": eid,
        "artifacts": None,
        "blockers": None,
        "notes": notes,
        "skills": _skill_names(skills),
        "checks": _checks(checks),
        "completed_by": None,
        "started_at": None,
        "completed_at": None,
    }

    return project.insert_entity("task", values, summary=f"Task created: {title}")


def update_plan(project: Project, plan_id: str, **fields) -> Dict[str, Any]:
    """Update plan fields."""
    pid = normalize_id(plan_id, "PLAN")
    if not project.exists("plan", pid):
        raise NotFound(f"Plan {pid} not found")

    if "question" in fields and "root_question_id" not in fields:
        fields["root_question_id"] = fields.pop("question")
    allowed = {"title", "objective", "status", "root_question_id", "success_criteria", "context", "completed_at",
               "skills"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "skills" in updates:
        updates["skills"] = _skill_names(updates["skills"])

    if "status" in updates:
        if updates["status"] not in STATUSES["plan"]:
            raise ResearchError(f"Invalid plan status: {updates['status']}")
        # Auto-set completed_at when completing
        if updates["status"] == "completed" and "completed_at" not in updates:
            updates["completed_at"] = now_iso()

    if "root_question_id" in updates:
        qid = normalize_id(updates["root_question_id"], "Q")  # '' → None clears the link
        if qid and not project.exists("question", qid):
            raise NotFound(f"Question {qid} not found")
        updates["root_question_id"] = qid

    if not updates:
        return project.get("plan", pid)

    return project.update_entity("plan", pid, updates, summary=f"Plan {pid} updated")


def update_task(project: Project, task_id: str, **fields) -> Dict[str, Any]:
    """Update task fields."""
    tid = normalize_id(task_id, "T")
    task = project.q1("SELECT * FROM tasks WHERE id=?", (tid,))
    if not task:
        raise NotFound(f"Task {tid} not found")

    allowed = {
        "title", "goal", "task_type", "status", "assigned_role", "depends_on",
        "inputs", "expected_outputs", "acceptance_criteria", "verification",
        "related_question_id", "related_experiment_id", "artifacts",
        "blockers", "notes", "result", "completed_by", "started_at", "completed_at",
        "skills", "checks", "claimed_by", "claimed_at",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if "skills" in updates:
        updates["skills"] = _skill_names(updates["skills"])
    if "checks" in updates:
        updates["checks"] = _checks(updates["checks"])
    # An emptied id field (e.g. cleared in an edit form) means "unlink", stored as NULL.
    for k in ("related_question_id", "related_experiment_id"):
        if k in updates and updates[k] == "":
            updates[k] = None

    # Validate status
    if "status" in updates and updates["status"] is not None:
        if updates["status"] not in STATUSES["task"]:
            raise ResearchError(f"Invalid task status: {updates['status']}")

        old_status = task["status"]
        new_status = updates["status"]

        # Auto-set timestamps on transitions
        if new_status == "running" and old_status != "running":
            if "started_at" not in updates:
                updates["started_at"] = now_iso()
        if new_status == "done" and old_status != "done":
            if "completed_at" not in updates:
                updates["completed_at"] = now_iso()

    # Validate dependencies if provided
    if "depends_on" in updates and updates["depends_on"] is not None:
        dep_ids = []
        for dep in (updates["depends_on"] or []):
            d = normalize_id(dep, "T")
            if not project.exists("task", d):
                raise NotFound(f"Task dependency {d} not found")
            dep_ids.append(d)
        updates["depends_on"] = dep_ids or None

    # Validate related entities
    if "related_question_id" in updates and updates["related_question_id"]:
        qid = normalize_id(updates["related_question_id"], "Q")
        if not project.exists("question", qid):
            raise NotFound(f"Question {qid} not found")
        updates["related_question_id"] = qid

    if "related_experiment_id" in updates and updates["related_experiment_id"]:
        eid = normalize_id(updates["related_experiment_id"], "EXP")
        if not project.exists("experiment", eid):
            raise NotFound(f"Experiment {eid} not found")
        updates["related_experiment_id"] = eid

    # Remove None values that would clear fields unintentionally
    updates = {k: v for k, v in updates.items() if v is not None or k in fields}

    if not updates:
        return project.get("task", tid)

    return project.update_entity("task", tid, updates, summary=f"Task {tid} updated")


def list_plans(project: Project, status: str = None) -> List[Dict[str, Any]]:
    """List plans, optionally filtered by status."""
    where = "status = ?" if status else ""
    params = (status,) if status else ()
    return project.list("plan", where, params, order="created_at DESC")


def list_tasks(
    project: Project,
    plan_id: str = None,
    status: str = None,
) -> List[Dict[str, Any]]:
    """List tasks, optionally filtered by plan or status."""
    wheres = []
    params = []

    if plan_id:
        pid = normalize_id(plan_id, "PLAN")
        wheres.append("plan_id = ?")
        params.append(pid)

    if status:
        wheres.append("status = ?")
        params.append(status)

    where = " AND ".join(wheres) if wheres else ""
    return project.list("task", where, tuple(params), order="created_at ASC")


def get_plan_with_tasks(project: Project, plan_id: str) -> Dict[str, Any]:
    """Get plan with all its tasks and summary statistics."""
    pid = normalize_id(plan_id, "PLAN")
    plan = project.get("plan", pid)

    tasks = list_tasks(project, plan_id=pid)
    done = sum(1 for t in tasks if t["status"] == "done")
    running = sum(1 for t in tasks if t["status"] == "running")
    blocked = sum(1 for t in tasks if t["status"] == "blocked")

    plan["tasks"] = tasks
    plan["task_count"] = len(tasks)
    plan["tasks_done"] = done
    plan["tasks_running"] = running
    plan["tasks_blocked"] = blocked

    return plan


def get_task_with_deps(project: Project, task_id: str) -> Dict[str, Any]:
    """Get task with resolved dependencies and readiness status."""
    tid = normalize_id(task_id, "T")
    task = project.get("task", tid)

    # Resolve dependencies
    dep_ids = task.get("depends_on") or []
    deps = [project.get("task", d) for d in dep_ids]

    # Task is ready if: status=todo AND all dependencies are done
    is_ready = (
        task["status"] == "todo" and
        all(d["status"] == "done" for d in deps)
    )

    # Include resolved dependency info
    task["dependencies"] = deps
    task["is_ready"] = is_ready

    return task


def get_next_ready_task(project: Project, plan_id: str = None) -> Optional[Dict[str, Any]]:
    """Get the next task that is ready to execute.

    A task is ready if:
    - status is 'todo' (not yet started)
    - all dependencies have status 'done'

    Returns the first ready task by creation order, or None if no task is ready.
    """
    tasks = list_tasks(project, plan_id=plan_id)

    for task in tasks:
        if task["status"] in ("done", "running", "blocked", "verify"):
            continue

        # Check dependencies
        dep_ids = task.get("depends_on") or []
        if not dep_ids:
            # No dependencies, ready if status is todo
            if task["status"] == "todo":
                return get_task_with_deps(project, task["id"])
        else:
            # Check all deps are done
            all_done = True
            for d in dep_ids:
                dep = project.get("task", d)
                if dep["status"] != "done":
                    all_done = False
                    break
            if all_done and task["status"] == "todo":
                return get_task_with_deps(project, task["id"])

    return None


def start_task(project: Project, task_id: str, assigned_role: str = None, force: bool = False) -> Dict[str, Any]:
    """Claim a task and mark it running. Refuses a task another actor has claimed (unless force)."""
    tid = normalize_id(task_id, "T")
    task = get_task_with_deps(project, tid)
    me = author_label(project)
    if task["status"] == "running" and task.get("claimed_by") and task["claimed_by"] != me and not force:
        raise PolicyBlocked(f"{tid} is already claimed by {task['claimed_by']} (since {task.get('claimed_at')})",
                            hint=f"Pick another task (`research task next`), or take it over with --force.")
    if task["status"] == "done" and not force:
        raise ResearchError(f"{tid} is already done", hint="Use --force to reopen it.")
    warnings = []
    unmet = [d["id"] for d in task["dependencies"] if d["status"] != "done"]
    if unmet:
        warnings.append(f"{tid} is not ready: waiting on {', '.join(unmet)}")
    kw = {"assigned_role": assigned_role} if assigned_role else {}
    out = update_task(project, tid, status="running", started_at=task.get("started_at") or now_iso(),
                      claimed_by=me, claimed_at=now_iso(), **kw)
    if warnings:
        out["warnings"] = warnings
    return out


def release_task(project: Project, task_id: str, note: str = None) -> Dict[str, Any]:
    """Give a claimed task back (status → todo) so another agent can pick it up."""
    tid = normalize_id(task_id, "T")
    if note:
        add_task_note(project, tid, note)
    return update_task(project, tid, status="todo", claimed_by=None, claimed_at=None)


def add_task_note(project: Project, task_id: str, text: str) -> Dict[str, Any]:
    """Append a progress note (an event): the hand-off trail for whoever continues the task."""
    tid = normalize_id(task_id, "T")
    if not project.exists("task", tid):
        raise NotFound(f"Task {tid} not found")
    if not text or not str(text).strip():
        raise ResearchError("A progress note needs text")
    project.event("task", tid, "progress", str(text).strip())
    return {"id": tid, "note": str(text).strip(), "by": author_label(project)}


def progress_notes(project: Project, task_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    return project.q("SELECT ts, summary AS text, author_type, author_name FROM events WHERE entity_id=? "
                     "AND action='progress' ORDER BY id DESC LIMIT ?", (task_id, limit))


def last_activity(project: Project, task: Dict[str, Any]) -> Optional[str]:
    r = project.q1("SELECT MAX(ts) AS ts FROM events WHERE entity_id=?", (task["id"],))
    stamps = [x for x in (task.get("updated_at"), task.get("claimed_at"), r and r["ts"]) if x]
    return max(stamps) if stamps else None


def stale_tasks(project: Project) -> List[Dict[str, Any]]:
    """Running tasks with no activity (update, note, check) for `coordination.stale_task_hours`."""
    import datetime as _dt
    hours = float((project.config.get("coordination") or {}).get("stale_task_hours") or 0)
    if not hours:
        return []
    now = _dt.datetime.now().astimezone()
    out = []
    for t in list_tasks(project, status="running"):
        last = parse_iso(last_activity(project, t))
        if last and (now - last).total_seconds() > hours * 3600:
            out.append({**t, "idle_hours": round((now - last).total_seconds() / 3600, 1)})
    return out


def complete_task(
    project: Project,
    task_id: str,
    result: str = None,
    completed_by: str = None,
    artifacts: List[str] = None
) -> Dict[str, Any]:
    """Mark a task as done. If it has checks, they run first and gate completion (see verification)."""
    from . import verification as _v
    tid = normalize_id(task_id, "T")
    warnings = _v.gate_task_completion(project, tid)
    updates = {
        "status": "done",
        "completed_at": now_iso(),
        "completed_by": completed_by or author_label(project),
    }
    if result:
        updates["result"] = result
    if artifacts:
        updates["artifacts"] = [normalize_id(a, "A") for a in as_list(artifacts)]

    out = update_task(project, tid, **updates)
    if warnings:
        out["warnings"] = warnings
    return out


def block_task(project: Project, task_id: str, blockers: str) -> Dict[str, Any]:
    """Mark a task as blocked with reason."""
    return update_task(project, task_id, status="blocked", blockers=blockers)


def task_context(project: Project, task_id: str) -> Dict[str, Any]:
    """Everything an agent needs to work on one task, and nothing more.

    Bundles the task, its plan's objective, dependency results, the related question and experiment
    (with run/synthesis state), and the agent policy.
    """
    from .services import experiment_state

    task = get_task_with_deps(project, task_id)
    plan = project.get("plan", task["plan_id"]) if task.get("plan_id") and project.exists("plan", task["plan_id"]) else None
    question = None
    if task.get("related_question_id") and project.exists("question", task["related_question_id"]):
        q = project.get("question", task["related_question_id"])
        question = {k: q.get(k) for k in ("id", "title", "status", "description")}
    experiment = None
    if task.get("related_experiment_id") and project.exists("experiment", task["related_experiment_id"]):
        e = project.get("experiment", task["related_experiment_id"])
        experiment = {k: e.get(k) for k in ("id", "title", "status", "hypothesis", "parameters", "success_criteria",
                                            "stop_conditions", "is_baseline")}
        experiment["state"] = experiment_state(project, e["id"])
        syn = project.q1("SELECT id, interpretation, next_experiment FROM syntheses WHERE experiment_id=? "
                         "ORDER BY created_at DESC, id DESC LIMIT 1", (e["id"],))
        experiment["latest_synthesis"] = syn
    brief = {k: task.get(k) for k in (
        "id", "title", "status", "task_type", "assigned_role", "goal", "inputs", "expected_outputs",
        "acceptance_criteria", "verification", "result", "blockers", "notes", "artifacts", "depends_on")}
    if task["status"] != "blocked":
        brief["blockers"] = None  # stale once unblocked; still kept on the task itself as history
    from . import skills as _skills
    from . import verification as _v
    checks = [_v.describe_check(_v.parse_check(c)) for c in (task.get("checks") or [])]
    last = _v.latest(project, task["id"], "check")
    related_ids = [x for x in (task.get("related_experiment_id"), task.get("related_question_id")) if x]
    findings = []
    if related_ids:
        marks = ",".join("?" * len(related_ids))
        rows = project.q(f"SELECT DISTINCT src_id FROM links WHERE src_type='finding' AND (dst_id IN ({marks}) OR dst_id IN "
                         f"(SELECT id FROM runs WHERE experiment_id IN ({marks})))", tuple(related_ids) * 2)
        for r in rows[:6]:
            f = project.get("finding", r["src_id"])
            findings.append({k: f.get(k) for k in ("id", "title", "status", "kind", "confidence")})
    return {
        "task": brief,
        "claimed_by": task.get("claimed_by"),
        "checks": checks,
        "last_verification": {k: last[k] for k in ("id", "verdict", "summary", "created_at")} if last else None,
        "progress": progress_notes(project, task["id"]),
        "skills": _skills.skills_for(project, task, plan),
        "related_findings": findings,
        "is_ready": task["is_ready"],
        "plan": {k: plan.get(k) for k in ("id", "title", "status", "objective", "success_criteria", "context")}
        if plan else None,
        "dependencies": [{"id": d["id"], "title": d["title"], "status": d["status"], "result": d.get("result")}
                         for d in task["dependencies"]],
        "question": question,
        "experiment": experiment,
        "policy": project.policy,
    }


def add_task_artifact(project: Project, task_id: str, artifact_id: str) -> Dict[str, Any]:
    """Add an artifact reference to a task."""
    tid = normalize_id(task_id, "T")
    task = project.get("task", tid)

    current = task.get("artifacts") or []
    aid = normalize_id(artifact_id, "A")
    if aid not in current:
        current.append(aid)

    return update_task(project, task_id, artifacts=current)
