"""`research decide` / `research decisions`: answer a decision request (an option key or free text) or list the open ones. Thin over research.decisions."""
from __future__ import annotations

from research import decisions as D
from research.util import ResearchError

from . import mut
from ..output import bold, dim, green, out_json, yellow


def _show(d) -> list:
    out = [bold(f"{d['id']} {d['status']}") + dim(f"  {d['subject'].get('id') or ''}  by {d['created_by']}"), d["question"]]
    for o in d["options"]:
        out.append(f"  {o['key']}. {o['label']}" + (yellow("  [needs PIN, computer only]") if o["protected"] else "") + (dim("  (default)") if d.get("default") == o["key"] else ""))
        out += [dim("     " + x) for x in ([o["consequence"]] if o["consequence"] else []) + o["effect"][:3]]
    if d.get("answer"):
        out.append(green("answered ") + f"by {d['answer']['by']} via {d['answer']['via']}: {d['answer']['outcome']}")
    return out


def cmd_decisions(a, p) -> int:
    ds = D.list_all(p, None if a.all else "open")
    if a.json:
        out_json(ds)
    else:
        for d in ds:
            print(f"{d['id']} {d['status']:<9} {(d['subject'].get('id') or '-'):<8} {d['question'][:70]}")
        if not ds:
            print("no open decision requests")
    return 0


def cmd_decide(a, p) -> int:
    d = D.load(p, a.id)
    if a.answer is None:
        if a.json:
            out_json(d)
        else:
            print("\n".join(_show(d)))
        return 0
    key = None if a.text or a.answer.lower() not in {o["key"].lower() for o in d["options"]} else a.answer
    r = D.answer(p, a.id, key=key, text=None if key else a.answer, via="local", note=a.note)
    if a.json:
        out_json(r)
    else:
        print(green("✓ ") + r["outcome"] + (dim("  (already answered elsewhere)") if r.get("already") else ""))
    return 0


cmd_decide = mut(cmd_decide)


def attach(sub, add, group) -> None:
    x = add(sub, "decisions", cmd_decisions, "open decision requests (what the supervisor or an agent is waiting for you to decide)")
    x.add_argument("--all", action="store_true", help="include answered and expired ones")
    x = add(sub, "decide", cmd_decide, "answer a decision request: decide DR-001 [KEY | \"free text\"] (no answer: show it)")
    x.add_argument("id")
    x.add_argument("answer", nargs="?", help="an option key, or free text for the supervisor")
    x.add_argument("--text", action="store_true", help="treat the answer as text even if it equals an option key")
    x.add_argument("--note", help="why (needed when the option is protected)")
