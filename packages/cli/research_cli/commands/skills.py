"""Agent skills and the AGENTS.md block."""
from __future__ import annotations

import os
import sys

from research import services as S
from research.util import ResearchError

from . import mut
from ..output import _done, bold, dim, green, out_json, red, yellow


def cmd_skills_list(a, p) -> int:
    from research import skills as SKL
    rows = SKL.list_skills(p)
    if a.json:
        out_json(rows)
    else:
        for s in rows:
            tags = ([green("always")] if s["always"] else []) + ([yellow(s["level"])] if s["level"] != "recommended" else []) + ([f"applies to {', '.join(s['applies_to'])}"] if s["applies_to"] else [])
            src = dim(f" ← {s['source']}" + (f"@{s['ref'][:8]}" if s.get("ref") else "")) if s.get("source") else ""
            print(f"{bold(s['name'])}  {dim(s['path'])}{src}" + (f"  {' · '.join(tags)}" if tags else ""))
            print(f"    {s['description'][:200]}")
    return 0


def cmd_skills_show(a, p) -> int:
    from research import skills as SKL
    s = SKL.get_skill(p, a.name)
    out_json(s) if a.json else print(s["markdown"])
    return 0


def cmd_skills_new(a, p) -> int:
    s = S.create_skill(p, a.name, a.description or "")
    _done(a, s, msg=f"skill {s['name']} → {s['path']}")
    return 0


def cmd_skills_duplicate(a, p) -> int:
    s = S.duplicate_skill(p, a.src, a.name)
    _done(a, s, msg=f"skill {s['name']} → {s['path']}")
    return 0


def cmd_agents_md(a, p) -> int:
    from research import agentsmd as AM
    if a.preview:
        st = AM.state(p)
        if a.json:
            out_json({k: st[k] for k in ("path", "action", "problem", "up_to_date", "new")})
        else:
            print(st["new"])
        return 1 if st["problem"] else 0
    if a.diff:
        st = AM.state(p)
        if st["problem"]:
            print(red("✗ ") + f"refusing to touch AGENTS.md: {st['problem']}", file=sys.stderr)
            print(dim("  Keep exactly one `<!-- research:begin … -->` and one `<!-- research:end -->` line."),
                  file=sys.stderr)
            return 1
        d = AM.diff(p)
        if a.json:
            out_json({"path": st["path"], "action": st["action"], "up_to_date": st["up_to_date"], "diff": d})
        elif d:
            sys.stdout.write(d)
        else:
            print(dim("AGENTS.md managed block is already up to date"))
        return 0
    r = AM.apply(p)
    _done(a, r, msg=(f"{os.path.relpath(r['path'])} already up to date" if not r["changed"]
                     else f"{os.path.relpath(r['path'])}: managed block {r['action']}d "
                          "(bytes outside the markers untouched)"))
    return 0


@mut
def cmd_skills_add(a, p) -> int:
    from research import skills as SKL
    r = SKL.add_skills(p, a.source, a.only, a.force, True if a.always else None, a.applies_to, link=not a.no_link,
                       list_only=a.list)
    if a.json:
        out_json(r)
        return 0
    if a.list:
        for s_ in r["available"]:
            print(f"  {bold(s_['name'])}  {dim(s_['subpath'] or '.')}\n    {s_['description'][:160]}")
        print(dim(f"{len(r['available'])} skills; import some with --only NAME …"))
        return 0
    print(green("✓ ") + f"imported {', '.join(r['added'])}")
    for where, rep_ in (r.get("links") or {}).items():
        if rep_["skipped"]:
            print(yellow(f"  {where}: left alone (not ours): {', '.join(rep_['skipped'])}"))
    return 0


@mut
def cmd_skills_remove(a, p) -> int:
    from research import skills as SKL
    r = SKL.remove_skill(p, a.name)
    _done(a, r, msg=f"removed {r['removed']} (kept in {r['kept_at']})")
    return 0


@mut
def cmd_skills_update(a, p) -> int:
    from research import skills as SKL
    r = SKL.update_skills(p, a.names)
    _done(a, r, msg="updated " + (", ".join(r["updated"]) or "nothing"))
    return 0


@mut
def cmd_skills_set(a, p) -> int:
    from research import skills as SKL
    r = SKL.set_skill(p, a.name, a.always, a.applies_to, a.level, a.check)
    _done(a, r, msg=f"{r['name']}: level={r['level']}, always={r['always']}, applies_to={', '.join(r['applies_to']) or '—'}")
    return 0


def cmd_skills_verify(a, p) -> int:
    from research import skills as SKL
    r = SKL.run_checks(p, a.name)
    if a.json:
        out_json(r)
    else:
        for c in r["checks"]:
            print((green("✓ ") if c["ok"] else red("✗ ")) + c["check"] + ("" if c["ok"] else f"  → {c['output']}"))
        print(f"{r['skill']} [{r['level']}] sha256 {r['sha256'][:12]}: {r['verdict']}"
              + (dim("  (no machine-checkable requirements declared: nothing verified)") if r["verdict"] == "no_checks" else ""))
    return 0 if r["verdict"] == "pass" else 1


def cmd_skills_audit(a, p) -> int:
    from research import skills as SKL
    r = SKL.audit(p)
    if a.json:
        out_json(r)
        return 0
    for s in r["skills"]:
        flags = [("claude" if s["discoverable_claude"] else None), ("codex" if s["discoverable_agents"] else None),
                 ("AGENTS.md" if s["named_in_agents_md"] else None)]
        print(f"{bold(s['name']):30} {s['level']:12} discoverable: {', '.join(f for f in flags if f) or 'no'}")
    for x in r["problems"]:
        print(yellow("  ! " + x))
    print(dim(r["note"]))
    return 0


def cmd_skills_catalog(a, p) -> int:
    from research import packs as PK
    st = PK.state(p)
    q = a.search or ""
    rows = [r for r in st["catalog"] if (not q or PK.matches(r, q)) and (not a.pack or a.pack in (r["packs"] or []))
            and (not a.kind or r["kind"] == a.kind)]
    if a.json:
        out_json(rows)
        return 0
    for r in rows:
        flag = green("on ") if r["enabled"] else yellow("off") if r["installed"] else dim("   ")
        print(f"{flag} {bold(r['name']):32} {r['kind']:11} {(r['license'] or '?')[:10]:10} {dim(','.join(r['packs'] or []))}")
        if a.verbose:
            print(dim(f"      {(r['description'] or '')[:160]}"))
    print(dim(f"{len(rows)} skill(s). on = installed and enabled · off = installed, disabled · blank = in the catalog, not installed (`research skills install NAME`)"))
    return 0


def cmd_skills_packs(a, p) -> int:
    from research import packs as PK
    act, pid = a.verb, a.pack_id
    if act in ("enable", "disable"):
        if not pid:
            raise ResearchError(f"usage: research skills packs {act} PACK-ID")
        r = PK.enable_pack(p, pid, a.optional) if act == "enable" else PK.disable_pack(p, pid)
        if act == "enable":
            bad = [x for x in r["results"] if x["state"] == "failed"]
            _done(a, r, msg=f"pack {pid}: " + ", ".join(f"{x['name']} {x['state']}" for x in r["results"]) + ("" if not bad else red("  (failed: " + "; ".join(x['why'] for x in bad) + ")")))
        else:
            _done(a, r, msg=f"pack {pid} off; skills disabled: {', '.join(r['disabled']) or 'none'} (files kept)")
        return 0
    st = PK.state(p)
    if a.json:
        out_json(st["packs"])
        return 0
    for x in st["packs"]:
        print(f"{green('on ') if x['enabled'] else dim('off')} {bold(x['id']):22} {x['installed']}/{x['total']} installed  {dim(x['description'])}")
    print(dim("`research skills packs enable ID` installs a pack (add --optional for skills installed from their source); the project or machine config key is skills.packs"))
    return 0


@mut
def cmd_skills_install(a, p) -> int:
    from research import packs as PK
    from research import skills as SKL
    out = [PK.install_skill(p, n, None, a.force) for n in a.names]
    SKL.link_skills(p)
    _done(a, out, msg="; ".join(f"{x['name']}: {x['state']}" + (f" ({x['why']})" if x.get("why") else "") for x in out))
    return 0


@mut
def cmd_skills_toggle(a, p) -> int:
    from research import packs as PK
    r = [PK.set_enabled(p, n, a.cmd_name == "enable") for n in a.names]
    _done(a, r, msg=f"{', '.join(a.names)} {'enabled' if a.cmd_name == 'enable' else 'disabled'} (files kept; `research skills link` already refreshed)")
    return 0


@mut
def cmd_skills_upgrade(a, p) -> int:
    from research import packs as PK
    r = PK.update(p)
    _done(a, r, msg=("updated " + ", ".join(r["updated"]) if r["updated"] else "bundled skills are current")
          + (yellow(f"; left alone because you changed them: {', '.join(r['kept_yours'])}") if r["kept_yours"] else ""))
    return 0


def cmd_skills_link(a, p) -> int:
    from research import skills as SKL
    r = SKL.link_skills(p, a.target)
    if a.json:
        out_json(r)
        return 0
    for where, rep_ in r.items():
        print(green("✓ ") + f"{where}: {len(rep_['linked'])} linked"
              + (yellow(f", left alone: {', '.join(rep_['skipped'])}") if rep_["skipped"] else "")
              + (dim(f", removed stale: {', '.join(rep_['removed'])}") if rep_["removed"] else ""))
    return 0


def _agents_md_args(x) -> None:
    x.add_argument("--preview", action="store_true", help="print the managed block this version generates")
    x.add_argument("--diff", action="store_true", help="unified diff of the current vs. the new block")
    x.add_argument("--apply", action="store_true", help="write the managed region (the default action)")


def attach(sub, add, group) -> None:
    g = group("skills", "agent skills (.research/skills/)", aliases=["skill"])
    add(g, "list", cmd_skills_list, "list skills", aliases=["ls"])
    x = add(g, "show", cmd_skills_show, "print a skill's SKILL.md")
    x.add_argument("name")
    x = add(g, "add", cmd_skills_add, "import skill(s) from a folder or git repo (URL, owner/repo, …/tree/main/skills/x)",
            aliases=["import"])
    x.add_argument("source")
    x.add_argument("--only", nargs="+", metavar="NAME", help="import only these skills from a multi-skill repo")
    x.add_argument("--list", action="store_true", help="show the skills in the source without importing")
    x.add_argument("--force", action="store_true", help="replace existing skills (old copy kept in cache)")
    x.add_argument("--always", action="store_true", help="always-on: listed for every agent session")
    x.add_argument("--applies-to", nargs="+", metavar="TYPE", help="task types/roles it is suggested for")
    x.add_argument("--no-link", action="store_true", help="don't expose it in .claude/skills / .agents/skills")
    x = add(g, "remove", cmd_skills_remove, "remove a skill (moved to .research/cache/removed-skills)", aliases=["rm"])
    x.add_argument("name")
    x = add(g, "update", cmd_skills_update, "re-import skills from their recorded source")
    x.add_argument("names", nargs="*")
    x = add(g, "set", cmd_skills_set, "project settings for a skill")
    x.add_argument("name")
    x.add_argument("--always", dest="always", action="store_true", default=None)
    x.add_argument("--no-always", dest="always", action="store_false")
    x.add_argument("--applies-to", nargs="*", metavar="TYPE")
    x.add_argument("--level", choices=["recommended", "required", "protocol"],
                   help="required/protocol skills block managed dispatch when missing and are injected with a hash")
    x.add_argument("--check", action="append", metavar="'research …'", help="protocol skills: a command that must exit 0 (repeatable)")
    x = add(g, "verify", cmd_skills_verify, "run a protocol skill's machine-checkable requirements")
    x.add_argument("name")
    add(g, "audit", cmd_skills_audit, "which skills gate managed dispatch, and what direct Claude Code/Codex sessions can see")
    x = add(g, "link", cmd_skills_link, "(re)create .claude/skills and .agents/skills links")
    x.add_argument("--target", action="append", help="claude, agents, or a folder")
    x = add(g, "catalog", cmd_skills_catalog, "the skills catalog: first-party, bundled (attributed copies) and install-from-source entries")
    x.add_argument("--search")
    x.add_argument("--pack")
    x.add_argument("--kind", choices=["first-party", "bundled", "external"])
    x.add_argument("-v", "--verbose", action="store_true")
    x = add(g, "packs", cmd_skills_packs, "skill packs: list | enable ID [--optional] | disable ID")
    x.add_argument("verb", nargs="?", choices=["list", "enable", "disable"], default="list")
    x.add_argument("pack_id", nargs="?")
    x.add_argument("--optional", action="store_true", help="enable: also install the pack's install-from-source skills (network, pinned commit)")
    x = add(g, "install", cmd_skills_install, "install catalog skill(s) (bundled copy, or fetched from the source at the pinned commit)")
    x.add_argument("names", nargs="+")
    x.add_argument("--force", action="store_true", help="replace a copy you changed (it is kept in .research/cache/removed-skills)")
    x = add(g, "enable", cmd_skills_toggle, "turn a skill on (it stays on disk either way)")
    x.add_argument("names", nargs="+")
    x.set_defaults(cmd_name="enable")
    x = add(g, "disable", cmd_skills_toggle, "turn a skill off: not suggested, not linked; files kept")
    x.add_argument("names", nargs="+")
    x.set_defaults(cmd_name="disable")
    add(g, "upgrade", cmd_skills_upgrade, "bring bundled and first-party skills to this release's version (never replaces a skill you changed)")
    x = add(g, "new", cmd_skills_new, "create a skill", aliases=["create"])
    x.add_argument("name")
    x.add_argument("-d", "--description")
    x = add(g, "duplicate", cmd_skills_duplicate, "copy a skill")
    x.add_argument("src")
    x.add_argument("name")

