"""Agent providers, profiles, dispatch, strategy, hooks, orchestration and structured agent reports."""
from __future__ import annotations

import json
import os
import sys

from research import providers as PV, supervisor as SV
from research.schema import normalize_id
from research.store import Project
from research.util import ResearchError, detect_author

from . import mut
from .guide import _setup_args, cmd_setup
from .skills import _agents_md_args, cmd_agents_md
from .supervisor import _emit, _read
from ..output import _done, bold, dim, green, out_json, red, section, yellow


@mut
def cmd_strategy(a, p) -> int:
    """`research strategy list|show|use|validate|route` — named model-allocation strategies."""
    from research import harness as H
    from research import orchestrate as O
    from research import strategy as S
    act, name = a.action, a.name
    if act == "list":
        r = {"active": S.active_name(p), "strategies": S.listing(p)}
        if a.json:
            out_json(r)
            return 0
        print(dim("  active: ") + (r["active"] or "none (legacy tier routing)"))
        for s_ in r["strategies"]:
            print(f"  {'*' if s_['active'] else ' '} {s_['name'].ljust(14)} {dim(s_['origin'].ljust(9))} {s_['description'] or ''}")
        return 0
    if act == "show":
        r = S.show(p, name)
        r["harness"] = {k: v for k, v in H.resolve(p).items() if k not in ("skills_detail", "context_detail")}
        if a.json:
            out_json(r)
            return 0
        if not r.get("name"):
            print(r["legacy"])
            return 0
        print(f"{r['name']} [{r['origin']}] @{r['fingerprint']}" + (f" extends {r['extends']}" if r["extends"] else ""))
        d = r["definition"]
        print(dim("  " + (d.get("description") or "")))
        for lv, x in r["resolved_levels"].items():
            print(f"  level {lv.ljust(9)} → {x.get('profile')} ({x.get('model') or 'cli default'})")
        print("  route    " + ", ".join(f"{c}→{lv}" for c, lv in d["route"].items()))
        print(f"  coordinator {d['coordinator']} · guide {d['guide']} · scientific-assumption → {d['on_scientific_assumption']}")
        print("  attempts " + ", ".join(f"{k}:{v}" for k, v in d["attempts"].items()) + " · escalate " +
              ", ".join(f"{k}→{v}" for k, v in d["escalate"].items()) + f" · max escalations {d['max_escalations']}")
        for e in r["validation"]["errors"]:
            print(red("✗ ") + e)
        for w in r["validation"]["warnings"] + H.warnings(p):
            print(yellow("⚠ ") + w)
        return 0
    if act == "validate":
        names = [name] if name else ([S.active_name(p)] if S.active_name(p) else sorted(p.config["orchestration"]["strategies"]))
        rs = [S.validate(p, n) for n in names]
        hw = H.warnings(p)
        if a.json:
            out_json({"results": rs, "harness_warnings": hw})
        else:
            for r in rs:
                print((green("✓ ") if r["ok"] else red("✗ ")) + r["strategy"])
                for e in r["errors"]:
                    print(red("    ✗ ") + e)
                for w in r["warnings"]:
                    print(yellow("    ⚠ ") + w)
            for w in hw:
                print(yellow("⚠ ") + w)
        return 0 if all(r["ok"] for r in rs) else 1
    if act == "route":
        if not name:
            raise ResearchError("route needs a task id: research strategy route T-001 [--strategy NAME] [--level L]")
        r = S.route_task(p, p.get("task", normalize_id(name, "T")), O.ledger(p, normalize_id(name, "T")),
                         level=a.level, strategy=a.strategy, profile=a.profile, model=a.agent_model)
        if a.json:
            out_json(r)
        else:
            print(f"{r['task']}: {r['level']} → {r['profile']} ({r['model'] or 'cli default'}) as {r['role']}")
            print(dim(f"  strategy {r['strategy']}@{r['fingerprint']} · class {r['class']} · {r['why']}"))
            if r.get("warning"):
                print(yellow("⚠ " + r["warning"]))
        return 0
    # use
    if not name:
        raise ResearchError("use needs a strategy name: research strategy use balanced_first")
    try:
        r = S.use(p, name, a.dry_run)
    except ResearchError as e:   # an invalid strategy is reported (exit 1), never activated
        v = S.validate(p, name)
        if a.json:
            out_json({"applied": False, **v})
        else:
            for err in v["errors"] or [str(e)]:
                print(red("✗ ") + err)
        return 1
    _done(a, r, msg=f"{'would switch' if a.dry_run else 'strategy'}: {r['previous'] or 'legacy'} → {name}" + dim("  (running sessions keep their snapshot)"))
    return 0


def _contract_fields(a) -> dict:
    m = {"objective": a.objective, "context": a.context, "expected_outputs": a.expected_output, "allowed_files": a.allowed_file,
         "constraints": a.constraint, "verification": a.require}
    return {k: v for k, v in m.items() if v}


@mut
def cmd_hook(a, p) -> int:
    """`research hook list|add|enable|disable|approve|revoke|run|results` — project harness hooks."""
    from research import hooks as HK
    act = a.action
    if act == "list":
        r = HK.listing(p)
        if a.json:
            out_json(r)
            return 0
        if not r:
            print(dim("no hooks. Register one: research hook add NAME --stage post_implementation --command \"pytest tests/oracle -q\""))
        for h in r:
            st = green("approved") if h["approved"] else yellow("needs approval")
            last = h["last"]
            print(f"  {h['name'].ljust(16)} {h['stage'].ljust(20)} {st if h['enabled'] else dim('disabled')}  "
                  + (dim(("passed" if last["ok"] else "FAILED") + " " + last["ts"][:16]) if last else dim("never run")))
            print(dim(f"      {h['command']}  (timeout {h['timeout']:g}s{'' if h['required'] else ', advisory'})"))
        return 0
    if act == "recommend":
        r = HK.recommended(p)
        if a.json:
            out_json(r)
            return 0
        print(dim("detected: " + (", ".join(r["stacks"]) or "nothing yet (no code in this checkout)")))
        for h in r["hooks"]:
            st = green("approved") if h["approved"] else yellow("installed, needs approval") if h["installed"] else (dim("not installed") if h["applies"] else dim("not applicable here"))
            print(f"  {h['name'].ljust(20)} {h['stage'].ljust(20)} {h['tier'].ljust(6)} {st}")
            print(dim(f"      {h['description']}"))
        print(dim("`research hook install` adds every cheap hook that applies here; `research hook install NAME` adds one (costly ones such as tests run project code, so approve them deliberately); add --approve to approve as you install"))
        return 0
    if act == "install":
        r = HK.install(p, [a.name] if a.name else None, a.approve)
        _done(a, r, msg=("added: " + ", ".join(r["added"]) if r["added"] else "nothing to add")
              + (f"; approved: {', '.join(r['approved'])}" if r["approved"] else "; they run only after `research hook approve`"))
        return 0
    if act == "results":
        r = HK.results(p, a.name, a.limit)
        if a.json:
            out_json(r)
        for x in ([] if a.json else r):
            print(f"{x['ts'][:19]} {x['hook']} {green('ok') if x['ok'] else red('FAIL')} {x['seconds']}s" + ("" if x["ok"] else "\n" + x["output"][-600:]))
        return 0
    if act == "add":
        if not (a.name and a.stage and a.command):
            raise ResearchError("add needs NAME --stage STAGE --command CMD")
        r = HK.add(p, a.name, a.stage, a.command, a.timeout, not a.advisory, a.description)
        _done(a, r, msg=f"hook {a.name} registered; it will not run until a human runs `research hook approve {a.name}`")
        return 0
    if act in ("enable", "disable"):
        _done(a, HK.set_enabled(p, a.name, act == "enable"), msg=f"hook {a.name} {act}d")
        return 0
    if act == "approve":
        r = HK.approve(p, [a.name] if a.name else None)
        _done(a, r, msg="approved: " + ", ".join(r["approved"]))
        return 0
    if act == "revoke":
        _done(a, HK.revoke(p, a.name), msg=f"approval for {a.name} revoked")
        return 0
    r = HK.run(p, a.stage or "post_implementation", a.task, a.experiment, names=[a.name] if a.name else None)
    if a.json:
        out_json(r)
    else:
        for x in r["ran"]:
            print((green("✓ ") if x["ok"] else red("✗ ")) + f"{x['hook']} ({x['seconds']}s)" + ("" if x["ok"] else "\n" + x["output"][-800:]))
        for s_ in r["skipped"]:
            print(yellow(f"⚠ {s_['hook']} skipped: {s_['reason']}"))
    return 0 if r["ok"] else 1


def cmd_usage(a, p) -> int:
    from research import usage as U
    if a.action == "import":
        parser = U.PARSERS.get(a.parser)
        if not parser or not a.file:
            raise ResearchError("usage import FILE [--parser claude_json] [--task T-…] [--level L]")
        with open(a.file, encoding="utf-8") as f:
            parsed = parser(f.read())
        rec = U.record(p, a.parser, parsed, task=a.task, level=a.level, strategy=a.strategy, model=a.agent_model)
        _done(a, rec or {"recorded": False}, msg="usage recorded" if rec else "that file reports no usage; nothing recorded")
        return 0 if rec else 1
    r = U.summary(p, a.by, a.task)
    if a.json:
        out_json(r)
        return 0
    if not r["groups"]:
        print(dim("no usage recorded yet. `orchestrate dispatch --headless` records it when the provider reports it."))
    for k, g in sorted(r["groups"].items()):
        def f(m):
            x = g[m]
            return "—" if x["total"] is None else f"{x['total']:,.4g}" + dim(f" ({x['reported']}/{g['records']})")
        print(f"  {k.ljust(16)} {g['records']} records · {g['tasks']} tasks · in {f('input_tokens')} · out {f('output_tokens')} · "
              f"cache r/w {f('cache_read_tokens')}/{f('cache_write_tokens')} · cost $ {f('cost_usd')}")
    print(dim("  (n/m = records that reported the field; missing is never counted as zero)"))
    return 0


def _orch_task(p, a):
    return p.get("task", normalize_id(a.task, "T"))


@mut
def cmd_orchestrate(a, p) -> int:
    from research import orchestrate as O
    act = a.action
    if act == "route":
        r = O.route(p, _orch_task(p, a), a.tier, a.available, a.strategy, a.profile, a.agent_model)
    elif act == "dispatch":
        r = O.dispatch_task(p, a.task, a.tier, a.available, a.allow_unfrozen, a.strategy, a.profile, a.agent_model, a.headless)
        if a.headless:
            from research import agents as AG
            h = AG.run_headless(p, r)
            _done(a, h, msg=f"headless session finished (exit {h['exit_code']}); usage: {h['usage'] if isinstance(h['usage'], str) else 'recorded'}")
            if not a.json:
                print(h["result"])
            return 124 if h["timed_out"] else (h["exit_code"] if h["exit_code"] is not None else 1)
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
        r = O.record_attempt(p, a.task, a.tier, a.outcome, a.category, a.note, json.loads(a.usage) if a.usage else None)
    elif act == "correct-test":
        r = O.correct_test(p, a.task, a.rest[0], a.reason, a.tier)
    elif act == "delegate":
        r = O.delegate(p, a.task, a.rest[0], a.tier, a.goal, a.acceptance, a.check)
        cf = _contract_fields(a)
        if cf:
            O.set_contract(p, r["id"], **cf)
    elif act == "contract":
        cf = _contract_fields(a)
        r = O.set_contract(p, a.task, **cf) if cf else {"task": a.task, "contract": O.ledger(p, a.task).get("contract") or {}}
    elif act == "verify":
        r = O.verify_work(p, a.task, a.tier, a.record)
        if not a.json:
            print((green("✓ ") + "verified") if r["passed"] else (red("✗ ") + "not verified"))
            for d in r["diagnostics"]:
                print("  " + d.replace("\n", "\n    "))
            for s_ in r["hooks_skipped"]:
                print(yellow(f"  ⚠ hook {s_['hook']} skipped: {s_['reason']}"))
            if r.get("next"):
                print(dim(f"  next: {r['next']['action']}" + (f" → {r['next']['tier']}" if r["next"].get("tier") else "")))
            return 0 if r["passed"] else 1
    elif act == "pin":
        r = O.set_override(p, a.task, a.level, a.profile, a.agent_model, a.clear)
    elif act == "status":
        r = O.status(p, a.task)
    else:
        raise ResearchError("unknown action")
    _done(a, r, msg=json.dumps(r, default=str) if not a.json else None)
    return (0 if r["passed"] else 1) if act == "verify" else 0


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


def cmd_execution(a, p) -> int:
    """Observe and control launched agent sessions. `completed` = the provider exited 0; verification is separate."""
    from research import agents as AG, execution as EX
    act = a.action
    if act == "list":
        rows = [{**{k: r.get(k) for k in ("id", "task", "state", "mode", "exit_code", "prepared_at")}, "profile": (r.get("request") or {}).get("profile"),
                 "model": (r.get("request") or {}).get("model")} for r in EX.list_all(p, getattr(a, "task", None))]
        if a.json:
            out_json(rows)
        for r in ([] if a.json else rows):
            print(f"  {r['id'][:8]}  {r['task']}  {bold(r['state'])}  {r['mode']}  {r['profile']}/{r['model'] or 'default'}")
        return 0
    ident = _exec_id(p, a.id)
    if act == "show":
        out_json(EX.show(p, ident))
    elif act == "logs":
        lg = EX.logs(p, ident)
        out_json(lg) if a.json else print((lg["out"] or "(no captured stdout: interactive sessions write to their terminal)") + (("\n[stderr]\n" + lg["err"]) if lg["err"] else ""))
    elif act == "stop":
        out_json(EX.stop(p, ident)) if a.json else print(green("✓ ") + "stop requested; the state becomes `cancelled` when the process exits")
    elif act == "retry":
        d = AG.redispatch(p, ident, a.profile, a.agent_model, getattr(a, "headless", None) or None)
        out_json(d) if a.json else print(green("✓ ") + f"prepared {d['execution_id'][:8]} ({d['profile']}); start it with: {d['shell']}")
    return 0


def _exec_id(p, prefix):
    from research import execution as EX
    m = [r["id"] for r in EX.list_all(p) if r["id"].startswith(prefix)]
    if len(m) != 1:
        raise ResearchError(f"{'No' if not m else 'Several'} executions match {prefix!r}", hint="`research execution list` shows ids.")
    return m[0]


def cmd_provider(a, p) -> int:
    """Agent providers: readiness, default, MCP registration (verified from an experiment worktree)."""
    from research import providers as PR, workspace as W
    profs = (p.config.get("agents") or {}).get("profiles") or {}
    if a.action == "list":
        rows = PR.table(p)
        default = (p.config.get("agents") or {}).get("default_profile")
        if a.json:
            out_json({"default": default, "providers": rows})
        for r in ([] if a.json else rows):
            print(f"  {bold(r['name'])}{' (default)' if r['name'] == default else ''}  {r['version'] or red('not installed')}  "
                  f"mcp={r['mcp_registered']}  headless={r['capabilities']['headless']}  effort={r['capabilities']['effort']}")
    elif a.action == "set-default":
        from research import registry
        registry.apply(p, {"agents.default_profile": a.name}, reason="provider set-default")
        print(green("✓ ") + f"default agent is now {a.name}; future dispatches only")
    elif a.action == "mcp":
        prof = profs.get(a.name) or {}
        kind = PR.provider_of(prof, a.name)
        if a.apply:
            r = PR.mcp_install(kind, p.root)
            print(green("✓ ") + f"registered for {kind}: {r['where']} ({r['scope']} scope)")
        ws = W.list_workspaces(p)
        cwd = a.cwd or (ws[0]["path"] if ws else p.root)
        v = PR.mcp_verify(p.root, cwd)
        out_json(v) if a.json else print((green("✓ ") if v["ok"] else red("✗ ")) + f"MCP from {cwd}: " + (f"{v['tools']} tools" if v["ok"] else v["problem"]))
        return 0 if v["ok"] else 1
    return 0


def cmd_dispatch(a, p) -> int:
    from research import agents as AG
    target = getattr(a, "target", None)
    d = AG.dispatch(p, getattr(a, "role", None), target, getattr(a, "objective", None), a.profile, a.agent_model,
                    getattr(a, "instructions", None), strategy=getattr(a, "strategy", None), level=getattr(a, "level", None),
                    headless=getattr(a, "headless", False), require_skills=getattr(a, "require_skill", None), effort=getattr(a, "effort", None))
    if d.get("problem") and not a.print:
        print(red("✗ ") + d["problem"], file=sys.stderr)
        return 127
    if d["headless"] and d.get("execution_id") and not a.print:
        import subprocess
        from research import execution as EX
        root = p.root
        p.close()
        rc = subprocess.call(d["argv"], cwd=d["cwd"], env={**os.environ, **d["env"]})
        with Project(root) as q:
            rec = EX.show(q, d["execution_id"])
            lg = EX.logs(q, d["execution_id"])
        if a.json:
            out_json({**d, "execution": rec, "logs": lg})
        else:
            print(lg["out"] or "")
            if lg["err"]:
                print(lg["err"], file=sys.stderr)
            print(dim(f"execution {d['execution_id'][:8]}: {rec['state']} (exit {rec.get('exit_code')}); completed means the provider exited 0, "
                      "not that the task is verified: run `research task verify`"), file=sys.stderr)
        return rc
    if d["headless"] and not a.print:
        h = AG.run_headless(p, d)
        if a.json:
            out_json({**d, "run": h})
        else:
            print(h["result"])
            print(dim("usage: " + (h["usage"] if isinstance(h["usage"], str) else "recorded in .research/usage.jsonl")), file=sys.stderr)
        return 124 if h["timed_out"] else (h["exit_code"] if h["exit_code"] is not None else 1)
    if a.json:
        out_json(d)
        return 0
    print(green("✓ ") + f"{d['profile']}/{d['role']} brief → {d['brief']}", file=sys.stderr)
    if a.print or not d["available"]:
        if not d["available"]:
            print(yellow(f"⚠ `{d['provider_argv'][0]}` is not on PATH; run this where it is:"), file=sys.stderr)
        print(d["shell"])
        return 0
    import subprocess
    root = d.get("cwd") or p.root
    p.close()
    return subprocess.call(d["argv"], cwd=root, env={**os.environ, **d["env"]})


def cmd_bin(a, p) -> int:
    from research import localbin
    name = getattr(a, "name", None) or "research"
    if a.action == "link":
        r = localbin.link(p.root, name) or {}     # links only; the launcher may be the very copy running now, which cannot reinstall itself
        if not r:
            raise ResearchError("No link made: run this in the repository's main checkout (not an experiment worktree), after `research bin install`.")
        _done(a, r, msg=f"{os.path.relpath(r['path'], os.getcwd())} -> {r['target']}" + ("" if r["created"] else " (already linked)"))
        return 0
    if a.action == "unlink":
        r = localbin.unlink(p.root, name)
        _done(a, r, msg=("removed " if r["removed"] else "no Research OS link at ") + str(r["path"]))
        return 0
    r = localbin.status(p.root) if a.action == "status" else localbin.install(p.root, force=a.force)
    if a.json:
        out_json(r)
    elif a.action == "status":
        print((green("✓ ") + r["path"]) if r["exists"] else f"not installed — run: research bin install")
    else:
        print(green("✓ ") + f"{os.path.relpath(r['path'], os.getcwd())} " + ("updated" if r["updated"] else "already up to date"))
        print(dim("  any agent or shell in this project can run it without installing anything"))
        if r.get("link") and r["link"].get("error"):
            print(dim("  no ./research link: " + r["link"]["error"].split(";")[0]))
        elif r.get("link"):
            print(dim(f"  {os.path.relpath(r['link']['path'], os.getcwd())} -> launcher: run `./research …` from the main checkout"))
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


def _dispatch_args(x) -> None:
    x.add_argument("--objective", help="for the planner: what the plan should achieve")
    x.add_argument("--profile", help="agent profile (default from config roles)")
    x.add_argument("--agent-model", help="override the profile's model for this session")
    x.add_argument("--strategy", help="use this allocation strategy for this dispatch only")
    x.add_argument("--level", help="use this strategy level (e.g. fast|balanced|deep) for this dispatch only")
    x.add_argument("--effort", help="reasoning effort for providers that support it (codex, agy)")
    x.add_argument("--headless", action="store_true", help="run non-interactively and record the usage the provider reports")
    x.add_argument("--instructions", help="extra instructions added to the brief")
    x.add_argument("--print", action="store_true", help="print the command instead of starting the agent")
    x.add_argument("--require-skill", action="append", metavar="NAME",
                   help="make this skill mandatory for this dispatch (repeatable): blocked if missing, injected and hash-recorded")


def cmd_agent(a, p) -> int:
    kind = a.action.replace("-", "_")
    f = {k: getattr(a, k, None) for k in ("message", "summary", "path", "name", "value", "run", "job", "category", "commit", "files", "rationale")}
    if kind == "propose":
        if not a.file:
            raise ResearchError("propose needs --file patch.yaml")
        SV._active_attempt(p, a.task)               # only the active execution may propose
        pr = SV.propose(p, a.task, _read(a.file), a.rationale or "", source="agent " + (os.environ.get("RESEARCH_EXECUTION", "")[:8]))
        SV.report(p, a.task, "propose", message=f"{pr['id']} {pr['classification']} {pr['status']}")
        return _emit(a, {k: pr[k] for k in ("id", "classification", "status")}, lambda d: [green("✓ ") + f"{d['id']} ({d['classification']}): {d['status']}"
                     + ("" if d["status"] == "applied" else " (waits for approval)")])
    if kind == "finish":
        kind = "finish"
        f["message"] = f["message"] or "finished; verification requested"
    f = {k: v for k, v in f.items() if v is not None}
    return _emit(a, SV.report(p, a.task, {"hpc_job": "hpc_job", "report": "report"}.get(kind, kind), **f),
                 lambda d: [green("✓ ") + f"{d['recorded']} recorded for {d['task']} (attempt {d['attempt']})"])


cmd_agent = mut(cmd_agent)


def _dispatch_cmd_args(x) -> None:
    x.add_argument("role", choices=["planner", "implementer", "verifier", "analyst"])
    x.add_argument("target", nargs="?", help="T-, F-, EXP-, PLAN- … id")
    _dispatch_args(x)


def _execution_args(x) -> None:
    x.add_argument("action", choices=["list", "show", "logs", "stop", "retry"])
    x.add_argument("id", nargs="?", help="execution id (a unique prefix is enough)")
    x.add_argument("--task", help="list: only this task")
    x.add_argument("--profile", help="retry: continue with this agent profile instead")
    x.add_argument("--agent-model", help="retry: model for the new session")
    x.add_argument("--headless", action="store_true", help="retry: run non-interactively")


def _provider_args(x) -> None:
    x.add_argument("action", choices=["list", "set-default", "mcp"])
    x.add_argument("name", nargs="?")
    x.add_argument("--apply", action="store_true", help="mcp: write the registration (then verify it)")
    x.add_argument("--cwd", help="mcp: directory to verify from (default: first experiment worktree)")


def _usage_args(x) -> None:
    x.add_argument("action", nargs="?", choices=["summary", "import"], default="summary")
    x.add_argument("file", nargs="?")
    x.add_argument("--by", default="strategy")
    x.add_argument("--task")
    x.add_argument("--parser", default="claude_json")
    x.add_argument("--level")
    x.add_argument("--strategy")
    x.add_argument("--agent-model")


def attach(sub, add, group) -> None:
    ag = group("agent", "agent sessions: dispatch | profiles | providers | executions | instructions | setup | usage; "
                        "and structured reports from a supervised agent (progress | milestone | … | finish)")
    x = add(ag, "dispatch", cmd_dispatch, "hand work to an agent CLI: research agent dispatch verifier F-003")
    _dispatch_cmd_args(x)
    _dispatch_cmd_args(add(sub, "dispatch", cmd_dispatch, None))                 # hidden alias (old name)
    x = add(ag, "executions", cmd_execution, "launched agent sessions: list | show | logs | stop | retry (completed ≠ verified)")
    _execution_args(x)
    _execution_args(add(sub, "execution", cmd_execution, None, aliases=["exec"]))
    x = add(ag, "providers", cmd_provider, "agent providers: list | set-default NAME | mcp NAME [--apply]")
    _provider_args(x)
    _provider_args(add(sub, "provider", cmd_provider, None))
    add(ag, "profiles", cmd_agents, "agent profiles and roles (config.yaml → agents)")
    add(sub, "agents", cmd_agents, None)
    _usage_args(add(ag, "usage", cmd_usage, "model usage: `agent usage [--by strategy|model|task|level]` · `agent usage import FILE`"))
    _usage_args(add(sub, "usage", cmd_usage, None))
    x = add(ag, "instructions", cmd_agents_md, "create/refresh the research block in AGENTS.md")
    _agents_md_args(x)
    _agents_md_args(add(sub, "agents-md", cmd_agents_md, None))
    x = add(ag, "setup", cmd_setup, "set up agent files for a provider: research agent setup PROVIDER --preset scientific [--apply]")
    x.set_defaults(what="agents")
    x.add_argument("provider", nargs="?", choices=PV.KNOWN, default=PV.KNOWN[0], help="one of " + ", ".join(PV.KNOWN))
    _setup_args(x)
    x = add(sub, "setup", cmd_setup, None)                                     # hidden: `setup agents --provider NAME`
    x.add_argument("what", choices=["agents"])
    x.add_argument("--provider", default=PV.KNOWN[0])
    _setup_args(x)
    x = add(sub, "bin", cmd_bin, None)                       # hidden: `doctor --fix bin.stale` and the launcher use it
    x.add_argument("action", nargs="?", choices=["install", "status", "link", "unlink"], default="install",
                   help="link/unlink: the local ./research symlink in the main checkout (git-ignored); install also creates it")
    x.add_argument("--force", action="store_true", help="re-copy even if up to date")
    x.add_argument("--name", help="link/unlink: file name of the link in the main checkout (default: research)")
    x = add(sub, "mcp", cmd_mcp, "MCP server for agents (stdio); `research mcp install` registers it")
    x.add_argument("action", nargs="?", choices=["serve", "install"], default="serve")
    x.add_argument("--client", action="append", choices=["claude", "vscode", "cursor", "gemini", "codex"],
                   help="with install: which clients (default: claude + vscode)")
    x = add(sub, "orchestrate", cmd_orchestrate,
            "route | dispatch | freeze | attempt | correct-test | delegate | status | contract | verify | pin")
    x.add_argument("action", choices=["route", "dispatch", "freeze", "attempt", "correct-test", "delegate", "status", "contract", "verify", "pin"])
    x.add_argument("task", help="task id (parent task for delegate)")
    x.add_argument("rest", nargs="*", help="freeze: test files · correct-test: file · delegate: title")
    x.add_argument("--tier", help="legacy: haiku|sonnet|opus · with an active strategy: one of its levels")
    x.add_argument("--strategy", help="route/dispatch with this strategy for this call only")
    x.add_argument("--profile", help="explicit agent profile (beats strategy routing)")
    x.add_argument("--agent-model", help="explicit model (beats strategy routing)")
    x.add_argument("--headless", action="store_true", help="dispatch: run the session non-interactively and record the usage the provider reports")
    x.add_argument("--record", action="store_true", help="verify: log the result as an attempt and return the bounded next step")
    x.add_argument("--clear", action="store_true", help="pin: remove the task's model override")
    x.add_argument("--level", help="pin: strategy level for this task's implementer")
    x.add_argument("--objective", help="contract: what the worker must achieve")
    x.add_argument("--context", help="contract: background the worker needs")
    x.add_argument("--expected-output", action="append", help="contract: a file or result the worker must produce")
    x.add_argument("--allowed-file", action="append", help="contract: file/glob/dir the worker may modify (everything else is a violation)")
    x.add_argument("--constraint", action="append", help="contract: a rule the worker must respect")
    x.add_argument("--require", action="append", help="contract: verification the worker must satisfy")
    x.add_argument("--usage", metavar="JSON", help="attempt: usage the caller really has, e.g. '{\"input_tokens\": 1200}'")
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
    x = add(sub, "strategy", cmd_strategy, "model-allocation strategies: list | show [NAME] | use NAME | validate [NAME] | route T-…")
    x.add_argument("action", choices=["list", "show", "use", "validate", "route"])
    x.add_argument("name", nargs="?", help="strategy name (route: task id)")
    x.add_argument("--dry-run", action="store_true", help="use: validate and report, change nothing")
    x.add_argument("--strategy", help="route: evaluate under this strategy instead of the active one")
    x.add_argument("--level")
    x.add_argument("--profile")
    x.add_argument("--agent-model")
    x = add(sub, "hook", cmd_hook, "project harness hooks: list | add | enable | disable | approve | revoke | run | results | recommend | install")
    x.add_argument("action", choices=["list", "add", "enable", "disable", "approve", "revoke", "run", "results", "recommend", "install"])
    x.add_argument("name", nargs="?")
    x.add_argument("--approve", action="store_true", help="install: approve what was installed (a human decision)")
    x.add_argument("--stage", help="add/run: preflight | post_implementation | pre_experiment | post_experiment")
    x.add_argument("--command")
    x.add_argument("--timeout", type=float)
    x.add_argument("--advisory", action="store_true", help="add: a failure is reported but does not block")
    x.add_argument("--description")
    x.add_argument("--task")
    x.add_argument("--experiment")
    x.add_argument("--limit", type=int, default=10)
    g = ag
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
