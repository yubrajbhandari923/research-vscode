"""Workspaces, sandbox profiles and remote (HPC) execution."""
from __future__ import annotations

import json
import os
from typing import Any, Dict

from research.util import ResearchError, human_size

from ..output import _done, bold, dim, green, kv, out_json, red, section, st, yellow
from .records import _shell_cmd


def _ws_row(w) -> str:
    flags = ("dirty " if w.get("dirty") else "") + (f"{len(w['active_runs'])} running " if w["active_runs"] else "") \
        + ("" if w["present"] or w["status"] == "archived" else "MISSING ")
    return f"{w['id']}  {w['status']:<8} {w.get('branch') or '—':<36} {w.get('experiment') or '—':<8} {flags.strip()}"


def _ws_list(a, p, W):
    r = W.list_workspaces(p, include_archived=a.all)
    if a.json:
        return r
    for w in r:
        print(_ws_row(w))
    if not r:
        print(dim("no workspaces — `research workspace create EXP-001` makes one"))
    return 0


def _ws_open(a, p, W):
    s = W.show(p, a.target)
    if not s["present"]:
        raise ResearchError(f"{a.target} has no directory on this machine")
    print(s["path"])
    return 0


_WORKSPACE = {
    "info": lambda a, p, W: W.info(p),
    "create": lambda a, p, W: W.create(p, a.target, name=a.name, base=a.base, path=a.path_),
    "attach": lambda a, p, W: W.attach(p, a.target, experiment=a.experiment),
    "list": _ws_list,
    "show": lambda a, p, W: W.show(p, a.target),
    "link-task": lambda a, p, W: W.link_task(p, a.target, a.task),
    "assign": lambda a, p, W: W.assign(p, a.target, a.agent_name),
    "archive": lambda a, p, W: W.archive_impact(p, a.target) if a.dry_run else W.archive(p, a.target, reason=a.reason),
    "open": _ws_open,
}


def cmd_workspace(a, p) -> int:
    from research import workspace as W
    r = _WORKSPACE[a.action](a, p, W)
    if isinstance(r, int):
        return r
    out_json(r) if a.json else print(json.dumps(r, indent=2, default=str))
    return 0


def _sb_list(a, p, SB):
    r = [SB.summary(p, n) for n in SB.profile_names(p)]
    if a.json:
        return r
    for x in r:
        print(f"  {x['name'].ljust(14)} {x['runtime'].ljust(10)} network {x['network'].ljust(9)} gpu {str(x['gpu']).ljust(5)} source {x['source']}")
    print(dim(f"\n  default: {p.config['sandbox'].get('default_profile')} ({'applies' if p.config['sandbox'].get('enabled') else 'not enabled'})"))
    return 0


def _sb_doctor(a, p, SB):
    r = SB.doctor(p)
    if a.json:
        return r
    for x in r["runtimes"]:
        print(f"  {x['runtime'].ljust(10)} " + (green("available") if x["available"] else yellow("unavailable")) + f"  {x.get('version') or ''}")
        for n in x["notes"]:
            print(dim("      " + n))
    print(dim("\n  " + r["note"]))
    return 0


def _sb_verify(a, p, SB):
    r = SB.verify(p, a.target or p.config["sandbox"].get("default_profile") or "restricted")
    if a.json:
        return r
    print(bold(f"{r['profile']} on {r.get('runtime') or 'no runtime'}"))
    for x in r["properties"]:
        col = green if x["state"] == "verified" else (yellow if x["state"] == "enforceable" else red)
        print(f"  {col(x['state'].ljust(11))} {x['property']}")
    for n in r.get("notes", []):
        print(dim("  " + n))
    print((green("✓ jobs would run") if r["allowed"] else red("✗ jobs would be refused: ") + (r.get("refusal") or "")))
    return 0 if r["allowed"] else 1


def _sb_preview(a, p, SB):
    r = SB.preview(p, experiment=a.target, task=a.task, cluster=a.cluster, profile_name=a.profile, datasets=a.dataset)
    if a.json:
        return r
    kv("profile", f"{r['profile']} (from {r['scope']})")
    kv("runtime", r["preflight"].get("runtime") or r["policy"]["runtime"])
    for m in r["preflight"]["mounts"]:
        print(f"    {m['mode']}  {m['target'].ljust(18)} <- {m['host']}   {dim(m['why'])}")
    if r["would_be_refused"]:
        print(red("✗ would be refused: ") + (r["preflight"]["refusal"] or ""))
    print(dim("  " + r["agent_sandbox"]["note"]))
    return 1 if r["would_be_refused"] else 0


# each returns the record (printed as JSON) or an int exit code when it already printed
_SANDBOX = {"list": _sb_list, "doctor": _sb_doctor, "verify": _sb_verify, "preview": _sb_preview,
            "show": lambda a, p, SB: {"name": a.target, "policy": SB.profile(p, a.target), "summary": SB.summary(p, a.target)}}


def cmd_sandbox(a, p) -> int:
    from research import sandbox as SB
    if a.action not in _SANDBOX:
        raise ResearchError(f"unknown sandbox action {a.action}")
    r = _SANDBOX[a.action](a, p, SB)
    if isinstance(r, int):
        return r
    out_json(r)
    return 0


def _hpc_config(a, p, cl) -> int:
    from research.hpc import config as HC
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


def _hpc_clusters(a, p, cl) -> int:
    from research.hpc import config as HC
    names = HC.names(p)
    if a.json:
        out_json({"clusters": names, "default": HC.default_name(p)})
    elif not names:
        print(dim("(none configured — `research hpc config --example`)"))
    else:
        for n in names:
            print(f"  {bold(n)}" + (green("  (default)") if n == HC.default_name(p) else ""))
    return 0


def _hpc_doctor(a, p, cl) -> int:
    from research.hpc import remote as HR
    d = HR.doctor(p, cl, compute=getattr(a, 'compute', False))
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


def _hpc_init(a, p, cl) -> int:
    from research.hpc import remote as HR
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


def _hpc_inspect(a, p, cl) -> int:
    from research.hpc import remote as HR
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


def _hpc_datasets(a, p, cl) -> int:
    from research.hpc import config as HC
    from research.hpc import datasets as HD
    return _cmd_hpc_datasets(a, p, HC, HD)


def _hpc_bundle(a, p, cl) -> int:
    from research.hpc import bundle as HB
    from research.hpc import config as HC
    cluster = HC.get(p, cl) if (cl or HC.names(p)) else None
    res = (HC.check_resources(cluster, _slurm_opts(a)) if cluster else _slurm_opts(a))
    r = HB.build(p, a.id, command=_shell_cmd(a) or None, fmt=a.format,
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


def _hpc_submit(a, p, cl) -> int:
    from research.hpc import remote as HR
    r = HR.submit(p, a.id, cluster_name=cl, command=_shell_cmd(a) or None,
                  dry_run=a.dry_run, approve=a.yes, resources=_slurm_opts(a),
                  stage_datasets=a.stage_datasets, deep_verify=a.deep, override=a.override)
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


def _hpc_status(a, p, cl) -> int:
    from research.hpc import remote as HR
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


def _hpc_sync(a, p, cl) -> int:
    from research.hpc import remote as HR
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
        if x.get("stderr_tail"):
            print(yellow(f"      stderr: {x['stderr_tail']}") + dim(f"   (research run logs {x['run']} --stderr)"))
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


def _hpc_collect(a, p, cl) -> int:
    from research.hpc import remote as HR
    r = HR.collect(p, a.id, cluster_name=cl, max_bytes=a.max_bytes)
    _done(a, r, msg=f"{r['run']}: {r['metrics_added']} new metric entries, "
                    f"{len(r.get('artifacts') or [])} artifact(s), status {r['status']}")
    if not a.json:
        for w in r.get("warnings") or []:
            print(yellow("  ⚠ " + w))
        for ref in r.get("referenced") or []:
            print(dim(f"  → {ref['artifact']} stays on the cluster at {ref['remote_path']}"))
    return 0


def _hpc_import(a, p, cl) -> int:
    from research.hpc import results as HRES
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


def _hpc_cancel(a, p, cl) -> int:
    from research.hpc import remote as HR
    r = HR.cancel(p, a.id, cluster_name=cl)
    _done(a, r, msg=f"{r['run']}: slurm job {r['slurm_job_id']} cancelled on {r['cluster']}")
    return 0


_HPC = {"config": _hpc_config, "clusters": _hpc_clusters, "doctor": _hpc_doctor, "init": _hpc_init, "inspect": _hpc_inspect, "datasets": _hpc_datasets, "bundle": _hpc_bundle, "submit": _hpc_submit, "status": _hpc_status, "sync": _hpc_sync, "collect": _hpc_collect, "import": _hpc_import, "cancel": _hpc_cancel}


def cmd_hpc(a, p) -> int:
    """`research hpc <action>`: remote execution on a configured cluster. All of it is opt-in."""
    if not a.action:                 # bare `research hpc` → show what it can do
        a._hpc_parser.print_help()
        return 0
    if a.action not in _HPC:
        raise ResearchError(f"unknown hpc action {a.action!r}")
    return _HPC[a.action](a, p, getattr(a, "cluster", None))


def _ds_list(a, p, cluster, ident) -> int:
    from research.hpc import datasets as HD
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


def _ds_inspect(a, p, cluster, ident) -> int:
    from research.hpc import datasets as HD
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


def _ds_check(a, p, cluster, ident) -> int:
    from research.hpc import datasets as HD
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


def _ds_fetch(a, p, cluster, ident) -> int:
    from research.hpc import datasets as HD
    r = HD.fetch(p, cluster, ident, url=a.url, from_local=a.from_local, approve=a.yes, deep=a.deep)
    if a.json:
        out_json(r)
        return 0
    mark = green("✓ ") if r["action"] in ("reuse", "staged") else yellow("⚠ ")
    print(mark + f"{r['id']}: {r['action']}")
    print(dim("  " + r["detail"]))
    return 0 if r["action"] in ("reuse", "staged") else 1


def _ds_adopt(a, p, cluster, ident) -> int:
    from research.hpc import datasets as HD
    r = HD.adopt(p, cluster, ident, version=a.version, source=a.url, description=a.description,
                 dry_run=a.dry_run)
    if a.json:
        out_json(r)
        return 0
    print((dim("would write") if r["dry_run"] else green("✓ wrote"))
          + f" {r['path']}/manifest.json  "
          + dim(f"({len(r['manifest'].get('expected_files') or [])} file(s) recorded)"))
    return 0


_DATASETS = {"list": _ds_list, "inspect": _ds_inspect, "check": _ds_check, "fetch": _ds_fetch, "adopt": _ds_adopt}


def _cmd_hpc_datasets(a, p, HC, HD) -> int:
    """`research hpc datasets <list|inspect|check|fetch|adopt>` — the filesystem is the source of truth."""
    sub = a.id or "list"
    cluster = HC.get(p, getattr(a, "cluster", None))
    ident = a.dataset
    if sub != "list" and not ident:
        raise ResearchError(f"`research hpc datasets {sub}` needs a dataset id",
                            hint="`research hpc datasets list` shows what is on the cluster.")
    if sub not in _DATASETS:
        raise ResearchError(f"unknown datasets action {sub!r} (list | inspect | check | fetch | adopt)")
    return _DATASETS[sub](a, p, cluster, ident)


def _slurm_opts(a) -> Dict[str, Any]:
    opts = {k: getattr(a, k, None) for k in ("partition", "account", "time", "cpus", "mem", "gpus")}
    if getattr(a, "sbatch_arg", None):
        opts["extra_args"] = list(a.sbatch_arg)
    return {k: v for k, v in opts.items() if v not in (None, "", [])}


def attach(sub, add, group) -> None:
    x = add(sub, "workspace", cmd_workspace, "experiment worktrees: info | create | attach | list | show | link-task | assign | archive",
            aliases=["ws"])
    x.add_argument("action", choices=["info", "create", "attach", "list", "show", "link-task", "assign", "archive", "open"])
    x.add_argument("target", nargs="?", help="EXP- id (create), path (attach) or WS- id")
    x.add_argument("--name", help="create: short branch suffix")
    x.add_argument("--base", help="create: base revision (default HEAD)")
    x.add_argument("--path", dest="path_", help="create: worktree directory")
    x.add_argument("--experiment", help="attach: experiment to associate")
    x.add_argument("--task", help="link-task: task id")
    x.add_argument("--agent-name", dest="agent_name", help="assign: agent name (omit to clear)")
    x.add_argument("--dry-run", action="store_true", help="archive: show impact only")
    x.add_argument("--reason", help="archive: why")
    x.add_argument("--all", action="store_true", help="list: include archived")
    x = add(sub, "sandbox", cmd_sandbox, "optional job isolation: list | show NAME | doctor | verify NAME | preview EXP")
    x.add_argument("action", choices=["list", "show", "doctor", "verify", "preview"])
    x.add_argument("target", nargs="?", help="profile name (show/verify) or EXP- id (preview)")
    x.add_argument("--task", help="preview: task id")
    x.add_argument("--cluster", help="preview: cluster name")
    x.add_argument("--profile", help="preview: profile for this run")
    x.add_argument("--dataset", action="append", help="preview: extra read-only dataset path")
    x = add(sub, "hpc", cmd_hpc, "remote execution: doctor | init | submit | status | sync | collect | "
                                 "import | bundle | datasets | inspect | cancel | clusters | config", takes_tail=True)
    x.add_argument("action", nargs="?",
                   choices=["doctor", "init", "config", "clusters", "inspect", "bundle", "submit",
                            "status", "sync", "collect", "import", "cancel", "datasets"])
    x.add_argument("id", nargs="?", help="EXP-… (bundle/submit) · RUN-… (status/collect/cancel) · "
                                         "archive path (import) · sub-action (datasets)")
    x.add_argument("dataset", nargs="?", help="dataset id, for `datasets inspect|check|fetch|adopt`")
    x.add_argument("--cluster", help="cluster name from hpc.clusters (default: hpc.default_cluster)")
    x.add_argument("--dry-run", action="store_true", help="preview only; transfers and submits nothing")
    x.add_argument("--compute", action="store_true", help="doctor: also prove the job environment on a compute node (one-minute, one-CPU srun)")
    x.add_argument("--yes", action="store_true", help="approve an expensive action (submit, dataset staging)")
    x.add_argument("--override", metavar="REASON", help="submit: go past the unsynthesized/failed-run budget (logged)")
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

