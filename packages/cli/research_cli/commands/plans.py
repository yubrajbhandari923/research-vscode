"""Plans and tasks."""
from __future__ import annotations

from typing import Any, Dict

from research import plans as P
from research.schema import STATUSES, TASK_TYPES, TASK_ROLES

from .agents import _dispatch_args, cmd_dispatch
from . import mut
from ..output import _done, author, bold, dim, green, kv, out_json, print_list, red, section, st


def print_plan(plan: Dict[str, Any]) -> None:
    print(f"{bold(plan['id'])}  {bold(plan['title'])}  [{st(plan['status'])}]")
    kv("objective", plan.get("objective"))
    kv("success criteria", plan.get("success_criteria"))
    kv("context", plan.get("context"))
    if plan.get("root_question_id"):
        kv("question", plan.get("root_question_id"))
    kv("author", author(plan))
    if plan.get("tasks"):
        section(f"Tasks ({plan['tasks_done']}/{plan['task_count']} done)")
        for t in plan["tasks"]:
            ready_str = ""
            if t["status"] == "todo" and t.get("is_ready"):
                ready_str = green(" ✓ ready")
            deps = t.get("depends_on") or []
            dep_str = dim(f" → {', '.join(deps)}") if deps else ""
            print(f"  {t['id']}  [{st(t['status'])}]  {t['title']}{ready_str}{dep_str}")
    elif plan.get("task_count", 0) == 0:
        print(dim("  (no tasks yet)"))


def print_task(task: Dict[str, Any]) -> None:
    print(f"{bold(task['id'])}  {bold(task['title'])}  [{st(task['status'])}]")
    if task.get("is_ready"):
        print(green("  ✓ Ready to start (all dependencies satisfied)"))
    kv("plan", task.get("plan_id"))
    kv("type", task.get("task_type"))
    kv("role", task.get("assigned_role"))
    kv("goal", task.get("goal"))
    kv("inputs", task.get("inputs"))
    kv("expected outputs", task.get("expected_outputs"))
    kv("acceptance", task.get("acceptance_criteria"))
    kv("verification", task.get("verification"))
    if task.get("dependencies"):
        section("Dependencies")
        for d in task["dependencies"]:
            icon = green("✓") if d["status"] == "done" else dim("○")
            print(f"  {icon} {d['id']}  {d['title']}  [{st(d['status'])}]")
    if task.get("related_question_id"):
        kv("question", task.get("related_question_id"))
    if task.get("related_experiment_id"):
        kv("experiment", task.get("related_experiment_id"))
    if task.get("claimed_by"):
        kv("claimed by", f"{task['claimed_by']} · {task.get('claimed_at') or ''}")
    if task.get("checks"):
        from research.verification import describe_check, parse_check
        kv("checks", "\n".join(describe_check(parse_check(c)) for c in task["checks"]))
    kv("skills", task.get("skills"))
    kv("result", task.get("result"))
    kv("blockers", task.get("blockers"))
    kv("notes", task.get("notes"))
    kv("started", task.get("started_at"))
    kv("completed", task.get("completed_at"))
    if task.get("completed_by"):
        kv("completed by", task.get("completed_by"))
    kv("author", author(task))


@mut
def cmd_plan_create(a, p) -> int:
    plan = P.create_plan(p, a.title, objective=a.objective, status=a.status,
                         root_question_id=a.question, success_criteria=a.success, context=a.context, skills=a.skill)
    _done(a, plan, msg=f"{plan['id']}  {plan['title']}  [{plan['status']}]")
    return 0


def cmd_plan_list(a, p) -> int:
    rows = P.list_plans(p, a.status)
    for r in rows:
        tasks = P.list_tasks(p, plan_id=r["id"])
        done = sum(1 for t in tasks if t["status"] == "done")
        r["progress"] = f"{done}/{len(tasks)} tasks"
    out_json(rows) if a.json else print_list(rows, ["id", "status", "title", "progress", "author_type"])
    return 0


def cmd_plan_show(a, p) -> int:
    plan = P.get_plan_with_tasks(p, a.id)
    # Add is_ready to each task for display
    for t in plan.get("tasks", []):
        if t["status"] == "todo":
            deps = t.get("depends_on") or []
            all_done = True
            for d in deps:
                dep_task = p.get("task", d)
                if dep_task["status"] != "done":
                    all_done = False
                    break
            t["is_ready"] = (len(deps) == 0 or all_done)
    if a.json:
        out_json(plan)
    else:
        print_plan(plan)
    return 0


@mut
def cmd_plan_update(a, p) -> int:
    plan = P.update_plan(p, a.id, title=a.title, objective=a.objective, status=a.status,
                         root_question_id=a.question, success_criteria=a.success, context=a.context, skills=a.skill)
    _done(a, plan, msg=f"{plan['id']} updated")
    return 0


@mut
def cmd_task_create(a, p) -> int:
    task = P.create_task(p, a.plan, a.title, goal=a.goal, task_type=a.type, status=a.status,
                         assigned_role=a.role, depends_on=a.depends, inputs=a.inputs,
                         expected_outputs=a.outputs, acceptance_criteria=a.acceptance,
                         verification=a.verification, related_question_id=a.question,
                         related_experiment_id=a.experiment, notes=a.notes, skills=a.skill, checks=a.check)
    _done(a, task, msg=f"{task['id']}  {task['title']}  [{task['status']}]")
    return 0


def cmd_task_list(a, p) -> int:
    rows = P.list_tasks(p, a.plan, a.status)
    for r in rows:
        deps = r.get("depends_on") or []
        r["deps"] = ", ".join(deps) if deps else None
    out_json(rows) if a.json else print_list(rows, ["id", "plan_id", "status", "title", "task_type", "deps"])
    return 0


def cmd_task_show(a, p) -> int:
    task = P.get_task_with_deps(p, a.id)
    if a.json:
        out_json(task)
    else:
        print_task(task)
    return 0


@mut
def cmd_task_update(a, p) -> int:
    kw = {}
    if a.title:
        kw["title"] = a.title
    if a.goal:
        kw["goal"] = a.goal
    if a.type:
        kw["task_type"] = a.type
    if a.status:
        kw["status"] = a.status
    if a.role:
        kw["assigned_role"] = a.role
    if a.depends:
        kw["depends_on"] = a.depends
    if a.inputs:
        kw["inputs"] = a.inputs
    if a.outputs:
        kw["expected_outputs"] = a.outputs
    if a.acceptance:
        kw["acceptance_criteria"] = a.acceptance
    if a.verification:
        kw["verification"] = a.verification
    if a.question:
        kw["related_question_id"] = a.question
    if a.experiment:
        kw["related_experiment_id"] = a.experiment
    if a.result:
        kw["result"] = a.result
    if a.blockers:
        kw["blockers"] = a.blockers
    if a.notes:
        kw["notes"] = a.notes
    if a.check is not None:
        kw["checks"] = [c for c in a.check if c]  # --check "" clears
    if a.skill is not None:
        kw["skills"] = [x for x in a.skill if x]
    task = P.update_task(p, a.id, **kw)
    _done(a, task, msg=f"{task['id']} updated")
    return 0


@mut
def cmd_task_start(a, p) -> int:
    task = P.start_task(p, a.id, a.role, force=a.force)
    _done(a, task, msg=f"{task['id']} → running")
    return 0


@mut
def cmd_task_done(a, p) -> int:
    task = P.complete_task(p, a.id, result=a.result, completed_by=a.by, artifacts=a.artifacts)
    _done(a, task, msg=f"{task['id']} → done")
    return 0


@mut
def cmd_task_block(a, p) -> int:
    task = P.block_task(p, a.id, a.reason)
    _done(a, task, msg=f"{task['id']} → blocked")
    return 0


def print_task_context(c: Dict[str, Any]) -> None:
    t = c["task"]
    print(f"# {t['id']} · {t['title']}  [{st(t['status'])}]" + (green("  ✓ ready") if c["is_ready"] else ""))
    kv("type", t.get("task_type"))
    kv("role", t.get("assigned_role"))
    if c["plan"]:
        section(f"Plan {c['plan']['id']} · {c['plan']['title']}")
        kv("objective", c["plan"].get("objective"))
        kv("success", c["plan"].get("success_criteria"))
        kv("context", c["plan"].get("context"))
    section("Task")
    for key, label in (("goal", "goal"), ("inputs", "inputs"), ("expected_outputs", "outputs"),
                       ("acceptance_criteria", "acceptance"), ("verification", "verification"),
                       ("result", "result"), ("blockers", "blockers"), ("notes", "notes")):
        kv(label, t.get(key))
    if c["dependencies"]:
        section("Dependencies")
        for d in c["dependencies"]:
            print(f"  {green('✓') if d['status'] == 'done' else dim('○')} {d['id']}  {d['title']}  [{st(d['status'])}]")
            if d.get("result"):
                kv("result", d["result"], width=18)
    if c["question"]:
        section(f"Question {c['question']['id']} · {c['question']['title']}  [{st(c['question']['status'])}]")
        kv("description", c["question"].get("description"))
    e = c["experiment"]
    if e:
        s = e["state"]
        section(f"Experiment {e['id']} · {e['title']}  [{st(e['status'])}]")
        for key in ("hypothesis", "parameters", "success_criteria", "stop_conditions"):
            kv(key.replace("_", " "), e.get(key))
        kv("runs", f"{s['runs']} ({s['unsynthesized']} unsynthesized)"
           + (red(" — run budget reached: synthesize first") if s["over_run_budget"] else ""))
        if e.get("latest_synthesis"):
            kv("last synthesis", e["latest_synthesis"].get("interpretation"))
            kv("suggested next", e["latest_synthesis"].get("next_experiment"))
    pol = c["policy"]
    section("Rules")
    print(f"  max runs without synthesis: {pol.get('max_runs_without_synthesis')} · "
          f"max failed runs without review: {pol.get('max_failed_runs_without_review')}")
    print(dim(f"  when finished: research task done {t['id']} -r \"…\"   ·   if stuck: research task block {t['id']} \"reason\""))


def cmd_task_context(a, p) -> int:
    c = P.task_context(p, a.id)
    out_json(c) if a.json else print_task_context(c)
    return 0


def cmd_task_next(a, p) -> int:
    task = P.get_next_ready_task(p, a.plan)
    if not task:
        if not a.json:
            print(dim("No tasks ready (all dependencies unsatisfied or no todo tasks)"))
        else:
            out_json(None)
        return 0
    if a.json:
        out_json(task)
    else:
        print(green("Next ready task:"))
        print_task(task)
    return 0


@mut
def cmd_task_note(a, p) -> int:
    n = P.add_task_note(p, a.id, " ".join(a.text))
    _done(a, n, msg=f"{n['id']} note added")
    return 0


@mut
def cmd_task_release(a, p) -> int:
    t = P.release_task(p, a.id, a.note)
    _done(a, t, msg=f"{t['id']} released → todo")
    return 0


def _print_verification(res: Dict[str, Any]) -> None:
    for r in res["results"]:
        print(f"  {green('✓') if r['ok'] else red('✗')} {r['check']}  {dim(r.get('detail') or '')}")
        if not r["ok"] and r.get("output"):
            for l in r["output"].strip().splitlines()[-8:]:
                print(dim("      " + l))
    print((green("passed") if res["passed"] else red("failed")) + dim(f"  {res['record']['id']} · task now [{res['task']['status']}]"))


@mut
def cmd_task_verify(a, p) -> int:
    from research import verification as VF
    res = VF.verify_task(p, a.id)
    out_json(res) if a.json else _print_verification(res)
    return 0 if res["passed"] else 1


def attach(sub, add, group) -> None:
    g = group("plan", "research plans (decomposed into tasks)", aliases=["plans", "p"])
    x = add(g, "create", cmd_plan_create, "create a plan", aliases=["new"])
    x.add_argument("title")
    x.add_argument("--objective", "-o")
    x.add_argument("--question", "-q", help="root question this plan addresses")
    x.add_argument("--success", help="success criteria")
    x.add_argument("--context", help="additional context for the plan")
    x.add_argument("--status", default="active", choices=STATUSES["plan"])
    x.add_argument("--skill", action="append", help="project skill for every task in the plan (repeatable)")
    x = add(g, "list", cmd_plan_list, "list plans", aliases=["ls"])
    x.add_argument("--status", choices=STATUSES["plan"])
    x = add(g, "show", cmd_plan_show, "show plan with all tasks")
    x.add_argument("id")
    x = add(g, "update", cmd_plan_update, "update a plan")
    x.add_argument("id")
    x.add_argument("--title")
    x.add_argument("--objective", "-o")
    x.add_argument("--question", "-q")
    x.add_argument("--success")
    x.add_argument("--context")
    x.add_argument("--status", choices=STATUSES["plan"])
    x.add_argument("--skill", action="append")
    from . import supervisor as SC
    SC.attach_plan(g, add)
    g = group("task", "tasks within plans (units of work)", aliases=["tasks", "t"])
    x = add(g, "create", cmd_task_create, "create a task", aliases=["new"])
    x.add_argument("plan", help="plan id (PLAN-001)")
    x.add_argument("title")
    x.add_argument("--goal", "-g")
    x.add_argument("--type", choices=TASK_TYPES, help="task type (extensible)")
    x.add_argument("--role", choices=TASK_ROLES, help="assigned role (extensible)")
    x.add_argument("--status", default="todo", choices=STATUSES["task"])
    x.add_argument("--depends", nargs="+", metavar="T-NNN", help="task dependencies")
    x.add_argument("--inputs")
    x.add_argument("--outputs", help="expected outputs")
    x.add_argument("--acceptance", help="acceptance criteria")
    x.add_argument("--verification", help="verification requirements")
    x.add_argument("--question", "-q", help="related question")
    x.add_argument("--experiment", "-e", help="related experiment")
    x.add_argument("--notes")
    x.add_argument("--check", action="append", metavar="CHECK",
                   help="verification check (repeatable): 'cmd:pytest -q', 'file:out/plot.png', 'metric:EXP-001:rmse<=0.05'")
    x.add_argument("--skill", action="append", help="project skill to load for this task (repeatable)")
    x = add(g, "list", cmd_task_list, "list tasks", aliases=["ls"])
    x.add_argument("--plan", "-p", help="filter by plan")
    x.add_argument("--status", choices=STATUSES["task"])
    x = add(g, "show", cmd_task_show, "show task detail")
    x.add_argument("id")
    x = add(g, "context", cmd_task_context, "focused context for working on a task: plan, deps, question, experiment, rules")
    x.add_argument("id")
    x = add(g, "update", cmd_task_update, "update a task")
    x.add_argument("id")
    x.add_argument("--title")
    x.add_argument("--goal", "-g")
    x.add_argument("--type", choices=TASK_TYPES)
    x.add_argument("--role", choices=TASK_ROLES)
    x.add_argument("--status", choices=STATUSES["task"])
    x.add_argument("--depends", nargs="+")
    x.add_argument("--inputs")
    x.add_argument("--outputs")
    x.add_argument("--acceptance")
    x.add_argument("--verification")
    x.add_argument("--question", "-q")
    x.add_argument("--experiment", "-e")
    x.add_argument("--result")
    x.add_argument("--blockers")
    x.add_argument("--notes")
    x.add_argument("--check", action="append", metavar="CHECK", help="replaces the checks (repeatable; --check '' clears)")
    x.add_argument("--skill", action="append", help="replaces the skills (repeatable; --skill '' clears)")
    x = add(g, "start", cmd_task_start, "claim a task and mark it running")
    x.add_argument("id")
    x.add_argument("--role", help="assign role when starting")
    x.add_argument("--force", action="store_true", help="take over a task another agent has claimed")
    x = add(g, "release", cmd_task_release, "give a claimed task back (→ todo) with a hand-off note")
    x.add_argument("id")
    x.add_argument("--note")
    x = add(g, "note", cmd_task_note, "add a progress note: research task note T-003 \"loader done, tests next\"")
    x.add_argument("id")
    x.add_argument("text", nargs="+")
    x = add(g, "verify", cmd_task_verify, "run the task's checks and record the result")
    x.add_argument("id")
    x = add(g, "dispatch", cmd_dispatch, "hand this task to an agent CLI (role from the task)")
    x.add_argument("target", metavar="id")
    x.add_argument("--role", choices=["planner", "implementer", "verifier", "analyst"])
    _dispatch_args(x)
    x = add(g, "done", cmd_task_done, "mark task as done (completed)", aliases=["complete"])
    x.add_argument("id")
    x.add_argument("--result", "-r")
    x.add_argument("--by", help="completed by (agent name or human)")
    x.add_argument("--artifacts", nargs="+", metavar="A-NNN", help="artifact ids")
    x = add(g, "block", cmd_task_block, "mark task as blocked")
    x.add_argument("id")
    x.add_argument("reason")
    x = add(g, "next", cmd_task_next, "show next ready task (dependencies satisfied)")
    x.add_argument("--plan", "-p", help="filter by plan")

