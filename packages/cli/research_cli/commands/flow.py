"""The everyday commands: run a plan file, watch it, and deal with whatever stops.

    research start plans/study.yaml    register (or sync) the plan file and let the supervisor execute it
    research watch                     the plan board, live: what runs, what is stuck, why, and the command when it needs you
    research why T-023                 the facts about a stopped task or promotion (no agent, no tokens); --ask adds the diagnostic agent
    research bump T-023                out of attempts: allow 3 more and retry now
    research retry T-023 · research tell T-023 "..." · research logs T-023|AP-001
    research plan edit|sync|set|file   the plan file is the plan
    research archive PLAN|EXP          out of sight, nothing deleted

They are thin: every one calls the same supervisor, plan-file and board functions the rest of Research OS uses.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from research import archive as AR, board as BD, planfile as PF, planspec as PS, supervisor as SV
from research.util import ResearchError

from . import mut
from ..output import bold, cyan, dim, green, out_json, red, yellow

GLYPH = {"done": ("✓", green), "run": ("●", cyan), "wait": ("·", dim), "you": ("✗", red), "off": ("-", dim)}


def _emit(a, obj: Any, lines) -> int:
    if a.json:
        out_json(obj)
    else:
        for l in lines(obj):
            print(l)
    return 0


def _dur(sec: Optional[float]) -> str:
    if sec is None:
        return ""
    sec = int(max(0, sec))
    return f"{sec // 3600}h{(sec % 3600) // 60:02d}m" if sec >= 3600 else f"{sec // 60}m" if sec >= 60 else f"{sec}s"


def _age(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        from datetime import datetime, timezone
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return time.time() - (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp()
    except ValueError:
        return None


def _bar(done: int, total: int, width: int = 16) -> str:
    n = int(round(width * done / total)) if total else 0
    return green("█" * n) + dim("░" * (width - n))


# ----------------------------------------------------------------------------------------------- the board
def plan_lines(s: Dict[str, Any], wide: int = 100) -> List[str]:
    h = s.get("health") or {}
    label = h.get("label") if s["state"] == "running" and h.get("label") else s["state"]
    label = red(bold(label)) if h.get("state") == "halted" else yellow(label) if h.get("state") in ("waiting_for_approval", "paused", "waiting_for_provider") else green(label) if h.get("state") == "completed" else bold(label)
    b = s["budgets"]
    done, total = (int(x) for x in s["progress"].split("/"))
    wall = f"  ·  {_dur(b.get('wall_seconds'))} of {_dur((b.get('max_wall_minutes') or 0) * 60)}" if b.get("max_wall_minutes") and b.get("wall_seconds") else ""
    out = [bold(f"{s['plan']}") + dim(f" ({s['plan_id']}, rev {s['revision']})") + ("  " + dim("[archived]") if s.get("archived") else "") + "  " + label,
           f"  {_bar(done, total)} {s['progress']} verified  ·  attempts {b.get('attempts', 0)}/{b['max_total_attempts']}" + wall]
    f = s.get("file")
    if f:
        note = {"in_sync": dim("in sync"), "edited": yellow("edited, not applied yet"), "merge": yellow("edited, and the plan changed too: will be merged"),
                "ledger_ahead": dim("being rewritten"), "missing": yellow("missing (research plan sync writes it again)"), "untracked": dim("not written yet (research plan sync)")}[f["status"]]
        if f.get("problem"):
            note = red("cannot be applied: " + f["problem"][0][:wide])
        elif f.get("held"):
            note = yellow("waits for `research plan sync`: " + "; ".join(f["held"])[:wide])
        out.append("  " + dim("file ") + f["path"] + "  " + note)
    for e in s.get("experiments") or []:
        ts = e["tasks"]
        nd = sum(1 for t in ts if t["phase"] == "verified")
        out.append("")
        out.append("  " + bold(e["title"][:60]) + dim(f"  {e['id'] or ''}  {nd}/{len(ts)}") + (dim("  [archived]") if e.get("archived") else ""))
        for t in ts:
            g, col = GLYPH[t["mark"]]
            ex = t.get("execution") or {}
            bits = []
            if t["attempts"]:
                bits.append(f"attempt {t['attempts']}/{t['allowed']}")
            if t["phase"] in ("running", "verifying") and t.get("started"):
                bits.append(_dur(_age(t["started"])))
            if t["phase"] == "running" and ex.get("state") and ex["state"] != "running":
                bits.append(f"agent {ex['state']}, verifying next")
            if t.get("continues_session"):
                bits.append("continues session")
            if t["phase"] in ("waiting",) and t["depends_on"] and not t.get("reason"):
                bits.append("after " + ", ".join(t["depends_on"][:4]))
            if t.get("resume_at"):
                bits.append(f"resumes {t['resume_at'][11:16]}")
            out.append(f"    {col(g)} {t['id']:<6} {t['title'][:46]:<46} {col(t['phase']):<18} " + dim("  ".join(bits)) + dim(f"  {t.get('profile') or ''}"))
            if t.get("activity"):
                out.append(dim(f"        {_dur(t['activity']['seconds_ago'])} ago: ") + t["activity"]["text"] + dim(f"   (research logs {t['id']} -f)"))
            if t.get("reason") and t["mark"] in ("you", "wait", "run"):
                out.append(dim("        " + t["reason"][:wide * 2]))
            if t.get("recovery") and t["phase"] in ("waiting_hpc", "blocked", "attention", "repair_pending"):
                from research import recovery
                out += [dim("        " + x) for x in recovery.line(t["recovery"]).splitlines()]
            for n in t.get("next") or []:
                out.append("        " + yellow("→ ") + bold(n["cmd"]) + dim("   " + n["what"]))
    if s.get("promotions"):
        out.append("")
        out.append("  " + bold("Promotion to main"))
        for it in s["promotions"]:
            col = red if it["status"] in ("paused", "suspended") else green if it["status"] in ("integrated", "noop") else cyan
            line = f"    {it['id']:<7} {it.get('experiment') or '':<10} {col(it['status'])}"
            r = it.get("reviewer")
            if r:
                line += dim(f"  reviewer {r.get('profile')}: {r['state']} for {_dur(r['minutes'] * 60)}" + (f" (attempt {r['attempt']})" if r["attempt"] > 1 else "") + f"  ·  research logs {it['id']}")
            out.append(line)
            if it.get("error") and it["status"] in ("paused", "suspended"):
                out.append(dim("        " + it["error"][:wide * 2]))
            for n in it.get("next") or []:
                out.append("        " + yellow("→ ") + bold(n["cmd"]) + dim("   " + n["what"]))
    wp = s.get("waiting_provider")
    if wp:
        out.append("")
        out.append(yellow(f"  waiting for {wp['provider']} until {time.strftime('%H:%M', time.localtime(wp['until_ts']))}") + dim("  (a usage limit; the supervisor resumes by itself)"))
    return out


def board_lines(d: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    if not d["plans"] and d.get("archived_hidden"):
        out.append(dim("No current plans."))
    elif not d["plans"]:
        out.append(dim("No plans yet. Write one (research plan yaml template -o plans/my-plan.yaml), then: research start plans/my-plan.yaml"))
    for s in d["plans"]:
        out += plan_lines(s) + [""]
    need = d["needs_you"]
    if need:
        out.append(red(bold(f"Needs you ({len(need)})")))
        for i, n in enumerate(need, 1):
            out.append(f"  {i}. {bold(n['ref'])} {n['title'][:60]}" + dim(f"  [{n['plan']}]"))
            if n.get("next"):
                out.append("       " + yellow("→ ") + n["next"][0]["cmd"])
    elif d["plans"]:
        out.append(green("Nothing needs you."))
    dm = d["daemon"]
    running = [s for s in d["plans"] if s["state"] == "running"]
    if dm.get("running"):
        what = (f"in a pass for {_dur(dm['pass_seconds'])}" if dm.get("pass_seconds") and dm["pass_seconds"] > 30 else "idle between passes")
        out.append(dim(f"Supervisor: running (pid {dm['pid']}), {what}" + ("; " + dm["restart_reason"] + ": research supervisor restart" if dm.get("restart_required") else "")))
    else:
        out.append((red if running else dim)("Supervisor: not running" + (" while a plan is running: research supervisor start" if running else "")))
    if d.get("archived_hidden"):
        out.append(dim(f"{d['archived_hidden']} archived plan(s) hidden (--all shows them)"))
    return out


def cmd_plan_status(a, p) -> int:
    d = BD.data(p, getattr(a, "id", None) or None, include_archived=getattr(a, "all", False))
    if a.json:
        out_json(d["plans"][0] if getattr(a, "id", None) else d)
        return 0
    for l in (plan_lines(d["plans"][0]) + [""] + board_lines({**d, "plans": []})[0:] if getattr(a, "id", None) else board_lines(d)):
        print(l)
    return 0


def cmd_watch(a, p) -> int:
    """The board, redrawn. Read-only: it never changes anything, so it is safe to leave open in a pane."""
    from research.store import Project
    root = p.root
    try:
        while True:
            with Project(root, sync=False) as q:
                d = BD.data(q, a.id or None, include_archived=a.all)
            lines = board_lines(d)
            if sys.stdout.isatty():
                sys.stdout.write("\x1b[H\x1b[2J")
            print(dim(time.strftime("%H:%M:%S") + f"  ·  refreshes every {int(a.interval)} s  ·  ctrl+c to leave"))
            print("\n".join(lines))
            sys.stdout.flush()
            if a.once:
                return 0
            time.sleep(max(1.0, a.interval))
    except KeyboardInterrupt:
        return 0


# ----------------------------------------------------------------------------------------------- run
def cmd_start(a, p) -> int:
    target = a.target
    msgs: List[str] = []
    if target and (os.path.isfile(target) or target.endswith((".yaml", ".yml"))):
        r = PF.add(p, target)
        key = r["plan"]
        msgs.append(green("✓ ") + f"{r['file']['path']}: " + {"created": f"plan {key} registered ({r['operations']} entries)", "applied": f"{r['operations']} change(s) applied",
                                                               "merged": f"{r['operations']} change(s) merged", "none": "no changes", "written": "written"}.get(r["action"], r["action"]))
    elif target:
        key = PS._state_for(p, target)["plan_key"]
        r = PF.sync(p, key)[0]
        if r["action"] in ("applied", "merged"):
            msgs.append(green("✓ ") + f"{r['file']['path']}: {r['operations']} change(s) applied")
    else:
        live = [s for s in PS.all_states(p) if not s.get("archived") and (SV.load_run(p, s["plan_id"]) or {}).get("state") != "completed"]
        if len(live) != 1:
            raise ResearchError("say which plan to start" + (": " + ", ".join(s["plan_key"] for s in live) if live else " (there is none yet)"),
                                hint="research start plans/my-plan.yaml   (a new one: research plan yaml template -o plans/my-plan.yaml)")
        key = live[0]["plan_key"]
        PF.sync(p, key)
    if a.unattended:
        from research import approval, profiles
        pv = profiles.preview(p, "overnight")
        if pv.get("changes"):
            pin = approval.read_pin() if approval.is_set() else None
            profiles.apply(p, "overnight", confirm=True, pin=pin, reason="research start --unattended")
            msgs.append(green("✓ ") + f"unattended settings applied ({len(pv['changes'])} changed; `research profile revert` undoes it)")
    st = PS._state_for(p, key)
    rs = SV.load_run(p, st["plan_id"])
    if rs and rs.get("state") in ("paused", "blocked", "attention"):
        SV.plan_resume(p, key)
    elif not rs or rs.get("state") not in ("running",):
        SV.plan_run(p, key, start_daemon=not a.no_daemon)
    if not a.no_daemon and not SV.daemon_status(p).get("running"):
        SV.daemon_start(p)
    d = BD.data(p, key)
    if a.json:
        out_json(d["plans"][0])
        return 0
    tail = ["", dim("Follow it: research watch · research logs -f T-ID    Change it: edit " + (d["plans"][0].get("file") or {}).get("path", "the plan file") + " and save")]
    if not a.no_daemon:
        try:
            from research import background as BG
            c = BG.check(p)
            bad = [r for r in c["rows"] if r["status"] != "ok" and r["what"] in ("logout", "cluster access")]
            tail.append(dim("You can close this terminal or the SSH session: it keeps running.") if not bad else
                        (red if not c["survives_logout"] else yellow)("Before you log out: ") + bad[0]["detail"] + dim("   (research background check)"))
        except Exception:    # noqa: BLE001 - advice only
            pass
    for l in msgs + plan_lines(d["plans"][0]) + tail:
        print(l)
    return 0


# ----------------------------------------------------------------------------------------------- the plan file
def _sync_lines(rs: List[Dict[str, Any]]) -> List[str]:
    out = []
    for r in rs:
        path = (r.get("file") or {}).get("path")
        if r["action"] in ("applied", "merged"):
            out.append(green("✓ ") + f"{r['plan']}: {r['operations']} change(s) applied from {path} (revision {r.get('revision')})" + (dim("  merged with changes the supervisor had made; the file was rewritten") if r["action"] == "merged" else ""))
            out += [f"    {o['op']:<18} {o.get('key')}" + (dim("  [" + ", ".join(o["fields"]) + "]") if o.get("fields") else "") for o in r.get("ops") or []]
            out += [yellow("  ! " + w) for w in r.get("warnings") or []]
        elif r["action"] == "written":
            out.append(green("✓ ") + f"{r['plan']}: {path} written from the current plan")
        else:
            out.append(dim(f"{r['plan']}: {path} is in sync"))
    return out or [dim("no plan has a file yet; `research plan sync PLAN` writes one")]


def cmd_plan_sync(a, p) -> int:
    if a.file:
        if not a.id:
            raise ResearchError("name the plan whose file moved: research plan sync PLAN --file NEW_PATH")
        return _emit(a, [{"plan": a.id, "action": "none", "file": PF.track(p, a.id, a.file)}], _sync_lines)
    if a.check:
        keys = [PS._state_for(p, a.id)["plan_key"]] if a.id else [s["plan_key"] for s in PS.all_states(p) if s.get("file")]
        res = [PF.check(p, k) for k in keys]
        for c in res:
            c.pop("_text", None)

        def lines(cs):
            out = []
            for c in cs:
                out.append(bold(c["plan"]) + dim(f"  {c['path']}  {c['status']}"))
                out += [f"    {o['op']:<18} {o.get('key')}" + (dim("  [" + ", ".join(o["fields"]) + "]") if o.get("fields") else "") for o in c["ops"]] or [dim("    no changes")]
                out += [red("  ✗ " + x) for x in c["conflicts"]] + [yellow("  ! " + w) for w in c["warnings"]]
                if c["scientific"]:
                    out.append(dim("    needs `research plan sync` (not applied automatically): " + "; ".join(c["scientific"])))
            return out
        return _emit(a, res, lines)
    return _emit(a, PF.sync(p, a.id or None), _sync_lines)


def cmd_plan_edit(a, p) -> int:
    """Open the plan file in $EDITOR; on save it is validated and applied. An invalid file is reopened with the problems listed, never half-applied."""
    key = PS._state_for(p, a.id)["plan_key"]
    i = PF.info(p, key)
    if i["status"] in ("untracked", "missing", "ledger_ahead"):
        i = PF.write(p, key)
    ed = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    while True:
        rc = subprocess.call(shlex.split(ed) + [i["abspath"]])
        if rc != 0:
            raise ResearchError(f"the editor exited with {rc}; nothing was applied")
        try:
            r = PF.sync(p, key)
            for l in _sync_lines(r):
                print(l)
            if SV.load_run(p, PS._state_for(p, key)["plan_id"]):
                freed = SV.release_budget_blocks(p, key)
                if freed:
                    print(green("✓ ") + "continuing: " + ", ".join(freed))
            return 0
        except ResearchError as ex:
            print(red("✗ ") + str(ex))
            if not sys.stdin.isatty() or input("Edit again? [Y/n] ").strip().lower() in ("n", "no"):
                print(dim(f"Nothing was applied. The file keeps your edit: {i['path']}"))
                return 1


def cmd_plan_set(a, p) -> int:
    r = PF.set_values(p, a.id, a.assignments)
    freed = SV.release_budget_blocks(p, r["plan"]) if SV.load_run(p, PS._state_for(p, r["plan"])["plan_id"]) else []
    r["released"] = freed
    return _emit(a, r, lambda d: [green("✓ ") + (f"{d['plan']}: {d['operations']} change(s), revision {d['revision']}; {d['file']['path']} updated" if d["operations"] else f"{d['plan']}: already so")]
                 + ([green("✓ ") + "continuing: " + ", ".join(d["released"])] if d["released"] else []))


def cmd_plan_file(a, p) -> int:
    i = PF.info(p, a.id)
    if i["status"] in ("untracked", "missing"):
        i = PF.write(p, i["plan"])
    return _emit(a, i, lambda d: [d["abspath"]] if a.path_only else [bold(d["plan"]) + f"  {d['path']}  " + dim(d["status"].replace("_", " "))]
                 + ([red("  cannot be applied: " + "; ".join(d["problem"]))] if d.get("problem") else []) + ([yellow("  waits for `research plan sync`: " + "; ".join(d["held"]))] if d.get("held") else []))


def cmd_bump(a, p) -> int:
    r = PF.bump(p, a.task, attempts=a.attempts, add=a.add)

    def lines(d):
        out = [green("✓ ") + f"{d['task']}: {d['after']} attempts allowed (was {d['before']}; {d['used']} used)"]
        pb = d["plan_budget"]
        if pb["after"] != pb["before"]:
            out.append(green("✓ ") + f"plan {d['plan']}: total attempt budget {pb['before']} → {pb['after']} ({pb['spent']} spent)")
        out.append(green("✓ ") + f"{d['task']} is queued again" if d.get("released") else dim(f"  {d['task']} was not stopped by its budget; it continues as it was"))
        out.append(dim(f"  recorded in {d['file']['path']} (revision {d['revision']})"))
        return out
    return _emit(a, r, lines)


# ----------------------------------------------------------------------------------------------- stopped things
def _task_plan(p, tid: str):
    from research.schema import normalize_id
    tid = normalize_id(tid, "T")
    hit = PS.find_task_key(p, tid)
    if not hit:
        raise ResearchError(f"{tid} is not part of a plan")
    return tid, hit[0], hit[1]


def _promotion_item(p, ref: str):
    for st in PS.all_states(p):
        rs = SV.load_run(p, st["plan_id"]) or {}
        for it in (rs.get("autopromote") or {}).get("items") or []:
            if it["id"].upper() == ref.upper():
                return st, rs, it
    raise ResearchError(f"{ref} is not a promotion item of any plan")


def cmd_why(a, p) -> int:
    ref = a.id
    if ref.upper().startswith("AP-"):
        from research import autopromote as AP, notify
        st, rs, it = _promotion_item(p, ref)
        text = notify.promotion_diagnosis(st, rs, {"task": it["id"], "reason": it.get("why") or it.get("error")}) if hasattr(notify, "promotion_diagnosis") else (it.get("error") or it["status"])
        d = {"id": it["id"], "status": it["status"], "diagnosis": text, "reviewer": AP.reviewer_status(p, it)}
        return _emit(a, d, lambda x: [bold(f"{x['id']}: {x['status']}"), x["diagnosis"]] + ([dim(f"reviewer {x['reviewer']['profile']}: {x['reviewer']['state']}, {x['reviewer']['minutes']} min; research logs {x['id']}")] if x["reviewer"] else []))
    tid, st, key = _task_plan(p, ref)
    s = BD.plan(p, st["plan_key"])
    t = next(x for x in s["tasks"] if x["id"] == tid)
    rs = SV.load_run(p, st["plan_id"]) or {}
    e = (rs.get("tasks") or {}).get(tid) or {}
    atts = [x for x in e.get("attempts") or []]
    d = {"task": tid, "key": key, "plan": st["plan_key"], "title": t["title"], "phase": t["phase"], "reason": t.get("reason"), "attempts": t["attempts"], "allowed": t["allowed"],
         "recovery": t.get("recovery"), "next": t["next"],
         "history": [{"n": x["n"], "kind": x.get("kind"), "profile": x.get("profile"), "outcome": x.get("outcome") or x.get("state"), "category": x.get("category"),
                      "override": x.get("category_override"), "voided": bool(x.get("voided")), "execution": x.get("execution"),
                      "diagnostics": ((x.get("verification") or {}).get("diagnostics") or [])[:6]} for x in atts]}
    if a.ask:
        from research import notify
        d["diagnosis"] = notify.run_debug(p, st["plan_key"], tasks=[tid])

    def lines(x):
        out = [bold(f"{x['task']} {x['title']}") + dim(f"  [{x['plan']}/{x['key']}]"), f"  state     {x['phase']}   attempts {x['attempts']}/{x['allowed']}"]
        if x.get("reason"):
            out.append("  why       " + x["reason"])
        r = x.get("recovery")
        if r:
            from research import recovery
            out += ["  cause     " + l for l in recovery.line(r).splitlines()]
        for h in x["history"]:
            out.append(dim(f"  attempt {h['n']} ({h.get('kind')}, {h.get('profile')}): {h.get('outcome')}" + (f" [{h['category']}]" if h.get("category") else "") + (" (voided)" if h["voided"] else "")))
            if h.get("override"):
                out.append(dim(f"      the agent reported {h['override'].get('reported')}; the checks say {h['override'].get('decided')}"))
            out += [dim("      " + str(g)[:220]) for g in h["diagnostics"]]
        if x.get("history"):
            out.append(dim(f"  agent output: research logs {x['task']}"))
        dg = x.get("diagnosis")
        if dg:
            out += ["", bold("Diagnosis") + dim(f"  ({dg.get('state')})"), dg.get("summary") or str(dg.get("error") or "")]
            out += [f"  {i + 1}. {o['label']} [{o['class']}]" + dim(f"\n     {o['instruction'][:200]}") for i, o in enumerate(dg.get("options") or [])]
            if dg.get("options"):
                out.append(dim("  start one with: research supervisor debug --choose N"))
        out += ["  " + yellow("→ ") + bold(n["cmd"]) + dim("   " + n["what"]) for n in x["next"] if not n["cmd"].startswith("research why ")]
        if not x["next"] and not dg:
            out.append(dim("  nothing here waits for you"))
        elif not dg:
            out.append(dim(f"  an agent's reading of the logs: research why {x['task']} --ask"))
        return out
    return _emit(a, d, lines)


def cmd_retry(a, p) -> int:
    tid, st, _ = _task_plan(p, a.id)
    r = SV.resolve(p, tid, "retry", a.note)
    return _emit(a, r, lambda d: [green("✓ ") + f"{tid} is queued again (its attempt history is kept)"])


def cmd_tell(a, p) -> int:
    from research import intervene as I
    from .supervisor import _show_intervention
    tid, st, _ = _task_plan(p, a.id)
    it = I.request(p, tid, " ".join(a.message), interrupt=a.interrupt)
    if it["state"] == "proposed" and (a.yes or (sys.stdin.isatty() and sys.stdout.isatty() and not a.json)):
        _show_intervention(it)
        if a.yes or input("Approve and run it? [y/N] ").strip().lower() in ("y", "yes"):
            it = I.approve(p, it["id"], "approved at the terminal")
    if a.json:
        out_json(it)
    else:
        _show_intervention(it)
    return 0


def _attempt_rows(p, plan: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every agent attempt of every (non-archived) plan, newest first: what `research logs` lists when no id is given."""
    from research import agentlog as AL, execution as EX
    rows = []
    for st in PS.all_states(p):
        if st.get("archived") or (plan and st["plan_key"] != plan):
            continue
        rs = SV.load_run(p, st["plan_id"]) or {}
        keys = {v: k for k, v in st["tasks"].items()}
        for tid, e in (rs.get("tasks") or {}).items():
            for x in e.get("attempts") or []:
                if not x.get("execution"):
                    continue
                try:
                    ex = EX.show(p, x["execution"])
                except (OSError, ValueError):
                    continue
                live = ex["state"] == "running" and ex.get("alive")
                rows.append({"task": tid, "key": keys.get(tid), "plan": st["plan_key"], "attempt": x["n"], "kind": x.get("kind"), "profile": x.get("profile"), "state": "running" if live else ex["state"],
                             "exit_code": ex.get("exit_code"), "started": x.get("started"), "ended": x.get("ended"), "execution": x["execution"],
                             "verified": (x.get("verification") or {}).get("passed"), "activity": AL.last_activity(p, x["execution"], x.get("session_id")) if live else None})
        for it in (rs.get("autopromote") or {}).get("items") or []:
            rr = it.get("review_requested") or {}
            if rr.get("execution"):
                try:
                    ex = EX.show(p, rr["execution"])
                except (OSError, ValueError):
                    continue
                live = ex["state"] == "running" and ex.get("alive")
                rows.append({"task": it["id"], "key": "review", "plan": st["plan_key"], "attempt": int(it.get("review_attempts") or 1), "kind": "review", "profile": rr.get("profile"),
                             "state": "running" if live else ex["state"], "exit_code": ex.get("exit_code"), "started": rr.get("at"), "ended": ex.get("ended_at"), "execution": rr["execution"],
                             "verified": None, "activity": AL.last_activity(p, rr["execution"]) if live else None})
    return sorted(rows, key=lambda r: (r["state"] != "running", -(_age(r["started"]) or 0) * -1))


def _logs_target(p, ref: str, attempt: Optional[int]):
    """(execution id, provider session id, label) for T-023, AP-001 or an execution id."""
    from research import execution as EX
    up = ref.upper()
    if up.startswith("AP-"):
        _, _, it = _promotion_item(p, ref)
        rr = it.get("review_requested") or {}
        if not rr.get("execution"):
            raise ResearchError(f"{it['id']} has no reviewer output" + (" (its reviewer was started by an older version that kept none)" if rr else " yet: no reviewer has been started"))
        return rr["execution"], None, f"{it['id']} reviewer ({rr.get('profile')})"
    if up.startswith("RUN-") or up.startswith("R-"):
        raise ResearchError("for a computational run's output use: research run logs " + ref)
    if not up.startswith("T-") and not up.isdigit():
        hits = [r for r in EX.list_all(p) if r["id"].startswith(ref)]
        if len(hits) == 1:
            return hits[0]["id"], None, f"execution {hits[0]['id'][:8]} ({hits[0].get('task')})"
        raise ResearchError(f"{ref} is not a task (T-023), a promotion (AP-001) or an execution id", hint="`research logs` without an id lists every attempt.")
    tid, st, _ = _task_plan(p, ref)
    e = ((SV.load_run(p, st["plan_id"]) or {}).get("tasks") or {}).get(tid) or {}
    atts = [x for x in e.get("attempts") or [] if x.get("execution")]
    if attempt:
        atts = [x for x in atts if x["n"] == attempt]
    if not atts:
        raise ResearchError(f"{tid} has no agent output" + (f" for attempt {attempt}" if attempt else " yet: no attempt has been launched"))
    x = atts[-1]
    return x["execution"], x.get("session_id") or x.get("resumed_session"), f"{tid} attempt {x['n']} of {len(e.get('attempts') or [])} ({x.get('kind')}, {x.get('profile')})"


def cmd_logs(a, p) -> int:
    """No id: every attempt, running ones first with what they are doing. With an id: the agent's steps, live with -f."""
    from research import agentlog as AL
    if not a.id:
        rows = _attempt_rows(p)

        def lines(rs):
            out = []
            for r in rs:
                col = cyan if r["state"] == "running" else green if r.get("verified") else red if r["state"] in ("failed", "unknown") or r.get("verified") is False else dim
                dur = _dur((_age(r["started"]) or 0) - ((_age(r["ended"]) or 0) if r.get("ended") else 0)) if r.get("started") else ""
                what = "verified" if r.get("verified") else "not verified" if r.get("verified") is False else r["state"]
                out.append(f"  {r['task']:<7} attempt {r['attempt']:<2} {col(what if r['state'] != 'running' else 'running'):<14} {dur:>7}  {(r['profile'] or ''):<16} " + dim(f"{r['plan']}/{r['key']}"))
                if r.get("activity"):
                    out.append(dim(f"            {_dur(r['activity']['seconds_ago'])} ago: ") + r["activity"]["text"])
            return out + [dim("  research logs T-ID          the steps of its latest attempt      -f  follow a running one      --attempt N  an earlier one")] if out else [dim("No agent has been launched yet.")]
        return _emit(a, rows, lines)
    ident, sid, label = _logs_target(p, a.id, a.attempt)
    rd = AL.Reader(p, ident, sid)
    ev = rd.more()
    rec = rd.record()
    if a.json:
        out_json({"execution": ident, "state": rec["state"], "exit_code": rec.get("exit_code"), "source": rd.source, "events": ev})
        return 0
    if a.raw:
        from research import execution as EX
        lg = EX.logs(p, ident, tail=a.tail)
        print((lg.get("out") or "") + (("\n[stderr]\n" + lg["err"]) if lg.get("err") else ""))
        return 0
    print(bold(label) + dim(f"  {rec['state']}" + (f", exit {rec['exit_code']}" if rec.get("exit_code") is not None else "") + f"  ·  {rec.get('cwd') or ''}"))
    if rd.source:
        print(dim(f"  from {rd.source}"))
    lines = AL.render(ev, full=a.full, show_prompt=a.prompt)
    if not a.full and not a.follow and len(lines) > a.lines:
        print(dim(f"  … {len(lines) - a.lines} earlier line(s); --lines N or --full shows more"))
        lines = lines[-a.lines:]
    print("\n".join(lines) if lines else dim("  (nothing recorded yet)"))
    if not a.follow:
        if rec["state"] == "running" and rec.get("alive"):
            print(dim(f"  still running: research logs {a.id} -f follows it"))
        return 0
    try:
        idle = 0
        while True:
            time.sleep(1.0)
            ev = rd.more()
            if ev:
                print("\n".join(AL.render(ev, full=a.full, show_prompt=a.prompt)), flush=True)
            rec = rd.record()
            if not (rec["state"] in ("prepared", "launched") or (rec["state"] == "running" and rec.get("alive"))):
                idle += 1
                if idle >= 3:          # a few more reads so the provider's last lines are not missed
                    print(dim(f"  the agent has finished ({rec['state']}" + (f", exit {rec['exit_code']}" if rec.get("exit_code") is not None else "") + "); the supervisor verifies its work next"))
                    return 0
    except KeyboardInterrupt:
        return 0


# ----------------------------------------------------------------------------------------------- archive
def cmd_archive(a, p) -> int:
    if a.list or not a.ids:
        rows = AR.listing(p)
        return _emit(a, rows, lambda rs: [f"  {r['id']:<9} {r['kind']:<10} {(r.get('at') or '')[:10]}  {r.get('title') or ''}" + dim(f"  {r['reason']}" if r.get("reason") else "") + dim(f"  (with {r['via']})" if r.get("via") else "") for r in rs]
                     or [dim("Nothing is archived. `research archive PLAN-001` or `research archive EXP-003` hides one without deleting anything.")])
    res = [AR.archive(p, i, a.reason) for i in a.ids]
    return _emit(a, res, lambda rs: [green("✓ ") + (f"{r['id']} archived" + (f" with {', '.join(r['also'])}" if r["also"] else "") + dim("  (everything is kept; `research unarchive " + r["id"] + "` brings it back)") if r["changed"] else f"{r['id']} was already archived") for r in rs])


def cmd_unarchive(a, p) -> int:
    res = [AR.unarchive(p, i) for i in a.ids]
    return _emit(a, res, lambda rs: [green("✓ ") + (f"{r['id']} is back" + (f" with {', '.join(r['also'])}" if r["also"] else "") if r["changed"] else f"{r['id']} was not archived") for r in rs])


def cmd_background(a, p) -> int:
    """Will it keep running after you close the terminal or the SSH session? `check` answers; `install` makes it a user service that also survives reboots."""
    from research import background as BG
    mark = {"ok": green("ok  "), "warn": yellow("warn"), "fail": red("FAIL")}

    def check_lines(c):
        out = [bold("After you log out: ") + (green("keeps running") if c["status"] == "ok" else yellow("keeps running, with caveats") if c["survives_logout"] else red("would be stopped"))]
        for r in c["rows"]:
            out.append(f"  {mark[r['status']]} {r['what']}: {r['detail']}")
            if r.get("fix") and r["status"] != "ok":
                out.append(dim("        fix: ") + r["fix"])
            elif r.get("fix"):
                out.append(dim("        " + r["fix"]))
        return out
    if a.action == "check":
        c = BG.check(p)
        _emit(a, c, check_lines)
        return 0 if c["survives_logout"] else 1
    if a.action == "install":
        r = BG.install(p, gateway=not a.no_gateway)

        def lines(d):
            out = [(green("✓ ") + f"{u} is enabled and running" if ok is True else red("✗ ") + f"{u}: {ok}") for u, ok in d["started"].items()]
            out.append(green("✓ ") + "lingering is on: your services run without an open session and start at boot" if d["lingering"] else
                       yellow("! ") + "lingering is off and could not be turned on from here; run once: " + bold(d["linger_command"] or "loginctl enable-linger $USER"))
            return out + [""] + check_lines(d["check"]) + [dim("  logs: journalctl --user -u " + d["units"][0] + " -f    stop: research supervisor stop    remove: research background uninstall")]
        return _emit(a, r, lines)
    r = BG.uninstall(p)
    return _emit(a, r, lambda d: [green("✓ ") + f"{d['unit']} removed" + dim("  (" + d["note"] + ")") if d["removed"] else dim(f"{d['unit']} is not installed")])


for _f in (cmd_start, cmd_plan_sync, cmd_plan_edit, cmd_plan_set, cmd_bump, cmd_retry, cmd_tell, cmd_archive, cmd_unarchive):
    mut(_f)


def attach_plan(g, add) -> None:
    x = add(g, "status", cmd_plan_status, "the plan board: every task by experiment, what is stuck, why, and the command when it needs you")
    x.add_argument("id", nargs="?", help="plan key or id (default: every plan)")
    x.add_argument("--all", action="store_true", help="include archived plans")
    x = add(g, "edit", cmd_plan_edit, "open the plan file in $EDITOR; it is validated and applied when you save")
    x.add_argument("id")
    x = add(g, "sync", cmd_plan_sync, "apply edits made to plan files now (the supervisor also does it by itself); --check previews")
    x.add_argument("id", nargs="?", help="plan key or id (default: every plan with a file)")
    x.add_argument("--check", action="store_true", help="show what would change; apply nothing")
    x.add_argument("--file", help="the plan's file moved: track it at this path")
    x = add(g, "set", cmd_plan_set, "change values without an editor: T-023.max_attempts=6 execution.max_total_attempts=60 analyze.profile=claude-deep")
    x.add_argument("id")
    x.add_argument("assignments", nargs="+", metavar="TARGET.FIELD=VALUE")
    x = add(g, "file", cmd_plan_file, "where the plan's file is and whether it is in sync")
    x.add_argument("id")
    x.add_argument("--path-only", action="store_true", help="print only the absolute path (for $EDITOR \"$(research plan file P --path-only)\")")


def attach(sub, add, group) -> None:
    x = add(sub, "start", cmd_start, "start a plan: `research start plans/study.yaml` registers or syncs the file and lets the supervisor run it", aliases=["go"])
    x.add_argument("target", nargs="?", help="a plan file, or a plan key/id (default: the only unfinished plan)")
    x.add_argument("--unattended", action="store_true", help="apply the `overnight` settings first (wait out usage limits, self-heal, promote verified code)")
    x.add_argument("--no-daemon", action="store_true", help="do not start the supervisor process")
    x = add(sub, "background", cmd_background, "keep running after you log out (no tmux): check | install (systemd user service + lingering, survives reboots) | uninstall", aliases=["bg"])
    x.add_argument("action", nargs="?", default="check", choices=["check", "install", "uninstall"])
    x.add_argument("--no-gateway", action="store_true", help="install: leave the chat gateway as it is")
    x = add(sub, "watch", cmd_watch, "the plan board, live (read-only)", aliases=["board"])
    x.add_argument("id", nargs="?", help="one plan (default: all)")
    x.add_argument("--interval", type=float, default=5.0, help="seconds between refreshes (default 5)")
    x.add_argument("--once", action="store_true", help="print once and exit")
    x.add_argument("--all", action="store_true", help="include archived plans")
    x = add(sub, "bump", cmd_bump, "a task ran out of attempts: allow more and retry now (`research bump T-023`, `research bump T-023 8`)")
    x.add_argument("task")
    x.add_argument("attempts", nargs="?", type=int, help="the new allowance (default: 3 more than it has used)")
    x.add_argument("--add", type=int, default=3, help="how many to add when no number is given (default 3)")
    x = add(sub, "why", cmd_why, "why a task (T-023) or promotion (AP-001) stopped, and what to do: recorded facts, no agent; --ask adds a diagnosis")
    x.add_argument("id")
    x.add_argument("--ask", action="store_true", help="also run the diagnostic agent on the logs (uses tokens)")
    x = add(sub, "retry", cmd_retry, "queue a stopped task again as it is")
    x.add_argument("id")
    x.add_argument("--note")
    x = add(sub, "tell", cmd_tell, "steer a task in words: research tell T-023 \"use the cached footprints instead of recomputing\"")
    x.add_argument("id")
    x.add_argument("message", nargs="+")
    x.add_argument("--interrupt", action="store_true", help="stop the running agent now (its attempt is kept)")
    x.add_argument("--yes", action="store_true", help="approve the resulting proposal right away (protected changes still ask for the PIN)")
    x = add(sub, "logs", cmd_logs, "what agents are doing and did: no id lists every attempt; T-023 / AP-001 shows its steps (-f follows a running one)", )
    x.add_argument("id", nargs="?", help="a task (T-023), a promotion's reviewer (AP-001) or an execution id; omit to list all attempts")
    x.add_argument("-f", "--follow", action="store_true", help="keep printing new steps until the agent finishes")
    x.add_argument("--attempt", type=int, help="a specific attempt (default: the latest)")
    x.add_argument("--lines", type=int, default=60, help="how many of the latest lines to show (default 60)")
    x.add_argument("--full", action="store_true", help="everything, with tool results uncut")
    x.add_argument("--prompt", action="store_true", help="include the task prompt the agent was given")
    x.add_argument("--raw", action="store_true", help="the provider's captured stdout/stderr as they are")
    x.add_argument("--tail", type=int, default=8000, help="--raw: characters from the end (default 8000)")
    x = add(sub, "archive", cmd_archive, "hide a plan or experiment you no longer care about (nothing is deleted); --list shows the archive")
    x.add_argument("ids", nargs="*", metavar="PLAN|EXP")
    x.add_argument("--reason")
    x.add_argument("--list", action="store_true")
    x = add(sub, "unarchive", cmd_unarchive, "bring an archived plan or experiment back")
    x.add_argument("ids", nargs="+", metavar="PLAN|EXP")
