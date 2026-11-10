"""`research job`: delegate a request in words; it is planned, carried out by the supervisor, repaired within bounds and independently verified."""
from __future__ import annotations

from research import jobs as JB
from research.util import ResearchError

from . import mut
from ..output import bold, dim, green, out_json, red, yellow

GLYPH = {"planning": "…", "running": "▶", "done": green("✓"), "blocked": red("■"), "cancelled": dim("×")}


def _line(j) -> str:
    return f"{GLYPH.get(j['state'], '?')} {j['id']} {j['state']:<9} {j['progress']:>5}  {j['objective'][:70]}"


def cmd_job(a, p) -> int:
    act = a.action
    if act == "run":
        j = JB.create(p, " ".join(a.text), source="cli")
        if a.json:
            out_json(j)
        else:
            print(green("✓ ") + f"{j['id']} delegated. The supervisor plans and runs it; `research job status {j['id']}` shows progress" + dim("  (is the supervisor running? research supervisor start)"))
        return 0
    if act == "list":
        rows = JB.list_all(p)
        if a.json:
            out_json(rows)
        else:
            print("\n".join(_line(j) for j in rows) or dim("no jobs yet: research job run \"…\""))
        return 0
    if not a.text:
        raise ResearchError(f"{act} needs a job id (JOB-001)")
    jid = a.text[0]
    if act == "cancel":
        j = JB.cancel(p, jid)
        print(green("✓ ") + f"{j['id']} cancelled; its plan was stopped and agents cancelled (history is kept)")
        return 0
    if act == "logs":
        r = JB.logs(p, jid)
        if a.json:
            out_json(r)
            return 0
        for c in r["checkpoints"]:
            print(dim(c["ts"]) + f" [{c['state']}] {c['note']}")
        for s in r["steps"]:
            print(bold(f"\n{s['step']} ({s['type']}) {s['status']}") + dim(f" {s.get('task') or ''}"))
            for at in s["attempts"]:
                print(f"  attempt {at['n']} {at['state']} {at['profile']} verified={at['verified']}")
            if s.get("output"):
                print(dim("  " + s["output"].replace("\n", "\n  ")))
        return 0
    j = JB.load(p, jid)
    if a.json:
        out_json(j)
        return 0
    print(bold(_line(j)))
    print(f"  requested by {j['requested_by']} via {j['source']}   planning attempts {j['attempts']}/{j['budget']['max_planning_attempts']}   repairs {j['budget']['repairs']}/{j['budget']['max_repairs']}")
    for acc in j["acceptance"]:
        print(f"  accept: {acc}")
    for s in j["steps"]:
        print(f"  {s['status']:<8} {s['type']:<15} {s.get('key') or s.get('item')} {dim(s.get('task') or '')}")
    if j.get("blocked"):
        print(red("  BLOCKED: ") + j["blocked"]["reason"])
        print(yellow("  smallest decision needed: ") + j["blocked"]["decision"])
    if j.get("result"):
        print(green("  independently verified: ") + str(j["result"]))
    return 0


cmd_job = mut(cmd_job)


def attach(sub, add, group) -> None:
    x = add(sub, "job", cmd_job, "delegate work in words: run \"…\" | status ID | logs ID | cancel ID | list")
    x.add_argument("action", choices=["run", "status", "logs", "cancel", "list"])
    x.add_argument("text", nargs="*", help="run: the request · status/logs/cancel: JOB-001")
