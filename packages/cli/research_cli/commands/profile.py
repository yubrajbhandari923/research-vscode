"""`research profile`: named bundles of settings (machine level), applied per project, compared, and checked for "safe to leave overnight"."""
from __future__ import annotations

from research import profiles as PF
from research.util import ResearchError

from . import mut
from ..output import bold, dim, green, out_json, red, yellow


def _val(v) -> str:
    return "on" if v is True else "off" if v is False else "unset" if v is None else str(v)


def _needs_project(a) -> bool:
    return a.action in ("apply", "revert", "status", "check") or (a.action == "diff" and not a.other) or (a.action == "create" and a.from_project)


def _rows(rows, left="profile", right="now") -> None:
    for r in rows:
        mark = yellow("≠") if r["differs"] else green("=")
        print(f"  {mark} {r['key']:<46} {right} {_val(r['current']):<10} {left} {_val(r['profile'])}")


def cmd_profile(a, p=None) -> int:
    act = a.action
    if act == "list":
        rows = PF.list_profiles()
        if a.json:
            out_json(rows)
            return 0
        for r in rows:
            print(f"  {bold(r['name']):<22} {'built-in' if r['builtin'] else 'yours':<9} {r['settings']:>2} settings  {dim(r['description'][:90])}")
        if p:
            s = PF.status(p)
            print(dim(f"\n  this project: " + (f"{s['applied']} applied" + (f", {len(s['drift'])} setting(s) changed since" if s.get("drift") else "") if s.get("applied") else "no profile applied")))
        return 0
    if act == "check":
        r = PF.check(p, a.name or "overnight")
        if a.json:
            out_json(r)
            return 0 if r["ready"] else 3
        print(bold(r["verdict"]))
        for c in r["checks"]:
            g = {"ok": green("ok  "), "warn": yellow("warn"), "fail": red("FAIL")}[c["status"]]
            print(f"  {g} {c['what']}" + (dim(f"\n        fix: {c['fix']}") if c.get("fix") and c["status"] != "ok" else ""))
        return 0 if r["ready"] else 3
    if act == "status":
        s = PF.status(p)
        if a.json:
            out_json(s)
            return 0
        if not s.get("applied"):
            print("no profile applied to this project (`research profile apply NAME`)")
            return 0
        print(bold(f"{s['applied']}") + dim(f" applied {s['at']}") + (yellow(f"  {len(s['drift'])} setting(s) changed since") if s.get("drift") else green("  unchanged since")))
        for d in s.get("drift") or []:
            print(f"  ≠ {d['key']:<46} now {_val(d['current'])}  profile {_val(d['profile'])}")
        return 0
    if act == "revert":
        r = PF.revert(p, confirm=a.yes)
        print(green("✓ ") + f"{r['reverted']} reverted: {len(r['restored'])} setting(s) restored")
        return 0
    if not a.name:
        raise ResearchError(f"{act} needs a profile name", hint="`research profile list`")
    if act == "show":
        pr = PF.get(a.name)
        if a.json:
            out_json(pr)
            return 0
        print(bold(pr["name"]) + dim(f"  ({'built-in' if pr['builtin'] else 'yours, machine level'})"))
        print("  " + pr["description"])
        for k, v in pr["settings"].items():
            print(f"  {k:<46} {_val(v)}" + (dim("   (must match for `check` to pass)") if k in pr["critical"] else ""))
        return 0
    if act == "diff":
        d = PF.diff(p, a.name, a.other, a.all)
        if a.json:
            out_json(d)
            return 0
        print(bold(f"{d['left']}  vs  {d['right']}") + dim(f"   {d['differing']} of {d['total']} differ"))
        _rows(d["rows"], left=("profile" if not a.other else a.other), right=("now" if not a.other else a.name))
        return 0
    if act == "apply":
        over = PF.parse_pairs(a.set)
        pv = PF.preview(p, a.name, over)
        if a.json and not a.yes:
            out_json(pv)
            return 0
        if not a.yes:
            print(bold(f"{a.name} on this project") + dim(f"   {len(pv['changes'])} setting(s) would change, {pv['unchanged']} already match"))
            _rows([r for r in pv["rows"] if r["differs"]])
            for k in pv["turns_on"]:
                print(yellow(f"  turns ON: {k}"))
            print(dim("  preview only; add --yes to apply" + (" (policy settings: asks for your PIN once)" if pv["needs_pin"] else "")))
            return 0
        from research import approval
        pin = approval.read_pin() if pv["needs_pin"] and approval.is_set() else None
        r = PF.apply(p, a.name, over, confirm=True, pin=pin, reason=a.note)
        print(green("✓ ") + f"{a.name} applied: {len(r['changes'])} setting(s) changed, {r['unchanged']} already matched" + dim(f"   check it: research profile check {a.name}"))
        return 0
    if act == "create":
        pr = PF.create(a.name, a.note or "", a.base, PF.parse_pairs(a.set), p if a.from_project else None)
        print(green("✓ ") + f"profile {a.name} saved on this machine ({len(pr['settings'])} settings); every project can apply it: research profile apply {a.name}")
        return 0
    if act == "delete":
        PF.delete(a.name)
        print(green("✓ ") + f"profile {a.name} deleted (projects that applied it keep their settings)")
        return 0
    raise ResearchError("unknown profile action")


cmd_profile = mut(cmd_profile)


def attach(sub, add, group) -> None:
    x = add(sub, "profile", cmd_profile, "settings profiles: list | show NAME | diff NAME [OTHER] | apply NAME [--set k=v] | check [NAME] | status | revert | create NAME | delete NAME (machine level; applied per project)",
            needs_project=_needs_project)
    x.add_argument("action", choices=["list", "show", "diff", "apply", "check", "status", "revert", "create", "delete"])
    x.add_argument("name", nargs="?", help="the profile (default / interactive / overnight / yours)")
    x.add_argument("other", nargs="?", help="diff: a second profile to compare with (default: this project)")
    x.add_argument("--set", action="append", metavar="KEY=VALUE", help="apply: override a setting for this project · create: set it in the new profile (repeatable)")
    x.add_argument("--base", help="create: start from this profile")
    x.add_argument("--from-project", action="store_true", help="create: start from this project's current values")
    x.add_argument("--all", action="store_true", help="diff: show settings that match too")
    x.add_argument("--yes", action="store_true", help="apply / revert: do it (without it apply only previews)")
    x.add_argument("--note", help="why / description (recorded)")
