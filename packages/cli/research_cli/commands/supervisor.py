"""CLI for plan YAML, plan runs, the supervisor, proposals and structured agent reporting. Thin wrappers over research.supervisor
and research.planspec; the VS Code UI, RPC and MCP call the same functions."""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from research import planspec as PS, providers as PV, supervisor as SV
from research.util import PolicyBlocked, ResearchError

from . import mut
from ..output import bold, dim, green, kv, out_json, red, yellow


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
        out_json(obj)
    elif lines:
        for l in lines(obj):
            print(l)
    return 0


def _ops_lines(d) -> list:
    out = []
    for pl in d.get("plans", []):
        out.append(bold(f"plan {pl['plan']}") + dim(f"  (ledger revision {pl.get('revision')})" if pl.get("revision") else "  (new)"))
        for o in pl["ops"]:
            f = o.get("fields")
            out.append(f"  + {o['op']:<18} {o.get('key')}" + (f"  [{', '.join(f)}]" if f else ""))
        if not pl["ops"]:
            out.append(dim("  no changes"))
    for w in d.get("warnings", []):
        out.append(yellow("  ! " + w))
    for c in d.get("conflicts", []):
        out.append(red("  ✗ " + c))
    return out


# ----------------------------------------------------------------------------------------------- plan yaml
def cmd_plan_yaml(a, p=None) -> int:
    act = a.action
    if act == "template":
        text = PS.TEMPLATE
        if a.out:
            if os.path.exists(a.out) and not a.force:
                raise ResearchError(f"{a.out} exists; pass --force to overwrite")
            open(a.out, "w", encoding="utf-8").write(text)
            return _emit(a, {"wrote": a.out}, lambda d: [green("✓ ") + f"template written to {d['wrote']}"])
        print(text, end="")
        return 0
    if act == "export":
        if not a.target:
            raise ResearchError("export needs a plan key or id: research plan yaml export PLAN-001 -o plan.yaml")
        text = PS.export(p, a.target, scope="partial" if a.partial else "full", experiment=a.experiment, task=a.task)
        if a.out:
            open(a.out, "w", encoding="utf-8").write(text)
            return _emit(a, {"wrote": a.out}, lambda d: [green("✓ ") + f"exported to {d['wrote']}"])
        print(text, end="")
        return 0
    if not a.target:
        raise ResearchError(f"{act} needs a YAML file (or - for stdin)")
    text = _read(a.target)
    if act == "validate":
        r = PS.validate(text, p)
        return _emit(a, r, lambda d: [green("✓ ") + f"valid: {len(d['plans'])} plan(s), {d['experiments']} experiment(s), {d['tasks']} task(s)"]
                     + [yellow("  ! " + w) for w in d.get("warnings", [])])
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
                out.append((green("✓ ") + f"{pl['plan']} → {pl['id']} revision {pl['revision']}: ") + (f"{pl.get('operations')} operation(s)" if pl["changed"] else "no changes"))
                for w in pl.get("warnings", []):
                    out.append(yellow("  ! " + w))
            return out
        return _emit(a, r, lines)
    raise ResearchError(f"unknown yaml action {act}")


cmd_plan_yaml = mut(cmd_plan_yaml)


# ----------------------------------------------------------------------------------------------- plan run control
def _status_lines(s) -> list:
    from research import recovery
    h = s.get("health") or {}
    shown = h.get("label") if s["state"] == "running" and h.get("label") else s["state"]
    shown = (red(bold(shown)) if h.get("state") == "halted" else yellow(shown) if h.get("state") == "waiting_for_approval" else shown)
    out = [bold(f"{s['plan']} ({s['plan_id']}) rev {s['revision']}: ") + shown + dim(f"  {s['progress']} verified  attempts {s['budgets'].get('attempts', 0)}/{s['budgets']['max_total_attempts']}")]
    for t in s["tasks"]:
        ex = t.get("execution") or {}
        out.append(f"  {t['id']:<7} {t['phase']:<18} {t['title'][:44]:<44}" + (f" attempt {t['attempts']}" if t["attempts"] else "")
                   + (f" [{ex.get('state')}]" if ex.get("state") and t["phase"] in ("running", "verifying") else "")
                   + (dim(f"  — {t['reason'][:90]}") if t.get("reason") else ""))
        r = t.get("recovery")
        if r and t["phase"] in ("waiting_hpc", "blocked", "attention", "repair_pending"):
            out += [dim("          " + x) for x in recovery.line(r).splitlines()]
    if h.get("state") in ("halted", "waiting_for_approval"):
        out.append((red if h["state"] == "halted" else yellow)(f"  {h['label']}: nothing is running; waiting on " + ", ".join(b["task"] for b in h["blocking"])
                                                              + "   (research supervisor debug · research supervisor instruct T-… -m \"…\")"))
    for it in (s.get("auto_promotion") or {}).get("items") or []:
        if it["status"] == "paused":
            out.append(red(f"  promotion {it['id']} ({it['experiment']}) is PAUSED: {it.get('error') or 'no reason recorded'}"))
            out.append(dim(f"      the verified tasks are untouched; fix it, then: research plan autopromote {s['plan']} retry --item {it['id']}"))
            if it.get("omits"):
                out.append(yellow(f"      its candidate omits {len(it['omits'])} verified commit(s) that were not recorded on their attempts; retry prepares a new candidate with them"))
    cc = s.get("concurrency")
    if cc:
        out.append(dim(f"  parallel agents: {cc['effective']} ({cc['source']}); {cc['running']} running, {cc['ready_waiting']} ready"))
    for pr in s.get("proposals", []):
        out.append(yellow(f"  proposal {pr['id']} ({pr['classification']}) waits for approval: {pr['rationale'][:80]}"))
    if s.get("git_preflight"):
        from research import githygiene as GH
        out += [yellow(l) if l.startswith("Git preflight") else dim(l) for l in GH.lines(s["git_preflight"])]
    d = s["daemon"]
    out.append(dim("  supervisor process: " + (f"running (pid {d['pid']})" if d.get("running") else "not running; `research supervisor start`")))
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
    return _emit(a, r, lambda d: [green("✓ ") + f"{x['id']}: {len(x['commits'])} verified commit(s) from {x['workspace']} (continue with `research promote show {x['id']}`)" for x in d["candidates"]])


def cmd_plan_autopromote(a, p) -> int:
    from research import approval as AP
    pin = AP.read_pin() if a.action == "authorize" and AP.is_set() else None
    r = SV.autopromote(p, a.id, a.action, note=a.note, pin=pin, item=a.item, commit=a.commit, task=a.task)
    def lines(d):
        if a.action == "coverage":
            out = []
            for c in d["coverage"]:
                out.append(bold(f"{c['experiment']} ({c['workspace']})") + dim(f"  base {str(c['base'])[:10]} head {str(c['head'])[:10]}"))
                out += [f"  included    {x['sha'][:10]} {x['task']} attempt {x['attempt']} ({x['basis']})" for x in c["included"]]
                out += [dim(f"  in progress {x['sha'][:10]} {x['task']} (not verified yet; left out)") for x in c["in_progress"]]
                out += [dim(f"  excluded    {x[:10]} (by a human)") for x in c["excluded"]]
                out += [yellow(f"  UNATTRIBUTED {x['sha'][:10]} {x['subject'][:60]} (by {x['author']}): no attempt claims it") for x in c["orphans"]]
                out += [yellow(f"  AMBIGUOUS    {x['sha'][:10]} {x['subject'][:60]} (claimed by {', '.join(x['tasks'])})") for x in c["ambiguous"]]
            return out or [dim("no experiment of this plan has verified work yet")]
        out = [dim("authorization recorded; it applies when you run the plan: `research plan run " + a.id + "`")] if a.action == "authorize" and not SV.load_run(p, SV._plan_state(p, a.id)["plan_id"]) else []
        out += [bold(f"automatic promotion: {d['mode']}") + (f" (authorized by {d['by']})" if d.get("by") else "") + (f" — {d['why']}" if d.get("why") else "")]
        out += [f"  {i['id']} {i['experiment']:<10} {i['status']:<16} {i.get('main_after') or ''}" + (yellow(f"  {i['error'][:100]}") if i.get("error") else "") for i in d["items"]]
        out += [dim(f"  pending {x['experiment']}: {', '.join(x['commits'])}") for x in d["pending"]]
        out += [yellow(f"  {w}: {len(c['orphans'])} unattributed, {len(c['ambiguous'])} ambiguous commit(s) hold promotion: research plan autopromote {a.id} coverage")
                for w, c in (d.get("coverage") or {}).items() if c.get("blocking")]
        return out
    return _emit(a, r, lines)


def cmd_plan_status(a, p) -> int:
    return _emit(a, SV.status(p, a.id), _status_lines)


for _f in (cmd_plan_run, cmd_plan_pause, cmd_plan_resume, cmd_plan_stop, cmd_plan_promote, cmd_plan_autopromote):
    mut(_f)


# ----------------------------------------------------------------------------------------------- supervisor
def _sv_start(a, p) -> int:
    from research import machine
    machine.remember(p.root)
    from research import localbin
    localbin.install(p.root)    # agents the supervisor starts run the CLI through it
    if a.foreground:
        return SV.run_forever(p.root, a.exit_when_idle)
    return _emit(a, SV.daemon_start(p, detach=True, exit_when_idle=a.exit_when_idle),
                 lambda d: [green("✓ ") + (f"supervisor running (pid {d.get('pid')})" if d.get("running") else str(d.get("note")))])


def _daemon_line(d) -> str:
    if not d["running"]:
        return yellow("not running") + dim("  " + (d.get("note") or ""))
    return green(f"running (pid {d['pid']}, heartbeat {d['heartbeat_age_seconds']}s ago)") \
        + (yellow("  restart recommended: " + d["restart_reason"]) if d.get("restart_required") else "")


def _sv_status(a, p) -> int:
    runs = [{"plan": r["plan_key"], "state": r.get("state"), "attempts": r["budgets"].get("attempts", 0)} for r in SV.all_runs(p)]
    return _emit(a, {"daemon": SV.daemon_status(p), "plans": runs}, lambda x: [bold("supervisor ") + _daemon_line(x["daemon"])]
                 + [f"  plan {r['plan']}: {r['state']} ({r['attempts']} attempts)" for r in x["plans"]])


def _reload_lines(d) -> list:
    if not d["valid"]:
        return [red("✗ ") + "settings are invalid; nothing was applied"] + [red("  " + e) for e in d["errors"]]
    out = [(green("✓ ") if d["applied"] else yellow("! ")) + d["note"]]
    for k, label in (("live", "applied live"), ("next_launch", "apply to agents/jobs launched from now on"), ("restart", "need `research supervisor restart`")):
        if d["changed"][k]:
            out.append(f"  {label}: " + ", ".join(d["changed"][k]))
    out += [yellow("  restart required: " + r) for r in d["restart_reasons"]]
    return out


def _sv_reload(a, p) -> int:
    return _emit(a, SV.daemon_reload(p), _reload_lines)


def _sv_restart(a, p) -> int:
    return _emit(a, SV.daemon_restart(p, timeout=float(a.timeout)), lambda d: [(green("✓ ") if d.get("restarted") else yellow("! ")) + (d.get("note") or "")
                 + (f" (pid {d.get('previous_pid')} -> {d.get('pid')})" if d.get("pid") and d.get("previous_pid") else "")])


def _sv_stop(a, p) -> int:
    return _emit(a, SV.daemon_stop(p), lambda d: [green("✓ ") + "stopped" if not d.get("running") else yellow("still running")])


def _sv_tick(a, p) -> int:
    return _emit(a, SV.tick(p), lambda d: [f"{d['plans']} plan(s): launched {len(d['launched'])}, verified {len(d['verified'])}, repairs {len(d['repairs'])}, blocked {len(d['blocked'])}"]
                 + [dim("  " + e) for e in d["events"]])


def _sv_approve(a, p) -> int:
    return _emit(a, SV.approve_task(p, a.target, a.note), lambda d: [green("✓ ") + f"{d['task']} approved to start"])


def _sv_accept(a, p) -> int:
    return _emit(a, SV.accept(p, a.target, a.reason or ""), lambda d: [yellow("✓ ") + f"{d['task']} accepted WITHOUT verification (logged)"])


def _sv_void(a, p) -> int:
    return _emit(a, SV.void_attempts(p, a.target, a.reason or a.note or "attempts voided by a human"),
                 lambda d: [green("✓ ") + f"{d['voided']} attempt(s) of {d['task']} voided (kept as evidence, not counted)"])


def _sv_continue(a, p) -> int:
    if not a.profile:
        raise ResearchError("continue needs --profile (" + ", ".join(PV.KNOWN) + ")")
    return _emit(a, SV.continue_with(p, a.target, a.profile, a.note), lambda d: [green("✓ ") + f"{d['task']} will continue with {d['profile']} as its next attempt, in the same worktree"])


def _sv_providers(a, p) -> int:
    return _emit(a, SV.provider_status(p, a.target), lambda d: [f"{r['provider']:<7} {'installed' if r['installed'] else 'NOT installed':<14} headless={r['headless']} native-resume={r['resume']}" for r in d["providers"]])


def _sv_resolve(a, p) -> int:
    o = "failed" if a.failed else "completed" if a.completed else "retry" if a.retry else "unblock" if a.unblock else None
    if not o:
        raise ResearchError("resolve needs --failed, --completed, --retry or --unblock")
    return _emit(a, SV.resolve(p, a.target, o, a.note), lambda d: [green("✓ ") + f"{d['task']} resolved: {d['outcome']}"])


_SUPERVISOR = {"start": _sv_start, "status": _sv_status, "instruct": lambda a, p: _cmd_instruct(a, p), "debug": lambda a, p: _cmd_debug(a, p),
               "reload": _sv_reload, "restart": _sv_restart, "stop": _sv_stop, "tick": _sv_tick, "approve": _sv_approve, "accept": _sv_accept,
               "void": _sv_void, "continue": _sv_continue, "providers": _sv_providers, "resolve": _sv_resolve}


def cmd_supervisor(a, p) -> int:
    if a.action not in _SUPERVISOR:
        raise ResearchError(f"unknown supervisor action {a.action}")
    return _SUPERVISOR[a.action](a, p)


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
    say(bold(f"{it['id']} {it['task']}: {it['state']}") + (f" — {it['summary']}" if it.get("summary") else ""))
    for line in it.get("preview", []):
        say("  " + line)
    if it["state"] == "proposed":
        say(yellow(f"  needs your approval ({', '.join(it['protected'])}): research intervention approve {it['id']}") if it["protected"]
            else yellow(f"  waiting for approval: research intervention approve {it['id']}"))
    if it["state"] == "failed":
        say(red("  " + str((it["history"][-1] or {}).get("note", ""))))
    if it["state"] in ("approved", "implementing", "verifying"):
        say(dim("  the supervisor is applying it and will resume the task; watch with `research plan status`"))


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
        out = [bold(f"{d['id']} {d['state']}") + dim(f"  model {d['model']}")] + ([d["summary"]] if d.get("summary") else [red(str(d.get("error")))])
        out += [f"  {i + 1}. {o['label']} [{o['class']}]" + (" (recommended)" if d.get("recommended") == i else "") + dim(f"\n     {o['instruction'][:160]}") for i, o in enumerate(d.get("options", []))]
        if d.get("options"):
            out.append(dim("  start one with: research supervisor debug --choose N"))
        return out
    return _emit(a, rec, lines)


def _diagnosis_lines(d) -> list:
    out = [bold(f"{d['id']} on {d['task']}: {d['state']}") + dim(f"  {d['applied']} applied, {d['pending']} not applied")]
    if d.get("error"):
        out.append(yellow("  " + d["error"][:400]))
    out += [f"  {r['n']}. [{'applied' if r['applied'] else 'NOT applied'}] {r['summary']}" for r in d["actions"]]
    out.append((green("  the rest can be applied now") if d["applicable_now"] else red("  the rest cannot be applied: " + str(d["problem"])[:300])))
    out.append("  next: " + d["next"])
    return out


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
    if act == "diagnose":
        return _emit(a, I.diagnose(p, a.id), _diagnosis_lines)
    if act == "retry":
        it = I.retry(p, a.id, a.note)
    elif act == "edit":
        import json as _json
        if not a.n or not a.action_json:
            raise ResearchError("edit needs the action number and --action-json '{\"type\": ...}'")
        it = I.edit_action(p, a.id, a.n, _json.loads(a.action_json), a.note)
    elif act == "approve":
        it = I.approve(p, a.id, a.note)
    elif act == "reject":
        it = I.reject(p, a.id, a.note)
    elif act == "cancel":
        it = I.cancel(p, a.id)
    else:
        it = I.plan_it(p, a.id)
    return _emit(a, it, lambda d: (_show_intervention(d), [])[1])


cmd_intervention = mut(cmd_intervention)

cmd_supervisor = mut(cmd_supervisor)


# ----------------------------------------------------------------------------------------------- proposals
def cmd_proposal(a, p) -> int:
    if a.action == "list":
        rows = SV.list_proposals(p)
        return _emit(a, rows, lambda r: [f"{x['id']}  {x['status']:<9} {x['classification']:<11} {x['plan_key']}  {x['rationale'][:70]}" for x in r] or [dim("no proposals")])
    if a.action == "show":
        for x in SV.list_proposals(p):
            if x["id"] == a.target:
                return _emit(a, x, lambda d: [f"{d['id']} [{d['status']}] {d['classification']} on {d['plan_key']}", f"rationale: {d['rationale']}", "", d["patch"]])
        raise ResearchError(f"No proposal {a.target}")
    if a.action == "experiment":
        r = SV.propose_experiment(p, {k: getattr(a, k, None) for k in SV.EXP_FIELDS}, source=(p.author or {}).get("author_name") or "human")
        return _emit(a, r, lambda d: [green("✓ ") + f"{d['id']} experiment proposal submitted; it waits for the researcher's approval"])
    if a.action == "revise" and not a.target:
        raise ResearchError("Name the proposal")
    r = SV.decide_proposal(p, a.target, {"approve": True, "reject": False}.get(a.action, a.action), a.note, rev=a.rev)
    return _emit(a, r, lambda d: [green("✓ ") + f"{d['id']} {d['status']}"])


def cmd_approval(a, p) -> int:
    from research import approval as AP
    if a.action == "status":
        return _emit(a, AP.status(), lambda d: [("PIN is set" if d["set"] else "No PIN set: protected actions are refused outside a terminal until you run `research approval set-pin` (in a terminal you are asked to create one the first time)")])
    if a.action == "chat-status":
        st = AP.chat_status()
        return _emit(a, st, [("authenticator enrolled" if st["chat_enrolled"] else "no authenticator enrolled (research approval chat-enroll)")
                                                                                + ("; chat approval is DISABLED until you log in with `research approval verify`" if st["chat_disabled"] else "")])
    if p is not None and (p.author or {}).get("author_type") == "agent" or AP.agent_environment():
        raise PolicyBlocked("Only the researcher can set or check the PIN", hint="Submit a proposal; the researcher approves it.")
    if a.action in ("chat-enroll", "chat-revoke"):
        if not AP.interactive():
            raise PolicyBlocked("This needs an interactive terminal", hint="Run it in your own terminal.")
        pin = AP.read_pin()
        if a.action == "chat-revoke":
            AP.chat_revoke(pin)
            return _emit(a, {"chat_enrolled": False}, [green("✓ ") + "authenticator removed; chat approval is off"])

        def show(uri, secret):
            print(f"Add this to your authenticator app (once):\n  {uri}\n  secret: {secret}", file=sys.stderr)
            if a.qr:
                try:
                    import qrcode
                    q = qrcode.QRCode()
                    q.add_data(uri)
                    q.print_ascii(out=sys.stderr)
                except ImportError:
                    print("(--qr needs the `qrcode` package; use the URI or secret above)", file=sys.stderr)
        AP.chat_enroll(pin, lambda: AP.read_pin("Current 6-digit code: "), show)
        return _emit(a, {"chat_enrolled": True}, [green("✓ ") + "authenticator enrolled. Also set governance.approval.chat.enabled: true to use it."])
    old = AP.read_pin("Current PIN: ") if AP.is_set() and a.action == "set-pin" else None
    if a.action == "verify":
        AP.verify(AP.read_pin())
        return _emit(a, {"ok": True}, lambda d: [green("✓ ") + "PIN accepted"])
    new = AP.read_pin("New PIN: ")
    if not new or new != AP.read_pin("Repeat new PIN: "):
        raise ResearchError("The PINs did not match, or there is no interactive terminal")
    AP.set_pin(new, old)
    return _emit(a, {"set": True}, lambda d: [green("✓ ") + "PIN saved on this machine (not in the project, git or agent context)"])


cmd_proposal = mut(cmd_proposal)
cmd_approval = mut(cmd_approval)


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
            out = [bold("project Python ") + (d["python"] or yellow("not configured")) + (f"  ({d['version']})" if d["version"] else "")]
            out += [f"  {'✓' if ok else '✗'} {name}" for name, ok in d["packages"].items()]
            out += [yellow("  ! " + x) for x in d["problems"]]
            if not d["configured"]:
                out.append(dim("  set one: `research env create` (uses uv) or `research config set environment.python /path/to/venv`"))
            return out
        _emit(a, r, lines)
        return 0 if r["ok"] or (not r["configured"] and not r["problems"][1:]) else 1
    if act == "create":
        path = a.path or _default_venv(p)
        pkgs = pk or (p.config.get("environment") or {}).get("packages") or []
        r = EC.create(path, pkgs, a.python)
        p.save_config({"environment.python": r["path"], "environment.packages": list(pkgs)})
        return _emit(a, r, lambda d: [green("✓ ") + f"created {d['path']} with {', '.join(d['packages']) or 'no packages'} and set environment.python"])
    if act == "install":
        return _emit(a, EC.install(p, pk or []), lambda d: [green("✓ ") + "installed " + ", ".join(d["installed"])])
    raise ResearchError(f"unknown env action {act}")


cmd_env = mut(cmd_env)


def _print_promotion(r) -> None:
    print(green("✓ ") + f"{r['id']} is {r['state']}")
    g = r["gates"]
    kv("tests", f"{g['tests']['state']} — {g['tests']['detail']}")
    kv("evidence", f"{g['evidence']['state']} — {g['evidence']['detail']}")
    kv("review", g["review"]["state"])
    kv("approval", g["approval"]["state"])
    kv("candidate", f"{r['branch']} @ {str(g['head'])[:8]}")
    kv("classification", ", ".join(f"{k}={v}" for k, v in r["classification"].items()))
    if r.get("path"):
        kv("worktree", r["path"])


def _promote_list(a, p, PR):
    r = PR.list_candidates(p, include_closed=a.all)
    if not a.json:
        for c in r:
            g = c["gates"]
            print(f"  {c['id']}  {c['state']:<9} tests {g['tests']['state']:<10} review {g['review']['state']:<9} {c.get('title') or ''}")
        if not r:
            print(dim("no promotion candidates — `research promote changes WS-001` shows what could be promoted"))
        return None
    return r


def _promote_diff(a, p, PR):
    r = PR.diff(p, a.target)
    if a.json:
        return r
    print(r["stat"] + "\n" + r["patch"])
    return None


_PROMOTE = {
    "list": _promote_list,
    "changes": lambda a, p, PR: PR.changes(p, a.target),
    "prepare": lambda a, p, PR: PR.prepare(p, a.target, commits=a.commit, paths=a.path, title=a.title, reason=a.reason),
    "show": lambda a, p, PR: PR.show(p, a.target),
    "diff": _promote_diff,
    "verify": lambda a, p, PR: PR.verify(p, a.target, a.command),
    "review": lambda a, p, PR: PR.review(p, a.target, a.verdict, a.note),
    "approve": lambda a, p, PR: PR.approve(p, a.target, a.note),
    "integrate": lambda a, p, PR: PR.integrate(p, a.target, a.confirm),
    "abandon": lambda a, p, PR: PR.abandon(p, a.target, a.reason),
}


def cmd_promote(a, p) -> int:
    from research import promotion as PR
    if a.action not in _PROMOTE:
        raise ResearchError(f"unknown promote action {a.action}")
    r = _PROMOTE[a.action](a, p, PR)
    if r is None:                    # the handler printed it
        return 0
    if a.json:
        out_json(r)
    elif isinstance(r, dict) and "gates" in r:
        _print_promotion(r)
    else:
        print(json.dumps(r, indent=2, default=str))
    return 0


# ----------------------------------------------------------------------------------------------- registration
def attach_plan(g, add) -> None:
    x = add(g, "yaml", cmd_plan_yaml, "YAML plans: template | export | validate | diff | apply",
            needs_project=lambda a: a.action != "template")  # a template needs no project
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
    x = add(g, "autopromote", cmd_plan_autopromote, "unattended code promotion into main (off by default): status | authorize (human, PIN; allowed before `plan run`, starts nothing) | revoke | retry | coverage | attribute | exclude")
    x.add_argument("id"); x.add_argument("action", choices=["status", "authorize", "revoke", "retry", "coverage", "attribute", "exclude"])
    x.add_argument("--note", help="authorize / attribute / exclude: why"); x.add_argument("--item", help="retry: the paused item (AP-001)")
    x.add_argument("--commit", help="attribute / exclude: a commit of the experiment branch"); x.add_argument("--task", help="attribute: the verified task it belongs to (T-003)")
    for name, fn, h in (("run", cmd_plan_run, "let the supervisor execute a plan"), ("pause", cmd_plan_pause, "pause scheduling for a plan"),
                        ("resume", cmd_plan_resume, "resume a paused or blocked plan"), ("stop", cmd_plan_stop, "stop a plan run (history is kept)"),
                        ("status", cmd_plan_status, "supervised status: phases, attempts, budgets")):
        x = add(g, name, fn, h)
        x.add_argument("id", help="plan id (PLAN-001) or key")
        if name == "run":
            x.add_argument("--no-daemon", action="store_true", help="do not start the supervisor process (use `research supervisor tick`)")
        if name == "stop":
            x.add_argument("--cancel-agents", action="store_true", help="also stop agents that are running now")


def attach(sub, add, group) -> None:
    x = add(sub, "intervention", cmd_intervention, "supervisor interventions: list | show | approve | reject | cancel | plan | diagnose | retry | edit")
    x.add_argument("action", choices=["list", "show", "approve", "reject", "cancel", "plan", "diagnose", "retry", "edit"])
    x.add_argument("id", nargs="?", help="INT-001 (list: optionally a task id to filter)")
    x.add_argument("n", nargs="?", type=int, help="edit: the number of the not-yet-applied action to correct")
    x.add_argument("--action-json", help="edit: the corrected action as JSON, e.g. '{\"type\": \"set_max_attempts\", \"value\": 12}'")
    x.add_argument("--note", help="approve: why · reject: reason")
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
    x = add(sub, "approval", cmd_approval, "the approval PIN: status | set-pin (create or change) | verify; chat-enroll | chat-revoke | chat-status (authenticator for chat approval)")
    x.add_argument("action", choices=["status", "set-pin", "verify", "chat-enroll", "chat-revoke", "chat-status"])
    x.add_argument("--qr", action="store_true", help="chat-enroll: also draw a QR code (only if the `qrcode` package is installed)")
    x = add(sub, "promote", cmd_promote, "select experiment code for main: list | changes | prepare | show | diff | verify | review | approve | integrate | abandon",
            aliases=["promotion"])
    x.add_argument("action", choices=["list", "changes", "prepare", "show", "diff", "verify", "review", "approve", "integrate", "abandon"])
    x.add_argument("target", nargs="?", help="WS- id (changes, prepare) or PROMO- id")
    x.add_argument("--commit", action="append", help="prepare: a commit of the workspace to promote (repeatable)")
    x.add_argument("--path", action="append", help="prepare: a committed path to take from the workspace head (repeatable)")
    x.add_argument("--title")
    x.add_argument("--reason")
    x.add_argument("--command", help="verify: test command (default promotion.test_command)")
    x.add_argument("--verdict", default="comment", choices=["approve", "changes", "comment"], help="review: your verdict")
    x.add_argument("--note")
    x.add_argument("--confirm", action="store_true", help="integrate: confirm that main will move")
    x.add_argument("--all", action="store_true", help="list: include promoted and abandoned")
