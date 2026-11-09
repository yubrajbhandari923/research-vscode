"""Research records: questions, experiments, runs, metrics, artifacts, findings, decisions, checkpoints, notes, evidence, sources."""
from __future__ import annotations

import json
import os
import sys
import time
from typing import List

from research import context as C
from research import runs as R
from research import services as S
from research import views as V
from research.schema import CONFIDENCE, FINDING_KINDS, STATUSES, normalize_id
from research.util import ResearchError, human_size, parse_kv

from . import mut
from ..output import _done, bold, dim, fmt_dur, fmt_val, green, kv, out_json, print_experiment, print_generic, print_list, red, section, st, yellow
from .project import cmd_show, cmd_sync


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


@mut
def cmd_experiment_create(a, p) -> int:
    from research import governance
    governance.confirm_human_experiment(p)
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
def cmd_experiment_fork(a, p) -> int:
    from research import fork
    r = fork.fork(p, a.id, a.title, at=a.at, override=a.override)
    _done(a, r, msg=f"{r['experiment']}  forked from {r['parent']} at {r['base_rev'][:8]} ({r['revision']})  worktree {r['workspace']} on {r['branch']}")
    if not a.json:
        print(dim("  pinned: " + r["pin"] + " (main is not followed; `research experiment sync` brings code in when you ask)"))
    return 0


@mut
def cmd_experiment_sync(a, p) -> int:
    from research import fork
    r = fork.sync(p, a.id, a.source)
    _done(a, r, msg=f"{r['experiment']}: " + (f"merged {a.source} ({r['merged'][:8]})" if r["synced"] else r["note"]))
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
        if G.settings(p)["enabled"] and G.exists(p) and G.undrafted(p):
            print(yellow("  Important results still need a draft in the Scientist Guide: research guide draft"))
    return 0


def _cmd_list(a) -> List[str]:
    cmd = list(a.cmd or [])
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    return cmd


@mut
def cmd_run_exec(a, p) -> int:
    cmd = _cmd_list(a)
    kw = dict(parameters=parse_kv(a.param), label=a.label, working_dir=a.cwd, notes=a.notes, override=a.override,
              sandbox=a.sandbox, sandbox_datasets=a.sandbox_dataset, sandbox_override_reason=a.sandbox_override_reason,
              task=a.sandbox_task)
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
                     working_dir=a.cwd, notes=a.notes, override=a.override, sandbox=a.sandbox,
                     sandbox_datasets=a.sandbox_dataset, sandbox_override_reason=a.sandbox_override_reason,
                     task=a.sandbox_task, **opts)
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
    if not path or not os.path.exists(path):   # HPC runs: logs come home under collected/logs
        alt = os.path.join(p.abspath(r["run_dir"] if r.get("run_dir") else os.path.join(".research", "runs", r["id"])),
                           "collected", "logs", "stderr.log" if a.stderr else "stdout.log")
        path = alt if os.path.exists(alt) else path
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
def cmd_metric_add(a, p) -> int:
    m = R.log_metric(p, a.name, a.value, a.step, a.unit, run=a.run, experiment=a.experiment)
    _done(a, m, msg=f"{a.name} = {a.value} → {a.run or a.experiment}")
    return 0


@mut
def _shell_cmd(a) -> str:
    """argv after `--` as one shell command line: words are quoted, a single argument is taken as already being a command line."""
    c = _cmd_list(a)
    return c[0] if len(c) == 1 else __import__("shlex").join(c)


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


@mut
def cmd_experiment_check(a, p) -> int:
    from research import expcheck
    r = expcheck.check(p, a.id, a.request_synthesis)
    if a.json:
        out_json(r)
        return 0 if r["deterministic"]["all_passed"] else 1
    d = r["deterministic"]
    print(bold(r["experiment"]) + dim(f"  runs checked: {', '.join(r['runs_checked']) or 'none'}"))
    print(green("Verified by programs"))
    for x in d["validated"]:
        print(f"  ✓ {x}")
    for x in d["failed"]:
        print(red(f"  ✗ {x['run']}: {x['problem']}"))
    for x in d["pending"]:
        print(yellow(f"  … {x['run']} {x['state']} — {x['next']}"))
    print(yellow("Needs your interpretation") if r["needs_human"] else dim("Nothing needs human interpretation right now"))
    for x in r["needs_human"]:
        print(f"  ? {x}")
    sd = r["synthesis"]["dispatch"]
    if sd:
        print(green("✓ ") + f"analyst session prepared ({sd['model']}): {sd['shell']}")
    return 0 if d["all_passed"] else 1


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
def cmd_finding_review(a, p) -> int:
    from research import verification as VF
    r = VF.review_finding(p, a.id, a.verdict, a.notes, a.confidence)
    _done(a, r, msg=f"{r['review']['id']} {a.verdict} → {r['finding']['id']} is now [{r['finding']['status']}]")
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


def _print_sweep_reservation(p, opts, n) -> None:
    """A sweep multiplies the reservation by every grid point, which is where a careless submission gets expensive fastest."""
    from research.hpc.remote import reservation
    merged = {**(p.config.get("backends", {}).get("slurm") or {}),
              **{k: v for k, v in opts.items() if v not in (None, [], "")}}
    one = reservation(merged, {"name": "local slurm"})
    print()
    print(bold(f"  Each of the {n} runs would RESERVE:"))
    print("    " + yellow(one["summary"]))
    if one.get("cpu_hours"):
        tot = [f"{round(one['cpu_hours'] * n, 1)} CPU-hours"]
        if one.get("gpu_hours"):
            tot.append(f"{round(one['gpu_hours'] * n, 1)} GPU-hours")
        print("    " + yellow(f"whole sweep, worst case: {' and '.join(tot)}"))
    print(yellow("  Confirm this with whoever owns the allocation before running the sweep."))


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
            _print_sweep_reservation(p, opts, len(res["points"]))
    else:
        for r in res["runs"]:
            print(f"  {r['id']}  [{st(r['status'])}]  {r.get('label') or ''}")
    return 0


def _run_args(x) -> None:
    x.add_argument("experiment")
    x.add_argument("--param", action="append", metavar="K=V", help="run parameters (repeatable)")
    x.add_argument("--label")
    x.add_argument("--cwd", help="working directory (default: current)")
    x.add_argument("--notes")
    x.add_argument("--override", metavar="REASON", help="bypass run budget (logged)")
    x.add_argument("--sandbox", metavar="PROFILE", help="isolation profile for this job (research sandbox list); never falls back to native")
    x.add_argument("--sandbox-dataset", action="append", metavar="PATH", help="extra read-only dataset to mount (repeatable)")
    x.add_argument("--sandbox-override-reason", metavar="WHY", help="required to run natively where a policy requires a sandbox (logged)")
    x.add_argument("--task", dest="sandbox_task", metavar="T-ID", help="task this run belongs to (selects a task-level sandbox assignment)")
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
    x.add_argument("--commit", action="store_true", help="also commit .research/ to git (only .research/, never pushed)")


def attach(sub, add, group) -> None:
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
    x = add(g, "fork", cmd_experiment_fork, "new experiment whose worktree starts from another experiment's pinned code (no promotion needed)")
    x.add_argument("id", help="the parent experiment")
    x.add_argument("--title", required=True)
    x.add_argument("--at", default="verified", help="verified (default) | a run id (RUN-…) | a git commit")
    x.add_argument("--override", metavar="REASON", help="bypass synthesis-before-new-experiment (logged)")
    x = add(g, "sync", cmd_experiment_sync, "explicitly merge main (or an EXP-/commit) into a forked experiment's branch")
    x.add_argument("id")
    x.add_argument("--from", dest="source", default="main", help="main (default) | EXP-id (its verified code) | a git commit")
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
    x = add(g, "check", cmd_experiment_check, "validate runs deterministically, list what needs interpretation, optionally prepare synthesis")
    x.add_argument("id")
    x.add_argument("--request-synthesis", action="store_true", help="prepare (not run) an analyst session when synthesis is due")
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
    x = add(g, "run", cmd_run_exec, "execute a run of this experiment (same as `run exec`)", takes_tail=True)
    _run_args(x)
    x.add_argument("--detach", action="store_true")
    g = group("run", "runs (single executions; always belong to an experiment)", aliases=["r"])
    x = add(g, "exec", cmd_run_exec, "run a command locally, tracked: research run exec EXP-001 -- python x.py", takes_tail=True)
    _run_args(x)
    x.add_argument("--detach", action="store_true", help="run in background (survives the terminal)")
    x = add(g, "submit", cmd_run_submit, "submit via SLURM: research run submit EXP-001 --time 1:00:00 -- python x.py", takes_tail=True)
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
    x = add(g, "attach", cmd_run_attach, "register a run you already executed yourself", aliases=["add"], takes_tail=True)
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
    x = add(g, "sweep", cmd_run_sweep, "one run per grid point: research run sweep EXP-1 --grid lr=1e-3,1e-4 -- python t.py --lr {lr}", takes_tail=True)
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

