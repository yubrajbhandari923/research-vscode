"""The Needs-attention inbox: what needs a person, as plain data. No widgets; every source is read through an existing op or query and a source that
fails becomes one informational row instead of an empty inbox.

An item is exactly one of three kinds, and the TUI never blurs them:
    action    REQUIRES YOUR ACTION: a decision, approval or fix that something is waiting for, or evidence that cannot be trusted
    followup  RECOMMENDED FOLLOW-UP: the work is not stuck, but a scientific step is due (synthesis, review, a missing file)
    info      INFORMATIONAL STATUS: nothing for you to do (for example a planned experiment)
`needs_review` on an experiment is a follow-up about interpretation. It is never a decision request.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from rich.text import Text

from .widgets import WRAP, badge, kv, plain

LEVELS = {"action": "REQUIRES YOUR ACTION", "followup": "Recommended follow-up", "info": "Informational"}
ORDER = {"action": 0, "followup": 1, "info": 2}
REMOTE = ("slurm", "hpc-bundle")
LIFECYCLE = ["proposed", "ready", "running", "needs_review", "completed"]
LIFECYCLE_WHY = {
    "proposed": "planned or proposed; no run has started. Becomes ready when you or the plan accept it, and running when the first run starts.",
    "ready": "accepted and waiting for its first run.",
    "running": "at least one run is queued or running.",
    "needs_review": "every run has finished. It stays here until a synthesis is recorded and you accept it: scientific review is yours, not automatic.",
    "completed": "you closed it after a synthesis. Only a person does this.",
    "failed": "closed as failed (a dead end or a bug, recorded by you).",
    "abandoned": "dropped on purpose; kept for the record.",
}


def _item(kind: str, level: str, ref: str, title: str, why: str, nxt: str, actions: List[str], tab: str, blocks: bool = False,
          source: str = "") -> Dict[str, Any]:
    return {"key": f"{kind}:{ref}", "kind": kind, "level": level, "ref": ref, "title": title, "why": why, "next": nxt, "actions": actions,
            "tab": tab, "blocks": blocks, "source": source or kind}


# ----------------------------------------------------------------------------------------------- artifacts
def artifact_rows(p, exp_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every registered artifact with where it is and why: local | collected | inaccessible | missing | modified. Never rewrites anything."""
    from research import evidence, runs as RUNS
    rows = []
    q = "SELECT id, run_id, experiment_id, name, path, external FROM artifacts" + (" WHERE experiment_id=?" if exp_id else "") + " ORDER BY id"
    for a in p.q(q, (exp_id,) if exp_id else ()):
        i = evidence.artifact_integrity(p, a["id"])
        run = {}
        try:
            run = RUNS.get_run(p, a["run_id"]) if a.get("run_id") else {}
        except Exception:      # noqa: BLE001
            pass
        remote = run.get("backend") in REMOTE
        hpc_state = ((run.get("slurm") or {}).get("hpc") or {}).get("state")
        s = i["status"]
        if s == "unavailable":
            state = "inaccessible"
            why = (f"registered at {a['path']}, on external storage this machine cannot reach ({i['detail']}). "
                   + (f"Run {a['run_id']} is {run.get('status')} and {hpc_state or 'recorded'}: the job finished and its metadata and logs were synced, but that does not "
                      "put this file here, and it does not prove the file exists. " if remote else "")
                   + "Nothing was rewritten and the artifact is not treated as verified.")
        elif s == "missing":
            state, why = "missing", f"registered at {a['path']} but not found ({i['detail']})."
        elif s in ("modified", "possibly_modified"):
            state, why = "modified", i["detail"]
        else:
            state = "collected" if (a.get("external") or remote) else "local"
            why = "present here" + (f" ({i['detail']})" if i.get("detail") else "") + (", collected from the external run" if state == "collected" else "")
        rows.append({"id": a["id"], "name": a.get("name") or a["path"].rsplit("/", 1)[-1], "path": a["path"], "run": a.get("run_id"), "experiment": a.get("experiment_id"),
                     "state": state, "integrity": s, "why": why, "remote": remote})
    return rows


def artifact_text(rows: List[Dict[str, Any]]) -> Text:
    t = Text()
    if not rows:
        return Text("No artifacts are registered for this experiment.\n", style="dim")
    for r in rows:
        t.append(f"{r['id']} ", style="bold")
        t.append_text(badge({"local": "ok", "collected": "ok", "inaccessible": "warn", "missing": "failed", "modified": "failed"}[r["state"]]))
        t.append(f" {r['state']}  {r['name']}\n")
        t.append(f"    {r['why']}\n", style="dim")
    return t


# ----------------------------------------------------------------------------------------------- the inbox
def gather(call: Callable[..., Any], p, tree: Dict[str, Any]) -> List[Dict[str, Any]]:
    """All items, most important first (action, then follow-up, then info; blocking before not blocking)."""
    items: List[Dict[str, Any]] = []
    errors: List[str] = []

    def src(name: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as e:      # noqa: BLE001 - one unreadable source must not empty the inbox
            errors.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")

    src("experiments", lambda: _experiments(call, p, tree, items))
    src("proposals", lambda: _proposals(call, items))
    src("interventions", lambda: _interventions(call, items))
    src("decisions", lambda: _decisions(call, items))
    src("findings", lambda: _findings(tree, items))
    src("blocked tasks", lambda: _blocked(p, items))
    src("promotions", lambda: _promotions(call, items))
    for e in errors:
        items.append(_item("source", "info", e.split(":")[0], f"Could not read {e.split(':')[0]}", e, "Press r to refresh; `research doctor` checks the project.", [], "t7", source="Inbox"))
    items.sort(key=lambda x: (ORDER[x["level"]], not x["blocks"], x["key"]))
    return items


def _experiments(call, p, tree, items) -> None:
    for e in tree.get("experiments", []):
        if e["status"] in ("abandoned", "failed"):
            continue
        eid, title = e["id"], e["title"]
        src = f"Experiment {eid}"
        st = call("experiment_state", id=eid)
        if e["status"] == "proposed":
            rec = call("show", id=eid)
            if rec.get("author_type") == "agent":
                items.append(_item("experiment", "followup", eid, f"{eid} proposed by an agent: {title}",
                                   "An agent registered this experiment. Nothing runs until you accept it. This is a proposal about WHAT to study; it is not an approval gate for code or science changes.",
                                   "Read the hypothesis and rationale; mark it ready to accept, or abandon it.", ["open", "mark_ready", "abandon"], "t6", source=src))
            else:
                items.append(_item("experiment", "info", eid, f"{eid} planned: {title}",
                                   "A planned experiment with no run yet. `proposed` is a normal starting state, not a request for you.",
                                   "Nothing is required. Start a run when you are ready (`research run exec`), or let a plan run it.", ["open", "help"], "t6", source=src))
            continue
        due = st["needs_synthesis"] or st["over_run_budget"]
        if due:
            blocks = bool(st["over_run_budget"])
            items.append(_item("synthesis", "action" if blocks else "followup", eid,
                               f"{eid}: {st['unsynthesized']} finished run(s) not yet synthesized",
                               "Runs finished but nobody has interpreted them against the hypothesis. " + (
                                   f"The run budget ({st['max_runs_without_synthesis']}) is reached: new runs are refused until you synthesize." if blocks else
                                   "This is a follow-up, not a decision request."),
                               "Prepare the analyst draft, launch the analyst if you want an agent's interpretation, then review and record it.",
                               ["prepare_analyst", "launch_analyst", "check", "record_synthesis", "open", "help"], "t6", blocks=blocks, source=src))
        elif e["status"] == "needs_review":
            items.append(_item("synthesis", "followup", eid, f"{eid} awaiting your review: {title}",
                               "All runs are synthesized, so the experiment waits for you to accept the interpretation and close it. `needs_review` is a status, not a decision request.",
                               "Read the synthesis; mark the experiment completed when you accept it.", ["open", "mark_completed", "help"], "t6", source=src))
        if st["unreviewed_failed"]:
            items.append(_item("failed_runs", "followup", eid, f"{eid}: {st['unreviewed_failed']} failed run(s) not reviewed",
                               "A failed run is either a dead end or a bug; recording a synthesis marks them reviewed.", "Open the experiment, then synthesize.", ["open", "record_synthesis"], "t6", source=src))
        bad = [r for r in artifact_rows(p, eid) if r["state"] != "local" and r["state"] != "collected"]
        if bad:
            worst = "action" if any(r["state"] in ("modified", "missing") for r in bad) else "followup"
            items.append(_item("artifacts", worst, eid, f"{eid}: {len(bad)} artifact(s) cannot be verified ({', '.join(sorted({r['state'] for r in bad}))})",
                               bad[0]["why"], "Reconnect the storage and re-check, sync HPC runs, or collect the files and register them; otherwise state the limitation.",
                               ["artifacts", "recheck", "reconcile", "hpc_sync", "open"], "t6", source=src))


def _proposals(call, items) -> None:
    for x in call("proposals"):
        if x.get("status") == "pending":
            items.append(_item("proposal", "action", x["id"], f"Proposal {x['id']}: {str(x.get('text') or x.get('kind') or '')[:70]}",
                               "An agent asks to change something that needs governance approval (a plan, resources or scientific content). It waits for you; the agent continues without it.",
                               "Open it in Plans & Approvals, read the change and rationale, then approve (PIN) or reject.", ["open_approvals"], "t4", source="Governance proposal"))


def _interventions(call, items) -> None:
    for x in call("interventions"):
        if x.get("state") == "proposed":
            items.append(_item("intervention", "action", x["id"], f"Intervention {x['id']} for {x.get('task')}",
                               "An instruction to a running task that changes something protected, so it waits for your approval. The task is held until you decide.",
                               "Read the instruction and what it changes, then approve with your PIN (`research intervention approve`) or reject.", ["help"], "t4", blocks=True,
                               source="Intervention"))


def _decisions(call, items) -> None:
    from research import api
    if "decision_list" not in api.OPS:
        return
    for d in call("decision_list", status=None):
        if d.get("status") in ("open", "answering"):
            items.append(_item("decision", "action", d["id"], f"Decision {d['id']}: {str(d.get('question') or '')[:70]}",
                               "A structured question with numbered options that the supervisor or an agent is waiting on.", "Pick an option on the Decisions tab (or answer in your own words).",
                               ["open_decisions"], "t5", blocks=True, source="Decision request"))


def _findings(tree, items) -> None:
    for f in tree.get("findings", []):
        if f.get("status") == "preliminary":
            items.append(_item("finding", "followup", f["id"], f"{f['id']} awaiting independent review: {f['title']}",
                               "A finding stays preliminary until a reviewer other than its author marks it supported or contradicted. Review is separate from synthesis.",
                               "Ask a reviewer (an agent in the reviewer role, or a colleague) or open it to read the evidence.", ["open", "help"], "t3", source="Finding"))


def _blocked(p, items) -> None:
    from research import notify
    for b in notify._blocked(p):
        items.append(_item("blocked_task", "action", b["task"], f"{b['task']} blocked: {str(b.get('title') or '')[:60]}",
                           f"Phase {b['phase']}: {str(b.get('reason') or 'waiting for a person')[:200]}", "Open Plans & Approvals; `/debug` or an instruction can unblock it.",
                           ["open_approvals", "help"], "t4", blocks=True, source=f"Plan {b.get('plan', '')}".strip()))


def _promotions(call, items) -> None:
    for x in call("promotions"):
        st = str(x.get("state") or x.get("status") or "")
        if st and st not in ("promoted", "abandoned", "rejected", "integrated"):
            items.append(_item("promotion", "followup", str(x["id"]), f"Code promotion {x['id']}: {st}",
                               "Verified code waiting to reach main. Engineering verification and the independent code review are recorded on the promotion; merging is a separate human step.",
                               "Open it to read the verification and reviewer outcome.", ["help"], "t4", source="Promotion"))


def counts(items: List[Dict[str, Any]]) -> Dict[str, int]:
    return {k: sum(1 for i in items if i["level"] == k) for k in LEVELS}


# ----------------------------------------------------------------------------------------------- rendering
GLYPH = {"action": ("!" if plain() else "●", "bold #F07880"), "followup": ("~" if plain() else "◐", "#F1B85B"), "info": ("-" if plain() else "○", "dim")}
SHORT = {"action": "needs you", "followup": "recommended", "info": "info"}


def row(i: Dict[str, Any]) -> tuple:
    g, st = GLYPH[i["level"]]
    title = Text(i["title"][:62], style="" if i["level"] != "info" or plain() else "dim")
    if i["blocks"]:
        title.append("  blocks work", style="" if plain() else "#F07880")
    return (i["key"], Text(g, style="" if plain() else st), title, i["source"].replace("Experiment ", "").replace("Governance proposal", "Proposal")[:22])


def _kv(t: Text, k: str, v: str) -> None:
    kv(t, k, v)


def detail(i: Dict[str, Any], ctx: Optional[Text] = None) -> Text:
    g, st = GLYPH[i["level"]]
    t = Text()
    t.append(f"{g} {LEVELS[i['level']]}\n", style="bold" if plain() else st)
    t.append(i["title"] + "\n\n", style="bold")
    _kv(t, "From", i["source"])
    _kv(t, "Blocks work", "yes: something is waiting for this" if i["blocks"] else "no: work continues without it")
    _kv(t, "Why", i["why"])
    _kv(t, "Next", i["next"])
    if ctx is not None:
        t.append("\n").append_text(ctx)
    if i["actions"]:
        t.append("\n")
        t.append("Enter", style="bold" if plain() else "bold #39D5C4").append(" choose an action    ")
        t.append("w", style="bold" if plain() else "bold #39D5C4").append(" what does this mean\n")
    return t


ACTION_LABELS = {
    "open": "Open the record", "open_approvals": "Go to Plans & Approvals", "open_decisions": "Go to Decisions", "help": "What does this mean?",
    "prepare_analyst": "Prepare analyst draft (writes a brief; starts nothing)", "launch_analyst": "Launch analyst (starts an agent; may cost money)",
    "check": "Check evidence now (experiment check)", "record_synthesis": "Record synthesis (your interpretation)",
    "mark_completed": "Mark experiment completed (you accept the science)", "mark_ready": "Mark experiment ready (accept the proposal)", "abandon": "Abandon experiment",
    "artifacts": "Show artifacts and why each is flagged", "reconcile": "Point artifacts at their collected copies (safe, in place)", "recheck": "Re-check artifacts", "hpc_sync": "Sync HPC runs (refresh state from the cluster)",
}


def experiment_header(p, exp_id: str) -> Text:
    """What the experiment is: title, question, hypothesis and the plan, in the order a reader asks for them."""
    e = p.get("experiment", exp_id)
    t = Text()
    t.append(f"{exp_id}  {e['title']}\n", style="bold")
    t.append_text(badge(e["status"])).append("\n\n")
    q = p.get("question", e["question_id"])["title"] if e.get("question_id") and p.exists("question", e["question_id"]) else None
    for k, v in (("Question", q), ("Hypothesis", e.get("hypothesis")), ("Motivation", e.get("motivation")), ("Method", e.get("method")),
                 ("Success", e.get("success_criteria")), ("Limitations", e.get("limitations")), ("Notes", e.get("notes"))):
        if v:
            kv(t, k, v)
    return t


def experiment_context(p, call, exp_id: str) -> Text:
    """Lifecycle, what drives it, the unsynthesized runs and artifacts, and who does what: shown for an experiment record."""
    e = p.get("experiment", exp_id)
    st = call("experiment_state", id=exp_id)
    t = Text()
    cur = e["status"]
    t.append("Lifecycle\n", style="bold")
    width = 0
    for k, s_ in enumerate(LIFECYCLE):
        on = s_ == cur
        seg = s_ + ("  ›  " if k < len(LIFECYCLE) - 1 else "")
        if width + len(seg) > WRAP[0]:
            t.append("\n")
            width = 0
        width += len(seg)
        t.append(s_, style=("bold #39D5C4" if on and not plain() else "bold" if on else "" if plain() else "dim"))
        t.append("  ›  " if k < len(LIFECYCLE) - 1 else "", style="" if plain() else "dim")
    t.append(f"\n{LIFECYCLE_WHY.get(cur, '')}\n\n")
    _kv(t, "Runs", f"{st['runs']} total · {st['active']} active · {st['finished']} finished")
    _kv(t, "Synthesis", f"{st['unsynthesized']} run(s) not yet synthesized" + (": " + ", ".join(st["unsynthesized_ids"]) if st["unsynthesized_ids"] else ""))
    if st["unreviewed_failed"]:
        _kv(t, "Failed", f"{st['unreviewed_failed']} failed run(s) not reviewed")
    pol = p.policy
    _kv(t, "Policy", f"new runs are refused at {pol.get('max_runs_without_synthesis')} unsynthesized runs")
    t.append("\nWho does what\n", style="bold")
    _kv(t, "Automatic", "detect finished runs, check evidence, notice a synthesis is due, draft a brief")
    _kv(t, "Agent", "an analyst interprets the results, only when you launch it")
    _kv(t, "You", "accept or edit the interpretation, close the experiment, decide")
    rows = artifact_rows(p, exp_id)
    if rows:
        t.append("\nArtifacts\n", style="bold")
        t.append_text(artifact_text(rows))
    return t


def synthesis_draft(p, call, exp_id: str) -> str:
    """Grounded starting text for the researcher: only recorded facts. The interpretation is deliberately left for the person to write."""
    e = p.get("experiment", exp_id)
    st = call("experiment_state", id=exp_id)
    lines = [f"Hypothesis (recorded): {e.get('hypothesis') or 'none recorded'}"]
    if e.get("success_criteria"):
        lines.append(f"Success criteria (recorded): {e['success_criteria']}")
    lines.append(f"Runs not yet synthesized: {', '.join(st['unsynthesized_ids']) or 'none'}")
    for r in p.q("SELECT id, status, exit_code, backend FROM runs WHERE experiment_id=? ORDER BY id", (exp_id,)):
        n = p.q1("SELECT COUNT(*) AS n FROM metrics WHERE run_id=?", (r["id"],))["n"]
        lines.append(f"  {r['id']}: {r['status']} (exit {r['exit_code']}, {r['backend']}), {n} metric value(s)")
    bad = [r for r in artifact_rows(p, exp_id) if r["state"] not in ("local", "collected")]
    if bad:
        lines.append("Artifacts that could not be verified: " + ", ".join(f"{r['id']} ({r['state']})" for r in bad))
    return "\n".join(lines)
