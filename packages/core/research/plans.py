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
from .util import NotFound, ResearchError, now_iso


def create_plan(
    project: Project,
    title: str,
    objective: str = None,
    status: str = "active",
    root_question_id: str = None,
    success_criteria: str = None,
    context: str = None,
) -> Dict[str, Any]:
    """Create a new plan."""
    if status not in STATUSES["plan"]:
        raise ResearchError(f"Invalid plan status: {status}. Valid: {STATUSES['plan']}")

    # Validate question exists if provided
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
) -> Dict[str, Any]:
    """Create a new task within a plan."""
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

    allowed = {"title", "objective", "status", "root_question_id", "success_criteria", "context", "completed_at"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}

    if "status" in updates:
        if updates["status"] not in STATUSES["plan"]:
            raise ResearchError(f"Invalid plan status: {updates['status']}")
        # Auto-set completed_at when completing
        if updates["status"] == "completed" and "completed_at" not in updates:
            updates["completed_at"] = now_iso()

    if "root_question_id" in updates:
        qid = normalize_id(updates["root_question_id"], "Q")
        if not project.exists("question", qid):
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
        "blockers", "notes", "result", "completed_by", "started_at", "completed_at"
    }
    updates = {k: v for k, v in fields.items() if k in allowed}

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


def start_task(project: Project, task_id: str, assigned_role: str = None) -> Dict[str, Any]:
    """Mark a task as running (started)."""
    return update_task(
        project, task_id,
        status="running",
        assigned_role=assigned_role,
        started_at=now_iso()
    )


def complete_task(
    project: Project,
    task_id: str,
    result: str = None,
    completed_by: str = None,
    artifacts: List[str] = None
) -> Dict[str, Any]:
    """Mark a task as done (completed)."""
    updates = {
        "status": "done",
        "completed_at": now_iso(),
    }
    if result:
        updates["result"] = result
    if completed_by:
        updates["completed_by"] = completed_by
    if artifacts:
        updates["artifacts"] = artifacts

    return update_task(project, task_id, **updates)


def block_task(project: Project, task_id: str, blockers: str) -> Dict[str, Any]:
    """Mark a task as blocked with reason."""
    return update_task(project, task_id, status="blocked", blockers=blockers)


def add_task_artifact(project: Project, task_id: str, artifact_id: str) -> Dict[str, Any]:
    """Add an artifact reference to a task."""
    tid = normalize_id(task_id, "T")
    task = project.get("task", tid)

    current = task.get("artifacts") or []
    aid = normalize_id(artifact_id, "A")
    if aid not in current:
        current.append(aid)

    return update_task(project, task_id, artifacts=current)
