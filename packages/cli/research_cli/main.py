"""`research` command-line interface. Thin layer over the `research` core package.

Exit codes: 0 ok · 1 error · 2 not initialized · 3 run budget / policy block · 4 not found.
Every command accepts --json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

import research
from research import context as C
from research import plans as P
from research import runs as R
from research import services as S
from research import views as V
from research.schema import CONFIDENCE, FINDING_KINDS, STATUSES, TASK_TYPES, TASK_ROLES, normalize_id
from research.store import Project
from research.util import NotFound, ResearchError, detect_author, human_size, parse_kv

# ----------------------------------------------------------------------------- output helpers

_TTY = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s


bold = lambda s: _c("1", s)
dim = lambda s: _c("2", s)
green = lambda s: _c("32", s)
red = lambda s: _c("31", s)
yellow = lambda s: _c("33", s)
cyan = lambda s: _c("36", s)
magenta = lambda s: _c("35", s)

STATUS_COLOR = {
    "open": cyan, "investigating": yellow, "answered": green, "blocked": red, "abandoned": dim,
    "proposed": dim, "ready": cyan, "running": yellow, "needs_review": magenta, "completed": green, "failed": red,
    "queued": dim, "cancelled": dim, "unknown": red,
    "preliminary": yellow, "supported": green, "contradicted": red, "superseded": dim,
    "active": green, "reversed": dim,
    # Plan/Task statuses (Phase 0)
    "todo": dim, "verify": magenta, "done": green,
}


def st(s: Optional[str]) -> str:
    if not s:
        return ""
    return STATUS_COLOR.get(s, lambda x: x)(s)


def author(r: Dict[str, Any]) -> str:
    at = r.get("author_type")
    if at == "agent":
        return magenta("agent" + (f":{r['author_name']}" if r.get("author_name") else ""))
    return dim(at or "")


def out_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str, ensure_ascii=False))


def section(title: str) -> None:
    print("\n" + bold(title))


def kv(label: str, value: Any, width: int = 16) -> None:
    if value in (None, "", [], {}):
        return
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        value = ", ".join(f"{k}={v}" for k, v in value.items())
    text = str(value).rstrip()
    lines = text.splitlines() or [""]
    print(f"  {dim(label.ljust(width - 1))} {lines[0]}")  # always at least one space after the label
    for l in lines[1:]:
        print(" " * (width + 2 + max(0, len(label) - width + 1)) + l)


def fmt_dur(sec: Optional[float]) -> str:
    if sec is None:
        return "—"
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m{sec % 60:02d}s"
    return f"{sec // 3600}h{(sec % 3600) // 60:02d}m"


def fmt_val(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4g}"
    return "—" if v is None else str(v)


# ----------------------------------------------------------------------------- printers

def print_experiment(e: Dict[str, Any]) -> None:
    s = e["state"]
    print(f"{bold(e['id'])}  {bold(e['title'])}  [{st(e['status'])}]" + (green("  ★ baseline") if e.get("is_baseline") else ""))
    if e.get("question"):
        kv("question", f"{e['question']['id']} {e['question']['title']}")
    if e.get("parent"):
        kv("derived from", f"{e['parent']['id']} {e['parent']['title']}")
    if e.get("param_delta"):
        kv("delta", ", ".join(f"{k}: {d.get('from')} → {d.get('to')}" for k, d in e["param_delta"].items()))
    for key, label in (("hypothesis", "hypothesis"), ("motivation", "motivation"), ("method", "method"),
                       ("expected_outcome", "expected"), ("success_criteria", "success"), ("stop_conditions", "stop"),
                       ("parameters", "parameters"), ("metrics_requested", "metrics"),
                       ("expected_artifacts", "exp. artifacts"), ("planned_runs", "planned runs"),
                       ("limitations", "limitations"), ("notes", "notes")):
        kv(label, e.get(key))
    kv("git", f"{e.get('git_branch') or '—'} @ {(e.get('git_commit') or '—')[:10]}" + (yellow(" (dirty)") if e.get("git_dirty") else ""))
    kv("author", author(e))
    flag = []
    if s["unsynthesized"]:
        flag.append(yellow(f"⚠ {s['unsynthesized']} unsynthesized runs"))
    if s["over_run_budget"]:
        flag.append(red("run budget reached — synthesize before running more"))
    if s["over_failure_budget"]:
        flag.append(red(f"{s['unreviewed_failed']} failed runs need review"))
    if flag:
        print("  " + " · ".join(flag))
    if e["runs"]:
        section(f"Runs ({len(e['runs'])})")
        pn, mn = e["run_param_names"], e["metric_names"]
        hdr = ["run", "status", "exit", "time"] + pn + mn + ["label"]
        rows = []
        for r in e["runs"]:
            rows.append([r["id"], r["status"], fmt_val(r.get("exit_code")), fmt_dur(r.get("duration_s"))]
                        + [fmt_val((r.get("parameters") or {}).get(k)) for k in pn]
                        + [fmt_val((r["metrics"].get(k) or {}).get("value") if (r["metrics"].get(k) or {}).get("value") is not None
                                   else (r["metrics"].get(k) or {}).get("text")) for k in mn]
                        + [r.get("label") or ""])
        widths = [max(len(str(h)), *(len(str(x[i])) for x in rows)) for i, h in enumerate(hdr)]
        print("  " + "  ".join(dim(str(h).ljust(w)) for h, w in zip(hdr, widths)))
        for row in rows:
            cells = [str(x).ljust(w) for x, w in zip(row, widths)]
            cells[1] = st(row[1]) + " " * (widths[1] - len(row[1]))
            print("  " + "  ".join(cells))
    if e["artifacts"]:
        section(f"Artifacts ({len(e['artifacts'])})")
        for a in e["artifacts"]:
            print(f"  {a['id']}  {a['path']}  {dim(a['type'])} {dim(human_size(a.get('size')))}"
                  + ("" if a["exists"] else red(" (missing)")) + (f"  — {a['description']}" if a.get("description") else ""))
    for syn in e["syntheses"]:
        section(f"Synthesis {syn['id']} · {syn['created_at'][:16]} · {author(syn)}")
        for key, label in (("what_happened", "happened"), ("what_worked", "worked"), ("what_failed", "failed"),
                           ("interpretation", "interpretation"), ("limitations", "limitations"),
                           ("unresolved", "unresolved"), ("next_experiment", "next")):
            kv(label, syn.get(key))
    if e["findings"]:
        section("Findings")
        for f in e["findings"]:
            print(f"  {f['id']}  {f['title']}  [{st(f['status'])}]")


def print_finding(f: Dict[str, Any]) -> None:
    kind = red("✗ failed direction") if f.get("kind") == "failure" else ""
    print(f"{bold(f['id'])}  {bold(f['title'])}  [{st(f['status'])}] {kind}")
    kv("statement", f.get("statement"))
    kv("confidence", f.get("confidence"))
    for k in ("supports", "contradicts", "related"):
        ev = f["evidence"][k]
        if ev:
            kv(k, "\n".join(f"{b['id']} {b['title']}" + (red(" (missing)") if b.get("missing") else "") for b in ev))
    kv("questions", [q["id"] for q in f["questions"]])
    kv("limitations", f.get("limitations"))
    kv("contradicting", f.get("contradicting_evidence"))
    if f.get("superseded_by"):
        kv("superseded by", f["superseded_by"])
    kv("decisions", [f"{d['id']} {d['title']}" for d in f["decisions"]])
    kv("author", author(f))


def print_generic(d: Dict[str, Any]) -> None:
    title = d.get("title") or d.get("name") or d.get("statement") or ""
    print(f"{bold(d.get('id', ''))}  {bold(str(title))}  [{st(d.get('status'))}]")
    skip = {"id", "title", "type", "events", "links", "status"}
    for k, v in d.items():
        if k in skip or v in (None, "", [], {}):
            continue
        if isinstance(v, list) and v and isinstance(v[0], dict):
            v = [f"{x.get('id', '')} {x.get('title') or x.get('name') or x.get('path') or ''}".strip() for x in v]
        if isinstance(v, dict) and all(isinstance(x, (list, dict, type(None))) for x in v.values()):
            v = json.dumps(v, default=str)
        kv(k.replace("_", " "), v)


def print_resume(r: Dict[str, Any]) -> None:
    pr = r["project"]
    g = r["git"]
    print(bold(f"━━ {pr['name']} ━━"))
    if pr.get("goal"):
        print(pr["goal"])
    la = r.get("last_activity")
    if la:
        age = r.get("last_activity_age_days") or 0
        print(dim(f"last activity {age:.0f} days ago — {la['summary']}") if age >= 1 else dim(f"last activity today — {la['summary']}"))
    if g.get("branch"):
        print(dim(f"git {g['branch']} @ {(g.get('commit') or '')[:10]}") + (yellow(" (uncommitted changes)") if g.get("dirty") else ""))
    cp = r.get("latest_checkpoint")
    section("Latest checkpoint")
    if cp:
        print(f"  {cp['id']} {cp['title']} " + dim(f"({cp['created_at'][:10]}, {cp.get('age_days') or 0:.0f}d ago)")
              + (yellow(" STALE") if cp.get("stale") else ""))
        kv("understanding", cp.get("understanding"))
        kv("problem", cp.get("current_problem"))
        kv("next", cp.get("next_experiment"))
        sc = r["since_checkpoint"]
        if sc["events"]:
            print(dim(f"  since then: {sc['events']} changes, {sc['runs']} runs, "
                      f"{len(sc['findings'])} findings, {len(sc['experiments'])} experiments"))
    else:
        print(dim("  none yet — `research checkpoint` to create one"))
    b = r.get("baseline")
    section("Baseline")
    print(f"  {b['id']} {b['title']}" if b else dim("  none set — `research experiment baseline EXP-…`"))
    section("Questions")
    for q in r["active_questions"] + r["open_questions"] + r["blocked_questions"]:
        print(f"  {q['id']}  {q['title']}  [{st(q['status'])}] {dim(str(q['experiments']) + ' exp')}")
    if not (r["active_questions"] or r["open_questions"] or r["blocked_questions"]):
        print(dim("  none open"))
    section("What we believe (findings)")
    for f in r["established_findings"][:10]:
        print(f"  {green('✓') if f['status'] == 'supported' else yellow('~')} {f['id']}  {f['title']}"
              + dim(f"  [{f['status']}{', ' + f['confidence'] if f.get('confidence') else ''}]") + (" " + author(f) if f.get("author_type") == "agent" else ""))
    if not r["established_findings"]:
        print(dim("  none yet"))
    section("Failed directions")
    for f in r["failed_directions"][:8]:
        print(f"  {red('✗')} {f['id']}  {f['title']}")
    if not r["failed_directions"]:
        print(dim("  none recorded"))
    section("Current experiments")
    for e in r["current_experiments"]:
        s = e["state"]
        extra = yellow(f"  ⚠ {s['unsynthesized']} unsynthesized") if s["unsynthesized"] else ""
        print(f"  {e['id']}  {e['title']}  [{st(e['status'])}] {dim(str(s['runs']) + ' runs')}{extra}")
    if not r["current_experiments"]:
        print(dim("  none"))
    if r["decisions"]:
        section("Active decisions")
        for d in r["decisions"][:8]:
            print(f"  {d['id']}  {d['statement']}  {dim(d.get('date') or '')}")
    if r["blockers"]:
        section("Blockers")
        for x in r["blockers"][:8]:
            print(f"  {red('!')} {x['text']}  {dim(x['id'] or '')}")
    if r["suggested_files"]:
        section("Look at")
        for s in r["suggested_files"][:8]:
            print(f"  {s['path']}  {dim(s['reason'])}" + ("" if s["exists"] else red(" (missing)")))


def print_status(r: Dict[str, Any]) -> None:
    c = r["counts"]
    pr = r["project"]
    print(bold(pr["name"]) + (dim(" — " + pr["goal"]) if pr.get("goal") else ""))
    print(dim(f"{c['questions']} questions · {c['experiments']} experiments · {c['runs']} runs · "
              f"{c['findings']} findings · {c['decisions']} decisions · {c['checkpoints']} checkpoints"))
    if r["active_runs"]:
        section("Active runs")
        for x in r["active_runs"]:
            print(f"  {x['id']} {dim(x['experiment_id'])} [{st(x['status'])}] {x['backend']}"
                  + (dim(f" job {x['slurm_job_id']}") if x.get("slurm_job_id") else ""))
    if r["needs_synthesis"]:
        section(yellow("Needs synthesis"))
        for e in r["needs_synthesis"]:
            print(f"  {e['id']}  {e['title']}  {yellow(str(e['state']['unsynthesized']) + ' unsynthesized runs')}")
    section("Current experiments")
    for e in r["current_experiments"]:
        print(f"  {e['id']}  {e['title']}  [{st(e['status'])}]")
    if not r["current_experiments"]:
        print(dim("  none"))
    cp = r["latest_checkpoint"]
    print("\n" + dim(f"latest checkpoint: {cp['id']} ({cp['created_at'][:10]})" if cp else "no checkpoint yet"))


def print_list(rows: List[Dict[str, Any]], cols: List[str]) -> None:
    if not rows:
        print(dim("(none)"))
        return
    for r in rows:
        parts = []
        for c in cols:
            v = r.get(c)
            if c == "status":
                parts.append(f"[{st(v)}]")
            elif c == "author_type":
                if v == "agent":
                    parts.append(author(r))
            elif v not in (None, ""):
                parts.append(bold(str(v)) if c == "id" else str(v))
        print("  ".join(parts))


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


# ----------------------------------------------------------------------------- command impls

def _proj(a, sync: bool = True) -> Project:
    auth = detect_author(a.agent, a.model, a.as_) if (a.agent or a.model or a.as_) else None
    if a.root:
        return Project(a.root, author=auth, sync=sync)
    return Project.find(author=auth, sync=sync)


MUTATING = set()


def mut(fn):
    MUTATING.add(fn.__name__)
    return fn


def _is_readonly(a) -> bool:
    """True when this invocation must not mutate the project: no hand-edit import, no run reconciliation,
    no Slurm polling, no event log. `report --full` and `upgrade --dry-run` depend on this."""
    return bool(getattr(a, "full", False) or getattr(a, "dry_run_readonly", False))


def _done(a, obj: Any, printer=None, msg: Optional[str] = None) -> None:
    if a.json:
        out_json(obj)
        return
    if msg:
        print(green("✓ ") + msg)
    for w in (obj or {}).get("warnings", []) if isinstance(obj, dict) else []:
        print(yellow("⚠ " + w))
    if printer:
        printer(obj)


def cmd_init(a) -> int:
    p = Project.init(a.path or ".", name=a.name, goal=a.goal, description=a.description, agents_md=not a.no_agents_md)
    if a.json:
        out_json({"root": p.root, "name": p.name})
    else:
        print(green("✓ ") + f"Research project '{p.name}' ready at {p.root}/.research")
        if not a.no_agents_md:
            print(dim("  AGENTS.md created/updated (only between research markers)"))
        print(dim("  next: research question create \"…\"   ·   research resume"))
    p.close()
    return 0


def cmd_status(a, p) -> int:
    r = C.resume_context(p)
    out_json(r) if a.json else print_status(r)
    return 0


def cmd_resume(a, p) -> int:
    r = C.resume_context(p)
    out_json(r) if a.json else print_resume(r)
    return 0


def cmd_context(a, p) -> int:
    path = C.write_current_md(p)
    if a.json:
        out_json({"path": p.rel(path)})
    elif a.print:
        print(open(path, encoding="utf-8").read())
    else:
        print(green("✓ ") + p.rel(path))
    return 0


def cmd_current(a, p) -> int:
    path = C.write_current_md(p)
    text = open(path, encoding="utf-8").read()
    out_json({"path": p.rel(path), "markdown": text}) if a.json else print(text)
    return 0


def cmd_show(a, p) -> int:
    d = V.show(p, a.id if a.id.startswith("note:") else normalize_id(a.id))
    if a.json:
        out_json(d)
    elif d.get("type") == "experiment":
        print_experiment(d)
    elif d.get("type") == "finding":
        print_finding(d)
    else:
        print_generic(d)
    return 0


@mut
def cmd_project_set(a, p) -> int:
    pr = p.update_project(name=a.name, goal=a.goal, description=a.description, status=a.status)
    _done(a, pr, msg="project updated")
    return 0


# questions
@mut
def cmd_question_create(a, p) -> int:
    q = S.create_question(p, a.title, a.description, a.parent, a.status, a.tags)
    _done(a, q, msg=f"{q['id']}  {q['title']}")
    return 0


def cmd_question_list(a, p) -> int:
    rows = p.list("question", "status=?" if a.status else "", (a.status,) if a.status else ())
    out_json(rows) if a.json else print_list(rows, ["id", "status", "title", "author_type"])
    return 0


@mut
def cmd_question_update(a, p) -> int:
    q = S.update_question(p, a.id, title=a.title, description=a.description, status=a.status, parent=a.parent, tags=a.tags)
    _done(a, q, msg=f"{q['id']} updated")
    return 0


# experiments
@mut
def cmd_experiment_create(a, p) -> int:
    e = S.create_experiment(p, a.title, question=a.question, hypothesis=a.hypothesis, motivation=a.motivation,
                            method=a.method, expected_outcome=a.expected, success_criteria=a.success,
                            stop_conditions=a.stop, parameters=parse_kv(a.param), metrics=a.metric,
                            expected_artifacts=a.expect_artifact, planned_runs=a.planned_runs, parent=a.parent,
                            status=a.status, tags=a.tags, notes=a.notes, override=a.override)
    _done(a, e, msg=f"{e['id']}  {e['title']}  [{e['status']}]")
    return 0


def cmd_experiment_list(a, p) -> int:
    where, args = [], []
    if a.status:
        where.append("status=?")
        args.append(a.status)
    if a.question:
        where.append("question_id=?")
        args.append(normalize_id(a.question, "Q"))
    rows = p.list("experiment", " AND ".join(where), tuple(args))
    for r in rows:
        r["state"] = S.experiment_state(p, r["id"])
        r["runs"] = f"{r['state']['runs']} runs" + (f" ⚠{r['state']['unsynthesized']}" if r["state"]["unsynthesized"] else "")
        r["q"] = r.get("question_id")
        r["b"] = "★" if r.get("is_baseline") else None
    out_json(rows) if a.json else print_list(rows, ["id", "status", "b", "title", "q", "runs", "author_type"])
    return 0


def cmd_experiment_show(a, p) -> int:
    e = V.experiment_detail(p, a.id)
    out_json(e) if a.json else print_experiment(e)
    return 0


@mut
def cmd_experiment_update(a, p) -> int:
    fields = dict(title=a.title, question=a.question, hypothesis=a.hypothesis, motivation=a.motivation,
                  method=a.method, expected=a.expected, success=a.success, stop=a.stop, notes=a.notes,
                  limitations=a.limitations, planned_runs=a.planned_runs, metrics=a.metric,
                  expected_artifacts=a.expect_artifact, tags=a.tags)
    if a.param:
        cur = p.get("experiment", normalize_id(a.id, "EXP"))
        fields["parameters"] = {**(cur.get("parameters") or {}), **parse_kv(a.param)}
    e = S.update_experiment(p, a.id, **fields)
    _done(a, e, msg=f"{e['id']} updated")
    return 0


@mut
def cmd_experiment_variant(a, p) -> int:
    e = S.create_variant(p, a.id, parse_kv(a.param), title=a.title, motivation=a.motivation,
                         hypothesis=a.hypothesis, override=a.override)
    _done(a, e, msg=f"{e['id']}  {e['title']}  (derived from {e['parent_id']})")
    if not a.json and e.get("param_delta"):
        for k, d in e["param_delta"].items():
            print(dim(f"  {k}: {d['from']} → {d['to']}"))
    return 0


@mut
def cmd_experiment_status(a, p) -> int:
    e = S.set_experiment_status(p, a.id, a.to_status, a.reason)
    _done(a, e, msg=f"{e['id']} → {e['status']}")
    return 0


@mut
def cmd_experiment_baseline(a, p) -> int:
    e = S.set_baseline(p, None if a.unset else a.id)
    _done(a, e or {}, msg=(f"{e['id']} is now the baseline" if e else "baseline cleared"))
    return 0


@mut
def cmd_experiment_synthesize(a, p) -> int:
    mark = "completed" if a.complete else ("failed" if a.fail else None)
    s = S.synthesize(p, a.id, a.happened, a.worked, a.failed, a.interpretation, a.limitations, a.unresolved, a.next, mark)
    _done(a, s, msg=f"synthesis {s['id']} recorded ({len(s['runs_covered'])} runs covered)")
    if not a.json:
        print(dim("  promote reusable conclusions: research finding create \"…\" --supports " + normalize_id(a.id, "EXP")))
        from research import guide as G
        if G.settings(p).get("enabled", True) and os.path.exists(G.guide_path(p)):
            print(yellow("  Scientist Guide is now out of date (not updated yet): research guide refresh"))
    return 0


# runs
def _cmd_list(a) -> List[str]:
    cmd = list(a.cmd or [])
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    return cmd


@mut
def cmd_run_exec(a, p) -> int:
    cmd = _cmd_list(a)
    kw = dict(parameters=parse_kv(a.param), label=a.label, working_dir=a.cwd, notes=a.notes, override=a.override)
    if not a.json and not a.detach:
        print(dim(f"▶ {R.command_str(cmd)}"), file=sys.stderr)
    r = R.exec_run(p, a.experiment, cmd, detach=a.detach, tee=not a.json, **kw)
    if a.json:
        out_json(r)
    else:
        color = green if r["status"] == "completed" else (yellow if r["status"] == "running" else red)
        print(color(f"■ {r['id']} {r['status']}") + dim(f"  exit {r.get('exit_code')}  {fmt_dur(r.get('duration_s'))}  logs: {r['run_dir']}/"),
              file=sys.stderr)
        st_ = S.experiment_state(p, r["experiment_id"])
        if st_["over_run_budget"]:
            print(yellow(f"⚠ {r['experiment_id']} reached its run budget — synthesize before running more."), file=sys.stderr)
    return 0 if r["status"] in ("completed", "running") else (r.get("exit_code") or 1)


@mut
def cmd_run_submit(a, p) -> int:
    from research.hpc.remote import reservation
    opts = dict(partition=a.partition, account=a.account, time=a.time, cpus=a.cpus, mem=a.mem, gpus=a.gpus,
                extra_args=a.sbatch_arg or None)
    merged = {**(p.config.get("backends", {}).get("slurm") or {}),
              **{k: v for k, v in opts.items() if v not in (None, [], "")}}
    res = reservation(merged, {"name": "local slurm"})
    if a.dry_run:
        # Same reservation disclosure as `hpc submit --dry-run`: this is what the user approves.
        if a.json:
            out_json({"dry_run": True, "experiment": a.experiment, "command": " ".join(_cmd_list(a)),
                      "resources": merged, "reservation": res})
            return 0
        print(bold(f"Would submit a run of {a.experiment} to SLURM"))
        kv("command", " ".join(_cmd_list(a)))
        print()
        print(bold("  Resources this would RESERVE:"))
        print("    " + yellow(res["summary"]))
        if res.get("worst_case"):
            print("    " + dim(f"worst case at the wall-time limit: {res['worst_case']}"))
        kv("partition", res.get("partition") or "(cluster default)")
        kv("account", res.get("account") or "(cluster default)")
        print(dim("\n  Nothing was submitted."))
        print(yellow("  Confirm these resources with whoever owns the allocation, then re-run without --dry-run."))
        return 0
    r = R.submit_run(p, a.experiment, _cmd_list(a), parameters=parse_kv(a.param), label=a.label,
                     working_dir=a.cwd, notes=a.notes, override=a.override, **opts)
    if not a.json:
        print(green("✓ ") + f"{r['id']} submitted as SLURM job {r['slurm_job_id']}")
        kv("reserved", res["summary"])
        if res.get("worst_case"):
            kv("worst case", res["worst_case"])
        return 0
    _done(a, r, msg=f"{r['id']} submitted as SLURM job {r['slurm_job_id']}")
    return 0


@mut
def cmd_run_attach(a, p) -> int:
    r = R.attach_run(p, a.experiment, command=" ".join(_cmd_list(a)) or a.command, status=a.status,
                     parameters=parse_kv(a.param), label=a.label, working_dir=a.cwd, notes=a.notes,
                     override=a.override, stdout_path=a.stdout, stderr_path=a.stderr, exit_code=a.exit_code,
                     started_at=a.started, ended_at=a.ended, slurm_job_id=a.slurm_job)
    _done(a, r, msg=f"{r['id']} attached to {r['experiment_id']} [{r['status']}]")
    return 0


def cmd_run_list(a, p) -> int:
    rows = R.list_runs(p, a.experiment, a.status, a.limit)
    for r in rows:
        r["dur"] = fmt_dur(r.get("duration_s"))
        r["exit"] = f"exit {r['exit_code']}" if r.get("exit_code") is not None else None
        r["cmd"] = (r.get("label") or r.get("command") or "")[:60]
    out_json(rows) if a.json else print_list(rows, ["id", "experiment_id", "status", "backend", "exit", "dur", "cmd"])
    return 0


def cmd_run_show(a, p) -> int:
    r = R.reconcile_run(p, a.id)
    d = V.run_detail(p, r["id"])
    if a.json:
        out_json(d)
        return 0
    print(f"{bold(d['id'])}  [{st(d['status'])}]  {dim(d['experiment_id'] + ' ' + d['experiment']['title'])}")
    for k in ("label", "command", "working_dir", "parameters", "backend", "hostname", "slurm_job_id", "slurm",
              "exit_code", "started_at", "ended_at", "run_dir", "notes"):
        kv(k.replace("_", " "), d.get(k))
    kv("duration", fmt_dur(d.get("duration_s")) if d.get("duration_s") else None)
    kv("git", f"{d.get('git_branch') or '—'} @ {(d.get('git_commit') or '—')[:10]}" + (yellow(" (dirty, see git.diff)") if d.get("git_dirty") else ""))
    if d["metrics"]:
        kv("metrics", {k: fmt_val(v["value"] if v["value"] is not None else v["text"]) for k, v in d["metrics"].items()})
    for x in d["artifacts"]:
        kv("artifact", f"{x['id']} {x['path']}")
    return 0


@mut
def cmd_run_update(a, p) -> int:
    r = R.update_run(p, a.id, status=a.status, exit_code=a.exit_code, notes=a.notes, label=a.label,
                     parameters=parse_kv(a.param) if a.param else None, reviewed=True if a.reviewed else None)
    _done(a, r, msg=f"{r['id']} updated [{r['status']}]")
    return 0


@mut
def cmd_run_cancel(a, p) -> int:
    r = R.cancel_run(p, a.id)
    _done(a, r, msg=f"{r['id']} cancelled")
    return 0


def cmd_run_logs(a, p) -> int:
    r = R.get_run(p, a.id)
    path = p.abspath(r["stderr_path"] if a.stderr else r["stdout_path"])
    if not path or not os.path.exists(path):
        print(dim("(no log file yet)"))
        return 0
    if a.json:
        out_json({"path": path})
        return 0
    with open(path, "rb") as f:
        data = f.read()
    lines = data.decode("utf-8", "replace").splitlines()
    for l in lines[-a.tail:] if a.tail else lines:
        print(l)
    if a.follow:
        pos = len(data)
        try:
            while True:
                time.sleep(1)
                with open(path, "rb") as f:
                    f.seek(pos)
                    chunk = f.read()
                if chunk:
                    sys.stdout.write(chunk.decode("utf-8", "replace"))
                    sys.stdout.flush()
                    pos += len(chunk)
                r = R.reconcile_run(p, r["id"])
                if r["status"] not in ("queued", "running"):
                    break
        except KeyboardInterrupt:
            pass
    return 0


@mut
def cmd_sync(a, p) -> int:
    stats = p.sync()
    _done(a, stats, msg=f"synced (imported {stats['imported']} edited files, restored {stats['restored']})")
    return 0


# metrics / artifacts
@mut
def cmd_metric_add(a, p) -> int:
    m = R.log_metric(p, a.name, a.value, a.step, a.unit, run=a.run, experiment=a.experiment)
    _done(a, m, msg=f"{a.name} = {a.value} → {a.run or a.experiment}")
    return 0


@mut
def cmd_artifact_add(a, p) -> int:
    res = []
    for path in a.paths:
        x = R.register_artifact(p, path, run=a.run, experiment=a.experiment, name=a.name, type=a.type,
                                description=a.description, tags=a.tags, allow_missing=a.allow_missing,
                                hash=True if a.hash else None)
        res.append(x)
        if not a.json:
            print(green("✓ ") + f"{x['id']}  {x['path']}  {dim(x['type'])} {dim(human_size(x.get('size')))}")
    if a.json:
        out_json(res)
    return 0


def cmd_artifact_list(a, p) -> int:
    rows = R.list_artifacts(p, a.run, a.experiment)
    for r in rows:
        r["owner"] = r.get("run_id") or r.get("experiment_id")
        r["sz"] = human_size(r.get("size"))
        r["missing"] = None if r["exists"] else "(missing)"
    out_json(rows) if a.json else print_list(rows, ["id", "path", "type", "sz", "owner", "missing"])
    return 0


# findings / decisions
@mut
def cmd_finding_create(a, p) -> int:
    f = S.create_finding(p, a.title, a.statement, a.kind, a.status, a.confidence, a.supports, a.contradicts,
                         a.related, a.questions, a.limitations, a.contradicting, a.tags)
    _done(a, f, msg=f"{f['id']}  {f['title']}  [{f['status']}]")
    return 0


def cmd_finding_list(a, p) -> int:
    where, args = [], []
    if a.status:
        where.append("status=?")
        args.append(a.status)
    if a.kind:
        where.append("kind=?")
        args.append(a.kind)
    rows = p.list("finding", " AND ".join(where), tuple(args))
    for r in rows:
        r["k"] = "✗" if r.get("kind") == "failure" else "•"
    out_json(rows) if a.json else print_list(rows, ["k", "id", "status", "confidence", "title", "author_type"])
    return 0


@mut
def cmd_finding_update(a, p) -> int:
    f = S.update_finding(p, a.id, supports=a.supports, contradicts=a.contradicts, related=a.related,
                         questions=a.questions, add_supports=a.add_supports, title=a.title, statement=a.statement,
                         status=a.status, confidence=a.confidence, limitations=a.limitations,
                         contradicting_evidence=a.contradicting, kind=a.kind)
    _done(a, f, msg=f"{f['id']} updated")
    return 0


@mut
def cmd_finding_supersede(a, p) -> int:
    f = S.supersede_finding(p, a.id, a.by, a.reason)
    _done(a, f, msg=f"{f['id']} superseded")
    return 0


@mut
def cmd_decision_create(a, p) -> int:
    d = S.create_decision(p, a.statement, a.reason, a.title, a.findings, a.experiments, a.date, tags=a.tags,
                          supersedes=a.supersedes)
    _done(a, d, msg=f"{d['id']}  {d['statement']}")
    return 0


def cmd_decision_list(a, p) -> int:
    rows = p.list("decision", "status=?" if a.status else "", (a.status,) if a.status else (), order="date DESC, id DESC")
    out_json(rows) if a.json else print_list(rows, ["id", "status", "date", "statement", "author_type"])
    return 0


@mut
def cmd_decision_update(a, p) -> int:
    d = S.update_decision(p, a.id, supporting_findings=a.findings, experiments=a.experiments, statement=a.statement,
                          reason=a.reason, status=a.status)
    _done(a, d, msg=f"{d['id']} updated")
    return 0


# generic
@mut
def cmd_mark(a, p) -> int:
    r = S.set_status(p, a.id, a.status, a.reason)
    _done(a, r, msg=f"{r['id']} → {r['status']}")
    return 0


# checkpoints
@mut
def cmd_checkpoint_create(a, p) -> int:
    c = C.create_checkpoint(p, title=a.title, goal=a.goal, understanding=a.understanding, baseline=a.baseline,
                            baseline_experiment=a.baseline_experiment, findings=a.findings, failures=a.failures,
                            questions=a.questions, experiments=a.experiments, current_problem=a.problem,
                            next_experiment=a.next, notes=a.notes, use_draft=not a.no_draft,
                            commit=True if a.commit else None)
    _done(a, c, msg=f"{c['id']}  {c['title']}  → {c['path']}"
          + (f"  (committed {c['snapshot_commit'][:10]})" if c.get("snapshot_commit") else ""))
    return 0


def cmd_checkpoint_list(a, p) -> int:
    rows = p.list("checkpoint", order="created_at DESC, id DESC")
    for r in rows:
        r["date"] = (r.get("created_at") or "")[:10]
    out_json(rows) if a.json else print_list(rows, ["id", "date", "title", "author_type"])
    return 0


def cmd_checkpoint_draft(a, p) -> int:
    d = C.checkpoint_draft(p)
    out_json(d) if a.json else print_generic({"id": "draft", **d})
    return 0


def cmd_checkpoint_show(a, p) -> int:
    cid = a.id or (p.q1("SELECT id FROM checkpoints ORDER BY created_at DESC, id DESC LIMIT 1") or {}).get("id")
    if not cid:
        print(dim("no checkpoints yet"))
        return 0
    c = V.checkpoint_detail(p, cid)
    if a.json:
        out_json(c)
        return 0
    print(open(p.mirror_path("checkpoint", c["id"]), encoding="utf-8").read())
    return 0


# notes
def cmd_note_new(a, p) -> int:
    n = S.create_note(p, a.title, a.body or "", a.link, a.pin)
    _done(a, n, msg=f"{n['id']} → {n['path']}")
    return 0


def cmd_note_list(a, p) -> int:
    rows = S.list_notes(p)
    for r in rows:
        r["pin"] = "📌" if r["pinned"] else None
        r["ln"] = ", ".join(r["links"]) or None
    out_json(rows) if a.json else print_list(rows, ["pin", "id", "title", "ln"])
    return 0


def cmd_note_pin(a, p) -> int:
    n = S.pin_note(p, a.id, not a.unpin)
    _done(a, n, msg=f"{n['id']} {'unpinned' if a.unpin else 'pinned'}")
    return 0


def cmd_note_link(a, p) -> int:
    n = S.link_note(p, a.id, a.targets, remove=a.remove)
    _done(a, n, msg=f"{n['id']} links: {', '.join(n['links']) or '—'}")
    return 0


# skills / agents
def cmd_skills_list(a, p) -> int:
    from research import skills as SKL
    rows = SKL.list_skills(p)
    if a.json:
        out_json(rows)
    else:
        for s in rows:
            tags = ([green("always")] if s["always"] else []) + ([f"applies to {', '.join(s['applies_to'])}"] if s["applies_to"] else [])
            src = dim(f" ← {s['source']}" + (f"@{s['ref'][:8]}" if s.get("ref") else "")) if s.get("source") else ""
            print(f"{bold(s['name'])}  {dim(s['path'])}{src}" + (f"  {' · '.join(tags)}" if tags else ""))
            print(f"    {s['description'][:200]}")
    return 0


def cmd_skills_show(a, p) -> int:
    from research import skills as SKL
    s = SKL.get_skill(p, a.name)
    out_json(s) if a.json else print(s["markdown"])
    return 0


def cmd_skills_new(a, p) -> int:
    s = S.create_skill(p, a.name, a.description or "")
    _done(a, s, msg=f"skill {s['name']} → {s['path']}")
    return 0


def cmd_skills_duplicate(a, p) -> int:
    s = S.duplicate_skill(p, a.src, a.name)
    _done(a, s, msg=f"skill {s['name']} → {s['path']}")
    return 0


def cmd_agents_md(a, p) -> int:
    from research import agentsmd as AM
    if a.preview:
        st = AM.state(p)
        if a.json:
            out_json({k: st[k] for k in ("path", "action", "problem", "up_to_date", "new")})
        else:
            print(st["new"])
        return 1 if st["problem"] else 0
    if a.diff:
        st = AM.state(p)
        if st["problem"]:
            print(red("✗ ") + f"refusing to touch AGENTS.md: {st['problem']}", file=sys.stderr)
            print(dim("  Keep exactly one `<!-- research:begin … -->` and one `<!-- research:end -->` line."),
                  file=sys.stderr)
            return 1
        d = AM.diff(p)
        if a.json:
            out_json({"path": st["path"], "action": st["action"], "up_to_date": st["up_to_date"], "diff": d})
        elif d:
            sys.stdout.write(d)
        else:
            print(dim("AGENTS.md managed block is already up to date"))
        return 0
    r = AM.apply(p)
    _done(a, r, msg=(f"{os.path.relpath(r['path'])} already up to date" if not r["changed"]
                     else f"{os.path.relpath(r['path'])}: managed block {r['action']}d "
                          "(bytes outside the markers untouched)"))
    return 0


def cmd_upgrade(a, p=None) -> int:
    """Bring an existing project up to the installed Research OS. --dry-run touches nothing."""
    from research import upgrade as UP
    root = a.root or None
    if a.status:
        r = UP.status(root)
        if a.json:
            out_json(r)
            return 0
        print(bold("Research OS upgrade status"))
        kv("project root", r["root"])
        kv("research version", r["tool_version"])
        kv("project schema", r["project_schema"])
        kv("target schema", r["target_schema"])
        kv("AGENTS.md block", "up to date" if r["agents_md"]["up_to_date"] else r["agents_md"]["action"])
        if r["downgrade_refused"]:
            print(red("✗ ") + r["downgrade_refused"])
            return 1
        print(green("✓ up to date") if r["up_to_date"] else yellow(f"{r['changes']} change(s) pending — "
                                                                   "run `research upgrade --dry-run`"))
        return 0
    if a.apply:
        r = UP.apply(root)
        if a.json:
            out_json(r)
        else:
            _print_upgrade(r, applied=True)
        return 0 if r.get("ok", True) else 1
    r = UP.plan(root)
    if a.json:
        out_json(r)
    else:
        _print_upgrade(r, applied=False)
    return 1 if r["downgrade_refused"] else 0


_UPGRADE_SIGIL = {"create": "+", "add": "+", "build": "+", "migrate": "~", "merge": "~", "replace": "~",
                  "add_block": "~", "refresh": "~", "refuse": "!"}


def _print_upgrade(r, applied: bool) -> None:
    print(bold("Research OS Upgrade " + ("Result" if applied else "Preview")))
    print()
    print(f"  Current project schema: {r['project_schema']}")
    print(f"  Target project schema:  {r['target_schema']}")
    print(f"  Research OS version:    {r['tool_version']}")
    if r["downgrade_refused"]:
        print()
        print(red("✗ ") + r["downgrade_refused"])
        return
    steps = r.get("applied") if applied else r["steps"]
    if not steps:
        print("\n" + green("✓ ") + "already up to date; nothing to do")
    else:
        print("\n  " + bold("Applied:" if applied else "Required:"))
        for s in steps:
            sig = _UPGRADE_SIGIL.get(s["change"], "~")
            line = f"  {sig} {s.get('path') or s['kind']}"
            detail = s.get("detail") or (f"add {len(s['keys'])} key(s)" if s.get("keys") else "")
            print(f"{line}" + (dim(f"  ({detail})") if detail else ""))
    for s in (r.get("refused") or [s for s in r["steps"] if s["change"] == "refuse"]):
        print(red(f"  ! {s.get('path')}: {s.get('detail')}"))
        print(dim("    Fix the markers by hand; everything else still upgrades."))
    for s in r.get("failed") or []:
        print(red(f"  ✗ {s.get('path') or s['kind']} failed: {s.get('error')}"))
    if r["preserved"]:
        print("\n  " + bold("Preserved:"))
        for x in r["preserved"]:
            print(f"  = {x}")
        print(f"  = {dim('user-authored AGENTS.md sections, custom config values, unknown config keys')}")
    print("\n  " + r["note"])
    if r.get("backup"):
        print(dim(f"  backup of edited metadata files: {r['backup']}"))
    if not applied and steps:
        print("\n" + dim("  Nothing was changed. Run `research upgrade --apply` to apply, or "
                         "`research agents-md --diff` to review the AGENTS.md block first."))
    return 0


@mut
def cmd_guide(a, p) -> int:
    from research import guide as G
    act = a.action
    if act == "init":
        r = G.init_guide(p)
        _done(a, r, msg=f"{r['path']} " + ("created" if r["created"] else "already exists (left untouched)"))
    elif act == "done":
        r = G.mark_done(p, a.ids)
        _done(a, r, msg=f"recorded; {len(r['pending'])} still pending")
    elif act == "refresh":
        r = G.refresh(p, a.profile, getattr(a, "model", None))
        if a.json:
            out_json(r)
        elif r.get("up_to_date"):
            print(green("✓ ") + "guide is up to date")
        elif a.run:
            print(f"running {r['profile']}/{r['role']} (model {r['model']}) on {len(r['pending'])} pending synthesis(es)", file=sys.stderr)
            import subprocess
            root = p.root
            p.close()
            return subprocess.call(r["argv"], cwd=root, env={**os.environ, **r["env"]})
        else:
            print(f"{len(r['pending'])} pending: {', '.join(r['pending'])} — run this (nothing is marked done until the agent does):")
            print(r["shell"])
    else:
        r = G.status(p)
        if a.json:
            out_json(r)
        else:
            print(f"{r['path']}  " + ("exists" if r["exists"] else "missing (research guide init)")
                  + ("" if r["enabled"] else "  [disabled]"))
            for s in r["pending"]:
                print(yellow(f"  pending {s['id']}") + dim("  findings: " + (", ".join(f"{f['id']}[{f['status']}, numbers {f['numerical']}]" for f in s["findings"]) or "none")))
            if r["up_to_date"]:
                print(green("✓ ") + "all syntheses reflected")
    return 0


def cmd_setup(a, p) -> int:
    from research import setup_agents as SA
    r = SA.setup(p, a.provider, a.preset, apply=a.apply and not a.dry_run, approve=a.approve or [], probe=a.probe)
    if a.json:
        out_json(r)
        return 0
    print(("applied" if r["applied"] else "dry run (add --apply)") + f" · claude {r['claude']['version'] or 'not found'}")
    for f in r["agent_files"]:
        print(f"  {f['state'].ljust(15)} {f['path']}  → {f['model']}")
    for k in ("installed", "skipped", "unavailable", "recommended"):
        for x in r[k]:
            print(f"  {k}: {x}")
    print(f"  routing: {r['routing_verified']}")
    return 0


def _orch_task(p, a):
    return p.get("task", normalize_id(a.task, "T"))


@mut
def cmd_orchestrate(a, p) -> int:
    from research import orchestrate as O
    act = a.action
    if act == "route":
        r = O.route(p, _orch_task(p, a), a.tier, a.available)
    elif act == "dispatch":
        r = O.dispatch_task(p, a.task, a.tier, a.available, a.allow_unfrozen)
        if not a.json and not a.run:
            print(f"{r['tier']} ({r['model']}) brief → {r['brief']}" + (f"  ⚠ {r['route']['note']}" if r["degraded"] else ""))
            print(r["shell"])
            return 0
        if a.run and not a.json:
            import subprocess
            root = p.root
            p.close()
            return subprocess.call(r["argv"], cwd=root, env={**os.environ, **r["env"]})
    elif act == "freeze":
        r = O.freeze(p, a.task, a.rest)
    elif act == "attempt":
        r = O.record_attempt(p, a.task, a.tier, a.outcome, a.category, a.note)
    elif act == "correct-test":
        r = O.correct_test(p, a.task, a.rest[0], a.reason, a.tier)
    elif act == "delegate":
        r = O.delegate(p, a.task, a.rest[0], a.tier or "haiku", a.goal, a.acceptance, a.check)
    elif act == "status":
        r = O.status(p, a.task)
    else:
        raise ResearchError("unknown action")
    _done(a, r, msg=json.dumps(r, default=str) if not a.json else None)
    return 0


@mut
def cmd_tuning(a, p) -> int:
    from research import orchestrate as O
    cfg_ = json.loads(a.config) if getattr(a, "config", None) else None
    if a.action == "declare":
        rng = {}
        for x in a.range or []:
            k, v = x.split("=", 1)
            rng[k] = v
        r = O.tuning_declare(p, a.exp, a.objective, rng, a.strategy, a.selection, a.seed, a.budget)
    elif a.action == "trial":
        r = O.tuning_trial(p, a.exp, cfg_, a.split, a.value, a.run)
    elif a.action == "select":
        r = O.tuning_select(p, a.exp, cfg_, a.rationale or "")
    else:
        r = O.tuning_test(p, a.exp, a.value, a.run)
    _done(a, r, msg=json.dumps(r, default=str) if not a.json else None)
    return 0


def _fmt_v(v) -> str:
    c = {"verified": green, "mismatch": red, "missing": red, "stale": red, "unverifiable": yellow}.get(v["status"], str)
    return f"{v['id']}  {c(v['status'].upper())}  claimed {v['claimed']:g} · recorded " + (f"{v['actual']:.6g}" if v["actual"] is not None else "—") \
        + (f" {v['unit']}" if v.get("unit") else "") + "".join(f"\n    ✗ {i}" for i in v["issues"]) + "".join(f"\n    ⚠ {w}" for w in v["warnings"])


@mut
def cmd_evidence(a, p) -> int:
    from research import evidence as E
    act = a.action
    if act == "bind":
        r = E.bind(p, a.id, a.run, a.metric, a.step, a.select, a.claim, a.unit, a.derive, a.input, a.factor, a.rel_tol,
                   a.artifact, a.partition, a.note, a.supersedes)
        if a.json:
            out_json(r)
        else:
            print(_fmt_v(r["verification"]))
        return 0 if r["verification"]["status"] == "verified" else 1
    if act == "value":
        r = E.resolve_metric(p, normalize_id(a.id, "RUN"), a.metric, a.step, a.select)
        if a.json:
            out_json(r)
        else:
            print(f"{r['run']} {r['metric']} = " + (f"{r['value']:.10g}" if r["value"] is not None else "—") + (f" {r['unit']}" if r["unit"] else "")
                  + f"  [{r['status']}]" + "".join(f"\n  {i}" for i in r["issues"]))
        return 0 if r["status"] == "verified" else 1
    if act == "verify":
        r = E.verify_finding(p, a.id)
        if a.json:
            out_json(r)
        else:
            print(f"{r['finding']}: numerical evidence {r['status']}")
            for b in r["bindings"]:
                print("  " + _fmt_v(b))
        return 0 if r["status"] in ("verified", "no_bindings") else 1
    if act == "suggest":
        r = E.suggest(p, a.id)
    elif act == "provenance":
        r = E.provenance(p, a.id)
    elif act == "artifact":
        r = E.artifact_integrity(p, a.id, deep=a.deep)
    elif act == "pin":
        r = E.pin_artifact(p, a.id)
    else:
        r = E.overview(p)
    if a.json:
        out_json(r)
    else:
        print(json.dumps(r, indent=1, default=str))
    return 0


@mut
def cmd_source(a, p) -> int:
    from research import evidence as E
    if a.action == "add":
        r = E.source_add(p, a.doi, a.arxiv, a.url, a.title)
    elif a.action == "mark":
        r = E.source_mark(p, a.id, a.identity_verified, a.examined, a.locator, a.finding, a.verdict, a.note)
    else:
        r = E.source_list(p, a.finding)
    _done(a, r, msg=None)
    if not a.json:
        print(json.dumps(r, indent=1, default=str))
    return 0


@mut
def cmd_config(a, p) -> int:
    """`research config show|check|migrate` — inspect and tidy configuration. show/check never write."""
    from research import configfile as CF
    from research.store import DEFAULT_CONFIG
    raw = p.raw_config

    if a.action == "show":
        if a.sources:
            src = CF.sources(raw, DEFAULT_CONFIG)
            if a.json:
                out_json(src)
                return 0
            width = max((len(k) for k in src), default=10)
            for k, info in src.items():
                colour = {"project": green, "machine": cyan, "default": dim}[info["source"]]
                print(f"  {k.ljust(width)}  {fmt_val(info['value']).ljust(22)} {colour(info['source'])}")
            m = CF.machine_path()
            print()
            kv("machine config", m if m and os.path.isfile(m) else f"{m} (absent)")
            kv("project config", p.rel(p.config_path))
            return 0
        shown = p.config if a.effective else raw
        if a.json:
            out_json(shown)
        else:
            from research import yamlio
            sys.stdout.write(yamlio.dump(shown, block=True))
            if not a.effective:
                print(dim("\n  (this project's own settings only — add --effective for every value in force)"))
        return 0

    if a.action == "check":
        with open(p.config_path, encoding="utf-8") as f:
            text = f.read()
        r = CF.check(raw, DEFAULT_CONFIG, text)
        if a.json:
            out_json(r)
            return 0 if r["ok"] else 1
        for e in r["errors"]:
            print(red("✗ ") + f"{e['key']}: {e['problem']}")
        for w in r["warnings"]:
            print(yellow("⚠ ") + w)
        if r["unknown"]:
            print(yellow(f"⚠ {len(r['unknown'])} key(s) this version does not recognize (kept as-is):"))
            for u in r["unknown"][:10]:
                print(dim(f"    {u['key']} = {fmt_val(u['value'])}"))
        if r["redundant"]:
            print(dim(f"  {len(r['redundant'])} key(s) repeat an inherited default and could be removed "
                      f"(`research config migrate`)"))
        if r["ok"] and not r["unknown"]:
            print(green("✓ ") + "configuration is valid")
        kv("machine config", r["machine_config"] or "none")
        return 0 if r["ok"] else 1

    # migrate
    from research.store import DEFAULT_CONFIG as D
    prev = CF.migrate_preview(p.config_path, D)
    if a.json:
        out_json({k: v for k, v in prev.items() if k not in ("old", "new")})
    if not prev["changed"]:
        if not a.json:
            print(green("✓ ") + "config.yaml is already sparse and block-style; nothing to do")
        return 0
    if not prev["safe"]:
        print(red("✗ ") + "refusing to migrate: the effective settings would change")
        for k in prev["dropped_keys"][:10]:
            print(red(f"    would drop {k}"))
        for k in prev["changed_values"][:10]:
            print(red(f"    would change {k}"))
        print(dim("  Nothing was written. This is a bug — please report the diff above."))
        return 1
    if not a.json:
        sys.stdout.write(prev["diff"])
        print()
        kv("lines", f"{prev['lines_before']} → {prev['lines_after']}")
        kv("effective settings", green("identical") if prev["effective_identical"] else red("CHANGED"))
        if prev["comments_before"]:
            print(yellow(f"  ⚠ {prev['comments_before']} comment line(s) in the current file will not survive "
                         "a full rewrite — this is the one operation that does not preserve them."))
    if not a.apply:
        if not a.json:
            print(dim("\n  Nothing was changed. Re-run with --apply to write it."))
        return 0
    import shutil as _sh
    backup = p.config_path + ".before-migrate"
    _sh.copy2(p.config_path, backup)
    from research.store import _atomic_write
    _atomic_write(p.config_path, prev["new"])
    p.config = p.load_config()
    if not a.json:
        print(green("✓ ") + f"migrated {p.rel(p.config_path)}  "
                            + dim(f"(previous file kept at {p.rel(backup)})"))
    return 0


def cmd_rebuild(a, p) -> int:
    counts = p.rebuild_index(backup=True)
    _done(a, counts, msg="index rebuilt from text files: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    return 0


# ----------------------------------------------------------------------------- plans
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


# ----------------------------------------------------------------------------- tasks
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


# ----------------------------------------------------------------------------- coordination / verification
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


@mut
def cmd_finding_review(a, p) -> int:
    from research import verification as VF
    r = VF.review_finding(p, a.id, a.verdict, a.notes, a.confidence)
    _done(a, r, msg=f"{r['review']['id']} {a.verdict} → {r['finding']['id']} is now [{r['finding']['status']}]")
    return 0


# ----------------------------------------------------------------------------- search / compare / sweep / report
def cmd_search(a, p) -> int:
    from research.search import search
    rows = search(p, " ".join(a.query), a.type, a.limit)
    if a.json:
        out_json(rows)
        return 0
    if not rows:
        print(dim("no matches"))
    for r in rows:
        print(f"{bold(r['id'])}  {dim(r['type'])}  {r['title'] or ''}" + (f"  [{st(r['status'])}]" if r.get("status") else ""))
        if r.get("snippet") and r["snippet"] != r["title"]:
            print(dim("    " + r["snippet"]))
    return 0


def cmd_run_compare(a, p) -> int:
    from research import compare as CMP
    c = CMP.compare_runs(p, a.a, a.b)
    if a.json:
        out_json(c)
        return 0
    A_, B_ = c["a"]["id"], c["b"]["id"]
    print(bold(f"{A_}  vs  {B_}") + dim(f"   {c['a']['experiment_id']} / {c['b']['experiment_id']}"))
    def table(title, rows, cols):
        if not rows:
            return
        section(title)
        w = max(len(str(r["name"])) for r in rows) + 2
        for r in rows:
            line = f"  {str(r['name']).ljust(w)}{fmt_val(r['a'])!s:<16} {fmt_val(r['b'])!s:<16}"
            if "delta" in r and r["delta"] is not None:
                line += (green if r["delta"] < 0 else yellow)(f" Δ {r['delta']:+.4g}") + (dim(f" ({r['rel']:+.1%})") if r.get("rel") is not None else "")
            elif "same" in r and not r["same"]:
                line += yellow(" ≠")
            print(line)
    table("Parameters", c["parameters"], None)
    table("Metrics", c["metrics"], None)
    table("Run", [m for m in c["meta"] if not m["same"]], None)
    if c["code_diff"]:
        section("Code changes between the two commits")
        print(c["code_diff"]["stat"].rstrip())
    for side in ("a", "b"):
        if c["uncommitted"][side]:
            print(yellow(f"\n{c[side]['id']} ran with uncommitted changes (see its git.diff)"))
    if c["artifacts"]:
        section("Artifacts")
        for x in c["artifacts"]:
            mark = dim("identical") if x["same_hash"] else ("" if x["a"] and x["b"] else yellow("only in " + (A_ if x["a"] else B_)))
            print(f"  {x['name']}  {dim(x['type'])}  {mark}")
    return 0


@mut
def cmd_run_sweep(a, p) -> int:
    grid = {}
    for g in a.grid or []:
        k, _, v = g.partition("=")
        if not k or not v:
            raise ResearchError(f"--grid expects name=v1,v2,… (got {g!r})")
        grid[k.strip()] = [parse_kv([f"x={x}"])["x"] for x in v.split(",")]
    opts = dict(partition=a.partition, account=a.account, time=a.time, gpus=a.gpus, mem=a.mem, cpus=a.cpus) if a.mode == "slurm" else {}
    res = R.sweep_run(p, a.experiment, " ".join(_cmd_list(a)), grid or None, mode=a.mode, override=a.override,
                      dry_run=a.dry_run, working_dir=a.cwd, tee=not a.json, **{k: v for k, v in opts.items() if v})
    if a.json:
        out_json(res)
        return 0
    for w in res["warnings"]:
        print(yellow("⚠ " + w))
    if a.dry_run:
        for pt in res["points"]:
            print(f"  {pt['label']}  {dim(pt['command'])}")
        print(dim(f"{len(res['points'])} runs (dry run; nothing started)"))
        if a.mode == "slurm":
            # A sweep multiplies the reservation by every grid point, which is where a careless
            # submission gets expensive fastest.
            from research.hpc.remote import reservation
            merged = {**(p.config.get("backends", {}).get("slurm") or {}),
                      **{k: v for k, v in opts.items() if v not in (None, [], "")}}
            one = reservation(merged, {"name": "local slurm"})
            n = len(res["points"])
            print()
            print(bold(f"  Each of the {n} runs would RESERVE:"))
            print("    " + yellow(one["summary"]))
            if one.get("cpu_hours"):
                tot = [f"{round(one['cpu_hours'] * n, 1)} CPU-hours"]
                if one.get("gpu_hours"):
                    tot.append(f"{round(one['gpu_hours'] * n, 1)} GPU-hours")
                print("    " + yellow(f"whole sweep, worst case: {' and '.join(tot)}"))
            print(yellow(f"  Confirm this with whoever owns the allocation before running the sweep."))
    else:
        for r in res["runs"]:
            print(f"  {r['id']}  [{st(r['status'])}]  {r.get('label') or ''}")
    return 0


# ----------------------------------------------------------------------------- hpc
def cmd_hpc(a, p) -> int:
    """`research hpc <action>`: remote execution on a configured cluster. All of it is opt-in."""
    from research.hpc import bundle as HB
    from research.hpc import config as HC
    from research.hpc import datasets as HD
    from research.hpc import remote as HR
    from research.hpc import results as HRES
    act = a.action
    if not act:                      # bare `research hpc` → show what it can do
        a._hpc_parser.print_help()
        return 0
    cl = getattr(a, "cluster", None)

    if act == "config":
        if a.example:
            print(HC.EXAMPLE)
            return 0
        names = HC.names(p)
        if a.json:
            out_json({"default": HC.default_name(p), "clusters": HC.clusters(p)})
            return 0
        if not names:
            print(yellow("No HPC cluster is configured.") + dim("  HPC support is opt-in."))
            print(dim("  See a worked example: research hpc config --example"))
            return 0
        for name, c in sorted(HC.clusters(p).items()):
            mark = green(" (default)") if name == HC.default_name(p) else ""
            print(f"{bold(name)}{mark}  {c.get('transport')} → {c.get('ssh_host') or '—'}")
            for k, v in sorted((c.get("paths") or {}).items()):
                if v:
                    kv(k, v)
            kv("slurm", {k: v for k, v in (c.get("slurm") or {}).items() if v not in (None, [], "")})
        return 0

    if act == "clusters":
        names = HC.names(p)
        if a.json:
            out_json({"clusters": names, "default": HC.default_name(p)})
        elif not names:
            print(dim("(none configured — `research hpc config --example`)"))
        else:
            for n in names:
                print(f"  {bold(n)}" + (green("  (default)") if n == HC.default_name(p) else ""))
        return 0

    if act == "doctor":
        d = HR.doctor(p, cl)
        if a.json:
            out_json(d)
            return 0
        print(bold(f"HPC doctor — cluster {d['cluster']} ({d.get('ssh_host') or d.get('transport')})"))
        for c in d["checks"]:
            mark = green("✓") if c["ok"] else (red("✗") if c["ok"] is False else yellow("•"))
            print(f"  {mark} {c['check'].ljust(20)} {c['detail']}")
            if c.get("fix"):
                print(dim(f"      → {c['fix']}"))
        print()
        print((green("✓ ") if d["ok"] else yellow("⚠ ")) + d["summary"])
        return 0 if d["ok"] else 1

    if act == "init":
        r = HR.init_workspace(p, cl, dry_run=a.dry_run)
        if a.json:
            out_json(r)
        elif a.dry_run:
            print(bold("Would create:"))
            for x in r["would_create"]:
                print(f"  + {x}")
            for x in r["existing"]:
                print(dim(f"  = {x} (exists; left alone)"))
            print(dim("\n  Nothing was created."))
        else:
            for x in r["created"]:
                print(green("✓ ") + x)
            for x in r["existing"]:
                print(dim(f"  = {x} (already there)"))
            print(dim("  Existing datasets, code, runs and results were not touched."))
        return 0

    if act == "inspect":
        r = HR.inspect(p, cluster_name=cl, project_slug=a.project)
        if a.json:
            out_json(r)
            return 0
        print(bold(f"Cluster {r['cluster']}"))
        kv("project dir", r["paths"]["project"])
        if r.get("datasets"):
            section(f"Datasets ({len(r['datasets'])})")
            for d in r["datasets"]:
                print(f"  {d['id'].ljust(24)} {st(d.get('status') or 'unknown')}"
                      + (dim(f"  v{d['version']}") if d.get("version") else "")
                      + ("" if d.get("has_manifest") else yellow("  (no manifest)")))
        section(f"Code deployments ({len(r['deployments'])})")
        for d in r["deployments"]:
            print(f"  {d['experiment']}/{d['run']}  {dim((d.get('token') or '')[:12])}")
        section(f"Remote runs ({len(r['remote_runs'])})")
        for x in r["remote_runs"]:
            s = (x.get("status") or {}).get("state") or "—"
            print(f"  {x['run'].ljust(12)} {st(s)}")
        if r["discrepancies"]:
            section(yellow("Discrepancies"))
            for d in r["discrepancies"]:
                print(f"  {d['run']}: {d['issue']}")
                print(dim(f"    → {d['do']}"))
        return 0

    if act == "datasets":
        return _cmd_hpc_datasets(a, p, HC, HD)

    if act == "bundle":
        cluster = HC.get(p, cl) if (cl or HC.names(p)) else None
        res = (HC.check_resources(cluster, _slurm_opts(a)) if cluster else _slurm_opts(a))
        r = HB.build(p, a.id, command=" ".join(_cmd_list(a)) or None, fmt=a.format,
                     cluster=cluster, resources=res)
        if a.json:
            out_json({k: v for k, v in r.items() if k != "manifest"})
            return 0
        print(green("✓ ") + f"bundle: {r['path']}  {dim(human_size(r['bytes']))}")
        kv("experiment", r["experiment"])
        kv("runs", r["runs"])
        kv("token", r["token"])
        skipped = (r["manifest"].get("source") or {}).get("skipped") or []
        if skipped:
            print(yellow(f"  {len(skipped)} file(s) not packaged:"))
            for s_ in skipped[:5]:
                print(dim(f"    {s_['path']}: {s_['reason']}"))
        print(dim(f"\n  tar -xzf {os.path.basename(r['path'])} && cd {os.path.basename(r['dir'])}"))
        print(dim("  bash scripts/preflight.sh && bash scripts/submit.sh"))
        print(dim(f"  # then: bash scripts/collect.sh  →  research hpc import results-{r['runs'][0]}.tar.gz"))
        return 0

    if act == "submit":
        r = HR.submit(p, a.id, cluster_name=cl, command=" ".join(_cmd_list(a)) or None,
                      dry_run=a.dry_run, approve=a.yes, resources=_slurm_opts(a),
                      stage_datasets=a.stage_datasets, deep_verify=a.deep)
        if a.json:
            out_json(r)
            return 0
        if r.get("dry_run"):
            res = r.get("reservation") or {}
            print(bold(f"Would submit {r['experiment']} to {r['cluster']}"))
            kv("command", r["command"])
            print()
            # The reservation is what the user is actually approving, so it gets its own block:
            # Slurm bills what you reserve, not what you use.
            print(bold("  Resources this would RESERVE:"))
            print("    " + yellow(res.get("summary") or "—"))
            if res.get("worst_case"):
                print("    " + dim(f"worst case if it runs to the wall-time limit: {res['worst_case']}"))
            kv("partition", res.get("partition") or "(cluster default)")
            kv("account", res.get("account") or "(cluster default)")
            print()
            for w in r["would"]:
                print(f"  · {w}")
            print(dim("\n  " + r["note"]))
            print(yellow("  Confirm these resources with whoever owns the allocation, then re-run with --yes."))
            return 0
        print(green("✓ ") + f"{r['run']} submitted to {r['cluster']} as slurm job {r['slurm_job_id']}")
        kv("reserved", (r.get("reservation") or {}).get("summary"))
        kv("experiment", r["experiment"])
        kv("remote code", r["remote"]["code"])
        kv("token", r["token"])
        print(dim(f"\n  research hpc status {r['run']}    # or just `research hpc sync` later"))
        return 0

    if act == "status":
        r = HR.status(p, a.id, cluster_name=cl)
        if a.json:
            out_json(r)
            return 0
        print(f"{bold(r['run'])}  {dim(r['experiment'])}  local [{st(r['local_status'])}]")
        kv("cluster", r["cluster"])
        kv("slurm job", r.get("slurm_job_id"))
        if r.get("slurm"):
            kv("slurm state", f"{r['slurm'].get('slurm_state')} (via {r['slurm'].get('source')})")
            if r["slurm"].get("note"):
                print(yellow("  " + r["slurm"]["note"]))
        if r.get("remote_status"):
            kv("remote state", r["remote_status"].get("state"))
            kv("exit code", r["remote_status"].get("exit_code"))
        kv("remote run dir", r.get("remote_run_dir"))
        return 0

    if act == "sync":
        r = HR.sync(p, cluster_name=cl, collect_results=not a.no_collect)
        if a.json:
            out_json(r)
            return 0
        print(f"checked {r['checked']} run(s)" + (f" on {', '.join(r['clusters'])}" if r["clusters"] else "")
              + (dim(f", skipped {r['skipped']} already settled") if r["skipped"] else ""))
        for x in r["recovered"]:
            print(green("✓ ") + f"{x['run']}: submission confirmed after the fact — slurm job "
                                f"{x['slurm_job_id']} (not resubmitted)")
        for x in r["updated"]:
            print(f"  {x['run']} → [{st(x['status'])}]" + dim(f"  job {x['slurm_job_id']}"))
        for x in r["collected"]:
            print(green("  ↓ ") + f"{x['run']} results imported"
                  + dim(f"  {x['metrics_added']} new metric entries, {x['artifacts']} artifact(s)"))
        for x in r["needs_attention"]:
            print(yellow(f"  ⚠ {x['run']}: {x['why']}"))
            print(dim(f"     → {x['do']}"))
        for e in r["errors"]:
            print(red(f"  ✗ {e.get('run') or e.get('cluster')}: {e['error']}"))
        if r["next_tasks"]:
            section("Ready for interpretation")
            for t in r["next_tasks"]:
                print(f"  {t['experiment']}  {t['title']}  "
                      + dim(f"{t['unsynthesized_runs']} unsynthesized run(s)"))
                print(dim(f"    → {t['do']}"))
            print(dim("\n  No agent was started: scientific interpretation stays a human/agent decision."))
        return 0

    if act == "collect":
        r = HR.collect(p, a.id, cluster_name=cl, max_bytes=a.max_bytes)
        _done(a, r, msg=f"{r['run']}: {r['metrics_added']} new metric entries, "
                        f"{len(r.get('artifacts') or [])} artifact(s), status {r['status']}")
        if not a.json:
            for w in r.get("warnings") or []:
                print(yellow("  ⚠ " + w))
            for ref in r.get("referenced") or []:
                print(dim(f"  → {ref['artifact']} stays on the cluster at {ref['remote_path']}"))
        return 0

    if act == "import":
        r = HRES.import_archive(p, a.id, dry_run=a.dry_run)
        if a.json:
            out_json(r)
            return 0
        if r["dry_run"]:
            print(bold(f"Would import into {r['run']} ({r['experiment']}):"))
            for f_ in r["would_import"]:
                print(f"  {f_}")
            print(dim("\n  Nothing was changed."))
            return 0
        print(green("✓ ") + f"{r['run']}: status {r['status']}, {r['metrics_added']} new metric entries, "
                            f"{len(r['artifacts'])} artifact(s), {len(r['logs'])} log(s)")
        for w in r["warnings"]:
            print(yellow("  ⚠ " + w))
        if not r["changed"]:
            print(dim("  (nothing new — this archive was already imported)"))
        return 0

    if act == "cancel":
        r = HR.cancel(p, a.id, cluster_name=cl)
        _done(a, r, msg=f"{r['run']}: slurm job {r['slurm_job_id']} cancelled on {r['cluster']}")
        return 0

    raise ResearchError(f"unknown hpc action {act!r}")


def _cmd_hpc_datasets(a, p, HC, HD) -> int:
    """`research hpc datasets <list|inspect|check|fetch|adopt>` — the filesystem is the source of truth."""
    sub = a.id or "list"
    cluster = HC.get(p, getattr(a, "cluster", None))
    if sub == "list":
        r = HD.list_datasets(p, cluster)
        if a.json:
            out_json(r)
            return 0
        print(bold(f"{r['shared_data']}  ({r['count']} dataset(s))"))
        for d in r["datasets"]:
            line = f"  {d['id'].ljust(26)} {st(d.get('status') or 'unknown')}"
            if d.get("version"):
                line += dim(f"  v{d['version']}")
            if not d.get("has_manifest"):
                line += yellow("  no manifest")
            print(line)
            if d.get("problem"):
                print(dim(f"      {d['problem']}"))
        print(dim("\n  " + r["note"]))
        return 0
    ident = a.dataset
    if not ident:
        raise ResearchError(f"`research hpc datasets {sub}` needs a dataset id",
                            hint="`research hpc datasets list` shows what is on the cluster.")
    if sub == "inspect":
        r = HD.inspect(p, cluster, ident, files=True)
        if a.json:
            out_json(r)
            return 0
        print(f"{bold(r['id'])}  {r['path']}")
        kv("status", r.get("status"))
        kv("version", r.get("version"))
        kv("source", r.get("source"))
        if r.get("problem"):
            print(yellow("  " + r["problem"]))
        for f_ in (r.get("files_listing") or [])[:20]:
            print(dim(f"    {f_}"))
        n = len(r.get("files_listing") or [])
        if n > 20:
            print(dim(f"    … {n - 20} more"))
        return 0
    if sub == "check":
        r = HD.check(p, cluster, ident, deep=a.deep)
        if a.json:
            out_json(r)
            return 0 if r["usable"] else 1
        print((green("✓ ") if r["usable"] else red("✗ "))
              + f"{r['id']}: {'usable' if r['usable'] else 'not usable'}  "
              + dim(f"({r['strength']} check, {r['checked']} file(s))"))
        for x in r["problems"]:
            print(red("  • " + x))
        for x in r["warnings"]:
            print(yellow("  • " + x))
        return 0 if r["usable"] else 1
    if sub == "fetch":
        r = HD.fetch(p, cluster, ident, url=a.url, from_local=a.from_local, approve=a.yes, deep=a.deep)
        if a.json:
            out_json(r)
            return 0
        mark = green("✓ ") if r["action"] in ("reuse", "staged") else yellow("⚠ ")
        print(mark + f"{r['id']}: {r['action']}")
        print(dim("  " + r["detail"]))
        return 0 if r["action"] in ("reuse", "staged") else 1
    if sub == "adopt":
        r = HD.adopt(p, cluster, ident, version=a.version, source=a.url, description=a.description,
                     dry_run=a.dry_run)
        if a.json:
            out_json(r)
            return 0
        print((dim("would write") if r["dry_run"] else green("✓ wrote"))
              + f" {r['path']}/manifest.json  "
              + dim(f"({len(r['manifest'].get('expected_files') or [])} file(s) recorded)"))
        return 0
    raise ResearchError(f"unknown datasets action {sub!r} (list | inspect | check | fetch | adopt)")


def _slurm_opts(a) -> Dict[str, Any]:
    opts = {k: getattr(a, k, None) for k in ("partition", "account", "time", "cpus", "mem", "gpus")}
    if getattr(a, "sbatch_arg", None):
        opts["extra_args"] = list(a.sbatch_arg)
    return {k: v for k, v in opts.items() if v not in (None, "", [])}


def cmd_report(a, p) -> int:
    if getattr(a, "full", False):
        return _cmd_report_full(a, p)
    from research import report as RP
    r = RP.write_report(p, a.scope, a.format, a.out)
    _done(a, r, msg=f"report: {', '.join(r['paths'])}")
    return 0


def _cmd_report_full(a, p) -> int:
    """Deterministic, exhaustive Markdown export. Read-only: writes nothing but the output file."""
    from research import fullreport as FR
    if a.stdout:
        snap, text = FR.export(p, a.scope, stamp=a.stamp)
        res = {"scope": snap["scope"], "counts": snap["counts"], "bytes": len(text.encode("utf-8"))}
        if a.check:
            res["check"] = FR.coverage(snap, text)
        if a.json:
            out_json({**res, "markdown": text})
        else:
            sys.stdout.write(text)
        return 0 if not (a.check and not res["check"]["ok"]) else 1
    r = FR.write(p, a.scope, a.out, stamp=a.stamp, check=a.check)
    chk = r.get("check")
    if a.json:
        out_json(r)
    else:
        print(green("✓ ") + f"full context export: {r['path']}  {dim(human_size(r['bytes']))}")
        for c in FR.CATEGORIES:
            print(f"  {dim((c + ':').ljust(14))} {r['counts'][c]}")
        if chk:
            if chk["ok"]:
                print(green("✓ ") + "coverage verified: every in-scope record is represented exactly once")
            else:
                print(red("✗ ") + f"coverage FAILED: {len(chk['exporter_problems'])} problem(s)")
                for pb in chk["exporter_problems"][:20]:
                    print(red("  • ") + pb["detail"])
            src = {k: v for k, v in chk["source_incompleteness"].items() if k != "detail" and v}
            if src:
                print(yellow("⚠ ") + "source-data incompleteness (not an exporter bug): "
                      + ", ".join(f"{k}={v}" for k, v in sorted(src.items())))
    return 0 if not (chk and not chk["ok"]) else 1


# ----------------------------------------------------------------------------- agents / dispatch / mcp
def cmd_agents(a, p) -> int:
    from research import agents as AG
    d = AG.list_profiles(p)
    if a.json:
        out_json(d)
        return 0
    section("Profiles")
    for x in d["profiles"]:
        print(f"  {bold(x['name'])}  {x['command']}" + (dim(f" · model {x['model']}") if x.get("model") else "")
              + ("" if x["available"] else red("  (not on PATH)")))
    section("Roles")
    for r, prof in d["roles"].items():
        print(f"  {r.ljust(12)} → {prof}")
    print(dim("\n  edit .research/config.yaml → agents to change"))
    return 0


def cmd_dispatch(a, p) -> int:
    from research import agents as AG
    target = getattr(a, "target", None)
    d = AG.dispatch(p, getattr(a, "role", None), target, getattr(a, "objective", None), a.profile, a.agent_model,
                    getattr(a, "instructions", None))
    if a.json:
        out_json(d)
        return 0
    print(green("✓ ") + f"{d['profile']}/{d['role']} brief → {d['brief']}", file=sys.stderr)
    if a.print or not d["available"]:
        if not d["available"]:
            print(yellow(f"⚠ `{d['argv'][0]}` is not on PATH; run this where it is:"), file=sys.stderr)
        print(d["shell"])
        return 0
    import subprocess
    root = p.root
    p.close()
    return subprocess.call(d["argv"], cwd=root, env={**os.environ, **d["env"]})


def cmd_bin(a, p) -> int:
    from research import localbin
    r = localbin.status(p.root) if a.action == "status" else localbin.install(p.root, force=a.force)
    if a.json:
        out_json(r)
    elif a.action == "status":
        print((green("✓ ") + r["path"]) if r["exists"] else f"not installed — run: research bin install")
    else:
        print(green("✓ ") + f"{os.path.relpath(r['path'], os.getcwd())} " + ("updated" if r["updated"] else "already up to date"))
        print(dim("  any agent or shell in this project can run it without installing anything"))
    return 0


def cmd_mcp(a, p) -> int:
    from research import mcp as MCP
    if a.action == "install":
        r = MCP.install(p.root, a.client)
        if a.json:
            out_json(r)
        else:
            for c, where in r["installed"].items():
                print(green("✓ ") + f"{c}: {where}")
            print(dim(f"  server command: {r['command']}"))
        return 0
    explicit = detect_author(a.agent, a.model, a.as_) if (a.agent or a.model or a.as_) else None
    root = p.root
    p.close()
    MCP.serve(root, author=explicit)
    return 0


# ----------------------------------------------------------------------------- skills management
@mut
def cmd_skills_add(a, p) -> int:
    from research import skills as SKL
    r = SKL.add_skills(p, a.source, a.only, a.force, True if a.always else None, a.applies_to, link=not a.no_link,
                       list_only=a.list)
    if a.json:
        out_json(r)
        return 0
    if a.list:
        for s_ in r["available"]:
            print(f"  {bold(s_['name'])}  {dim(s_['subpath'] or '.')}\n    {s_['description'][:160]}")
        print(dim(f"{len(r['available'])} skills; import some with --only NAME …"))
        return 0
    print(green("✓ ") + f"imported {', '.join(r['added'])}")
    for where, rep_ in (r.get("links") or {}).items():
        if rep_["skipped"]:
            print(yellow(f"  {where}: left alone (not ours): {', '.join(rep_['skipped'])}"))
    return 0


@mut
def cmd_skills_remove(a, p) -> int:
    from research import skills as SKL
    r = SKL.remove_skill(p, a.name)
    _done(a, r, msg=f"removed {r['removed']} (kept in {r['kept_at']})")
    return 0


@mut
def cmd_skills_update(a, p) -> int:
    from research import skills as SKL
    r = SKL.update_skills(p, a.names)
    _done(a, r, msg="updated " + (", ".join(r["updated"]) or "nothing"))
    return 0


@mut
def cmd_skills_set(a, p) -> int:
    from research import skills as SKL
    r = SKL.set_skill(p, a.name, a.always, a.applies_to)
    _done(a, r, msg=f"{r['name']}: always={r['always']}, applies_to={', '.join(r['applies_to']) or '—'}")
    return 0


def cmd_skills_link(a, p) -> int:
    from research import skills as SKL
    r = SKL.link_skills(p, a.target)
    if a.json:
        out_json(r)
        return 0
    for where, rep_ in r.items():
        print(green("✓ ") + f"{where}: {len(rep_['linked'])} linked"
              + (yellow(f", left alone: {', '.join(rep_['skipped'])}") if rep_["skipped"] else "")
              + (dim(f", removed stale: {', '.join(rep_['removed'])}") if rep_["removed"] else ""))
    return 0


def cmd_log(a, p) -> int:
    rows = p.q("SELECT * FROM events ORDER BY id DESC LIMIT ?", (a.limit,))
    if a.json:
        out_json(rows)
        return 0
    for e in reversed(rows):
        who = magenta(e["author_name"] or "agent") if e["author_type"] == "agent" else dim(e["author_type"] or "")
        print(f"{dim((e['ts'] or '')[:16].replace('T', ' '))}  {who:<10} {e['summary']}")
    return 0


# ----------------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    def _common(suppress: bool) -> argparse.ArgumentParser:
        c = argparse.ArgumentParser(add_help=False)
        kw = {"default": argparse.SUPPRESS} if suppress else {}
        c.add_argument("--json", action="store_true", help="machine-readable output", **kw)
        c.add_argument("--root", help="project root (default: search upwards from cwd, or $RESEARCH_ROOT)", **kw)
        c.add_argument("--agent", help="act as an agent with this name (or set RESEARCH_AGENT)", **kw)
        c.add_argument("--model", help="agent model name (or RESEARCH_AGENT_MODEL)", **kw)
        c.add_argument("--as", dest="as_", choices=["human", "agent", "system"], help="author type override", **kw)
        return c

    common = _common(True)
    ap = argparse.ArgumentParser(prog="research", parents=[_common(False)],
                                 description="Project-local research memory: questions → experiments → runs → findings → decisions → checkpoints.",
                                 epilog="Exit codes: 0 ok, 1 error, 2 not initialized, 3 blocked by run budget/policy, 4 not found.")
    ap.add_argument("--version", action="version", version=f"research {research.__version__}")
    sub = ap.add_subparsers(dest="cmd", metavar="<command>")

    def add(parent, name, fn, help_, aliases=()):
        sp = parent.add_parser(name, help=help_, parents=[common], aliases=list(aliases))
        sp.set_defaults(fn=fn)
        return sp

    def group(name, help_, aliases=()):
        g = sub.add_parser(name, help=help_, parents=[common], aliases=list(aliases))
        gs = g.add_subparsers(dest="sub", metavar="<action>")
        g.set_defaults(fn=None, _group=g)
        return gs

    x = add(sub, "init", cmd_init, "initialize .research/ in a project (and AGENTS.md)")
    x.add_argument("path", nargs="?")
    x.add_argument("--name")
    x.add_argument("--goal")
    x.add_argument("--description")
    x.add_argument("--no-agents-md", action="store_true")

    add(sub, "status", cmd_status, "compact project status")
    add(sub, "resume", cmd_resume, "what were we doing? (latest checkpoint + current state)")
    x = add(sub, "context", cmd_context, "regenerate .research/context/current.md")
    x.add_argument("--print", action="store_true")
    add(sub, "current", cmd_current, "print the current research state for agents (regenerates current.md)")
    x = add(sub, "search", cmd_search, "search everything recorded: research search \"dataset prep\"")
    x.add_argument("query", nargs="+")
    x.add_argument("--type", action="append", help="limit to a type (finding, experiment, task, run, note, skill, …)")
    x.add_argument("--limit", type=int, default=20)
    x = add(sub, "report", cmd_report, "write a readable report (project, or one Q-/PLAN-/EXP-) to .research/reports/")
    x.add_argument("scope", nargs="?")
    x.add_argument("--format", choices=["md", "html", "both"], default="both")
    x.add_argument("-o", "--out", help="output file (with --format md or html, or --full)")
    x.add_argument("--full", action="store_true",
                   help="exhaustive deterministic Markdown export of everything recorded (for an LLM hand-off)")
    x.add_argument("--check", action="store_true", help="with --full: verify coverage; nonzero exit if it fails")
    x.add_argument("--stdout", action="store_true", help="with --full: write to stdout instead of a file")
    x.add_argument("--stamp", action="store_true",
                   help="with --full: add a generation timestamp (output is then NOT byte-reproducible)")
    x = add(sub, "dispatch", cmd_dispatch, "hand work to an agent CLI: research dispatch verifier F-003")
    x.add_argument("role", choices=["planner", "implementer", "verifier", "analyst"])
    x.add_argument("target", nargs="?", help="T-, F-, EXP-, PLAN- … id")
    _dispatch_args(x)
    add(sub, "agents", cmd_agents, "agent profiles and roles (config.yaml → agents)")
    x = add(sub, "bin", cmd_bin, "project-local CLI at .research/bin/research (no install needed for agents)")
    x.add_argument("action", nargs="?", choices=["install", "status"], default="install")
    x.add_argument("--force", action="store_true", help="re-copy even if up to date")
    x = add(sub, "mcp", cmd_mcp, "MCP server for agents (stdio); `research mcp install` registers it")
    x.add_argument("action", nargs="?", choices=["serve", "install"], default="serve")
    x.add_argument("--client", action="append", choices=["claude", "vscode", "cursor", "gemini", "codex"],
                   help="with install: which clients (default: claude + vscode)")
    x = add(sub, "show", cmd_show, "show any object by id (Q-…, EXP-…, RUN-…, F-…, D-…, CP-…, A-…, note:…)")
    x.add_argument("id")
    x = add(sub, "mark", cmd_mark, "set status of any object: research mark Q-001 answered")
    x.add_argument("id")
    x.add_argument("status")
    x.add_argument("--reason")
    add(sub, "sync", cmd_sync, "import hand-edited files, ingest job outputs, refresh run status")
    add(sub, "rebuild", cmd_rebuild, "rebuild research.db from the text files (keeps a backup)")
    x = add(sub, "config", cmd_config, "configuration: show [--effective|--sources] | check | migrate")
    x.add_argument("action", nargs="?", choices=["show", "check", "migrate"], default="show")
    x.add_argument("--effective", action="store_true", help="show: every value in force, not just overrides")
    x.add_argument("--sources", action="store_true", help="show: which layer supplies each value")
    x.add_argument("--apply", action="store_true", help="migrate: write the file (default is a dry run)")
    x = add(sub, "log", cmd_log, "audit log of recent changes")
    x.add_argument("--limit", type=int, default=30)
    x = add(sub, "agents-md", cmd_agents_md, "create/refresh the research block in AGENTS.md")
    x.add_argument("--preview", action="store_true", help="print the managed block this version generates")
    x.add_argument("--diff", action="store_true", help="unified diff of the current vs. the new block")
    x.add_argument("--apply", action="store_true", help="write the managed region (the default action)")
    x = add(sub, "hpc", cmd_hpc, "remote execution: doctor | init | submit | status | sync | collect | "
                                 "import | bundle | datasets | inspect | cancel | clusters | config")
    x.add_argument("action", nargs="?",
                   choices=["doctor", "init", "config", "clusters", "inspect", "bundle", "submit",
                            "status", "sync", "collect", "import", "cancel", "datasets"])
    x.add_argument("id", nargs="?", help="EXP-… (bundle/submit) · RUN-… (status/collect/cancel) · "
                                         "archive path (import) · sub-action (datasets)")
    x.add_argument("dataset", nargs="?", help="dataset id, for `datasets inspect|check|fetch|adopt`")
    x.add_argument("--cluster", help="cluster name from hpc.clusters (default: hpc.default_cluster)")
    x.add_argument("--dry-run", action="store_true", help="preview only; transfers and submits nothing")
    x.add_argument("--yes", action="store_true", help="approve an expensive action (submit, dataset staging)")
    x.add_argument("--format", choices=["tar.gz", "zip"], default="tar.gz", help="bundle archive format")
    x.add_argument("--project", help="inspect another project slug on the same cluster")
    x.add_argument("--no-collect", action="store_true", help="sync: update status only, fetch no results")
    x.add_argument("--max-bytes", type=int, help="collect: artifacts larger than this stay on the cluster")
    x.add_argument("--stage-datasets", action="store_true",
                   help="submit: proceed to staging if a required dataset is missing")
    x.add_argument("--deep", action="store_true", help="verify dataset checksums (can be expensive)")
    x.add_argument("--url", help="datasets fetch/adopt: the source URL")
    x.add_argument("--from-local", help="datasets fetch: upload this local path instead of downloading")
    x.add_argument("--dataset-version", dest="version",
                   help="datasets adopt: the dataset version you verified")
    x.add_argument("--description", help="datasets adopt: what this dataset is")
    x.add_argument("--example", action="store_true", help="config: print an example cluster profile")
    x.add_argument("--partition", "-p")
    x.add_argument("--account", "-A")
    x.add_argument("--time", "-t")
    x.add_argument("--cpus", "-c", type=int)
    x.add_argument("--mem")
    x.add_argument("--gpus", "-G")
    x.add_argument("--sbatch-arg", action="append", help="extra #SBATCH line")
    x.add_argument("cmd", nargs="*", help="command to run; put it after `--`")
    x.set_defaults(_hpc_parser=x)

    x = add(sub, "upgrade", cmd_upgrade, "bring an existing project up to this Research OS (preview by default)")
    x.add_argument("--dry-run", action="store_true", help="preview only (the default); changes nothing")
    x.add_argument("--apply", action="store_true", help="apply the upgrade")
    x.add_argument("--status", action="store_true", help="version/compatibility summary only")

    x = add(sub, "guide", cmd_guide, "Scientist Guide: init | status | refresh [--run] | done [SYN-ID…]")
    x.add_argument("action", nargs="?", default="status", choices=["init", "status", "refresh", "done"])
    x.add_argument("ids", nargs="*", help="synthesis ids for `done` (default: all pending)")
    x.add_argument("--run", action="store_true", help="refresh: start the agent now instead of printing the command")
    x.add_argument("--profile")

    x = add(sub, "setup", cmd_setup, "optional setup: `setup agents --provider claude --preset scientific [--apply]`")
    x.add_argument("what", choices=["agents"])
    x.add_argument("--provider", default="claude")
    x.add_argument("--preset", default="scientific")
    x.add_argument("--apply", action="store_true")
    x.add_argument("--dry-run", action="store_true")
    x.add_argument("--approve", nargs="+", choices=["ponytail"], help="approve third-party skill imports")
    x.add_argument("--probe", action="store_true", help="also make one tiny live call per model")

    x = add(sub, "orchestrate", cmd_orchestrate,
            "route | dispatch | freeze | attempt | correct-test | delegate | status for tiered (Opus/Sonnet/Haiku) work")
    x.add_argument("action", choices=["route", "dispatch", "freeze", "attempt", "correct-test", "delegate", "status"])
    x.add_argument("task", help="task id (parent task for delegate)")
    x.add_argument("rest", nargs="*", help="freeze: test files · correct-test: file · delegate: title")
    x.add_argument("--tier", choices=["haiku", "sonnet", "opus"])
    x.add_argument("--available", nargs="*", help="models known to be available (default: config)")
    x.add_argument("--allow-unfrozen", metavar="REASON")
    x.add_argument("--run", action="store_true", help="dispatch: start the agent now")
    x.add_argument("--outcome", choices=["pass", "fail"])
    x.add_argument("--category")
    x.add_argument("--note")
    x.add_argument("--reason")
    x.add_argument("--goal")
    x.add_argument("--acceptance")
    x.add_argument("--check", action="append")

    x = add(sub, "tuning", cmd_tuning, "declare | trial | select | test (held-out, once)")
    x.add_argument("action", choices=["declare", "trial", "select", "test"])
    x.add_argument("exp")
    x.add_argument("--objective")
    x.add_argument("--range", action="append", metavar="NAME=LO:HI")
    x.add_argument("--strategy")
    x.add_argument("--selection")
    x.add_argument("--seed", type=int, default=0)
    x.add_argument("--budget", type=int)
    x.add_argument("--config", help="JSON parameter configuration")
    x.add_argument("--split", choices=["design", "validation", "test"])
    x.add_argument("--value", type=float)
    x.add_argument("--run")
    x.add_argument("--rationale")

    x = add(sub, "evidence", cmd_evidence, "numerical evidence: bind | verify | value | suggest | provenance | artifact | pin | status")
    x.add_argument("action", choices=["bind", "verify", "value", "suggest", "provenance", "artifact", "pin", "status"])
    x.add_argument("id", nargs="?", help="F-… (bind/verify/suggest) · RUN-… (value/provenance) · A-… (artifact/pin)")
    x.add_argument("--run")
    x.add_argument("--metric")
    x.add_argument("--step", type=int)
    x.add_argument("--select", choices=["first", "last", "min", "max"])
    x.add_argument("--claim", help="the number as stated; omit to READ it from the evidence")
    x.add_argument("--unit")
    x.add_argument("--factor", type=float, help="declared unit conversion applied to the recorded value")
    x.add_argument("--rel-tol", type=float, help="exact comparison instead of rounding to the claim's decimals")
    x.add_argument("--derive", choices=["mean", "sum", "min", "max", "std", "difference", "abs_difference", "ratio",
                                        "relative_improvement", "relative_change"])
    x.add_argument("--input", action="append", metavar="RUN-…:metric[@step]")
    x.add_argument("--artifact")
    x.add_argument("--partition", choices=["design", "validation", "test"])
    x.add_argument("--note")
    x.add_argument("--supersedes", metavar="F-…/B1")
    x.add_argument("--deep", action="store_true", help="artifact: also compute the (streamed) hash")

    x = add(sub, "source", cmd_source, "literature sources: add | mark | list (offline; identity, examination and support are separate)")
    x.add_argument("action", choices=["add", "mark", "list"])
    x.add_argument("id", nargs="?")
    x.add_argument("--doi")
    x.add_argument("--arxiv")
    x.add_argument("--url")
    x.add_argument("--title")
    x.add_argument("--identity-verified", action="store_true", help="I checked that this identifier is the intended paper")
    x.add_argument("--examined", action="store_true", help="I read the relevant part")
    x.add_argument("--locator", help="section / page / figure")
    x.add_argument("--finding")
    x.add_argument("--verdict", choices=["supports", "partially_supports", "does_not_support"])
    x.add_argument("--note")

    g = group("project", "project metadata")
    x = add(g, "set", cmd_project_set, "set name/goal/description")
    x.add_argument("--name")
    x.add_argument("--goal")
    x.add_argument("--description")
    x.add_argument("--status")

    # questions
    g = group("question", "research questions", aliases=["q"])
    x = add(g, "create", cmd_question_create, "create a question", aliases=["new"])
    x.add_argument("title")
    x.add_argument("-d", "--description")
    x.add_argument("--parent")
    x.add_argument("--status", default="open", choices=STATUSES["question"])
    x.add_argument("--tags", nargs="*")
    x = add(g, "list", cmd_question_list, "list questions", aliases=["ls"])
    x.add_argument("--status", choices=STATUSES["question"])
    x = add(g, "update", cmd_question_update, "edit a question")
    x.add_argument("id")
    x.add_argument("--title")
    x.add_argument("-d", "--description")
    x.add_argument("--status", choices=STATUSES["question"])
    x.add_argument("--parent")
    x.add_argument("--tags", nargs="*")
    x = add(g, "show", cmd_show, "show a question")
    x.add_argument("id")

    # experiments
    def exp_fields(x, create=True):
        x.add_argument("--question", "-q")
        x.add_argument("--hypothesis")
        x.add_argument("--motivation", help="why this experiment (reason)")
        x.add_argument("--method")
        x.add_argument("--expected", help="expected outcome")
        x.add_argument("--success", help="success / evaluation criteria")
        x.add_argument("--stop", help="stop conditions")
        x.add_argument("--param", action="append", metavar="K=V", help="parameter (repeatable); lists allowed: alpha=[0.1,0.5]")
        x.add_argument("--metric", action="append", help="metric to evaluate (repeatable)")
        x.add_argument("--expect-artifact", action="append", help="expected artifact (repeatable)")
        x.add_argument("--planned-runs", type=int)
        x.add_argument("--tags", nargs="*")
        x.add_argument("--notes")

    g = group("experiment", "experiments (designed tests; own their runs)", aliases=["exp", "e"])
    x = add(g, "create", cmd_experiment_create, "register an experiment BEFORE running it", aliases=["new"])
    x.add_argument("title")
    exp_fields(x)
    x.add_argument("--parent")
    x.add_argument("--status", default="proposed", choices=STATUSES["experiment"])
    x.add_argument("--override", metavar="REASON", help="bypass synthesis-before-new-experiment (logged)")
    x = add(g, "list", cmd_experiment_list, "list experiments", aliases=["ls"])
    x.add_argument("--status", choices=STATUSES["experiment"])
    x.add_argument("--question", "-q")
    x = add(g, "show", cmd_experiment_show, "experiment detail: runs, metrics, artifacts, synthesis")
    x.add_argument("id")
    x = add(g, "update", cmd_experiment_update, "edit experiment fields")
    x.add_argument("id")
    x.add_argument("--title")
    x.add_argument("--limitations")
    exp_fields(x, False)
    x = add(g, "variant", cmd_experiment_variant, "derive a new experiment with changed parameters", aliases=["duplicate"])
    x.add_argument("id")
    x.add_argument("--param", action="append", metavar="K=V")
    x.add_argument("--title")
    x.add_argument("--motivation")
    x.add_argument("--hypothesis")
    x.add_argument("--override", metavar="REASON")
    for name, status in (("start", "running"), ("ready", "ready"), ("review", "needs_review"),
                         ("complete", "completed"), ("fail", "failed"), ("abandon", "abandoned")):
        x = add(g, name, cmd_experiment_status, f"mark experiment {status}")
        x.add_argument("id")
        x.add_argument("--reason")
        x.set_defaults(to_status=status)
    x = add(g, "baseline", cmd_experiment_baseline, "mark as the current baseline")
    x.add_argument("id", nargs="?")
    x.add_argument("--unset", action="store_true")
    x = add(g, "synthesize", cmd_experiment_synthesize, "record a synthesis of the experiment's runs", aliases=["synth"])
    x.add_argument("id")
    x.add_argument("--happened", help="what happened")
    x.add_argument("--worked", help="what worked")
    x.add_argument("--failed", help="what did not work")
    x.add_argument("--interpretation")
    x.add_argument("--limitations")
    x.add_argument("--unresolved")
    x.add_argument("--next", help="recommended next experiment")
    x.add_argument("--complete", action="store_true", help="also mark the experiment completed")
    x.add_argument("--fail", action="store_true", help="also mark the experiment failed")
    x = add(g, "run", cmd_run_exec, "execute a run of this experiment (same as `run exec`)")
    _run_args(x)
    x.add_argument("--detach", action="store_true")

    # runs
    g = group("run", "runs (single executions; always belong to an experiment)", aliases=["r"])
    x = add(g, "exec", cmd_run_exec, "run a command locally, tracked: research run exec EXP-001 -- python x.py")
    _run_args(x)
    x.add_argument("--detach", action="store_true", help="run in background (survives the terminal)")
    x = add(g, "submit", cmd_run_submit, "submit via SLURM: research run submit EXP-001 --time 1:00:00 -- python x.py")
    _run_args(x)
    x.add_argument("--dry-run", action="store_true",
                   help="print the resources this would reserve and submit nothing")
    x.add_argument("--partition", "-p")
    x.add_argument("--account", "-A")
    x.add_argument("--time", "-t")
    x.add_argument("--cpus", "-c", type=int)
    x.add_argument("--mem")
    x.add_argument("--gpus", "-G")
    x.add_argument("--sbatch-arg", action="append", help="extra #SBATCH line, e.g. --sbatch-arg='--constraint=a100'")
    x = add(g, "attach", cmd_run_attach, "register a run you already executed yourself", aliases=["add"])
    _run_args(x)
    x.add_argument("--command")
    x.add_argument("--status", default="completed", choices=STATUSES["run"])
    x.add_argument("--exit-code", type=int)
    x.add_argument("--stdout")
    x.add_argument("--stderr")
    x.add_argument("--started")
    x.add_argument("--ended")
    x.add_argument("--slurm-job")
    x = add(g, "list", cmd_run_list, "list runs", aliases=["ls"])
    x.add_argument("--experiment", "-e")
    x.add_argument("--status", choices=STATUSES["run"])
    x.add_argument("--limit", type=int)
    x = add(g, "status", cmd_run_show, "show run status (refreshes from backend)", aliases=["show"])
    x.add_argument("id")
    x.add_argument("--refresh", action="store_true")
    x = add(g, "update", cmd_run_update, "update a run (status, exit code, notes, reviewed)")
    x.add_argument("id")
    x.add_argument("--status", choices=STATUSES["run"])
    x.add_argument("--exit-code", type=int)
    x.add_argument("--notes")
    x.add_argument("--label")
    x.add_argument("--param", action="append", metavar="K=V")
    x.add_argument("--reviewed", action="store_true", help="mark a failed run as reviewed")
    x = add(g, "cancel", cmd_run_cancel, "cancel a running/queued run")
    x.add_argument("id")
    x = add(g, "logs", cmd_run_logs, "print run logs")
    x.add_argument("id")
    x.add_argument("--stderr", action="store_true")
    x.add_argument("--tail", type=int)
    x.add_argument("-f", "--follow", action="store_true")
    add(g, "sync", cmd_sync, "refresh status of all active runs")
    x = add(g, "compare", cmd_run_compare, "side by side: parameters, metrics, code, artifacts of two runs", aliases=["diff"])
    x.add_argument("a")
    x.add_argument("b")
    x = add(g, "sweep", cmd_run_sweep, "one run per grid point: research run sweep EXP-1 --grid lr=1e-3,1e-4 -- python t.py --lr {lr}")
    _run_args(x)
    x.add_argument("--grid", action="append", metavar="NAME=V1,V2", help="default: the experiment's list parameters")
    x.add_argument("--mode", choices=["local", "detach", "slurm"], default="local")
    x.add_argument("--dry-run", action="store_true", help="show the runs without starting them")
    x.add_argument("--partition", "-p")
    x.add_argument("--account", "-A")
    x.add_argument("--time", "-t")
    x.add_argument("--cpus", "-c", type=int)
    x.add_argument("--mem")
    x.add_argument("--gpus", "-G")

    g = group("metric", "metrics", aliases=["m"])
    x = add(g, "add", cmd_metric_add, "log a metric value", aliases=["log"])
    x.add_argument("name")
    x.add_argument("value")
    x.add_argument("--run")
    x.add_argument("--experiment", "-e")
    x.add_argument("--step", type=int)
    x.add_argument("--unit")

    g = group("artifact", "artifacts (referenced by path, never copied)", aliases=["a"])
    x = add(g, "add", cmd_artifact_add, "register one or more files/dirs", aliases=["register"])
    x.add_argument("paths", nargs="+")
    x.add_argument("--run")
    x.add_argument("--experiment", "-e")
    x.add_argument("--name")
    x.add_argument("--type")
    x.add_argument("-d", "--description")
    x.add_argument("--tags", nargs="*")
    x.add_argument("--hash", action="store_true", help="hash even large files")
    x.add_argument("--allow-missing", action="store_true")
    x = add(g, "list", cmd_artifact_list, "list artifacts", aliases=["ls"])
    x.add_argument("--run")
    x.add_argument("--experiment", "-e")

    g = group("finding", "findings (interpreted, reusable results)", aliases=["f"])
    x = add(g, "create", cmd_finding_create, "record a finding with evidence", aliases=["new"])
    x.add_argument("title")
    _finding_args(x)
    x.set_defaults(kind="result", status="preliminary")
    x = add(g, "list", cmd_finding_list, "list findings", aliases=["ls"])
    x.add_argument("--status", choices=STATUSES["finding"])
    x.add_argument("--kind", choices=FINDING_KINDS)
    x = add(g, "update", cmd_finding_update, "edit a finding / its evidence")
    x.add_argument("id")
    x.add_argument("--title")
    _finding_args(x)
    x.add_argument("--add-supports", nargs="+")
    x = add(g, "review", cmd_finding_review, "independent review: --verdict supported|contradicted|needs_work")
    x.add_argument("id")
    x.add_argument("--verdict", required=True, choices=["supported", "contradicted", "needs_work"])
    x.add_argument("--notes", help="your reasoning")
    x.add_argument("--confidence", choices=CONFIDENCE)
    x = add(g, "supersede", cmd_finding_supersede, "mark superseded (optionally by a newer finding)")
    x.add_argument("id")
    x.add_argument("--by")
    x.add_argument("--reason")
    x = add(g, "show", cmd_show, "show a finding")
    x.add_argument("id")

    g = group("decision", "decisions (deliberate choices based on findings)", aliases=["d"])
    x = add(g, "create", cmd_decision_create, "record a decision", aliases=["new"])
    x.add_argument("statement")
    x.add_argument("--reason")
    x.add_argument("--title")
    x.add_argument("--findings", nargs="+")
    x.add_argument("--experiments", nargs="+")
    x.add_argument("--date")
    x.add_argument("--tags", nargs="*")
    x.add_argument("--supersedes")
    x = add(g, "list", cmd_decision_list, "list decisions", aliases=["ls"])
    x.add_argument("--status", choices=STATUSES["decision"])
    x = add(g, "update", cmd_decision_update, "edit a decision")
    x.add_argument("id")
    x.add_argument("--statement")
    x.add_argument("--reason")
    x.add_argument("--status", choices=STATUSES["decision"])
    x.add_argument("--findings", nargs="+")
    x.add_argument("--experiments", nargs="+")

    g = group("checkpoint", "checkpoints (snapshots of project understanding)", aliases=["cp"])
    x = add(g, "create", cmd_checkpoint_create, "create a checkpoint (pre-filled from current state)", aliases=["new"])
    _checkpoint_args(x)
    add(g, "list", cmd_checkpoint_list, "list checkpoints", aliases=["ls"])
    add(g, "draft", cmd_checkpoint_draft, "show the auto-generated draft without saving")
    x = add(g, "show", cmd_checkpoint_show, "show a checkpoint (default: latest)")
    x.add_argument("id", nargs="?")
    g._checkpoint_default = True  # `research checkpoint` alone creates one

    g = group("note", "your project notes (.research/notes/)", aliases=["notes"])
    x = add(g, "new", cmd_note_new, "create a note", aliases=["create"])
    x.add_argument("title")
    x.add_argument("--body")
    x.add_argument("--link", nargs="+")
    x.add_argument("--pin", action="store_true")
    add(g, "list", cmd_note_list, "list notes", aliases=["ls"])
    x = add(g, "pin", cmd_note_pin, "pin/unpin a note")
    x.add_argument("id")
    x.add_argument("--unpin", action="store_true")
    x = add(g, "link", cmd_note_link, "link a note to questions/experiments/findings")
    x.add_argument("id")
    x.add_argument("targets", nargs="+")
    x.add_argument("--remove", action="store_true")

    g = group("skills", "agent skills (.research/skills/)", aliases=["skill"])
    add(g, "list", cmd_skills_list, "list skills", aliases=["ls"])
    x = add(g, "show", cmd_skills_show, "print a skill's SKILL.md")
    x.add_argument("name")
    x = add(g, "add", cmd_skills_add, "import skill(s) from a folder or git repo (URL, owner/repo, …/tree/main/skills/x)",
            aliases=["import"])
    x.add_argument("source")
    x.add_argument("--only", nargs="+", metavar="NAME", help="import only these skills from a multi-skill repo")
    x.add_argument("--list", action="store_true", help="show the skills in the source without importing")
    x.add_argument("--force", action="store_true", help="replace existing skills (old copy kept in cache)")
    x.add_argument("--always", action="store_true", help="always-on: listed for every agent session")
    x.add_argument("--applies-to", nargs="+", metavar="TYPE", help="task types/roles it is suggested for")
    x.add_argument("--no-link", action="store_true", help="don't expose it in .claude/skills / .agents/skills")
    x = add(g, "remove", cmd_skills_remove, "remove a skill (moved to .research/cache/removed-skills)", aliases=["rm"])
    x.add_argument("name")
    x = add(g, "update", cmd_skills_update, "re-import skills from their recorded source")
    x.add_argument("names", nargs="*")
    x = add(g, "set", cmd_skills_set, "project settings for a skill")
    x.add_argument("name")
    x.add_argument("--always", dest="always", action="store_true", default=None)
    x.add_argument("--no-always", dest="always", action="store_false")
    x.add_argument("--applies-to", nargs="*", metavar="TYPE")
    x = add(g, "link", cmd_skills_link, "(re)create .claude/skills and .agents/skills links")
    x.add_argument("--target", action="append", help="claude, agents, or a folder")
    x = add(g, "new", cmd_skills_new, "create a skill", aliases=["create"])
    x.add_argument("name")
    x.add_argument("-d", "--description")
    x = add(g, "duplicate", cmd_skills_duplicate, "copy a skill")
    x.add_argument("src")
    x.add_argument("name")

    # Phase 0: Plans
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

    # Phase 0: Tasks
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

    return ap


def _run_args(x) -> None:
    x.add_argument("experiment")
    x.add_argument("--param", action="append", metavar="K=V", help="run parameters (repeatable)")
    x.add_argument("--label")
    x.add_argument("--cwd", help="working directory (default: current)")
    x.add_argument("--notes")
    x.add_argument("--override", metavar="REASON", help="bypass run budget (logged)")
    x.add_argument("cmd", nargs="*", help="command to run; put it after `--` if it has its own flags")


def _dispatch_args(x) -> None:
    x.add_argument("--objective", help="for the planner: what the plan should achieve")
    x.add_argument("--profile", help="agent profile (default from config roles)")
    x.add_argument("--agent-model", help="override the profile's model for this session")
    x.add_argument("--instructions", help="extra instructions added to the brief")
    x.add_argument("--print", action="store_true", help="print the command instead of starting the agent")


def _finding_args(x) -> None:
    x.add_argument("--statement", "-s")
    x.add_argument("--kind", choices=FINDING_KINDS)
    x.add_argument("--status", choices=STATUSES["finding"])
    x.add_argument("--confidence", choices=CONFIDENCE)
    x.add_argument("--supports", nargs="+", help="evidence ids (EXP-/RUN-/A-/F-)")
    x.add_argument("--contradicts", nargs="+")
    x.add_argument("--related", nargs="+")
    x.add_argument("--questions", nargs="+")
    x.add_argument("--limitations")
    x.add_argument("--contradicting", help="contradicting evidence (text)")
    x.add_argument("--tags", nargs="*")


def _checkpoint_args(x) -> None:
    x.add_argument("--title")
    x.add_argument("--goal")
    x.add_argument("--understanding")
    x.add_argument("--baseline")
    x.add_argument("--baseline-experiment")
    x.add_argument("--findings", nargs="*")
    x.add_argument("--failures", nargs="*")
    x.add_argument("--questions", nargs="*")
    x.add_argument("--experiments", nargs="*")
    x.add_argument("--problem", help="current problems / blockers")
    x.add_argument("--next", help="next likely experiment")
    x.add_argument("--notes")
    x.add_argument("--no-draft", action="store_true", help="don't pre-fill from current state")
    x.add_argument("--commit", action="store_true", help="also commit .research/ to git (only .research/, never pushed)")


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # `research checkpoint` with no action (or only flags) → create
    if argv and argv[0] in ("checkpoint", "cp") and (len(argv) == 1 or argv[1].startswith("-")) \
            and not any(x in ("-h", "--help") for x in argv[1:]):
        argv.insert(1, "create")
    tail: Optional[List[str]] = None
    if "--" in argv:
        i = argv.index("--")
        argv, tail = argv[:i], argv[i + 1:]
    ap = build_parser()
    a = ap.parse_args(argv)
    if tail is not None:
        if hasattr(a, "cmd") and isinstance(getattr(a, "cmd", None), list) or getattr(a, "fn", None) in (
                cmd_run_exec, cmd_run_submit, cmd_run_attach, cmd_run_sweep, cmd_hpc):
            a.cmd = list(getattr(a, "cmd", None) or []) + tail
        else:
            ap.error("unexpected arguments after --")
    # A subcommand was chosen iff the parser set `fn` or `_group`. (`a.cmd` cannot be used for this:
    # the run/hpc subparsers define their own `cmd` positional, which shadows the top-level dest.)
    if not hasattr(a, "fn") and not hasattr(a, "_group"):
        ap.print_help()
        return 0
    fn = getattr(a, "fn", None)
    if fn is None:
        a._group.print_help()
        return 0
    try:
        if fn is cmd_init:
            return cmd_init(a)
        if fn is cmd_upgrade:
            return cmd_upgrade(a)   # must not open the project: --dry-run would migrate the database
        p = _proj(a, sync=not _is_readonly(a))
        try:
            rc = fn(a, p)
            if fn.__name__ in MUTATING:
                try:
                    C.write_current_md(p)
                except Exception:
                    pass
            return rc or 0
        finally:
            p.close()
    except ResearchError as e:
        if getattr(a, "json", False):
            print(json.dumps({"error": str(e), "hint": e.hint, "code": e.exit_code}), file=sys.stderr)
        else:
            print(red("✗ ") + str(e), file=sys.stderr)
            if e.hint:
                print(dim("  " + e.hint), file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
