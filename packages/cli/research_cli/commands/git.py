"""`research git`: what is uncommitted and why it matters, a managed .gitignore block for generated files, and a reviewed, scoped commit on main."""
from __future__ import annotations

import sys

from research import githygiene as GH
from research.util import ResearchError

from . import mut
from ..output import bold, dim, green, out_json, red, yellow


def cmd_git_status(a, p) -> int:
    r = GH.report(p)
    if a.json:
        out_json(r)
        return 0
    if not r.get("ok"):
        print(yellow(r.get("error") or "git status could not be read"))
        return 1
    m, s = r["main"], r["summary"]
    print(bold(f"Main repository ({m['branch']} @ {m['head']})") + dim(f"   {m['path']}"))
    if not r["attention"]:
        print("  clean: nothing to review")
    for e in r["attention"]:
        print(f"  {e['state']:<9} {e['kind']:<9} {e['path']}")
        print(dim(f"            {e['why']}"))
    for e in m["secrets"]:
        print(red(f"  SECRET    {e['path']}: {e['why']}"))
    if m["untracked_noise"]:
        print(dim(f"  {len(m['untracked_noise'])} generated/internal path(s) are not ignored: " + ", ".join(e["path"] for e in m["untracked_noise"][:5]) + "  → research git ignore --apply"))
    for e in m["ignored_important"]:
        print(yellow(f"  ignored but looks important: {e['path']} ({e['kind']})"))
    print(bold("Experiment worktrees") + dim("   (their verified work is committed on each experiment branch by the supervisor)"))
    for w in r["worktrees"] or []:
        print(f"  {w['id']:<8} {w['experiment'] or '':<9} {w['branch'] or '':<28} " + (f"{w['dirty']} modified, {w['untracked']} untracked, " if w["present"] else "not on this machine, ")
              + f"{w['unpromoted_commits']} unpromoted commit(s)")
    if not r["worktrees"]:
        print("  none")
    print(bold("Pending promotions") + ("" if r["promotions"] else ": none"))
    for x in r["promotions"]:
        print(f"  {x['id']}  {x['state']}  {x.get('experiment') or ''}")
    led = r["ledger"]
    print(bold("Ledger") + dim(f"   {led['path']}" + (" (shared)" if led["shared"] else "")) + ("" if led["uncommitted_files"] is None else f"  {led['uncommitted_files']} uncommitted file(s); checkpoint it with its own commands"))
    print(dim(f"Summary: {s['tracked_changes']} modified, {s['untracked']} untracked to review, {s['dirty_worktrees']} dirty worktree(s), {s['unpromoted_commits']} unpromoted commit(s), {s['pending_promotions']} pending promotion(s)."))
    print(dim("Review and commit: research git commit --paths notes.md   ·   generated scaffolding: research git commit --scaffold"))
    return 0


def cmd_git_preflight(a, p) -> int:
    r = GH.preflight(p, a.purpose)
    if a.json:
        out_json(r)
    else:
        for l in GH.lines(r) or ["Git preflight: nothing to report."]:
            print(l)
    return 0 if r["ok"] else 2


@mut
def cmd_git_ignore(a, p) -> int:
    r = GH.ignore_apply(p, a.pattern) if a.apply else GH.ignore_plan(p, a.pattern)
    if a.json:
        out_json(r)
        return 0
    for x in r["refused"]:
        print(yellow(f"  refused {x['pattern']}: {x['why']} ({', '.join(x['would_hide'][:3])})"))
    if not r["add"]:
        print("Nothing to add: the generated and internal files are already ignored.")
    else:
        print((green("✓ ") + "wrote" if r.get("written") else bold("Would add")) + f" to .gitignore (managed block; your own lines are untouched): " + ", ".join(r["add"]))
        if not r.get("written"):
            print(dim("Run again with --apply to write it. Research OS never ignores source, plans, reports or documentation."))
    if r["note"]:
        print(dim(r["note"]))
    return 0


@mut
def cmd_git_commit(a, p) -> int:
    pv = GH.preview_commit(p, a.paths, a.scaffold, a.message)
    if a.json and not a.yes:
        out_json(GH.commit(p, a.paths, a.scaffold, a.message, confirm=False))
        return 0
    for x in pv["refused"]:
        print(yellow(f"  not committed: {x['path']}: {x['why']}"))
    if not pv["files"]:
        print("Nothing to commit." + ("" if a.paths or a.scaffold else " Name files with --paths or use --scaffold."))
        return 0 if (a.paths or a.scaffold) else 1
    if not a.json:
        print(bold(f"Staged diff of {len(pv['files'])} file(s): {', '.join(pv['files'])}"))
        print(pv["stat"])
        if not a.stat_only:
            print(pv["diff"])
        print(dim(f"Message: {pv['message']}   (nothing else is committed; other changes stay as they are; nothing is pushed)"))
    ok = a.yes
    if not ok and sys.stdin.isatty() and sys.stdout.isatty() and not a.json:
        ok = input("Commit exactly these files? [y/N] ").strip().lower() in ("y", "yes")
        if not ok:
            print("Not committed.")
            return 1
    if not ok:
        print(yellow("Review the diff above, then repeat with --yes to commit it."))
        return 2
    r = GH.commit(p, a.paths, a.scaffold, a.message, confirm=True)
    if a.json:
        out_json(r)
    else:
        print(green("✓ ") + f"committed {r['commit'][:8]}: {len(r['files'])} file(s). Not pushed.")
    return 0


def attach(sub, add, group) -> None:
    g = group("git", "git hygiene: status | preflight | ignore | commit (reviewed, scoped, never pushes)")
    add(g, "status", cmd_git_status, "main repo, experiment worktrees, promotions and ledger: what is uncommitted and why it matters")
    x = add(g, "preflight", cmd_git_preflight, "the check run before a plan starts/resumes or promotion is authorized (exit 2 when blocked)")
    x.add_argument("--purpose", choices=("plan", "promotion"), default="plan")
    x = add(g, "ignore", cmd_git_ignore, "preview (default) or --apply a managed .gitignore block for generated and internal files")
    x.add_argument("--apply", action="store_true")
    x.add_argument("--pattern", action="append", help="also ignore this pattern (refused if it would hide an important file)")
    x = add(g, "commit", cmd_git_commit, "show the staged diff of the named files; --yes commits exactly those files on main (humans only, never pushes)")
    x.add_argument("--paths", nargs="+", help="files to commit (relative to the repository)")
    x.add_argument("--scaffold", action="store_true", help="the scaffolding Research OS generated (AGENTS.md, CLAUDE.md, .gitignore, website sources ...)")
    x.add_argument("-m", "--message", help="commit message")
    x.add_argument("--yes", action="store_true", help="confirm after reviewing the diff (required when not at a terminal)")
    x.add_argument("--stat-only", action="store_true", help="show only the file summary, not the full diff")
