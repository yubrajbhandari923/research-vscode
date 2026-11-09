"""Project lifecycle and generic commands: init, status, current, config, doctor, search, show, mark, sync, log."""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict

import research
from research import context as C, providers as PV
from research import services as S
from research import views as V
from research.schema import normalize_id
from research.store import Project
from research.util import ResearchError

from . import mut
from ..output import _done, author, bold, cyan, dim, fmt_val, green, kv, magenta, out_json, print_experiment, print_finding, print_generic, red, section, st, yellow


def _resume_checkpoint(r) -> None:
    cp = r.get("latest_checkpoint")
    section("Latest checkpoint")
    if not cp:
        print(dim("  none yet — `research checkpoint` to create one"))
        return
    print(f"  {cp['id']} {cp['title']} " + dim(f"({cp['created_at'][:10]}, {cp.get('age_days') or 0:.0f}d ago)")
          + (yellow(" STALE") if cp.get("stale") else ""))
    kv("understanding", cp.get("understanding"))
    kv("problem", cp.get("current_problem"))
    kv("next", cp.get("next_experiment"))
    sc = r["since_checkpoint"]
    if sc["events"]:
        print(dim(f"  since then: {sc['events']} changes, {sc['runs']} runs, "
                  f"{len(sc['findings'])} findings, {len(sc['experiments'])} experiments"))


def _resume_beliefs(r) -> None:
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


def _resume_work(r) -> None:
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
    _resume_checkpoint(r)
    b = r.get("baseline")
    section("Baseline")
    print(f"  {b['id']} {b['title']}" if b else dim("  none set — `research experiment baseline EXP-…`"))
    section("Questions")
    for q in r["active_questions"] + r["open_questions"] + r["blocked_questions"]:
        print(f"  {q['id']}  {q['title']}  [{st(q['status'])}] {dim(str(q['experiments']) + ' exp')}")
    if not (r["active_questions"] or r["open_questions"] or r["blocked_questions"]):
        print(dim("  none open"))
    _resume_beliefs(r)
    _resume_work(r)


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


def cmd_ledger(a) -> int:
    from research import workspace as W
    root = os.path.abspath(a.root or os.getcwd())
    act = a.action
    if act == "status":
        loc = W.locate(root)
        if not loc:
            print(dim("not a research project"))
            return 1
        ident = W.project_identity(os.path.join(loc.ledger_root, ".research"))
        r = {"mode": "shared-ledger" if loc.linked else "classic", "ledger_root": loc.ledger_root,
             "project_id": (ident or {}).get("project_id"), "source_root": loc.source_root}
        if a.json:
            out_json(r)
        else:
            kv("mode", r["mode"])
            kv("ledger", r["ledger_root"])
            kv("project id", r["project_id"] or "—")
        return 0
    if act == "relink":
        if not a.path:
            raise ResearchError("relink needs the new ledger path")
        r = W.relink(root, a.path)
        out_json(r) if a.json else print(green("✓ ") + f"ledger now {r['ledger_root']}")
        return 0
    plan = W.plan_enable(root, a.path)
    if not a.apply:
        if a.json:
            out_json(plan)
            return 0 if plan["ok"] else 1
        print(bold("Enable shared ledger (dry run — nothing changed)"))
        for c in plan["changes"]:
            print("  • " + c)
        for n in plan["notes"]:
            print(dim("  " + n))
        for pr in plan.get("problems", []):
            print(yellow("  ✗ " + pr))
        print(dim("\nRe-run with --apply to perform exactly these steps.") if plan["ok"] else "")
        return 0 if plan["ok"] else 1
    r = W.enable(root, a.path)
    if a.json:
        out_json(r)
    else:
        print(green("✓ ") + f"shared ledger at {r['ledger_root']}  ({sum(r['records'].values())} records verified)")
        print(dim(f"  original .research kept at {r['backup']}"))
    return 0


def cmd_rpc(a) -> int:
    from research import rpc
    from research.store import find_root
    return rpc.main(["--root", a.root or find_root() or os.getcwd()])


def cmd_version(a) -> int:
    from research import rpc
    info = {"version": research.__version__, "protocol": rpc.PROTOCOL, "python": sys.version.split()[0], "executable": sys.executable}
    print(json.dumps(info) if a.json else f"research {info['version']} (protocol {info['protocol']})")
    return 0


def cmd_self_update(a) -> int:
    from research import selfupdate
    return selfupdate.run(check=a.check, version=a.version, repo=a.repo, major=a.major, from_file=a.from_file)


def cmd_tui(a) -> int:
    from research.store import find_root
    try:
        from research_cli.tui import run
    except ImportError:
        print("TUI needs textual: pip install 'research-panel[tui]'")
        return 1
    run(os.path.abspath(a.root or find_root() or os.getcwd()))
    return 0


def cmd_doctor(a) -> int:
    from research import doctor as DR
    from research.store import find_root
    root = os.path.abspath(a.root or find_root() or os.getcwd())
    if a.fix:
        r = DR.apply_fix(root, a.fix)
        out_json(r) if a.json else print(green("✓ ") + f"{r['fixed']}: wrote " + ", ".join(r["wrote"]))
        return 0
    r = DR.run(root)
    if a.json:
        out_json(r)
        return 1 if r["summary"]["errors"] else 0
    mark = {"ok": green("✓"), "info": dim("·"), "warn": yellow("⚠"), "error": red("✗")}
    area = None
    for c in r["checks"]:
        if c["level"] == "ok" and not a.all:
            continue
        if c["area"] != area:
            area = c["area"]
            print("\n" + bold(area))
        print(f"  {mark[c['level']]} {c['title']}")
        if c["detail"]:
            print(dim("      " + c["detail"]))
        if c["fix"]:
            fx = c["fix"]
            print("      → " + (fx.get("text") or (f"Run `{fx['command']}`." if fx.get("command") else "")) + (f"   (apply: research doctor --fix {c['id']})" if fx.get("safe") else ""))
    s = r["summary"]
    print(f"\n{s['errors']} error(s), {s['warnings']} warning(s), {s['info']} optional, {s['ok']} ok" + dim("   (--all lists the passing checks)"))
    return 1 if s["errors"] else 0


def cmd_init_from(a) -> int:
    """`research init --from research-os.yaml` previews by default; --apply does it. Same initializer as the wizard and `project-init`."""
    from research import bootstrap as BS
    root = os.path.abspath(a.path or os.getcwd())
    text = open(a.from_file, encoding="utf-8").read()
    r = BS.check(text, root)
    if a.json and not a.apply:
        out_json(r)
        return 0 if r["ok"] else 1
    if not r["ok"]:
        for e in r["errors"]:
            print(red("  ✗ ") + e)
        return 1
    if not a.apply:
        for x in r["plan"]["actions"]:
            print("  • " + x)
        print("\n" + "\n".join(r["plan"]["tree"]) + dim("\n\n  add --apply to do it (nothing was written)"))
        return 0
    res = BS.apply(text, root)
    out_json(res) if a.json else ([print(green("✓ ") + x) for x in res["done"]], print(dim("  next: research approval set-pin  (protects approving agent proposals and promotion)")))
    return 0


def cmd_init(a) -> int:
    if a.template:
        from research import bootstrap as BS
        if a.out:
            if os.path.exists(a.out):
                raise ResearchError(f"{a.out} already exists; remove it or choose another name")
            open(a.out, "w", encoding="utf8").write(BS.TEMPLATE)
            print(green("✓ ") + f"wrote {a.out}; edit it, then: research init --from {a.out} --dry-run")
        else:
            print(BS.TEMPLATE, end="")
        return 0
    if a.export:
        from research import bootstrap as BS
        with Project.find() as p:
            print(BS.export(p), end="")
        return 0
    if a.from_file:
        return cmd_init_from(a)
    if a.layout == "ledger":                   # the old `project-init`: main/ + ledger/ + experiments/
        a.folder = a.path
        return cmd_project_init(a)
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
    """`status` (compact) or `status --full` (= the old `resume`)."""
    r = C.resume_context(p)
    out_json(r) if a.json else (print_resume if a.full_status else print_status)(r)
    return 0


def cmd_current(a, p) -> int:
    """`current` prints current.md; `current --quiet` (= the old `context`) only regenerates it."""
    path = C.write_current_md(p)
    text = open(path, encoding="utf-8").read()
    if a.quiet:
        if a.json:
            out_json({"path": p.rel(path)})
        elif getattr(a, "print", False):       # the old `context --print`
            print(text)
        else:
            print(green("✓ ") + p.rel(path))
        return 0
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


@mut
def cmd_sync(a, p) -> int:
    stats = p.sync()
    _done(a, stats, msg=f"synced (imported {stats['imported']} edited files, restored {stats['restored']})")
    return 0


@mut
def cmd_mark(a, p) -> int:
    r = S.set_status(p, a.id, a.status, a.reason)
    _done(a, r, msg=f"{r['id']} → {r['status']}")
    return 0


def _cfg_options(a, p) -> int:
    from research import registry as R
    d = R.describe(p, a.category, a.search)
    if a.json:
        out_json(d)
        return 0
    for x in d["options"]:
        tag = "" if x["source"] == "default" else f" [{x['source']}]"
        print(f"  {x['key'].ljust(48)} {fmt_val(x['value']).ljust(10)}{tag}  {dim(x['label'])}")
    return 0


def _cfg_explain(a, p) -> int:
    from research import registry as R
    x = R.explain(p, a.key)
    if a.json:
        out_json(x)
        return 0
    print(bold(x["label"]) + dim(f"   {x['key']}"))
    print("\n" + x["help"])
    print("\nWhy: " + x["why"] + "\nEffect: " + x["effect"])
    kv("value", f"{fmt_val(x['value'])} ({x['source']}{', inherited' if x['inherited'] else ''})")
    kv("default", fmt_val(x["default"]))
    kv("allowed", x["choices"] or (f"{x['min']}..{x['max']}" if x["min"] is not None or x["max"] is not None else x["type"]))
    kv("needs a human", "yes" if x["approval"] else "no")
    return 0


def _cfg_set(a, p) -> int:
    """set | reset"""
    from research import registry as R
    remove = a.action == "reset"
    if a.action == "set" and a.value is None:
        raise ResearchError("config set needs KEY and VALUE")
    change = {a.key: None if remove else a.value}
    if a.cfg_dry:
        r = R.preview(p, change, a.scope, remove)
        if a.json:
            out_json(r)
        else:
            print(r["diff"] or dim("(no change)"))
            for k, e in r["errors"].items():
                print(red("✗ ") + f"{k}: {e}")
        return 0 if r["ok"] else 1
    r = R.apply(p, change, a.scope, a.reason, a.confirm, remove)
    out_json(r) if a.json else print(green("✓ ") + (f"{a.key} " + ("reset" if remove else f"= {fmt_val(r['values'][a.key])}") + f" ({a.scope})" if r["changed"] else "no change"))
    return 0


def _cfg_sources(a, p) -> int:
    from research import configfile as CF
    from research.store import DEFAULT_CONFIG
    src = CF.sources(p.raw_config, DEFAULT_CONFIG)
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


def _cfg_show(a, p) -> int:
    if a.sources:
        return _cfg_sources(a, p)
    shown = p.config if a.effective else p.raw_config
    if a.json:
        out_json(shown)
    else:
        from research import yamlio
        sys.stdout.write(yamlio.dump(shown, block=True))
        if not a.effective:
            print(dim("\n  (this project's own settings only — add --effective for every value in force)"))
    return 0


def _cfg_check(a, p) -> int:
    from research import configfile as CF
    from research.store import DEFAULT_CONFIG
    with open(p.config_path, encoding="utf-8") as f:
        text = f.read()
    r = CF.check(p.raw_config, DEFAULT_CONFIG, text)
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
                  f"(`research config reset KEY`)"))
    if r["ok"] and not r["unknown"]:
        print(green("✓ ") + "configuration is valid")
    kv("machine config", r["machine_config"] or "none")
    return 0 if r["ok"] else 1


_CONFIG = {"options": _cfg_options, "explain": _cfg_explain, "set": _cfg_set, "reset": _cfg_set, "show": _cfg_show, "check": _cfg_check}


@mut
def cmd_config(a, p) -> int:
    """`research config show|check|options|explain|set|reset`. show/check never write."""
    if a.action in ("explain", "set", "reset") and not a.key:
        raise ResearchError(f"config {a.action} needs a setting key")
    return _CONFIG[a.action](a, p)


def cmd_rebuild(a, p) -> int:
    counts = p.rebuild_index(backup=True)
    _done(a, counts, msg="index rebuilt from text files: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    return 0


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


def cmd_project_init(a) -> int:
    """Initialize an empty folder as main/ + ledger/ + experiments/ + .code-workspace (preview unless --apply)."""
    from research import initproject as IP
    root = os.path.abspath(a.folder or os.getcwd())
    spec = json.loads(open(a.spec).read()) if a.spec else {"name": a.name or os.path.basename(root), "goal": a.goal or "",
           "agents": {"enabled": a.agents.split(","), "default": a.agents.split(",")[0]}}
    pl = IP.plan(root, spec)
    if not a.apply or not pl["ok"]:
        out_json({k: pl[k] for k in ("ok", "problems", "actions", "notes", "tree")}) if a.json else (
            [print(("  • " if pl["ok"] else red("  ✗ ")) + x) for x in (pl["actions"] if pl["ok"] else pl["problems"])] and print(dim("\n  add --apply to do it")))
        return 0 if pl["ok"] else 1
    r = IP.apply(root, spec)
    out_json(r) if a.json else [print(green("✓ ") + x) for x in r["done"]]
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


def _project_init_args(x, folder: bool = True) -> None:
    if folder:
        x.add_argument("folder", nargs="?")
        x.add_argument("--name"); x.add_argument("--goal")
        x.add_argument("--apply", action="store_true")
    x.add_argument("--agents", default="claude", help="comma list from " + ",".join(PV.KNOWN) + "; first is the default")
    x.add_argument("--spec", help="JSON file with the full wizard spec")


def attach(sub, add, group) -> None:
    x = add(sub, "init", cmd_init, "initialize .research/ in a project (and AGENTS.md); or --from research-os.yaml for a whole project", needs_project=False)
    x.add_argument("--from", dest="from_file", metavar="YAML", help="set up a whole project from a bootstrap file (previews unless --apply)")
    x.add_argument("--apply", action="store_true", help="with --from: perform the setup")
    x.add_argument("--dry-run", action="store_true", help="with --from: preview only (the default)")
    x.add_argument("--template", action="store_true", help="print a complete documented bootstrap file")
    x.add_argument("-o", "--out", metavar="FILE", help="with --template: write the file instead of printing it (never overwrites)")
    x.add_argument("--export", action="store_true", help="print this project's portable settings as a bootstrap file")
    x.add_argument("path", nargs="?")
    x.add_argument("--name")
    x.add_argument("--goal")
    x.add_argument("--description")
    x.add_argument("--no-agents-md", action="store_true")
    x.add_argument("--layout", choices=["classic", "ledger"], default="classic",
                   help="ledger: initialize an empty folder as main/ + ledger/ + experiments/ + workspace (preview unless --apply)")
    _project_init_args(x, folder=False)
    x = add(sub, "status", cmd_status, "compact project status; --full: latest checkpoint + current state")
    x.add_argument("--full", dest="full_status", action="store_true", help="what were we doing? (the old `resume`)")
    x = add(sub, "resume", cmd_status, None)                    # hidden alias of `status --full`
    x.set_defaults(full_status=True)
    x = add(sub, "current", cmd_current, "print the current research state for agents (regenerates current.md); --quiet only regenerates it")
    x.add_argument("--quiet", action="store_true", help="regenerate .research/context/current.md and print only its path")
    x = add(sub, "context", cmd_current, None)                  # hidden alias of `current --quiet`
    x.set_defaults(quiet=True)
    x.add_argument("--print", action="store_true")
    x = add(sub, "search", cmd_search, "search everything recorded: research search \"dataset prep\"")
    x.add_argument("query", nargs="+")
    x.add_argument("--type", action="append", help="limit to a type (finding, experiment, task, run, note, skill, …)")
    x.add_argument("--limit", type=int, default=20)
    x = add(sub, "project-init", cmd_project_init, None, needs_project=False)   # hidden alias of `init --layout ledger`
    _project_init_args(x)
    x = add(sub, "ledger", cmd_ledger, "shared research ledger for git worktrees: status | enable [--apply] | relink", needs_project=False)
    x.add_argument("action", choices=["status", "enable", "relink"])
    x.add_argument("path", nargs="?", help="enable: where to create the ledger worktree; relink: its new location")
    x.add_argument("--apply", action="store_true", help="enable: perform the migration (default is a dry run)")
    x.add_argument("--dry-run", action="store_true", help="enable: preview only (the default)")
    add(sub, "rpc", cmd_rpc, None, needs_project=False)         # hidden: the extension's transport, not for people
    add(sub, "version", cmd_version, "print the core version and RPC protocol (--json for the extension)", needs_project=False)
    x = add(sub, "self-update", cmd_self_update, "install the newest GitHub release (sha256-verified)", needs_project=False)
    x.add_argument("--check", action="store_true", help="only report current vs latest")
    x.add_argument("--version", help="install this release instead of the latest")
    from research.selfupdate import REPO as _REPO
    x.add_argument("--repo", default=_REPO, help="GitHub OWNER/NAME")
    x.add_argument("--major", action="store_true", help="allow a major-version (breaking) upgrade")
    x.add_argument("--from-file", metavar="WHEEL", help="offline install from a local wheel (checks a sibling SHA256SUMS)")
    add(sub, "tui", cmd_tui, "terminal UI (needs the tui extra)", needs_project=False)
    x = add(sub, "doctor", cmd_doctor, "inspect the project: records, workspaces, runs, agents, sandbox, context (read-only)", needs_project=False)
    x.add_argument("--all", action="store_true", help="also list the checks that passed")
    x.add_argument("--fix", metavar="CHECK", help="apply one safe fix for generated files (context.stale, agents_md.stale, bin.stale)")
    x = add(sub, "show", cmd_show, "show any object by id (Q-…, EXP-…, RUN-…, F-…, D-…, CP-…, A-…, note:…)")
    x.add_argument("id")
    x = add(sub, "mark", cmd_mark, "set status of any object: research mark Q-001 answered")
    x.add_argument("id")
    x.add_argument("status")
    x.add_argument("--reason")
    add(sub, "sync", cmd_sync, "import hand-edited files, ingest job outputs, refresh run status")
    add(sub, "rebuild", cmd_rebuild, None)                      # hidden: recovery (`doctor` points to it)
    x = add(sub, "config", cmd_config, "configuration: show [--effective|--sources] | check | options | explain | set | reset")
    x.add_argument("action", nargs="?", choices=["show", "check", "options", "explain", "set", "reset"], default="show")
    x.add_argument("key", nargs="?", help="explain/set/reset: setting key, e.g. agent_policy.max_runs_without_synthesis")
    x.add_argument("value", nargs="?", help="set: the new value")
    x.add_argument("--scope", choices=["project", "machine"], default="project", help="set/reset: which config file")
    x.add_argument("--category", help="options: only this category")
    x.add_argument("--search", help="options: words to look for")
    x.add_argument("--dry-run", dest="cfg_dry", action="store_true", help="set/reset: show the YAML diff, write nothing")
    x.add_argument("--confirm", action="store_true", help="set: confirm a risky change")
    x.add_argument("--reason", help="set/reset: why (kept in the audit log)")
    x.add_argument("--effective", action="store_true", help="show: every value in force, not just overrides")
    x.add_argument("--sources", action="store_true", help="show: which layer supplies each value")
    x = add(sub, "log", cmd_log, "audit log of recent changes")
    x.add_argument("--limit", type=int, default=30)
    g = group("project", "project metadata")
    x = add(g, "set", cmd_project_set, "set name/goal/description")
    x.add_argument("--name")
    x.add_argument("--goal")
    x.add_argument("--description")
    x.add_argument("--status")

