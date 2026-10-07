"""Tests for Plan and Task entities (Phase 0)."""
import json
import os
import pytest

from research import plans as P
from research import services as S
from research import views as V
from research.schema import STATUSES, normalize_id
from research.store import Project
from research.util import NotFound, ResearchError

from conftest import git


# ---------------------------------------------------------------------------- Plan CRUD

def test_create_plan(proj):
    plan = P.create_plan(proj, "Reproduce Paper X",
                         objective="Reproduce Table 1 results",
                         success_criteria="Match paper metrics within 5%")
    assert plan["id"] == "PLAN-001"
    assert plan["title"] == "Reproduce Paper X"
    assert plan["status"] == "active"
    assert plan["objective"] == "Reproduce Table 1 results"
    assert plan["success_criteria"] == "Match paper metrics within 5%"
    assert "created_at" in plan


def test_create_plan_with_question(proj):
    q = S.create_question(proj, "Does method X scale?")
    plan = P.create_plan(proj, "Scalability study", root_question_id=q["id"],
                         objective="Test method X on larger datasets")
    assert plan["root_question_id"] == "Q-001"


def test_create_plan_invalid_question_raises(proj):
    with pytest.raises(NotFound):
        P.create_plan(proj, "Bad plan", root_question_id="Q-999")


def test_create_plan_invalid_status_raises(proj):
    with pytest.raises(ResearchError):
        P.create_plan(proj, "Bad plan", status="invalid_status")


def test_update_plan(proj):
    plan = P.create_plan(proj, "Plan 1")
    updated = P.update_plan(proj, plan["id"],
                            title="Plan 1 Updated",
                            objective="New objective",
                            status="completed")
    assert updated["title"] == "Plan 1 Updated"
    assert updated["objective"] == "New objective"
    assert updated["status"] == "completed"
    assert updated["completed_at"] is not None  # Auto-set on completion


def test_list_plans(proj):
    P.create_plan(proj, "Plan A")
    P.create_plan(proj, "Plan B", status="completed")
    P.create_plan(proj, "Plan C")

    all_plans = P.list_plans(proj)
    assert len(all_plans) == 3

    active = P.list_plans(proj, status="active")
    assert len(active) == 2

    completed = P.list_plans(proj, status="completed")
    assert len(completed) == 1


def test_get_plan_with_tasks(proj):
    plan = P.create_plan(proj, "My Plan")
    P.create_task(proj, plan["id"], "Task 1")
    P.create_task(proj, plan["id"], "Task 2")
    P.create_task(proj, plan["id"], "Task 3", status="done")

    detail = P.get_plan_with_tasks(proj, plan["id"])
    assert detail["task_count"] == 3
    assert detail["tasks_done"] == 1
    assert detail["tasks_running"] == 0
    assert len(detail["tasks"]) == 3


# ---------------------------------------------------------------------------- Task CRUD

def test_create_task(proj):
    plan = P.create_plan(proj, "Plan")
    task = P.create_task(proj, plan["id"], "Implement baseline",
                         goal="Create baseline model implementation",
                         task_type="implementation",
                         acceptance_criteria="Tests pass, model runs")
    assert task["id"] == "T-001"
    assert task["plan_id"] == "PLAN-001"
    assert task["title"] == "Implement baseline"
    assert task["status"] == "todo"
    assert task["goal"] == "Create baseline model implementation"
    assert task["task_type"] == "implementation"
    assert task["acceptance_criteria"] == "Tests pass, model runs"


def test_create_task_invalid_plan_raises(proj):
    with pytest.raises(NotFound):
        P.create_task(proj, "PLAN-999", "Orphan task")


def test_create_task_with_dependencies(proj):
    plan = P.create_plan(proj, "Plan")
    t1 = P.create_task(proj, plan["id"], "Step 1")
    t2 = P.create_task(proj, plan["id"], "Step 2")
    t3 = P.create_task(proj, plan["id"], "Step 3",
                       depends_on=[t1["id"], t2["id"]])

    assert t3["depends_on"] == ["T-001", "T-002"]


def test_create_task_invalid_dependency_raises(proj):
    plan = P.create_plan(proj, "Plan")
    with pytest.raises(NotFound):
        P.create_task(proj, plan["id"], "Task", depends_on=["T-999"])


def test_create_task_with_related_entities(proj):
    plan = P.create_plan(proj, "Plan")
    q = S.create_question(proj, "Question?")
    e = S.create_experiment(proj, "Exp")

    task = P.create_task(proj, plan["id"], "Task",
                         related_question_id=q["id"],
                         related_experiment_id=e["id"])

    assert task["related_question_id"] == "Q-001"
    assert task["related_experiment_id"] == "EXP-001"


def test_update_task(proj):
    plan = P.create_plan(proj, "Plan")
    task = P.create_task(proj, plan["id"], "Task 1")

    updated = P.update_task(proj, task["id"],
                            title="Updated Task",
                            goal="New goal",
                            task_type="research")

    assert updated["title"] == "Updated Task"
    assert updated["goal"] == "New goal"
    assert updated["task_type"] == "research"


def test_list_tasks(proj):
    plan = P.create_plan(proj, "Plan")
    P.create_task(proj, plan["id"], "Task 1")
    P.create_task(proj, plan["id"], "Task 2", status="running")
    P.create_task(proj, plan["id"], "Task 3", status="done")

    all_tasks = P.list_tasks(proj)
    assert len(all_tasks) == 3

    by_plan = P.list_tasks(proj, plan_id=plan["id"])
    assert len(by_plan) == 3

    todo = P.list_tasks(proj, status="todo")
    assert len(todo) == 1

    running = P.list_tasks(proj, status="running")
    assert len(running) == 1


# ---------------------------------------------------------------------------- Task Workflow

def test_start_task(proj):
    plan = P.create_plan(proj, "Plan")
    task = P.create_task(proj, plan["id"], "Task")

    assert task["status"] == "todo"
    assert task["started_at"] is None

    started = P.start_task(proj, task["id"], assigned_role="implementer")
    assert started["status"] == "running"
    assert started["started_at"] is not None
    assert started["assigned_role"] == "implementer"


def test_complete_task(proj):
    plan = P.create_plan(proj, "Plan")
    task = P.create_task(proj, plan["id"], "Task")
    P.start_task(proj, task["id"])

    completed = P.complete_task(proj, task["id"],
                                 result="Implemented successfully",
                                 completed_by="agent:claude")

    assert completed["status"] == "done"
    assert completed["completed_at"] is not None
    assert completed["result"] == "Implemented successfully"
    assert completed["completed_by"] == "agent:claude"


def test_block_task(proj):
    plan = P.create_plan(proj, "Plan")
    task = P.create_task(proj, plan["id"], "Task")

    blocked = P.block_task(proj, task["id"], "Missing API access")
    assert blocked["status"] == "blocked"
    assert blocked["blockers"] == "Missing API access"


# ---------------------------------------------------------------------------- Derived Ready State

def test_task_is_ready_no_deps(proj):
    plan = P.create_plan(proj, "Plan")
    task = P.create_task(proj, plan["id"], "Task")

    detail = P.get_task_with_deps(proj, task["id"])
    assert detail["is_ready"] is True  # No deps, status=todo -> ready


def test_task_is_ready_all_deps_done(proj):
    plan = P.create_plan(proj, "Plan")
    t1 = P.create_task(proj, plan["id"], "Prereq 1")
    t2 = P.create_task(proj, plan["id"], "Prereq 2")
    t3 = P.create_task(proj, plan["id"], "Main task",
                       depends_on=[t1["id"], t2["id"]])

    # Initially not ready (deps not done)
    detail = P.get_task_with_deps(proj, t3["id"])
    assert detail["is_ready"] is False

    # Complete one dep
    P.complete_task(proj, t1["id"])
    detail = P.get_task_with_deps(proj, t3["id"])
    assert detail["is_ready"] is False

    # Complete second dep
    P.complete_task(proj, t2["id"])
    detail = P.get_task_with_deps(proj, t3["id"])
    assert detail["is_ready"] is True


def test_task_not_ready_if_not_todo(proj):
    plan = P.create_plan(proj, "Plan")
    task = P.create_task(proj, plan["id"], "Task")
    P.start_task(proj, task["id"])  # Now running

    detail = P.get_task_with_deps(proj, task["id"])
    assert detail["is_ready"] is False  # Already running, not ready


# ---------------------------------------------------------------------------- Get Next Ready Task

def test_get_next_ready_task(proj):
    plan = P.create_plan(proj, "Plan")
    t1 = P.create_task(proj, plan["id"], "Task 1")
    t2 = P.create_task(proj, plan["id"], "Task 2", depends_on=[t1["id"]])

    # First ready task should be t1
    next_task = P.get_next_ready_task(proj, plan["id"])
    assert next_task["id"] == t1["id"]
    assert next_task["is_ready"] is True

    # Complete t1, now t2 should be ready
    P.complete_task(proj, t1["id"])
    next_task = P.get_next_ready_task(proj, plan["id"])
    assert next_task["id"] == t2["id"]

    # Complete t2, no more ready tasks
    P.complete_task(proj, t2["id"])
    next_task = P.get_next_ready_task(proj, plan["id"])
    assert next_task is None


def test_get_next_ready_task_across_plans(proj):
    plan1 = P.create_plan(proj, "Plan 1")
    plan2 = P.create_plan(proj, "Plan 2")

    t1 = P.create_task(proj, plan1["id"], "Plan1 Task")
    t2 = P.create_task(proj, plan2["id"], "Plan2 Task")

    # Without plan filter, gets first ready
    next_task = P.get_next_ready_task(proj)
    assert next_task is not None

    # With plan filter
    next_task = P.get_next_ready_task(proj, plan2["id"])
    assert next_task["id"] == t2["id"]


# ---------------------------------------------------------------------------- Views and Mirror

def test_plan_detail_view(proj):
    plan = P.create_plan(proj, "Plan")
    P.create_task(proj, plan["id"], "Task 1")
    P.create_task(proj, plan["id"], "Task 2", status="done")

    detail = V.plan_detail(proj, plan["id"])
    assert detail["id"] == "PLAN-001"
    assert detail["task_count"] == 2
    assert detail["tasks_done"] == 1


def test_task_detail_view(proj):
    plan = P.create_plan(proj, "Plan")
    t1 = P.create_task(proj, plan["id"], "Task 1")
    t2 = P.create_task(proj, plan["id"], "Task 2", depends_on=[t1["id"]])

    detail = V.task_detail(proj, t2["id"])
    assert detail["id"] == "T-002"
    assert detail["plan"]["id"] == "PLAN-001"
    assert len(detail["dependencies"]) == 1


def test_plan_mirror_created(proj, repo):
    plan = P.create_plan(proj, "Plan")
    mirror = repo / ".research" / "plans" / f"{plan['id']}.md"
    assert mirror.exists()
    content = mirror.read_text()
    assert "PLAN-001" in content
    assert "Plan" in content


def test_task_mirror_created(proj, repo):
    plan = P.create_plan(proj, "Plan")
    task = P.create_task(proj, plan["id"], "Task", goal="Do something")
    mirror = repo / ".research" / "tasks" / f"{task['id']}.md"
    assert mirror.exists()
    content = mirror.read_text()
    assert "T-001" in content
    assert "Task" in content
    assert "Do something" in content


# ---------------------------------------------------------------------------- ID Normalization

def test_plan_id_normalization():
    assert normalize_id("plan-1") == "PLAN-001"
    assert normalize_id("PLAN12") == "PLAN-012"


def test_task_id_normalization():
    assert normalize_id("t-1") == "T-001"
    assert normalize_id("T42") == "T-042"


# ---------------------------------------------------------------------------- Schema Migration

def test_plans_tasks_tables_exist(proj):
    # Check tables exist
    tables = [r[0] for r in proj.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "plans" in tables
    assert "tasks" in tables


def test_plans_tasks_indices_exist(proj):
    indices = [r[0] for r in proj.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()]
    assert "idx_tasks_plan" in indices
    assert "idx_tasks_status" in indices
    assert "idx_plans_status" in indices


# ---------------------------------------------------------------------------- Rebuild from mirrors

def test_rebuild_preserves_plans_and_tasks(proj, repo):
    plan = P.create_plan(proj, "Plan", objective="Obj")
    task = P.create_task(proj, plan["id"], "Task", goal="Goal")

    # Rebuild index
    proj.rebuild_index(backup=False)

    # Should still be there
    p = proj.get("plan", plan["id"])
    assert p["title"] == "Plan"
    assert p["objective"] == "Obj"

    t = proj.get("task", task["id"])
    assert t["title"] == "Task"
    assert t["goal"] == "Goal"


# ---------------------------------------------------------------------------- Integration with existing entities

def test_task_links_to_experiment_and_question(proj):
    q = S.create_question(proj, "How does X work?")
    e = S.create_experiment(proj, "Test X")
    plan = P.create_plan(proj, "Plan", root_question_id=q["id"])
    task = P.create_task(proj, plan["id"], "Run experiments",
                         related_question_id=q["id"],
                         related_experiment_id=e["id"])

    detail = V.task_detail(proj, task["id"])
    assert detail["question"]["id"] == q["id"]
    assert detail["experiment"]["id"] == e["id"]
