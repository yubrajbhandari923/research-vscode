"""CLI for plan YAML, plan runs, the supervisor, proposals and structured agent reporting. Thin wrappers over research.supervisor
and research.planspec; the VS Code UI, RPC and MCP call the same functions."""
from __future__ import annotations

import os
import sys
from typing import Any

from research import planspec as PS, supervisor as SV
from research.util import PolicyBlocked, ResearchError

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
        return _emit(a, r, lambda d: [M.green("✓ ") + f"valid: {len(d['plans'])} plan(s), {d['experiments']} experiment(s), {d['tasks']} task(s)"]
                     + [M.yellow("  ! " + w) for w in d.get("warnings", [])])
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
    h = s.get("health") or {}
    shown = h.get("label") if s["state"] == "running" and h.get("label") else s["state"]
    shown = (M.red(M.bold(shown)) if h.get("state") == "halted" else M.yellow(shown) if h.get("state") == "waiting_for_approval" else shown)
    out = [M.bold(f"{s['plan']} ({s['plan_id']}) rev {s['revision']}: ") + shown + M.dim(f"  {s['progress']} verified  attempts {s['budgets'].get('attempts', 0)}/{s['budgets']['max_total_attempts']}")]
    for t in s["tasks"]:
        ex = t.get("execution") or {}
        out.append(f"  {t['id']:<7} {t['phase']:<18} {t['title'][:44]:<44}" + (f" attempt {t['attempts']}" if t["attempts"] else "")
                   + (f" [{ex.get('state')}]" if ex.get("state") and t["phase"] in ("running", "verifying") else "")
                   + (M.dim(f"  — {t['reason'][:90]}") if t.get("reason") else ""))
    if h.get("state") in ("halted", "waiting_for_approval"):
        out.append((M.red if h["state"] == "halted" else M.yellow)(f"  {h['label']}: nothing is running; waiting on " + ", ".join(b["task"] for b in h["blocking"])
                                                              + "   (research supervisor debug · research supervisor instruct T-… -m \"…\")"))
    cc = s.get("concurrency")
    if cc:
        out.append(M.dim(f"  parallel agents: {cc['effective']} ({cc['source']}); {cc['running']} running, {cc['ready_waiting']} ready"))
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


def cmd_plan_autopromote(a, p) -> int:
    from research import approval as AP
    pin = AP.read_pin() if a.action == "authorize" and AP.is_set() else None
    r = SV.autopromote(p, a.id, a.action, note=a.note, pin=pin, item=a.item)
    def lines(d):
        out = [M.bold(f"automatic promotion: {d['mode']}") + (f" (authorized by {d['by']})" if d.get("by") else "") + (f" — {d['why']}" if d.get("why") else "")]
        out += [f"  {i['id']} {i['experiment']:<10} {i['status']:<16} {i.get('main_after') or ''}" + (M.yellow(f"  {i['error'][:100]}") if i.get("error") else "") for i in d["items"]]
        out += [M.dim(f"  pending {x['experiment']}: {', '.join(x['commits'])}") for x in d["pending"]]
        return out
    return _emit(a, r, lines)


def cmd_plan_status(a, p) -> int:
    return _emit(a, SV.status(p, a.id), _status_lines)


for _f in (cmd_plan_run, cmd_plan_pause, cmd_plan_resume, cmd_plan_stop, cmd_plan_promote, cmd_plan_autopromote):
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
            M.bold("supervisor ") + (M.green(f"running (pid {x['daemon']['pid']}, heartbeat {x['daemon']['heartbeat_age_seconds']}s ago)")
                                      + (M.yellow("  restart recommended: " + x["daemon"]["restart_reason"]) if x["daemon"].get("restart_required") else "") if x["daemon"]["running"]
                                      else M.yellow("not running") + M.dim("  " + (x["daemon"].get("note") or "")))] +
            [f"  plan {r['plan']}: {r['state']} ({r['attempts']} attempts)" for r in x["plans"]])
    if act == "instruct":
        return _cmd_instruct(a, p)
    if act == "debug":
        return _cmd_debug(a, p)
    if act == "reload":
        def rl(d):
            if not d["valid"]:
                return [M.red("✗ ") + "settings are invalid; nothing was applied"] + [M.red("  " + e) for e in d["errors"]]
            out = [(M.green("✓ ") if d["applied"] else M.yellow("! ")) + d["note"]]
            for k, label in (("live", "applied live"), ("next_launch", "apply to agents/jobs launched from now on"), ("restart", "need `research supervisor restart`")):
                if d["changed"][k]:
                    out.append(f"  {label}: " + ", ".join(d["changed"][k]))
            out += [M.yellow("  restart required: " + r) for r in d["restart_reasons"]]
            return out
        return _emit(a, SV.daemon_reload(p), rl)
    if act == "restart":
        return _emit(a, SV.daemon_restart(p, timeout=float(a.timeout)), lambda d: [(M.green("✓ ") if d.get("restarted") else M.yellow("! ")) + (d.get("note") or "")
                     + (f" (pid {d.get('previous_pid')} -> {d.get('pid')})" if d.get("pid") and d.get("previous_pid") else "")])
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
    if act == "continue":
        if not a.profile:
            raise ResearchError("continue needs --profile (claude, codex or agy)")
        return _emit(a, SV.continue_with(p, a.target, a.profile, a.note), lambda d: [M.green("✓ ") + f"{d['task']} will continue with {d['profile']} as its next attempt, in the same worktree"])
    if act == "providers":
        return _emit(a, SV.provider_status(p, a.target), lambda d: [f"{r['provider']:<7} {'installed' if r['installed'] else 'NOT installed':<14} headless={r['headless']} native-resume={r['resume']}" for r in d["providers"]])
    if act == "resolve":
        o = "failed" if a.failed else "completed" if a.completed else "retry" if a.retry else "unblock" if a.unblock else None
        if not o:
            raise ResearchError("resolve needs --failed, --completed, --retry or --unblock")
        return _emit(a, SV.resolve(p, a.target, o, a.note), lambda d: [M.green("✓ ") + f"{d['task']} resolved: {d['outcome']}"])
    raise ResearchError(f"unknown supervisor action {act}")


def _read_multiline() -> str:
    import sys
    if sys.stdin.isatty():
        print("Instruction for the supervisor (finish with an empty line):", file=sys.stderr)
    lines = []
    for line in sys.stdin:
        if not line.strip() and lines:
            break
        lines.append(line.rstrip("\n"))
    return "\n".join(lines).strip()


def _show_intervention(it, say=print) -> None:
    say(M.bold(f"{it['id']} {it['task']}: {it['state']}") + (f" — {it['summary']}" if it.get("summary") else ""))
    for line in it.get("preview", []):
        say("  " + line)
    if it["state"] == "proposed":
        say(M.yellow(f"  needs your approval ({', '.join(it['protected'])}): research intervention approve {it['id']}") if it["protected"]
            else M.yellow(f"  waiting for approval: research intervention approve {it['id']}"))
    if it["state"] == "failed":
        say(M.red("  " + str((it["history"][-1] or {}).get("note", ""))))
    if it["state"] in ("approved", "implementing", "verifying"):
        say(M.dim("  the supervisor is applying it and will resume the task; watch with `research plan status`"))


def _cmd_instruct(a, p) -> int:
    import sys
    from research import intervene as I
    if not a.target:
        raise ResearchError("instruct needs a task: research supervisor instruct T-12 --message \"…\"")
    msg = a.message or _read_multiline()
    it = I.request(p, a.target, msg, interrupt=a.interrupt)
    if it["state"] == "proposed" and (a.yes or (sys.stdin.isatty() and sys.stdout.isatty() and not a.json)):
        _show_intervention(it)
        if a.yes or input("Approve and run it? [y/N] ").strip().lower() in ("y", "yes"):
            it = I.approve(p, it["id"], a.note or "approved at the terminal")
    return _emit(a, it, lambda d: (_show_intervention(d), [])[1])


def _cmd_debug(a, p) -> int:
    from research import intervene as I, notify, planspec as PS
    plan = a.target or next((s["plan_key"] for s in PS.all_states(p) if SV.plan_health(p, s, SV.load_run(p, s["plan_id"]) or {})["blocking"]), None)
    if not plan:
        raise ResearchError("Nothing is blocked, so there is nothing to diagnose")
    rec = notify.latest_debug(p) if a.choose and notify.latest_debug(p) else notify.run_debug(p, plan)
    if a.choose:
        if not 1 <= a.choose <= len(rec["options"]):
            raise ResearchError("choose a number from the options shown")
        it = I.request(p, rec["tasks"][0], rec["options"][a.choose - 1]["instruction"], source="debug")
        return _emit(a, it, lambda d: (_show_intervention(d), [])[1])
    def lines(d):
        out = [M.bold(f"{d['id']} {d['state']}") + M.dim(f"  model {d['model']}")] + ([d["summary"]] if d.get("summary") else [M.red(str(d.get("error")))])
        out += [f"  {i + 1}. {o['label']} [{o['class']}]" + (" (recommended)" if d.get("recommended") == i else "") + M.dim(f"\n     {o['instruction'][:160]}") for i, o in enumerate(d.get("options", []))]
        if d.get("options"):
            out.append(M.dim("  start one with: research supervisor debug --choose N"))
        return out
    return _emit(a, rec, lines)


def cmd_intervention(a, p) -> int:
    from research import intervene as I
    act = a.action
    if act == "list":
        return _emit(a, I.list_all(p, a.id if a.id and a.id.startswith("T-") else None),
                     lambda d: [f"{i['id']} {i['task']:<7} {i['state']:<13} {i['instruction'][:70]}" for i in d] or ["no interventions"])
    if not a.id:
        raise ResearchError(f"{act} needs an intervention id (INT-001)")
    if act == "show":
        return _emit(a, I.load(p, a.id), lambda d: (_show_intervention(d), [])[1])
    if act == "approve":
        it = I.approve(p, a.id, a.note)
    elif act == "reject":
        it = I.reject(p, a.id, a.note)
    elif act == "cancel":
        it = I.cancel(p, a.id)
    else:
        it = I.plan_it(p, a.id)
    return _emit(a, it, lambda d: (_show_intervention(d), [])[1])


cmd_intervention = M.mut(cmd_intervention)


def cmd_telegram(a, p) -> int:
    from research import approval as AP, notify
    act = a.action
    if act == "status":
        return _emit(a, notify.status(p), lambda d: [("token saved" if d["token_set"] else "no token") + ", " + ("chat linked" if d["chat_id_set"] else "no chat linked")
                                                      + f", {'enabled' if d['enabled'] else 'disabled'} for this project; stored at {d['stored_at']}"])
    if AP.agent_environment() or (p.author or {}).get("author_type") == "agent":
        raise PolicyBlocked("Only the researcher can configure Telegram")
    if act == "setup":
        token = a.token or AP.read_pin("Bot token (from @BotFather; not shown): ")
        if not token:
            raise ResearchError("No token entered (a terminal is needed, or pass --token)")
        chat = a.chat_id
        if chat is None:
            print("Now open your bot in Telegram and send it any message (waiting up to 60 s)…")
            chat = notify.discover_chat(token.strip(), 60)
            if chat is None:
                raise ResearchError("No message reached the bot; send one and run setup again")
        notify.set_credentials(token, chat)
        p.save_config({"notifications.telegram.enabled": True, "notifications.auto_debug": True})
        notify.send("Research OS is connected. Send /help for commands.")
        p.config = p.load_config()               # pick up the setting just written
        sup = notify.ensure_running(p)
        return _emit(a, {**notify.status(p), "supervisor": sup}, lambda d: [M.green("✓ ") + "Telegram connected; token and chat id are stored only on this machine",
                     M.green("✓ ") + "supervisor " + ("started: it answers your chat and alerts you on halts" if sup["action"] == "start" else "is running: it answers your chat and alerts you on halts")
                     if not sup.get("error") else M.yellow("! start it with `research supervisor start`: " + sup["error"])])
    if act == "test":
        return _emit(a, {"sent": notify.send("Research OS test message. Send /help for commands.")}, lambda d: [M.green("✓ ") + "sent" if d["sent"] else M.yellow("not configured")])
    if act == "remove":
        notify.clear_credentials()
        p.save_config({"notifications.telegram.enabled": False})
        return _emit(a, {"removed": True}, lambda d: [M.green("✓ ") + "credentials deleted and Telegram disabled"])
    if act == "poll":
        return _emit(a, {"handled": notify.poll(p)}, lambda d: [f"{d['handled']} message(s) handled"])
    raise ResearchError(f"unknown telegram action {act}")


cmd_telegram = M.mut(cmd_telegram)
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
    if a.action == "experiment":
        r = SV.propose_experiment(p, {k: getattr(a, k, None) for k in SV.EXP_FIELDS}, source=(p.author or {}).get("author_name") or "human")
        return _emit(a, r, lambda d: [M.green("✓ ") + f"{d['id']} experiment proposal submitted; it waits for the researcher's approval"])
    if a.action == "revise" and not a.target:
        raise ResearchError("Name the proposal")
    r = SV.decide_proposal(p, a.target, {"approve": True, "reject": False}.get(a.action, a.action), a.note, rev=a.rev)
    return _emit(a, r, lambda d: [M.green("✓ ") + f"{d['id']} {d['status']}"])


def cmd_approval(a, p) -> int:
    from research import approval as AP
    if a.action == "status":
        return _emit(a, AP.status(), lambda d: [("PIN is set" if d["set"] else "No PIN set (protected actions are not PIN-gated yet): research approval set-pin")])
    if p is not None and (p.author or {}).get("author_type") == "agent" or AP.agent_environment():
        raise PolicyBlocked("Only the researcher can set or check the PIN", hint="Submit a proposal; the researcher approves it.")
    old = AP.read_pin("Current PIN: ") if AP.is_set() and a.action == "set-pin" else None
    if a.action == "verify":
        AP.verify(AP.read_pin())
        return _emit(a, {"ok": True}, lambda d: [M.green("✓ ") + "PIN accepted"])
    new = AP.read_pin("New PIN: ")
    if not new or new != AP.read_pin("Repeat new PIN: "):
        raise ResearchError("The PINs did not match, or there is no interactive terminal")
    AP.set_pin(new, old)
    return _emit(a, {"set": True}, lambda d: [M.green("✓ ") + "PIN saved on this machine (not in the project, git or agent context)"])


cmd_proposal = M.mut(cmd_proposal)
cmd_approval = M.mut(cmd_approval)


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


# ----------------------------------------------------------------------------------------------- environment
def _default_venv(p) -> str:
    return os.path.join(os.path.dirname(p.root) if p.location.linked else p.root, ".venv")


def cmd_env(a, p) -> int:
    from research import envcheck as EC
    act = a.action
    pk = a.package or None
    if act == "check":
        r = EC.check(p, pk)
        def lines(d):
            out = [M.bold("project Python ") + (d["python"] or M.yellow("not configured")) + (f"  ({d['version']})" if d["version"] else "")]
            out += [f"  {'✓' if ok else '✗'} {name}" for name, ok in d["packages"].items()]
            out += [M.yellow("  ! " + x) for x in d["problems"]]
            if not d["configured"]:
                out.append(M.dim("  set one: `research env create` (uses uv) or `research config set environment.python /path/to/venv`"))
            return out
        _emit(a, r, lines)
        return 0 if r["ok"] or (not r["configured"] and not r["problems"][1:]) else 1
    if act == "create":
        path = a.path or _default_venv(p)
        pkgs = pk or (p.config.get("environment") or {}).get("packages") or []
        r = EC.create(path, pkgs, a.python)
        p.save_config({"environment.python": r["path"], "environment.packages": list(pkgs)})
        return _emit(a, r, lambda d: [M.green("✓ ") + f"created {d['path']} with {', '.join(d['packages']) or 'no packages'} and set environment.python"])
    if act == "install":
        return _emit(a, EC.install(p, pk or []), lambda d: [M.green("✓ ") + "installed " + ", ".join(d["installed"])])
    raise ResearchError(f"unknown env action {act}")


cmd_env = M.mut(cmd_env)


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
    x = add(g, "autopromote", cmd_plan_autopromote, "unattended code promotion into main (off by default): status | authorize (human, PIN) | revoke | retry")
    x.add_argument("id"); x.add_argument("action", choices=["status", "authorize", "revoke", "retry"])
    x.add_argument("--note", help="authorize: why"); x.add_argument("--item", help="retry: the paused item (AP-001)")
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
    x = add(sub, "intervention", cmd_intervention, "supervisor interventions: list | show | approve | reject | cancel | plan")
    x.add_argument("action", choices=["list", "show", "approve", "reject", "cancel", "plan"])
    x.add_argument("id", nargs="?", help="INT-001 (list: optionally a task id to filter)")
    x.add_argument("--note", help="approve: why · reject: reason")
    x = add(sub, "telegram", cmd_telegram, "Telegram alerts and chat commands (opt-in): setup | status | test | remove | poll")
    x.add_argument("action", choices=["setup", "status", "test", "remove", "poll"])
    x.add_argument("--token", help="setup: the bot token (prefer the hidden prompt; arguments are visible in process lists)")
    x.add_argument("--chat-id", dest="chat_id", help="setup: the chat id (default: the first chat that messages the bot)")
    x = add(sub, "env", cmd_env, "the project's Python environment: check | create (uv) | install")
    x.add_argument("action", choices=["check", "create", "install"])
    x.add_argument("--path", help="create: where to create the venv (default: .venv next to main/)")
    x.add_argument("--python", help="create: python version/interpreter for uv (for example 3.12)")
    x.add_argument("--package", "-k", action="append", help="packages (repeatable)")
    x = add(sub, "supervisor", cmd_supervisor, "the persistent supervisor: start | status | stop | instruct T-12 (steer a task in words) | debug [PLAN] (diagnose a halt) | reload (apply settings, agents untouched) | restart (new process, agents untouched) | tick | approve | accept | resolve | continue (hand a task to another agent) | providers")
    x.add_argument("action", choices=["start", "status", "stop", "reload", "restart", "instruct", "debug", "tick", "approve", "accept", "resolve", "void", "continue", "providers"])
    x.add_argument("--profile", help="continue: the agent profile to hand the task to")
    x.add_argument("target", nargs="?", help="approve/accept/resolve: task id")
    x.add_argument("--message", "-m", help="instruct: the instruction (otherwise type it, finish with a blank line)")
    x.add_argument("--interrupt", action="store_true", help="instruct: safely stop the running agent now (its attempt is kept as evidence); default is to queue")
    x.add_argument("--yes", action="store_true", help="instruct: approve the proposal right away if it asks (protected changes still ask for the PIN)")
    x.add_argument("--choose", type=int, help="debug: start an intervention from option N of the diagnosis")
    x.add_argument("--timeout", type=float, default=120.0, help="restart: seconds to wait for the current scheduling pass to finish")
    x.add_argument("--foreground", action="store_true", help="start: run in this terminal instead of detaching")
    x.add_argument("--detach", action="store_true", help="start: detach (the default)")
    x.add_argument("--exit-when-idle", action="store_true", help="start: exit once no plan is running")
    x.add_argument("--note")
    x.add_argument("--reason", help="accept: why the task is accepted without verification")
    x.add_argument("--failed", action="store_true"); x.add_argument("--completed", action="store_true")
    x.add_argument("--retry", action="store_true"); x.add_argument("--unblock", action="store_true")
    x = add(sub, "proposal", cmd_proposal, "proposals: list | show | approve | reject | revise (ask for changes) | experiment (agents: propose one)", aliases=["proposals"])
    x.add_argument("action", choices=["list", "show", "approve", "reject", "revise", "experiment"])
    x.add_argument("target", nargs="?")
    x.add_argument("--note", help="approve/reject/revise: why (required for approval)")
    x.add_argument("--rev", type=int, help="the revision you reviewed; refused if the proposal changed since")
    for f in ("title", "hypothesis", "rationale", "method", "expected_outputs", "related"):
        x.add_argument("--" + f.replace("_", "-"), dest=f, help="experiment proposal: " + f.replace("_", " "))
    x = add(sub, "approval", cmd_approval, "the approval PIN: status | set-pin (create or change) | verify")
    x.add_argument("action", choices=["status", "set-pin", "verify"])
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
