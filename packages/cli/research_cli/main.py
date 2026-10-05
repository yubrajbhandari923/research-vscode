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
from research import runs as R
from research import services as S
from research import views as V
from research.schema import CONFIDENCE, FINDING_KINDS, STATUSES, normalize_id
from research.store import Project
from research.util import ResearchError, detect_author, human_size, parse_kv

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
    print(f"  {dim(label.ljust(width))}{lines[0]}")
    for l in lines[1:]:
        print(" " * (width + 2) + l)


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


# ----------------------------------------------------------------------------- command impls

def _proj(a) -> Project:
    auth = detect_author(a.agent, a.model, a.as_) if (a.agent or a.model or a.as_) else None
    if a.root:
        return Project(a.root, author=auth)
    return Project.find(author=auth)


MUTATING = set()


def mut(fn):
    MUTATING.add(fn.__name__)
    return fn


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
    opts = dict(partition=a.partition, account=a.account, time=a.time, cpus=a.cpus, mem=a.mem, gpus=a.gpus,
                extra_args=a.sbatch_arg or None)
    r = R.submit_run(p, a.experiment, _cmd_list(a), parameters=parse_kv(a.param), label=a.label,
                     working_dir=a.cwd, notes=a.notes, override=a.override, **opts)
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
                            next_experiment=a.next, notes=a.notes, use_draft=not a.no_draft)
    _done(a, c, msg=f"{c['id']}  {c['title']}  → {c['path']}")
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
    rows = S.list_skills(p)
    if a.json:
        out_json(rows)
    else:
        for s in rows:
            print(f"{bold(s['name'])}  {dim(s['path'])}\n    {s['description']}")
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
    path = p.write_agents_md()
    _done(a, {"path": path}, msg=f"{os.path.relpath(path)} updated (research block only)")
    return 0


@mut
def cmd_rebuild(a, p) -> int:
    counts = p.rebuild_index(backup=True)
    _done(a, counts, msg="index rebuilt from text files: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
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
    x = add(sub, "show", cmd_show, "show any object by id (Q-…, EXP-…, RUN-…, F-…, D-…, CP-…, A-…, note:…)")
    x.add_argument("id")
    x = add(sub, "mark", cmd_mark, "set status of any object: research mark Q-001 answered")
    x.add_argument("id")
    x.add_argument("status")
    x.add_argument("--reason")
    add(sub, "sync", cmd_sync, "import hand-edited files, ingest job outputs, refresh run status")
    add(sub, "rebuild", cmd_rebuild, "rebuild research.db from the text files (keeps a backup)")
    x = add(sub, "log", cmd_log, "audit log of recent changes")
    x.add_argument("--limit", type=int, default=30)
    add(sub, "agents-md", cmd_agents_md, "create/refresh the research block in AGENTS.md")

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
    x = add(g, "new", cmd_skills_new, "create a skill", aliases=["create"])
    x.add_argument("name")
    x.add_argument("-d", "--description")
    x = add(g, "duplicate", cmd_skills_duplicate, "copy a skill")
    x.add_argument("src")
    x.add_argument("name")
    return ap


def _run_args(x) -> None:
    x.add_argument("experiment")
    x.add_argument("--param", action="append", metavar="K=V", help="run parameters (repeatable)")
    x.add_argument("--label")
    x.add_argument("--cwd", help="working directory (default: current)")
    x.add_argument("--notes")
    x.add_argument("--override", metavar="REASON", help="bypass run budget (logged)")
    x.add_argument("cmd", nargs="*", help="command to run; put it after `--` if it has its own flags")


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
                cmd_run_exec, cmd_run_submit, cmd_run_attach):
            a.cmd = list(getattr(a, "cmd", None) or []) + tail
        else:
            ap.error("unexpected arguments after --")
    if not getattr(a, "cmd", None):
        ap.print_help()
        return 0
    fn = getattr(a, "fn", None)
    if fn is None:
        a._group.print_help()
        return 0
    try:
        if fn is cmd_init:
            return cmd_init(a)
        p = _proj(a)
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
