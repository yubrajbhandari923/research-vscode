# Phase 0: Plan/Task Foundation — Detailed Implementation Spec

**Duration:** Week 1
**Goal:** Add Plan and Task entities to Research OS without breaking existing functionality
**Status:** Ready for implementation

---

## Overview

Phase 0 lays the groundwork for the Research OS transformation by:
1. Adding Plan and Task table schemas
2. Implementing mirror file format (Markdown + YAML front-matter)
3. Extending sync/rebuild logic to handle new entities
4. Adding basic CRUD operations
5. Testing storage/retrieval/migration

**Critical requirement:** All existing tests must pass. This is an additive change.

---

## 1. Schema Changes

### 1.1 New Tables (schema.py)

```python
# packages/core/research/schema.py

SCHEMA_VERSION = 2  # Increment from 1

# Add to ENTITIES dict:

ENTITIES["plan"] = {
    "table": "plans",
    "prefix": "PLAN",
    "width": 3,
    "dir": "plans",
    "columns": [
        ("title", "text"),
        ("objective", "text"),
        ("status", "text"),  # active, completed, blocked, abandoned
        ("root_question_id", "text"),
        ("success_criteria", "text"),
        ("context", "text"),
    ] + AUTHOR_COLS + TIME_COLS + [
        ("completed_at", "text"),
    ],
    "fm": {"root_question_id": "question"},
    "sections": [
        ("objective", "Objective"),
        ("success_criteria", "Success Criteria"),
        ("context", "Context"),
    ],
    "links": [],
}

ENTITIES["task"] = {
    "table": "tasks",
    "prefix": "T",
    "width": 3,
    "dir": "tasks",
    "columns": [
        ("plan_id", "text"),
        ("title", "text"),
        ("goal", "text"),
        ("task_type", "text"),  # research, implementation, experiment, analysis, verification, synthesis, debug, data
        ("status", "text"),  # todo, ready, running, verify, done, blocked
        ("assigned_role", "text"),  # planner, implementer, verifier, analyst, human
        ("depends_on", "json"),  # ["T-001", "T-002"]
        ("inputs", "text"),
        ("expected_outputs", "text"),
        ("acceptance_criteria", "text"),
        ("verification", "text"),
        ("related_question_id", "text"),
        ("related_experiment_id", "text"),
        ("artifacts", "json"),  # ["A-001", "A-002"]
        ("blockers", "text"),
        ("notes", "text"),
    ] + AUTHOR_COLS + TIME_COLS + [
        ("created_by", "text"),  # human | planner-agent
        ("completed_by", "text"),  # implementer-agent
        ("started_at", "text"),
        ("completed_at", "text"),
    ],
    "fm": {
        "plan_id": "plan",
        "related_question_id": "question",
        "related_experiment_id": "experiment",
    },
    "sections": [
        ("goal", "Goal"),
        ("inputs", "Inputs"),
        ("expected_outputs", "Expected Outputs"),
        ("acceptance_criteria", "Acceptance Criteria"),
        ("verification", "Verification"),
        ("blockers", "Blockers"),
        ("notes", "Notes"),
    ],
    "links": [],
}

# Add statuses
STATUSES["plan"] = ["active", "completed", "blocked", "abandoned"]
STATUSES["task"] = ["todo", "ready", "running", "verify", "done", "blocked"]

TASK_TYPES = ["research", "implementation", "experiment", "analysis", "verification", "synthesis", "debug", "data"]
TASK_ROLES = ["planner", "implementer", "verifier", "analyst", "human"]
```

### 1.2 Migration (schema.py)

```python
# packages/core/research/schema.py

MIGRATIONS = {
    1: [
        # Existing v1 schema (keep as-is)
        ...
    ],
    2: [
        # Add Plan table
        """CREATE TABLE plans (
            id text PRIMARY KEY,
            title text NOT NULL,
            objective text,
            status text DEFAULT 'active',
            root_question_id text,
            success_criteria text,
            context text,
            author_type text,
            author_name text,
            author_model text,
            created_at text,
            updated_at text,
            completed_at text,
            FOREIGN KEY (root_question_id) REFERENCES questions(id)
        )""",

        # Add Task table
        """CREATE TABLE tasks (
            id text PRIMARY KEY,
            plan_id text NOT NULL,
            title text NOT NULL,
            goal text,
            task_type text,
            status text DEFAULT 'todo',
            assigned_role text,
            depends_on text,
            inputs text,
            expected_outputs text,
            acceptance_criteria text,
            verification text,
            related_question_id text,
            related_experiment_id text,
            artifacts text,
            blockers text,
            notes text,
            author_type text,
            author_name text,
            author_model text,
            created_by text,
            completed_by text,
            created_at text,
            updated_at text,
            started_at text,
            completed_at text,
            FOREIGN KEY (plan_id) REFERENCES plans(id),
            FOREIGN KEY (related_question_id) REFERENCES questions(id),
            FOREIGN KEY (related_experiment_id) REFERENCES experiments(id)
        )""",

        # Indices
        "CREATE INDEX idx_tasks_plan ON tasks(plan_id)",
        "CREATE INDEX idx_tasks_status ON tasks(status)",
        "CREATE INDEX idx_plans_status ON plans(status)",

        # Add counters
        "INSERT INTO counters (prefix, next) VALUES ('PLAN', 1)",
        "INSERT INTO counters (prefix, next) VALUES ('T', 1)",
    ],
}
```

### 1.3 Update store.py

```python
# packages/core/research/store.py

# Add to SUBDIRS
SUBDIRS = ["questions", "experiments", "findings", "decisions", "checkpoints",
           "plans", "tasks",  # NEW
           "notes", "runs", "skills", "context", "prompts", "templates", "cache"]
```

---

## 2. Mirror File Format

### 2.1 Plan Mirror Template

```markdown
# .research/plans/PLAN-001.md
---
id: PLAN-001
title: Reproduce Paper X Main Results
status: active
question: Q-001
objective: ...
success_criteria: ...
context: ...
author_type: human
author_name: null
author_model: null
created_at: '2026-10-05T10:30:00+00:00'
updated_at: '2026-10-05T10:30:00+00:00'
completed_at: null
---

# PLAN-001 · Reproduce Paper X Main Results

## Objective

Reproduce the main quantitative results (Table 2, F1-macro scores)
and qualitative comparison (Figure 4, material maps) from the paper
"Physically Plausible Material Decomposition".

## Success Criteria

- F1-macro matches paper ± 2 percentage points
- Visual inspection: material maps qualitatively match Figure 4
- Full reproduction completed within 2 weeks
- All experiments documented with provenance

## Context

**Paper:** Smith et al. 2025, "Physically Plausible Material Decomposition"
**Dataset:** Clinical CT scans (45 subjects)
**Focus:** Quantitative accuracy first, then qualitative comparison

<!-- research:generated — everything below is regenerated -->

## Status

**Active** · 3 of 8 tasks completed

## Tasks

| ID | Title | Status | Assigned | Dependencies |
|----|-------|--------|----------|--------------|
| T-001 | Understand paper methodology | ✓ done | human | — |
| T-002 | Implement baseline architecture | ✓ done | implementer | T-001 |
| T-003 | Prepare dataset | ✓ done | implementer | — |
| T-004 | Sanity run (1 subject) | ● running | implementer | T-002, T-003 |
| T-005 | Full reproduction (45 subjects) | ○ todo | implementer | T-004 |
| T-006 | Generate comparison plots | ○ ready | analyst | T-005 |
| T-007 | Verify against paper tolerances | ○ ready | verifier | T-006 |
| T-008 | Write synthesis | ○ ready | analyst | T-007 |

## Activity

- 2026-10-05 10:30: Plan created by human
- 2026-10-05 11:00: Task T-001 completed
- 2026-10-05 14:20: Task T-002 started
- 2026-10-05 16:45: Task T-002 completed
- 2026-10-05 17:00: Task T-004 started
```

### 2.2 Task Mirror Template

```markdown
# .research/tasks/T-042.md
---
id: T-042
plan: PLAN-001
title: Implement baseline architecture
goal: ...
task_type: implementation
status: done
assigned_role: implementer
depends_on: ["T-001", "T-003"]
inputs: ...
expected_outputs: ...
acceptance_criteria: ...
verification: ...
question: null
experiment: EXP-012
artifacts: ["A-0234", "A-0235"]
blockers: null
notes: null
author_type: human
author_name: null
author_model: null
created_by: planner-agent
completed_by: implementer-agent
created_at: '2026-10-05T11:00:00+00:00'
updated_at: '2026-10-05T16:45:00+00:00'
started_at: '2026-10-05T14:20:00+00:00'
completed_at: '2026-10-05T16:45:00+00:00'
---

# T-042 · Implement baseline architecture

## Goal

Create a working implementation of the baseline model from the paper
(section 3.2), including unit tests and a passing sanity run.

## Inputs

- Paper: "Physically Plausible Material Decomposition", section 3.2
- Dataset specification: see T-003 output
- Environment: Python 3.10, PyTorch 2.0

## Expected Outputs

1. `src/models/baseline.py` — model implementation
2. `tests/test_baseline.py` — unit tests (shapes, forward pass)
3. Sanity run artifact confirming model loads and runs

## Acceptance Criteria

- Unit tests pass
- Model architecture matches paper specification
- Forward pass produces expected output shapes
- Sanity run (1 subject, 1 epoch) completes without errors

## Verification

**Deterministic:**
- `pytest tests/test_baseline.py` — all tests pass
- Sanity run exit code 0

**Model-based:**
- Code review by verifier-agent
- Architecture matches paper description

<!-- research:generated -->

## Status

✓ Completed · 2026-10-05 16:45

## Dependencies

✓ T-001: Understand paper methodology (completed)
✓ T-003: Prepare dataset (completed)

## Artifacts

- A-0234: src/models/baseline.py (code)
- A-0235: tests/test_baseline.py (code)
- A-0236: sanity run output (log)

## Related

- Experiment: EXP-012 (Baseline sanity run)
- Question: Q-001 (Can we reproduce the paper baseline?)

## Activity

- 2026-10-05 11:00: Task created by planner-agent
- 2026-10-05 14:20: Task started by implementer-agent
- 2026-10-05 16:45: Task completed by implementer-agent
- Verification: Tests passed, code review approved
```

---

## 3. CRUD Operations

### 3.1 Create new file: packages/core/research/plans.py

```python
"""Plan and Task CRUD operations."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .schema import format_id, parse_id, STATUSES, TASK_TYPES, TASK_ROLES
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
        raise ResearchError(f"Invalid plan status: {status}")

    # Validate question exists
    if root_question_id:
        qid = parse_id(root_question_id, "question")
        if not project.get("question", qid):
            raise NotFound(f"Question {qid} not found")

    with project.transaction():
        plan_id = project.next_id("PLAN")
        now = now_iso()

        row = {
            "id": plan_id,
            "title": title,
            "objective": objective,
            "status": status,
            "root_question_id": qid if root_question_id else None,
            "success_criteria": success_criteria,
            "context": context,
            **project.author,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }

        project.insert("plans", row)
        project.export_mirror("plan", plan_id)
        project.event("plan", plan_id, "created", f"Plan created: {title}")

    return project.get("plan", plan_id)


def create_task(
    project: Project,
    plan_id: str,
    title: str,
    goal: str = None,
    task_type: str = "implementation",
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
    """Create a new task."""
    # Validate
    if task_type and task_type not in TASK_TYPES:
        raise ResearchError(f"Invalid task type: {task_type}")
    if status not in STATUSES["task"]:
        raise ResearchError(f"Invalid task status: {status}")
    if assigned_role and assigned_role not in TASK_ROLES:
        raise ResearchError(f"Invalid task role: {assigned_role}")

    # Validate plan exists
    pid = parse_id(plan_id, "plan")
    if not project.get("plan", pid):
        raise NotFound(f"Plan {pid} not found")

    # Validate dependencies exist
    dep_ids = []
    if depends_on:
        for dep in depends_on:
            tid = parse_id(dep, "task")
            if not project.get("task", tid):
                raise NotFound(f"Task {tid} not found")
            dep_ids.append(tid)

    # Validate related entities
    qid = None
    if related_question_id:
        qid = parse_id(related_question_id, "question")
        if not project.get("question", qid):
            raise NotFound(f"Question {qid} not found")

    eid = None
    if related_experiment_id:
        eid = parse_id(related_experiment_id, "experiment")
        if not project.get("experiment", eid):
            raise NotFound(f"Experiment {eid} not found")

    with project.transaction():
        task_id = project.next_id("T")
        now = now_iso()

        row = {
            "id": task_id,
            "plan_id": pid,
            "title": title,
            "goal": goal,
            "task_type": task_type,
            "status": status,
            "assigned_role": assigned_role,
            "depends_on": project.json_dumps(dep_ids) if dep_ids else None,
            "inputs": inputs,
            "expected_outputs": expected_outputs,
            "acceptance_criteria": acceptance_criteria,
            "verification": verification,
            "related_question_id": qid,
            "related_experiment_id": eid,
            "artifacts": None,
            "blockers": None,
            "notes": notes,
            **project.author,
            "created_by": project.author.get("author_name") or project.author.get("author_type"),
            "completed_by": None,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": None,
        }

        project.insert("tasks", row)
        project.export_mirror("task", task_id)
        project.event("task", task_id, "created", f"Task created: {title}")

    return project.get("task", task_id)


def update_plan(project: Project, plan_id: str, **fields) -> Dict[str, Any]:
    """Update plan fields."""
    pid = parse_id(plan_id, "plan")
    plan = project.get("plan", pid)
    if not plan:
        raise NotFound(f"Plan {pid} not found")

    allowed = {"title", "objective", "status", "root_question_id", "success_criteria", "context", "completed_at"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}

    if "status" in updates and updates["status"] not in STATUSES["plan"]:
        raise ResearchError(f"Invalid plan status: {updates['status']}")

    if not updates:
        return plan

    with project.transaction():
        updates["updated_at"] = now_iso()
        project.update("plans", pid, updates)
        project.export_mirror("plan", pid)
        project.event("plan", pid, "updated", f"Plan updated")

    return project.get("plan", pid)


def update_task(project: Project, task_id: str, **fields) -> Dict[str, Any]:
    """Update task fields."""
    tid = parse_id(task_id, "task")
    task = project.get("task", tid)
    if not task:
        raise NotFound(f"Task {tid} not found")

    allowed = {
        "title", "goal", "task_type", "status", "assigned_role", "depends_on",
        "inputs", "expected_outputs", "acceptance_criteria", "verification",
        "related_question_id", "related_experiment_id", "artifacts",
        "blockers", "notes", "completed_by", "started_at", "completed_at"
    }
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}

    # Validate status
    if "status" in updates and updates["status"] not in STATUSES["task"]:
        raise ResearchError(f"Invalid task status: {updates['status']}")

    # Handle status transitions
    if "status" in updates:
        old_status = task["status"]
        new_status = updates["status"]

        # Auto-set timestamps
        if new_status == "running" and not task["started_at"]:
            updates["started_at"] = now_iso()
        if new_status == "done" and not task["completed_at"]:
            updates["completed_at"] = now_iso()

    if not updates:
        return task

    with project.transaction():
        updates["updated_at"] = now_iso()

        # JSON fields
        if "depends_on" in updates:
            updates["depends_on"] = project.json_dumps(updates["depends_on"])
        if "artifacts" in updates:
            updates["artifacts"] = project.json_dumps(updates["artifacts"])

        project.update("tasks", tid, updates)
        project.export_mirror("task", tid)
        project.event("task", tid, "updated", f"Task updated")

    return project.get("task", tid)


def list_plans(project: Project, status: str = None) -> List[Dict[str, Any]]:
    """List plans, optionally filtered by status."""
    where = "status = ?" if status else ""
    params = (status,) if status else ()
    return project.list("plan", where, params, order="created_at DESC")


def list_tasks(
    project: Project,
    plan_id: str = None,
    status: str = None
) -> List[Dict[str, Any]]:
    """List tasks, optionally filtered by plan or status."""
    wheres = []
    params = []

    if plan_id:
        pid = parse_id(plan_id, "plan")
        wheres.append("plan_id = ?")
        params.append(pid)

    if status:
        wheres.append("status = ?")
        params.append(status)

    where = " AND ".join(wheres) if wheres else ""
    return project.list("task", where, tuple(params), order="created_at ASC")


def get_plan_with_tasks(project: Project, plan_id: str) -> Dict[str, Any]:
    """Get plan with all its tasks."""
    pid = parse_id(plan_id, "plan")
    plan = project.get("plan", pid)
    if not plan:
        raise NotFound(f"Plan {pid} not found")

    tasks = list_tasks(project, plan_id=pid)
    plan["tasks"] = tasks
    return plan


def get_task_dependencies(project: Project, task_id: str) -> Dict[str, Any]:
    """Get task with resolved dependencies."""
    tid = parse_id(task_id, "task")
    task = project.get("task", tid)
    if not task:
        raise NotFound(f"Task {tid} not found")

    # Resolve dependencies
    dep_ids = project.json_loads(task.get("depends_on") or "[]")
    deps = [project.get("task", d) for d in dep_ids]

    # Check if ready (all deps done)
    ready = all(d["status"] == "done" for d in deps)

    return {
        **task,
        "dependencies": deps,
        "is_ready": ready,
    }


def get_next_ready_task(project: Project, plan_id: str = None) -> Optional[Dict[str, Any]]:
    """Get the next task that is ready to execute (status=ready or todo with all deps done)."""
    tasks = list_tasks(project, plan_id=plan_id, status=None)

    for task in tasks:
        if task["status"] in ("done", "running", "blocked"):
            continue

        # Check dependencies
        dep_ids = project.json_loads(task.get("depends_on") or "[]")
        if not dep_ids:
            # No dependencies, ready if status is todo
            if task["status"] in ("todo", "ready"):
                return task
        else:
            # Check all deps are done
            deps = [project.get("task", d) for d in dep_ids]
            if all(d["status"] == "done" for d in deps):
                # Update status to ready if it's todo
                if task["status"] == "todo":
                    update_task(project, task["id"], status="ready")
                return project.get("task", task["id"])

    return None
```

### 3.2 Extend store.py

```python
# packages/core/research/store.py

# Add to Project class:

def get_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
    """Get a plan by ID."""
    return self.get("plan", plan_id)

def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
    """Get a task by ID."""
    return self.get("task", task_id)
```

### 3.3 Update __init__.py

```python
# packages/core/research/__init__.py

from . import plans as _plans

__all__ = [
    # ... existing exports ...
    "create_plan", "create_task", "update_plan", "update_task",
    "list_plans", "list_tasks", "get_plan_with_tasks", "get_next_ready_task",
]

# Plans
create_plan = _with_project(_plans.create_plan)
create_task = _with_project(_plans.create_task)
update_plan = _with_project(_plans.update_plan)
update_task = _with_project(_plans.update_task)
list_plans = _with_project(_plans.list_plans)
list_tasks = _with_project(_plans.list_tasks)
get_plan_with_tasks = _with_project(_plans.get_plan_with_tasks)
get_next_ready_task = _with_project(_plans.get_next_ready_task)
```

---

## 4. Mirror Rendering

### 4.1 Extend mirrors.py

Plan and Task entities follow the same mirror pattern as existing entities (Question, Experiment, etc.), so the existing `render_mirror` and `parse_mirror` functions in `mirrors.py` should work with minimal changes.

**Required changes:**

```python
# packages/core/research/mirrors.py

# Add to _render_generated_tail():

def _render_generated_tail(project: Project, entity_type: str, entity_id: str, row: Dict) -> str:
    # ... existing code for questions, experiments, findings, etc.

    if entity_type == "plan":
        return _render_plan_tail(project, entity_id, row)

    if entity_type == "task":
        return _render_task_tail(project, entity_id, row)

    return ""


def _render_plan_tail(project: Project, plan_id: str, plan: Dict) -> str:
    """Render generated tail for plan mirror."""
    lines = ["## Status\n"]

    # Status summary
    tasks = project.list("task", "plan_id = ?", (plan_id,))
    done = sum(1 for t in tasks if t["status"] == "done")
    total = len(tasks)

    lines.append(f"**{plan['status'].title()}** · {done} of {total} tasks completed\n")

    # Task table
    if tasks:
        lines.append("## Tasks\n")
        lines.append("| ID | Title | Status | Assigned | Dependencies |")
        lines.append("|----|-------|--------|----------|--------------|")

        for task in tasks:
            status_icon = {
                "done": "✓",
                "running": "●",
                "ready": "○",
                "todo": "○",
                "blocked": "✗",
            }.get(task["status"], "○")

            deps = project.json_loads(task.get("depends_on") or "[]")
            dep_str = ", ".join(deps) if deps else "—"

            lines.append(
                f"| {task['id']} | {task['title']} | {status_icon} {task['status']} | "
                f"{task['assigned_role'] or '—'} | {dep_str} |"
            )

        lines.append("")

    # Activity
    lines.append("## Activity\n")
    events = project.query(
        "SELECT * FROM events WHERE entity_id = ? ORDER BY ts DESC LIMIT 10",
        (plan_id,)
    )
    for evt in events:
        lines.append(f"- {evt['ts']}: {evt['summary']}")

    return "\n".join(lines)


def _render_task_tail(project: Project, task_id: str, task: Dict) -> str:
    """Render generated tail for task mirror."""
    lines = ["## Status\n"]

    # Status
    status_icon = {
        "done": "✓ Completed",
        "running": "● Running",
        "ready": "○ Ready",
        "todo": "○ To Do",
        "blocked": "✗ Blocked",
    }.get(task["status"], task["status"])

    completion = f" · {task['completed_at']}" if task["completed_at"] else ""
    lines.append(f"{status_icon}{completion}\n")

    # Dependencies
    dep_ids = project.json_loads(task.get("depends_on") or "[]")
    if dep_ids:
        lines.append("## Dependencies\n")
        for dep_id in dep_ids:
            dep = project.get("task", dep_id)
            if dep:
                icon = "✓" if dep["status"] == "done" else "○"
                lines.append(f"{icon} {dep_id}: {dep['title']} ({dep['status']})")
        lines.append("")

    # Artifacts
    artifact_ids = project.json_loads(task.get("artifacts") or "[]")
    if artifact_ids:
        lines.append("## Artifacts\n")
        for aid in artifact_ids:
            artifact = project.get("artifact", aid)
            if artifact:
                lines.append(f"- {aid}: {artifact['name']} ({artifact['type']})")
        lines.append("")

    # Related
    related = []
    if task.get("related_experiment_id"):
        exp = project.get("experiment", task["related_experiment_id"])
        if exp:
            related.append(f"- Experiment: {exp['id']} ({exp['title']})")

    if task.get("related_question_id"):
        q = project.get("question", task["related_question_id"])
        if q:
            related.append(f"- Question: {q['id']} ({q['title']})")

    if related:
        lines.append("## Related\n")
        lines.extend(related)
        lines.append("")

    # Activity
    lines.append("## Activity\n")
    events = project.query(
        "SELECT * FROM events WHERE entity_id = ? ORDER BY ts DESC LIMIT 10",
        (task_id,)
    )
    for evt in events:
        lines.append(f"- {evt['ts'][:10]} {evt['ts'][11:16]}: {evt['summary']}")

    return "\n".join(lines)
```

---

## 5. CLI Commands

### 5.1 Add to packages/cli/research_cli/main.py

```python
# packages/cli/research_cli/main.py

def cmd_plan(args):
    """Plan commands."""
    import research

    if args.plan_cmd == "create":
        plan = research.create_plan(
            title=args.title,
            objective=args.objective,
            status=args.status or "active",
            root_question_id=args.question,
            success_criteria=args.success,
            context=args.context,
        )
        if args.json:
            print_json(plan)
        else:
            print(f"Created {plan['id']}: {plan['title']}")

    elif args.plan_cmd == "list":
        plans = research.list_plans(status=args.status)
        if args.json:
            print_json(plans)
        else:
            for p in plans:
                print(f"{p['id']} · {p['title']} ({p['status']})")

    elif args.plan_cmd == "show":
        plan = research.get_plan_with_tasks(plan_id=args.id)
        if args.json:
            print_json(plan)
        else:
            print_plan_detail(plan)

    elif args.plan_cmd == "update":
        updates = {}
        if args.title:
            updates["title"] = args.title
        if args.objective:
            updates["objective"] = args.objective
        if args.status:
            updates["status"] = args.status
        if args.success:
            updates["success_criteria"] = args.success

        plan = research.update_plan(args.id, **updates)
        if args.json:
            print_json(plan)
        else:
            print(f"Updated {plan['id']}")


def cmd_task(args):
    """Task commands."""
    import research

    if args.task_cmd == "create":
        task = research.create_task(
            plan_id=args.plan,
            title=args.title,
            goal=args.goal,
            task_type=args.type or "implementation",
            status=args.status or "todo",
            assigned_role=args.role,
            depends_on=args.depends,
            inputs=args.inputs,
            expected_outputs=args.outputs,
            acceptance_criteria=args.criteria,
            verification=args.verification,
            related_question_id=args.question,
            related_experiment_id=args.experiment,
            notes=args.notes,
        )
        if args.json:
            print_json(task)
        else:
            print(f"Created {task['id']}: {task['title']}")

    elif args.task_cmd == "list":
        tasks = research.list_tasks(plan_id=args.plan, status=args.status)
        if args.json:
            print_json(tasks)
        else:
            for t in tasks:
                deps = " (deps: " + ", ".join(t["depends_on"] or []) + ")" if t.get("depends_on") else ""
                print(f"{t['id']} · {t['title']} ({t['status']}){deps}")

    elif args.task_cmd == "show":
        task = research.get_task_dependencies(task_id=args.id)
        if args.json:
            print_json(task)
        else:
            print_task_detail(task)

    elif args.task_cmd == "next":
        task = research.get_next_ready_task(plan_id=args.plan)
        if args.json:
            print_json(task or {})
        else:
            if task:
                print(f"Next ready task: {task['id']} · {task['title']}")
            else:
                print("No ready tasks")

    elif args.task_cmd == "update":
        updates = {}
        if args.status:
            updates["status"] = args.status
        if args.role:
            updates["assigned_role"] = args.role
        if args.blockers:
            updates["blockers"] = args.blockers

        task = research.update_task(args.id, **updates)
        if args.json:
            print_json(task)
        else:
            print(f"Updated {task['id']}")


# Add to main argparse setup:

subparsers.add_parser("plan", help="Plan commands").set_defaults(func=cmd_plan)
subparsers.add_parser("task", help="Task commands").set_defaults(func=cmd_task)

# Detailed argparse setup for plan/task subcommands would go here
# (similar to existing question/experiment commands)
```

---

## 6. RPC Methods

### 6.1 Add to packages/core/research/rpc.py

```python
# packages/core/research/rpc.py

# Add to METHODS dict:

METHODS.update({
    "create_plan": lambda p, **kw: _plans.create_plan(p, **kw),
    "create_task": lambda p, **kw: _plans.create_task(p, **kw),
    "update_plan": lambda p, id, **kw: _plans.update_plan(p, id, **kw),
    "update_task": lambda p, id, **kw: _plans.update_task(p, id, **kw),
    "list_plans": lambda p, **kw: _plans.list_plans(p, **kw),
    "list_tasks": lambda p, **kw: _plans.list_tasks(p, **kw),
    "get_plan_with_tasks": lambda p, id: _plans.get_plan_with_tasks(p, id),
    "get_next_ready_task": lambda p, **kw: _plans.get_next_ready_task(p, **kw),
})
```

---

## 7. Tests

### 7.1 Create tests/python/test_plans_tasks.py

```python
"""Tests for Plan and Task entities."""
import pytest
import research
from research.util import NotFound, ResearchError


def test_create_plan(tmp_project):
    """Test plan creation."""
    p = tmp_project

    plan = research.create_plan(
        title="Reproduce Paper X",
        objective="Reproduce main results",
        success_criteria="Metrics match within tolerance",
        project=p
    )

    assert plan["id"] == "PLAN-001"
    assert plan["title"] == "Reproduce Paper X"
    assert plan["status"] == "active"
    assert plan["objective"] == "Reproduce main results"

    # Check filesystem
    path = p.rdir / "plans" / "PLAN-001.md"
    assert path.exists()
    content = path.read_text()
    assert "# PLAN-001 · Reproduce Paper X" in content
    assert "## Objective" in content


def test_create_task(tmp_project):
    """Test task creation."""
    p = tmp_project

    plan = research.create_plan(title="Test Plan", project=p)

    task = research.create_task(
        plan_id=plan["id"],
        title="Implement baseline",
        goal="Working baseline model",
        task_type="implementation",
        status="todo",
        project=p
    )

    assert task["id"] == "T-001"
    assert task["plan_id"] == "PLAN-001"
    assert task["title"] == "Implement baseline"
    assert task["status"] == "todo"
    assert task["task_type"] == "implementation"

    # Check filesystem
    path = p.rdir / "tasks" / "T-001.md"
    assert path.exists()
    content = path.read_text()
    assert "# T-001 · Implement baseline" in content


def test_task_dependencies(tmp_project):
    """Test task dependency resolution."""
    p = tmp_project

    plan = research.create_plan(title="Test Plan", project=p)

    # Create tasks with dependencies
    t1 = research.create_task(
        plan_id=plan["id"],
        title="Task 1",
        status="done",
        project=p
    )

    t2 = research.create_task(
        plan_id=plan["id"],
        title="Task 2",
        status="done",
        project=p
    )

    t3 = research.create_task(
        plan_id=plan["id"],
        title="Task 3",
        depends_on=[t1["id"], t2["id"]],
        status="todo",
        project=p
    )

    # Check dependency resolution
    deps = research.get_task_dependencies(t3["id"], project=p)
    assert deps["is_ready"] is True  # Both deps are done
    assert len(deps["dependencies"]) == 2


def test_next_ready_task(tmp_project):
    """Test getting next ready task."""
    p = tmp_project

    plan = research.create_plan(title="Test Plan", project=p)

    # Create task chain
    t1 = research.create_task(
        plan_id=plan["id"],
        title="Task 1",
        status="todo",
        project=p
    )

    t2 = research.create_task(
        plan_id=plan["id"],
        title="Task 2",
        depends_on=[t1["id"]],
        status="todo",
        project=p
    )

    # T1 should be next (no deps)
    next_task = research.get_next_ready_task(plan_id=plan["id"], project=p)
    assert next_task["id"] == t1["id"]

    # Complete T1
    research.update_task(t1["id"], status="done", project=p)

    # Now T2 should be next
    next_task = research.get_next_ready_task(plan_id=plan["id"], project=p)
    assert next_task["id"] == t2["id"]


def test_plan_with_tasks(tmp_project):
    """Test getting plan with all tasks."""
    p = tmp_project

    plan = research.create_plan(title="Test Plan", project=p)

    for i in range(3):
        research.create_task(
            plan_id=plan["id"],
            title=f"Task {i+1}",
            project=p
        )

    full_plan = research.get_plan_with_tasks(plan["id"], project=p)
    assert len(full_plan["tasks"]) == 3


def test_migration_v2(tmp_project):
    """Test schema migration from v1 to v2."""
    p = tmp_project

    # Check new tables exist
    tables = p.query("SELECT name FROM sqlite_master WHERE type='table'")
    table_names = [t["name"] for t in tables]

    assert "plans" in table_names
    assert "tasks" in table_names

    # Check indices
    indices = p.query("SELECT name FROM sqlite_master WHERE type='index'")
    index_names = [i["name"] for i in indices]

    assert "idx_tasks_plan" in index_names
    assert "idx_tasks_status" in index_names


def test_plan_task_rebuild(tmp_project):
    """Test that plans and tasks survive rebuild."""
    p = tmp_project

    # Create plan and tasks
    plan = research.create_plan(title="Rebuild Test", project=p)
    task = research.create_task(plan_id=plan["id"], title="Test Task", project=p)

    # Rebuild index
    p.close()
    import os
    os.remove(p.db_path)

    p2 = research.Project(p.root)

    # Verify plan and task restored
    restored_plan = p2.get("plan", plan["id"])
    restored_task = p2.get("task", task["id"])

    assert restored_plan["title"] == "Rebuild Test"
    assert restored_task["title"] == "Test Task"
    assert restored_task["plan_id"] == plan["id"]
```

---

## 8. Validation Checklist

### 8.1 Before merging Phase 0:

- [ ] All existing tests pass
- [ ] New tests pass (test_plans_tasks.py)
- [ ] Schema migration v1→v2 works
- [ ] Can create Plan and Task via CLI
- [ ] Can create Plan and Task via Python API
- [ ] Can create Plan and Task via RPC
- [ ] Mirror files render correctly
- [ ] Rebuild reconstructs Plan/Task from mirrors
- [ ] Hand-edit Plan/Task markdown → imported correctly
- [ ] Plan/Task appear in events.jsonl
- [ ] Dependencies resolve correctly
- [ ] `get_next_ready_task` returns correct task

### 8.2 Manual testing:

```bash
# Initialize test project
cd /tmp
mkdir test-research-os && cd test-research-os
research init --name "Test" --goal "Test Plan/Task"

# Create plan
research plan create "Reproduce Paper X" \
  --objective "Test objective" \
  --success "Metrics match"

# Create tasks
research task create PLAN-001 "Understand paper" \
  --type research --status todo

research task create PLAN-001 "Implement baseline" \
  --type implementation --status todo \
  --depends T-001

research task create PLAN-001 "Run experiments" \
  --type experiment --status todo \
  --depends T-002

# Check next ready task
research task next  # Should return T-001

# Complete task
research task update T-001 --status done

# Check next ready task
research task next  # Should return T-002

# View plan
research plan show PLAN-001

# Rebuild
research rebuild

# Verify still works
research plan show PLAN-001
```

---

## 9. File Checklist

### Files to create:
- [x] `docs/PHASE_0_SPEC.md` (this file)
- [ ] `packages/core/research/plans.py`
- [ ] `tests/python/test_plans_tasks.py`

### Files to modify:
- [ ] `packages/core/research/schema.py` — Add Plan/Task schemas + migration v2
- [ ] `packages/core/research/store.py` — Add plans/tasks to SUBDIRS
- [ ] `packages/core/research/mirrors.py` — Add Plan/Task tail rendering
- [ ] `packages/core/research/__init__.py` — Export plan/task functions
- [ ] `packages/core/research/rpc.py` — Add plan/task RPC methods
- [ ] `packages/cli/research_cli/main.py` — Add plan/task CLI commands

---

## 10. Estimated Effort

**Development:** 3-4 days
**Testing:** 1 day
**Documentation:** 0.5 days

**Total: ~5 days** (1 week with buffer)

---

## Success Criteria

Phase 0 is complete when:

1. ✅ All existing tests pass
2. ✅ Can create/update/list Plan and Task entities
3. ✅ Plan/Task mirrors render correctly
4. ✅ Plan/Task survive rebuild from filesystem
5. ✅ Hand-edited Plan/Task markdown imports correctly
6. ✅ Task dependencies resolve correctly
7. ✅ `get_next_ready_task` returns correct task based on deps
8. ✅ CLI commands work: `research plan create/list/show`, `research task create/list/show/next`
9. ✅ RPC methods work (for VS Code UI)
10. ✅ Schema migration v1→v2 completes successfully

**No breaking changes.** Existing projects without Plans/Tasks continue to work.

---

## Next Phase Preview

**Phase 1: Task Context Generation**
- Implement tiered context system
- `research plan current` — concise current state
- `research task context T-042` — focused task context
- Update AGENTS.md to bootstrap approach (~500 tokens)
- Agents can operate with bounded context
