"""CLI for plan YAML, plan runs, the supervisor, proposals and structured agent reporting. Thin wrappers over research.supervisor
and research.planspec; the VS Code UI, RPC and MCP call the same functions."""
from __future__ import annotations

import os
import sys
from typing import Any

from research import planspec as PS, supervisor as SV
from research.util import ResearchError

from . import main as M


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        raise ResearchError(f"Cannot read {path}: {e.strerror}")


def _emit(a, obj: Any, lines=None) -> int:
    if a.json:
        M.out_json(obj)
    elif lines:
        for l in lines(obj):
            print(l)
    return 0


def _ops_lines(d) -> list:
    out = []
    for pl in d.get("plans", []):
        out.append(M.bold(f"plan {pl['plan']}") + M.dim(f"  (ledger revision {pl.get('revision')})" if pl.get("revision") else "  (new)"))
        for o in pl["ops"]:
            f = o.get("fields")
            out.append(f"  + {o['op']:<18} {o.get('key')}" + (f"  [{', '.join(f)}]" if f else ""))
        if not pl["ops"]:
            out.append(M.dim("  no changes"))
    for w in d.get("warnings", []):
        out.append(M.yellow("  ! " + w))
    for c in d.get("conflicts", []):
        out.append(M.red("  ✗ " + c))
    return out


# ----------------------------------------------------------------------------------------------- plan yaml
def cmd_plan_yaml(a, p) -> int:
    act = a.action
    if act == "template":
        text = PS.TEMPLATE
        if a.out:
            if os.path.exists(a.out) and not a.force:
                raise ResearchError(f"{a.out} exists; pass --force to overwrite")
            open(a.out, "w", encoding="utf-8").write(text)
            return _emit(a, {"wrote": a.out}, lambda d: [M.green("✓ ") + f"template written to {d['wrote']}"])
        print(text, end="")
        return 0
    if act == "export":
        if not a.target:
            raise ResearchError("export needs a plan key or id: research plan yaml export PLAN-001 -o plan.yaml")
        text = PS.export(p, a.target, scope="partial" if a.partial else "full", experiment=a.experiment, task=a.task)
        if a.out:
            open(a.out, "w", encoding="utf-8").write(text)
            return _emit(a, {"wrote": a.out}, lambda d: [M.green("✓ ") + f"exported to {d['wrote']}"])
        print(text, end="")
        return 0
    if not a.target:
        raise ResearchError(f"{act} needs a YAML file (or - for stdin)")
    text = _read(a.target)
    if act == "validate":
        r = PS.validate(text, p)
        return _emit(a, r, lambda d: [M.green("✓ ") + f"valid: {len(d['plans'])} plan(s), {d['experiments']} experiment(s), {d['tasks']} task(s)"])
    if act == "diff":
        r = PS.diff(p, text)
        return _emit(a, r, _ops_lines) or (0 if r["ok"] else 3)
    if act == "apply":
        r = PS.apply(p, text, dry_run=a.dry_run)
        if a.dry_run:
            return _emit(a, r, _ops_lines)
        def lines(d):
            out = []
            for pl in d["plans"]:
                out.append((M.green("✓ ") + f"{pl['plan']} → {pl['id']} revision {pl['revision']}: ") + (f"{pl.get('operations')} operation(s)" if pl["changed"] else "no changes"))
                for w in pl.get("warnings", []):
                    out.append(M.yellow("  ! " + w))
            return out
        return _emit(a, r, lines)
    raise ResearchError(f"unknown yaml action {act}")


cmd_plan_yaml = M.mut(cmd_plan_yaml)


# ----------------------------------------------------------------------------------------------- plan run control
def _status_lines(s) -> list:
    out = [M.bold(f"{s['plan']} ({s['plan_id']}) rev {s['revision']}: {s['state']}") + M.dim(f"  {s['progress']} verified  attempts {s['budgets'].get('attempts', 0)}/{s['budgets']['max_total_attempts']}")]
    for t in s["tasks"]:
        ex = t.get("execution") or {}
        out.append(f"  {t['id']:<7} {t['phase']:<18} {t['title'][:44]:<44}" + (f" attempt {t['attempts']}" if t["attempts"] else "")
                   + (f" [{ex.get('state')}]" if ex.get("state") and t["phase"] in ("running", "verifying") else "")
                   + (M.dim(f"  — {t['reason'][:90]}") if t.get("reason") else ""))
    for pr in s.get("proposals", []):
        out.append(M.yellow(f"  proposal {pr['id']} ({pr['classification']}) waits for approval: {pr['rationale'][:80]}"))
    d = s["daemon"]
    out.append(M.dim("  supervisor process: " + (f"running (pid {d['pid']})" if d.get("running") else "not running; `research supervisor start`")))
    return out


def cmd_plan_run(a, p) -> int:
    return _emit(a, SV.plan_run(p, a.id, start_daemon=not a.no_daemon), _status_lines)


def cmd_plan_pause(a, p) -> int:
    return _emit(a, SV.plan_pause(p, a.id), _status_lines)


def cmd_plan_resume(a, p) -> int:
    return _emit(a, SV.plan_resume(p, a.id), _status_lines)


def cmd_plan_stop(a, p) -> int:
    return _emit(a, SV.plan_stop(p, a.id, cancel_agents=a.cancel_agents), _status_lines)


def cmd_plan_promote(a, p) -> int:
    r = SV.promote_plan(p, a.id, a.experiment, a.title)
    return _emit(a, r, lambda d: [M.green("✓ ") + f"{x['id']}: {len(x['commits'])} verified commit(s) from {x['workspace']} (continue with `research promote show {x['id']}`)" for x in d["candidates"]])


def cmd_plan_status(a, p) -> int:
    return _emit(a, SV.status(p, a.id), _status_lines)


for _f in (cmd_plan_run, cmd_plan_pause, cmd_plan_resume, cmd_plan_stop, cmd_plan_promote):
    M.mut(_f)


# ----------------------------------------------------------------------------------------------- supervisor
def cmd_supervisor(a, p) -> int:
    act = a.action
    if act == "start":
        if a.foreground:
            return SV.run_forever(p.root, a.exit_when_idle)
        return _emit(a, SV.daemon_start(p, detach=True, exit_when_idle=a.exit_when_idle),
                     lambda d: [M.green("✓ ") + (f"supervisor running (pid {d.get('pid')})" if d.get("running") else str(d.get("note")))])
    if act == "status":
        d = SV.daemon_status(p)
        runs = [{"plan": r["plan_key"], "state": r.get("state"), "attempts": r["budgets"].get("attempts", 0)} for r in SV.all_runs(p)]
        return _emit(a, {"daemon": d, "plans": runs}, lambda x: [
            M.bold("supervisor ") + (M.green(f"running (pid {x['daemon']['pid']}, heartbeat {x['daemon']['heartbeat_age_seconds']}s ago)") if x["daemon"]["running"]
                                      else M.yellow("not running") + M.dim("  " + (x["daemon"].get("note") or "")))] +
            [f"  plan {r['plan']}: {r['state']} ({r['attempts']} attempts)" for r in x["plans"]])
    if act == "stop":
        return _emit(a, SV.daemon_stop(p), lambda d: [M.green("✓ ") + "stopped" if not d.get("running") else M.yellow("still running")])
    if act == "tick":
        r = SV.tick(p)
        return _emit(a, r, lambda d: [f"{d['plans']} plan(s): launched {len(d['launched'])}, verified {len(d['verified'])}, repairs {len(d['repairs'])}, blocked {len(d['blocked'])}"]
                     + [M.dim("  " + e) for e in d["events"]])
    if act == "approve":
        return _emit(a, SV.approve_task(p, a.target, a.note), lambda d: [M.green("✓ ") + f"{d['task']} approved to start"])
    if act == "accept":
        return _emit(a, SV.accept(p, a.target, a.reason or ""), lambda d: [M.yellow("✓ ") + f"{d['task']} accepted WITHOUT verification (logged)"])
    if act == "void":
        return _emit(a, SV.void_attempts(p, a.target, a.reason or a.note or "attempts voided by a human"),
                     lambda d: [M.green("✓ ") + f"{d['voided']} attempt(s) of {d['task']} voided (kept as evidence, not counted)"])
    if act == "resolve":
        o = "failed" if a.failed else "completed" if a.completed else "retry" if a.retry else "unblock" if a.unblock else None
        if not o:
            raise ResearchError("resolve needs --failed, --completed, --retry or --unblock")
        return _emit(a, SV.resolve(p, a.target, o, a.note), lambda d: [M.green("✓ ") + f"{d['task']} resolved: {d['outcome']}"])
    raise ResearchError(f"unknown supervisor action {act}")


cmd_supervisor = M.mut(cmd_supervisor)


# ----------------------------------------------------------------------------------------------- proposals
def cmd_proposal(a, p) -> int:
    if a.action == "list":
        rows = SV.list_proposals(p)
        return _emit(a, rows, lambda r: [f"{x['id']}  {x['status']:<9} {x['classification']:<11} {x['plan_key']}  {x['rationale'][:70]}" for x in r] or [M.dim("no proposals")])
    if a.action == "show":
        for x in SV.list_proposals(p):
            if x["id"] == a.target:
                return _emit(a, x, lambda d: [f"{d['id']} [{d['status']}] {d['classification']} on {d['plan_key']}", f"rationale: {d['rationale']}", "", d["patch"]])
        raise ResearchError(f"No proposal {a.target}")
    r = SV.decide_proposal(p, a.target, a.action == "approve", a.note)
    return _emit(a, r, lambda d: [M.green("✓ ") + f"{d['id']} {d['status']}"])


cmd_proposal = M.mut(cmd_proposal)


# ----------------------------------------------------------------------------------------------- agent reports
def cmd_agent(a, p) -> int:
    kind = a.action.replace("-", "_")
    f = {k: getattr(a, k, None) for k in ("message", "summary", "path", "name", "value", "run", "job", "category", "commit", "files", "rationale")}
    if kind == "propose":
        if not a.file:
            raise ResearchError("propose needs --file patch.yaml")
        SV._active_attempt(p, a.task)               # only the active execution may propose
        pr = SV.propose(p, a.task, _read(a.file), a.rationale or "", source="agent " + (os.environ.get("RESEARCH_EXECUTION", "")[:8]))
        SV.report(p, a.task, "propose", message=f"{pr['id']} {pr['classification']} {pr['status']}")
        return _emit(a, {k: pr[k] for k in ("id", "classification", "status")}, lambda d: [M.green("✓ ") + f"{d['id']} ({d['classification']}): {d['status']}"
                     + ("" if d["status"] == "applied" else " (waits for approval)")])
    if kind == "finish":
        kind = "finish"
        f["message"] = f["message"] or "finished; verification requested"
    f = {k: v for k, v in f.items() if v is not None}
    return _emit(a, SV.report(p, a.task, {"hpc_job": "hpc_job", "report": "report"}.get(kind, kind), **f),
                 lambda d: [M.green("✓ ") + f"{d['recorded']} recorded for {d['task']} (attempt {d['attempt']})"])


cmd_agent = M.mut(cmd_agent)


# ----------------------------------------------------------------------------------------------- registration
def attach_plan(g, add) -> None:
    x = add(g, "yaml", cmd_plan_yaml, "YAML plans: template | export | validate | diff | apply")
    x.add_argument("action", choices=["template", "export", "validate", "diff", "apply"])
    x.add_argument("target", nargs="?", help="export: plan key/id · validate/diff/apply: YAML file (or -)")
    x.add_argument("-o", "--out", help="write to this file")
    x.add_argument("--force", action="store_true", help="template: overwrite an existing file")
    x.add_argument("--partial", action="store_true", help="export: mark the document partial (applying it can never retire tasks)")
    x.add_argument("--experiment", help="export: only this experiment key (implies partial)")
    x.add_argument("--task", help="export: only this task key (implies partial)")
    x.add_argument("--dry-run", action="store_true", help="apply: preview only")
    x = add(g, "promote", cmd_plan_promote, "prepare promotion candidates from verified, committed work (nothing merges)")
    x.add_argument("id"); x.add_argument("--experiment"); x.add_argument("--title")
    for name, fn, h in (("run", cmd_plan_run, "let the supervisor execute a plan"), ("pause", cmd_plan_pause, "pause scheduling for a plan"),
                        ("resume", cmd_plan_resume, "resume a paused or blocked plan"), ("stop", cmd_plan_stop, "stop a plan run (history is kept)"),
                        ("status", cmd_plan_status, "supervised status: phases, attempts, budgets")):
        x = add(g, name, fn, h)
        x.add_argument("id", help="plan id (PLAN-001) or key")
        if name == "run":
            x.add_argument("--no-daemon", action="store_true", help="do not start the supervisor process (use `research supervisor tick`)")
        if name == "stop":
            x.add_argument("--cancel-agents", action="store_true", help="also stop agents that are running now")


def attach_top(sub, add, group) -> None:
    x = add(sub, "supervisor", cmd_supervisor, "the persistent supervisor: start | status | stop | tick | approve | accept | resolve")
    x.add_argument("action", choices=["start", "status", "stop", "tick", "approve", "accept", "resolve", "void"])
    x.add_argument("target", nargs="?", help="approve/accept/resolve: task id")
    x.add_argument("--foreground", action="store_true", help="start: run in this terminal instead of detaching")
    x.add_argument("--detach", action="store_true", help="start: detach (the default)")
    x.add_argument("--exit-when-idle", action="store_true", help="start: exit once no plan is running")
    x.add_argument("--note")
    x.add_argument("--reason", help="accept: why the task is accepted without verification")
    x.add_argument("--failed", action="store_true"); x.add_argument("--completed", action="store_true")
    x.add_argument("--retry", action="store_true"); x.add_argument("--unblock", action="store_true")
    x = add(sub, "proposal", cmd_proposal, "plan-change proposals: list | show | approve | reject", aliases=["proposals"])
    x.add_argument("action", choices=["list", "show", "approve", "reject"])
    x.add_argument("target", nargs="?")
    x.add_argument("--note")
    g = group("agent", "structured reports from a supervised agent (run inside an agent session)")
    for name, h in (("progress", "free-text progress"), ("milestone", "a named milestone"), ("artifact", "an output file (workspace-relative)"),
                    ("metric", "a metric value"), ("run", "a computational run id"), ("hpc-job", "an HPC job id"), ("code", "a commit / changed files"),
                    ("problem", "a problem with a failure category"), ("report", "summary of what was done and found"),
                    ("finish", "request verification"), ("propose", "propose a plan change (YAML patch)")):
        x = add(g, name, cmd_agent, h)
        x.set_defaults(action=name)
        x.add_argument("task", help="task id (T-001)")
        x.add_argument("--message", "-m"); x.add_argument("--summary"); x.add_argument("--path"); x.add_argument("--name"); x.add_argument("--value", type=float)
        x.add_argument("--run"); x.add_argument("--job"); x.add_argument("--category"); x.add_argument("--commit"); x.add_argument("--files", nargs="*")
        x.add_argument("--rationale"); x.add_argument("--file")
        x.add_argument("--request-verification", action="store_true", help="finish: explicit (it is the only meaning of finish)")
