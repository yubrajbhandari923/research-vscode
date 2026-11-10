"""`research heal`: self-healing (supervisor_assistant.self_heal). `check-source` is the deterministic check a repair task is verified against; `status` shows the cases."""
from __future__ import annotations

import os

from research import guide_quarto as GQ, supervisor as SV
from research.util import ResearchError

from ..output import bold, dim, green, out_json, red, yellow


def cmd_heal(a, p) -> int:
    if a.action == "check-source":
        if not a.files:
            raise ResearchError("name the files to check, e.g. `research heal check-source chapter.qmd`")
        bad = []
        for f in a.files:
            path = os.path.join(p.root, f) if not os.path.isabs(f) and not os.path.exists(f) else f
            try:
                text = open(path, encoding="utf-8").read()
            except OSError as ex:
                raise ResearchError(f"{f}: {ex.strerror}")
            bad += [i for i in GQ.text_issues(p, f, text) if i["severity"] == "error"]
        if a.json:
            out_json({"ok": not bad, "issues": [i["message"] for i in bad]})
        for i in bad:
            print(red("✗ ") + i["message"])
        if not bad:
            print(green("✓ ") + f"{len(a.files)} file(s): no executable cells, identifiers in scientific prose or invented attribution")
        return 1 if bad else 0
    from research import selfheal as SH
    rows = []
    for rs in SV.all_runs(p):
        rows += [{"plan": rs["plan_key"], **r} for r in SH.status(rs)]
    if a.json:
        out_json({"enabled": SH.enabled(p), "cases": rows})
        return 0
    print(bold("self-healing: ") + (green("on") if SH.enabled(p) else yellow("off") + dim("  (research config set supervisor_assistant.self_heal true)")))
    for r in rows:
        print(f"  {r['plan']:<12} {r['id']:<22} {str(r['phase']):<10} attempt {r['attempt']}  {r.get('class') or ''}  {r.get('task') or ''}" + (f"  — {r['reason']}" if r.get("reason") else ""))
    if not rows:
        print(dim("  no failures handled yet"))
    return 0


def attach(sub, add, group) -> None:
    x = add(sub, "heal", cmd_heal, "self-healing: status | check-source FILE... (the check a promotion repair task must pass)")
    x.add_argument("action", choices=["status", "check-source"])
    x.add_argument("files", nargs="*")
