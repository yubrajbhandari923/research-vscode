"""research — project-local research memory: questions → experiments → runs → findings → decisions → checkpoints.

Python API (all functions operate on the project containing the current directory,
or $RESEARCH_ROOT, unless you pass `project=Project(...)`):

    import research
    research.get_resume_context()
    q = research.create_question("Does alpha affect error?")
    e = research.create_experiment("alpha sweep", question=q["id"], hypothesis="…", parameters={"alpha": [0.1, 0.5]})
    run = research.create_run(e["id"], command="python sweep.py --alpha 0.1", parameters={"alpha": 0.1})
    research.log_metric("rmse", 0.12, run=run["id"])       # inside a `research run exec` job: run is implicit
    research.register_artifact("outputs/plot.png", run=run["id"], description="error vs alpha")
    research.create_finding("alpha=0.5 best tradeoff", supports=[e["id"], run["id"]], confidence="medium")
    research.create_decision("Use alpha=0.5 as baseline", supporting_findings=["F-001"])
    research.create_checkpoint()

    # Plans and Tasks (Phase 0)
    plan = research.create_plan("Reproduce paper X", objective="Reproduce Table 1")
    t1 = research.create_task(plan["id"], "Implement baseline model", goal="...")
    t2 = research.create_task(plan["id"], "Run experiments", depends_on=[t1["id"]])
    research.start_task(t1["id"])
    research.complete_task(t1["id"], result="baseline implemented in model.py")
"""
from __future__ import annotations

import functools
from typing import Any, Dict, List, Optional

from . import context as _context
from . import plans as _plans
from . import runs as _runs
from . import services as _services
from . import views as _views
from .store import Project, find_root
from .util import BudgetExceeded, NotFound, NotInitialized, ResearchError

__version__ = "0.1.0"

__all__ = [
    "Project", "ResearchError", "BudgetExceeded", "NotFound", "NotInitialized", "init", "open_project",
    "get_project_context", "get_resume_context", "list_questions", "create_question", "update_question",
    "create_experiment", "create_variant", "get_experiment", "list_experiments", "update_experiment",
    "set_status", "synthesize", "set_baseline",
    "create_run", "attach_run", "update_run", "get_run", "list_runs", "exec_run", "submit_run", "cancel_run",
    "register_artifact", "list_artifacts", "log_metric",
    "create_finding", "update_finding", "list_findings", "supersede_finding",
    "create_decision", "list_decisions", "create_checkpoint", "checkpoint_draft", "list_checkpoints",
    "create_note", "list_notes", "list_skills", "show", "regenerate_context",
    # Phase 0: Plans and Tasks
    "create_plan", "update_plan", "list_plans", "get_plan",
    "create_task", "update_task", "list_tasks", "get_task", "get_next_ready_task",
    "start_task", "complete_task", "block_task",
]


def init(root: str = ".", name: Optional[str] = None, goal: Optional[str] = None) -> Project:
    return Project.init(root, name=name, goal=goal)


def open_project(root: Optional[str] = None) -> Project:
    return Project(root) if root else Project.find()


def _with_project(fn):
    @functools.wraps(fn)
    def wrapper(*args, project: Optional[Project] = None, **kwargs):
        if project is not None:
            return fn(project, *args, **kwargs)
        p = Project.find()
        try:
            return fn(p, *args, **kwargs)
        finally:
            p.close()
    return wrapper


# ------------------------------------------------------------------------------------ context
@_with_project
def get_resume_context(p: Project) -> Dict[str, Any]:
    return _context.resume_context(p)


get_project_context = get_resume_context


@_with_project
def regenerate_context(p: Project) -> str:
    return _context.write_current_md(p)


@_with_project
def show(p: Project, id_: str) -> Dict[str, Any]:
    return _views.show(p, id_)


# ------------------------------------------------------------------------------------ questions
@_with_project
def list_questions(p: Project, status: Optional[str] = None) -> List[Dict[str, Any]]:
    return p.list("question", "status=?" if status else "", (status,) if status else ())


create_question = _with_project(_services.create_question)
update_question = _with_project(_services.update_question)

# ------------------------------------------------------------------------------------ experiments
create_experiment = _with_project(_services.create_experiment)
create_variant = _with_project(_services.create_variant)
update_experiment = _with_project(_services.update_experiment)
set_status = _with_project(_services.set_status)
synthesize = _with_project(_services.synthesize)
set_baseline = _with_project(_services.set_baseline)


@_with_project
def get_experiment(p: Project, exp_id: str) -> Dict[str, Any]:
    return _views.experiment_detail(p, exp_id)


@_with_project
def list_experiments(p: Project, status: Optional[str] = None, question: Optional[str] = None) -> List[Dict[str, Any]]:
    where, args = [], []
    if status:
        where.append("status=?")
        args.append(status)
    if question:
        from .schema import normalize_id
        where.append("question_id=?")
        args.append(normalize_id(question, "Q"))
    return p.list("experiment", " AND ".join(where), tuple(args))


# ------------------------------------------------------------------------------------ runs
create_run = _with_project(_runs.create_run)
attach_run = _with_project(_runs.attach_run)
update_run = _with_project(_runs.update_run)
get_run = _with_project(_runs.get_run)
list_runs = _with_project(_runs.list_runs)
exec_run = _with_project(_runs.exec_run)
submit_run = _with_project(_runs.submit_run)
cancel_run = _with_project(_runs.cancel_run)
list_artifacts = _with_project(_runs.list_artifacts)


def log_metric(name: str, value: Any, step: Optional[int] = None, unit: Optional[str] = None,
               run: Optional[str] = None, experiment: Optional[str] = None,
               project: Optional[Project] = None) -> Dict[str, Any]:
    """Log a metric. Inside a `research run` job (RESEARCH_RUN_DIR set) this only appends to the
    run directory — safe on SLURM compute nodes, no database access."""
    if run is None and experiment is None and project is None and _runs.in_job_run_dir():
        return _runs.job_log_metric(name, value, step, unit)
    return _with_project(_runs.log_metric)(name, value, step, unit, run, experiment, project=project)


def register_artifact(path: str, run: Optional[str] = None, experiment: Optional[str] = None,
                      name: Optional[str] = None, type: Optional[str] = None,
                      description: Optional[str] = None, tags: Any = None,
                      project: Optional[Project] = None, **kw: Any) -> Dict[str, Any]:
    """Register a file/dir by reference. Inside a running job this is recorded in the run dir
    and ingested (assigned an A-id) the next time the project is synced."""
    if run is None and experiment is None and project is None and _runs.in_job_run_dir():
        return _runs.job_register_artifact(path, name, description, type, tags)
    return _with_project(_runs.register_artifact)(path, run, experiment, name, type, description, tags,
                                                  project=project, **kw)


# ------------------------------------------------------------------------------------ findings/decisions
create_finding = _with_project(_services.create_finding)
update_finding = _with_project(_services.update_finding)
supersede_finding = _with_project(_services.supersede_finding)
create_decision = _with_project(_services.create_decision)


@_with_project
def list_findings(p: Project, status: Optional[str] = None, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    where, args = [], []
    if status:
        where.append("status=?")
        args.append(status)
    if kind:
        where.append("kind=?")
        args.append(kind)
    return p.list("finding", " AND ".join(where), tuple(args))


@_with_project
def list_decisions(p: Project, status: Optional[str] = None) -> List[Dict[str, Any]]:
    return p.list("decision", "status=?" if status else "", (status,) if status else ())


# ------------------------------------------------------------------------------------ checkpoints/notes/skills
create_checkpoint = _with_project(_context.create_checkpoint)
checkpoint_draft = _with_project(_context.checkpoint_draft)


@_with_project
def list_checkpoints(p: Project) -> List[Dict[str, Any]]:
    return p.list("checkpoint", order="created_at DESC, id DESC")


create_note = _with_project(_services.create_note)
list_notes = _with_project(_services.list_notes)
list_skills = _with_project(_services.list_skills)


# ------------------------------------------------------------------------------------ plans/tasks (Phase 0)
create_plan = _with_project(_plans.create_plan)
update_plan = _with_project(_plans.update_plan)
list_plans = _with_project(_plans.list_plans)
create_task = _with_project(_plans.create_task)
update_task = _with_project(_plans.update_task)
list_tasks = _with_project(_plans.list_tasks)
start_task = _with_project(_plans.start_task)
complete_task = _with_project(_plans.complete_task)
block_task = _with_project(_plans.block_task)
get_next_ready_task = _with_project(_plans.get_next_ready_task)


@_with_project
def get_plan(p: Project, plan_id: str) -> Dict[str, Any]:
    """Get plan with all its tasks and summary statistics."""
    return _plans.get_plan_with_tasks(p, plan_id)


@_with_project
def get_task(p: Project, task_id: str) -> Dict[str, Any]:
    """Get task with resolved dependencies and readiness status."""
    return _plans.get_task_with_deps(p, task_id)
