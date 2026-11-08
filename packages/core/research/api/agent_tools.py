"""Operations that exist for agents (the MCP tools) and have no RPC counterpart with the same shape.

Tool descriptions and JSON schemas are hand-written in mcp.py; a contract test checks them against these signatures.
Agents may call `mcp=` ops only; governance (claims, gates, PIN, role rules) is enforced by the core functions they call."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .. import context as C
from .. import plans as P
from .. import runs as R
from .. import services as SV
from .. import skills as SK
from . import op, ro


def _sv():
    from .. import supervisor
    return supervisor


@op(sync=True, mcp="research_current")
def research_current(p) -> str:
    C.write_current_md(p)
    return C.render_current_md(p)


@op(mcp="research_search")
def research_search(p, query: str, type: Optional[str] = None, limit: int = 20) -> Any:
    from ..search import search
    return search(p, query, type, limit)


@op(mcp="task_next")
def task_next(p, plan: Optional[str] = None) -> Any:
    return P.get_next_ready_task(p, plan)


@op(mcp="task_context")
def agent_task_context(p, id: str) -> str:
    from .. import agents as A
    return A.task_brief_md(P.task_context(p, id))


@op(mutates=True, mcp="agent_report")
def agent_report(p, task: str, kind: str, **kw: Any) -> Any:
    return _sv().report(p, task, kind, **kw)


@op(mutates=True, mcp="agent_propose")
def agent_propose(p, task: str, yaml: str, rationale: str) -> Any:
    sv = _sv()
    sv._active_attempt(p, task)
    return sv.propose(p, task, yaml, rationale, source="agent")


@op(mutates=True, mcp="task_start")
def task_start(p, id: str, force: bool = False) -> Any:
    return P.start_task(p, id, force=force)


@op(mutates=True, mcp="task_done")
def task_done(p, id: str, result: Optional[str] = None, artifacts: Optional[List[str]] = None) -> Any:
    return P.complete_task(p, id, result=result, artifacts=artifacts)


@op(mutates=True, mcp="task_block")
def task_block(p, id: str, reason: str) -> Any:
    return P.block_task(p, id, reason)


@op(mutates=True, mcp="task_verify")
def task_verify(p, id: str) -> Dict[str, Any]:
    from .. import verification as V
    return {k: v for k, v in V.verify_task(p, id).items() if k != "task"}


@op(mutates=True, mcp="task_create")
def task_create(p, plan: str, title: str, goal: Optional[str] = None, type: Optional[str] = None, role: Optional[str] = None,
                depends_on: Optional[List[str]] = None, acceptance_criteria: Optional[str] = None, checks: Optional[List[str]] = None,
                skills: Optional[List[str]] = None, experiment: Optional[str] = None, question: Optional[str] = None,
                inputs: Optional[str] = None, expected_outputs: Optional[str] = None) -> Any:
    return P.create_task(p, plan, title, goal=goal, task_type=type, assigned_role=role, depends_on=depends_on,
                         acceptance_criteria=acceptance_criteria, checks=checks, skills=skills, related_experiment_id=experiment,
                         related_question_id=question, inputs=inputs, expected_outputs=expected_outputs)


@op(mutates=True, mcp="plan_create")
def plan_create(p, title: str, objective: Optional[str] = None, success_criteria: Optional[str] = None,
                question: Optional[str] = None, skills: Optional[List[str]] = None) -> Any:
    return P.create_plan(p, title, objective=objective, success_criteria=success_criteria, question=question, skills=skills)


@op(mutates=True, mcp="question_create")
def question_create(p, title: str, description: Optional[str] = None, parent: Optional[str] = None) -> Any:
    return SV.create_question(p, title, description, parent)


@op(mutates=True, mcp="experiment_create")
def experiment_create(p, title: str, **kw: Any) -> Any:
    return SV.create_experiment(p, title, **kw)


@op(mutates=True, mcp="experiment_propose")
def experiment_propose(p, **kw: Any) -> Any:
    return _sv().propose_experiment(p, kw, source=(p.author or {}).get("author_name") or "agent")


@op(mutates=True, mcp="run_start")
def agent_run_start(p, experiment: str, command: str, parameters: Optional[Dict[str, Any]] = None, label: Optional[str] = None,
                    cwd: Optional[str] = None) -> Any:
    return R.exec_run(p, experiment, command, detach=True, parameters=parameters, label=label,
                      working_dir=os.path.join(p.root, cwd) if cwd else p.root, tee=False)


@op(mcp="run_status")
def run_status(p, id: str) -> Dict[str, Any]:
    from .. import views as VW
    r = R.reconcile_run(p, id, force=True)
    d = VW.run_detail(p, r["id"])
    out = {k: d.get(k) for k in ("id", "experiment_id", "status", "exit_code", "started_at", "ended_at",
                                 "duration_s", "label", "command", "metrics", "slurm_job_id", "run_dir")}
    out["artifacts"] = [{"id": a["id"], "path": a["path"]} for a in d["artifacts"]]
    return out


@op(mcp="run_logs")
def run_logs(p, id: str, stream: str = "stdout", tail: int = 80) -> str:
    r = R.get_run(p, id)
    path = p.abspath(r["stderr_path"] if stream == "stderr" else r["stdout_path"])
    if not path or not os.path.exists(path):
        return "(no log yet)"
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    return "\n".join(lines[-int(tail):])


@op(mutates=True, mcp="metric_log")
def metric_log(p, name: str, value: Any, run: Optional[str] = None, experiment: Optional[str] = None, step: Optional[int] = None,
               unit: Optional[str] = None) -> Any:
    return R.log_metric(p, name, value, step, unit, run=run, experiment=experiment)


@op(mutates=True, mcp="artifact_register")
def artifact_register(p, path: str, run: Optional[str] = None, experiment: Optional[str] = None, description: Optional[str] = None) -> Any:
    return R.register_artifact(p, path, run=run, experiment=experiment, description=description)


@op(mutates=True, mcp="experiment_synthesize")
def experiment_synthesize(p, id: str, **kw: Any) -> Any:
    return SV.synthesize(p, id, **kw)


@op(mutates=True, mcp="finding_create")
def finding_create(p, title: str, statement: Optional[str] = None, kind: str = "result", confidence: Optional[str] = None,
                   supports: Optional[List[str]] = None, contradicts: Optional[List[str]] = None, questions: Optional[List[str]] = None,
                   limitations: Optional[str] = None) -> Any:
    return SV.create_finding(p, title, statement, kind, "preliminary", confidence, supports, contradicts, None, questions, limitations)


@op(mutates=True, mcp="checkpoint_create")
def checkpoint_create(p, understanding: Optional[str] = None, problem: Optional[str] = None, next: Optional[str] = None,
                      commit: bool = False) -> Dict[str, Any]:
    c = C.create_checkpoint(p, understanding=understanding, current_problem=problem, next_experiment=next, commit=commit)
    return {"id": c["id"], "path": c.get("path"), "snapshot_commit": c.get("snapshot_commit")}


@op(mcp="skills_list")
def skills_list(p) -> Any:
    return [{k: s[k] for k in ("name", "description", "always", "applies_to", "level")} for s in SK.list_skills(p)]


@op(mcp="skill_show")
def skill_show(p, name: str) -> str:
    return SK.get_skill(p, name)["markdown"]


@op(mutates=True, mcp="dispatch_prepare")
def dispatch_prepare(p, role: Optional[str] = None, target: Optional[str] = None, objective: Optional[str] = None,
                     profile: Optional[str] = None) -> Any:
    from .. import agents as A
    return A.dispatch(p, role, target, objective, profile)
