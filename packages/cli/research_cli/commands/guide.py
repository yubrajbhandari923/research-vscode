"""Scientist Guide, setup and reports."""
from __future__ import annotations

import os
import sys

from research.util import NotFound, ResearchError, human_size

from . import mut
from ..output import _done, bold, dim, green, out_json, red, yellow


@mut
def _src_list(a) -> list:
    return list(getattr(a, "source", None) or [])


def _guide_prov(a, p) -> int:
    """claim | figure | section | validate | coverage: the provenance side of the Scientist Guide."""
    from research import provenance as PV
    act, args = a.action, list(a.ids)
    verb = args[0] if args else ("list" if act != "validate" and act != "coverage" else "")
    if act == "claim" and verb == "add":
        if len(args) < 2 or not a.kind or not a.section:
            raise ResearchError('usage: research guide claim add "<statement>" --kind observation|… --section chapter.qmd '
                                '--source F-003 [--source challenges:F-004] [--figure FIG-001] [--historical]')
        r = PV.claim_add(p, args[1], a.kind, a.section, _src_list(a), a.figure, a.historical)
        _done(a, r, msg=f"{r['id']} ({r['kind']}) → {r['section']}; mark it in the text as [..]{{#{r['id'].lower()} .claim}}")
    elif act == "claim" and verb in ("retire", "update"):
        r = PV.claim_update(p, args[1], status="retired" if verb == "retire" else None, kind=a.kind, section=a.section,
                            sources=_src_list(a) or None, figures=a.figure or None, historical=a.historical or None)
        _done(a, r, msg=f"{r['id']} {r['status']}")
    elif act == "claim" and verb in ("list", "show"):
        cl = PV.load_claims(p)
        if verb == "show" and len(args) > 1:
            if args[1].upper() not in cl:
                raise NotFound(f"Claim {args[1]} not found")
            cl = {args[1].upper(): cl[args[1].upper()]}
        v = PV.validate(p)["claims"]
        if a.json:
            out_json({k: {**c, "validation": v.get(k)} for k, c in cl.items()})
        else:
            for k, c in cl.items():
                print(f"{bold(k)} [{c['kind']}] {v[k]['status']:12} {c['section']}  {c['statement'][:100]}")
                if verb == "show":
                    for s in c["sources"]:
                        print(f"    {s['role']:10} {s['ref']}")
                    for i in v[k]["issues"]:
                        print(f"    {i['severity']}: {i['message']}")
                    for m in v[k]["stale"]:
                        print(yellow(f"    stale: {m}"))
    elif act == "figure" and verb == "add":
        if len(args) < 2 or not (a.fig_kind and a.section and a.label and a.title):
            raise ResearchError("usage: research guide figure add FILE --fig-kind archived|recreated|derived|conceptual "
                                "--section chapter.qmd --label fig-name --title \"…\" [--source A-0001 …] [--script x.py --input data.csv]")
        gen = {"script": a.script, "inputs": a.input} if (a.script or a.input) else None
        r = PV.figure_add(p, a.fig_kind, args[1], a.section, a.label, a.title, _src_list(a), gen)
        _done(a, r, msg=f"{r['id']} ({r['kind']}) {r['label']}")
    elif act == "figure" and verb == "refresh":
        if len(args) < 2:
            raise ResearchError("usage: research guide figure refresh FIG-001 [--notes TEXT]   (after regenerating it on purpose)")
        r = PV.figure_refresh(p, args[1], a.notes)
        _done(a, r, msg=f"{r['id']} re-pinned to the regenerated file ({len(r['history'])} earlier version(s) kept)")
    elif act == "figure":
        fg = PV.load_figures(p)
        out_json(fg) if a.json else [print(f"{bold(k)} [{f['kind']}] {f['label']}  {f['file']}  {f['title']}") for k, f in fg.items()]
    elif act == "section" and verb == "pin":
        r = PV.pin_section(p, args[1])
        _done(a, r, msg=f"{r['section']}: re-pinned {r['claims_repinned']} claim(s) to the current records")
    elif act == "section" and verb == "set":
        r = PV.section_set(p, args[1], a.owner, _src_list(a) or None)
        _done(a, r, msg=f"{r['section']}: owner {r['owner']}, {len(r['depends_on'])} dependencies")
    elif act == "section":
        v = PV.validate(p)["sections"]
        if a.json:
            out_json(v)
        else:
            for k, s in v.items():
                print(f"{bold(k)} [{s['owner']}] {s['state']}" + ("  (prose edited since last pin)" if s["edited_since_pin"] else ""))
                for m in s["stale_because"] + s["errors"] + s["contains_errors"]:
                    print(dim("    " + m))
    elif act == "coverage":
        out = PV.coverage(p)
        if a.json:
            out_json(out)
        else:
            print("experiments not yet in the guide: " + (", ".join(out["uncited_experiments"]) or "none"))
            print("findings not yet in the guide:    " + (", ".join(out["uncited_findings"]) or "none"))
    elif act == "validate":
        r = PV.validate(p)
        if a.json:
            out_json(r)
        else:
            for sect in ("claims", "figures"):
                for k, x in r[sect].items():
                    mark = green("✓") if x["status"] in ("traceable", "ok", "historical") else red("✗") if any(
                        i["severity"] == "error" for i in x["issues"]) else yellow("~")
                    print(f"{mark} {k} {x['status']}")
                    for i in x["issues"]:
                        if i["severity"] != "info":
                            print(f"    {i['severity']}: {i['message']}")
                    for m in x["stale"]:
                        print(yellow(f"    stale: {m}"))
            for i in r["prose"]:
                print(red(f"✗ {i['message']}"))
            print(f"{r['errors']} error(s), {r['warnings']} warning(s), {r['stale']} stale · uncited experiments: "
                  f"{', '.join(r['coverage']['uncited_experiments']) or 'none'}")
            print(dim(r["note"]))
        return 0 if r["ok"] else 1
    else:
        raise ResearchError(f"unknown {act} action {verb!r}")
    return 0


def _guide_review(a, p) -> int:
    """review request|record|approve|status: independent scientific review and human approval of the guide's chapters."""
    from research import guide_review as GR
    verb = a.ids[0] if a.ids else "status"
    chap = a.ids[1] if len(a.ids) > 1 else a.section
    if verb == "status":
        st = GR.status(p)
        if a.json:
            out_json(st)
        else:
            for k, x in st.items():
                d = x["deterministic"]
                print(f"{bold(k)}  {x['label']}  (provenance: {d['state']}, {d['errors']} error(s), {d['stale']} stale)")
                for kind, who in (("model_review", "reviewer"), ("human_approval", "by")):
                    e = x[kind]
                    if e:
                        print(dim(f"    {kind.replace('_', ' ')}: {e.get('verdict', 'approved')} · {e[who]} · {e['at'][:10]}"
                                  + ("" if e["current"] else " · OUTDATED (chapter or evidence changed since)")))
            print(dim("  Deterministic provenance, independent model review and human approval are separate; only approval approves."))
    elif verb == "request":
        r = GR.request(p, chap, a.profile, getattr(a, "model", None), a.strategy)
        _done(a, r, msg="reviewer session prepared: " + " ".join(r["argv"]))
    elif verb == "record":
        if not chap or not a.verdict or not a.reviewer:
            raise ResearchError("usage: research guide review record CHAPTER.qmd --verdict sound|concerns|unsound --reviewer NAME "
                                "[--rating CRITERION=ok|concern|fail|not_assessed …] [--concern TEXT …] [--notes TEXT]")
        ratings = dict(x.split("=", 1) for x in (a.rating or []) if "=" in x)
        r = GR.record(p, chap, a.verdict, a.reviewer, ratings, a.notes, a.concern, getattr(a, "model", None))
        _done(a, r, msg=f"{r['section']}: independent review recorded ({r['verdict']}). This is not approval.")
    elif verb == "approve":
        if not chap or not a.by:
            raise ResearchError("usage: research guide review approve CHAPTER.qmd --by YOUR-NAME [--notes TEXT] [--force]")
        r = GR.approve(p, chap, a.by, a.notes, a.force)
        _done(a, r, msg=f"{r['section']}: approved by {r['by']}")
    else:
        raise ResearchError("usage: research guide review status|request|record|approve")
    return 0


def _guide_quarto(a, p) -> int:
    """quarto init|prepare|build|preview|doctor · chapter add"""
    from research import guide_quarto as GQ
    verb = a.ids[0] if a.ids else ("doctor" if a.action == "quarto" else "")
    if a.action == "chapter" and verb == "add" and len(a.ids) > 1:
        r = GQ.chapter_add(p, a.ids[1], a.title, a.owner)
        _done(a, r, msg=f"{r['chapter']} " + ("created and " if r["file_created"] else "") + ("added to the book" if r["added_to_book"] else "already in the book"))
    elif a.action == "quarto" and verb == "init":
        return cmd_guide(type("A", (), {**vars(a), "action": "init"})(), p)
    elif a.action == "quarto" and verb == "prepare":
        r = GQ.prepare(p)
        _done(a, r, msg=f"{r['files_written']} generated file(s) updated in {r['generated']}")
    elif a.action == "quarto" and verb == "doctor":
        r = GQ.doctor(p)
        if a.json:
            out_json(r)
        else:
            print((green("✓ ") if r["ok"] else red("✗ ")) + f"Quarto {r['version'] or 'not found'}" + (f" at {r['quarto']}" if r["quarto"] else ""))
            print(f"  HTML: {'yes' if r['html'] else 'no'} · PDF (Typst): {'yes' if r['pdf'] else 'no'}")
            for x in r["problems"] + r["hints"]:
                print(yellow("  " + x))
        return 0 if r["ok"] else 1
    elif a.action == "quarto" and verb in ("build", "preview"):
        eds = ["scientific", "researcher"] if a.edition == "both" else [a.edition or "scientific"]
        if verb == "preview":
            import subprocess
            argv = GQ.preview_argv(p, eds[0])
            p.close()
            return subprocess.call(argv)
        rc, results = 0, []
        for ed in eds:
            r = GQ.build(p, ed, a.pdf, a.allow_invalid)
            results.append(r)
            if not a.json:
                print((green("✓ ") if r["ok"] else red("✗ ")) + f"{ed} edition → {r['html']['dir']}"
                      + (f"  ({r['validation']['errors']} provenance error(s))" if r["validation"]["errors"] else ""))
                for l in r.get("leaks") or []:
                    print(red(f"    identifier {l['identifier']} visible in {l['file']}"))
                for w in r["warnings"]:
                    print(yellow("  " + w))
                if not r["ok"] and not r.get("leaks"):
                    print(dim(r["html"]["log"][-600:]))
            rc = rc or (0 if r["ok"] else 1)
        if a.json:
            out_json(results)
        return rc
    else:
        raise ResearchError("usage: research guide quarto prepare|build|preview|doctor  ·  research guide chapter add PATH [--title …]")
    return 0


def cmd_guide(a, p) -> int:
    """`research guide`: the Quarto Scientist Guide. Without an action it shows status."""
    from research import guide as G
    act = a.action
    if act in ("claim", "figure", "section", "validate", "coverage"):
        return _guide_prov(a, p)
    if act in ("quarto", "chapter"):
        return _guide_quarto(a, p)
    if act == "review":
        return _guide_review(a, p)
    ids = list(a.ids or [])
    if act == "init":
        r = G.init(p, dry_run=a.dry_run)
        if a.json:
            out_json(r)
        elif r.get("dry_run"):
            for f in r["files"]:
                print(("exists   " if f["exists"] else "would add") + "  " + f["path"])
        else:
            print(green("✓ ") + f"Scientist Guide in {r['guide_dir']}: {len(r['created'])} file(s) created, {len(r['left_untouched'])} left untouched")
            if r["workflow"]:
                print(dim("  publish on every push: GitHub → Settings → Pages → Source: GitHub Actions (once)"))
            print(dim("  next: " + " · ".join(r["next"])))
    elif act == "highlights":
        hl = G.highlights(p)
        if a.json:
            out_json(hl)
        else:
            rule = G.settings(p)["highlights"]
            print(dim(f"important results (guide.highlights = {rule}; star with `research guide star F-001`)"))
            for h in hl:
                print(f"  {h['id']}  {h['status']:12} {'★ ' if h['starred'] else '  '}{h['title']}  "
                      + dim(("in guide" if h["in_guide"] else "not in guide") + (", drafted" if h["drafted"] else ", no draft")))
            if not hl:
                print(dim("  none yet"))
    elif act in ("star", "unstar"):
        if not ids:
            raise ResearchError(f"usage: research guide {act} F-001")
        r = G.star(p, ids[0], act == "star")
        _done(a, r, msg=f"{r['id']} {'is now an important result' if r['starred'] else 'is no longer starred'}"
                        + (f"; logged into results.qmd" if (r.get("logged") or {}).get("logged") else ""))
    elif act == "log":
        r = G.log(p, ids or None)
        _done(a, r, msg=("logged " + ", ".join(r["logged"]) + f" into {r.get('chapter')}") if r.get("logged") else "nothing new to log")
    elif act == "cite":
        verb = ids[0] if ids else "list"
        if verb == "add":
            r = G.cite_add(p, a.bibtex, a.key, a.title, a.authors, a.year, a.doi, a.journal, a.url)
            _done(a, r, msg=f"added to {r['file']}: cite as {r['cite_as']}")
        elif verb in ("list", "check"):
            r = G.cite_list(p)
            if a.json:
                out_json(r)
            else:
                for e in r["entries"]:
                    print(f"  {e['key']:28} {e.get('year') or '':5} {(e.get('title') or '')[:60]}  " + dim(("cited in " + ", ".join(e["cited_in"])) if e["cited_in"] else "not cited yet"))
                for k, where in r["missing"].items():
                    print(red(f"  ✗ [@{k}] is cited in {', '.join(where)} but is not in {r['file']}"))
                if not r["entries"] and not r["missing"]:
                    print(dim(f"  no references yet: `research guide cite add --bibtex '@article{{…}}'` or paste into {r['file']}"))
            return 1 if verb == "check" and r["missing"] else 0
        else:
            raise ResearchError("usage: research guide cite list|check|add [--bibtex TEXT | --title … --authors … --year … --doi …]")
    elif act in ("draft", "refresh"):
        verb = ids[0] if ids and ids[0] in ("done", "check") else None
        if verb == "done":
            r = G.draft_done(p, ids[1:])
            _done(a, r, msg=f"recorded: {', '.join(r['drafted'])}" + ("" if r["check"]["ok"] else "; limits exceeded, see `research guide draft check`"))
        elif verb == "check":
            r = G.draft_check(p)
            if a.json:
                out_json(r)
            elif not r["prepared"]:
                print(dim(r["note"]))
            else:
                print((green("✓ ") if r["ok"] else red("✗ ")) + ("draft is within the limits" if r["ok"] else "draft exceeds the limits")
                      + dim(f"  ({r['limits']['max_words']} words/section, {r['limits']['max_sections']} section(s), {r['limits']['max_figures']} figure(s))"))
                for k, x in r["per_chapter"].items():
                    print(dim(f"    {k}: +{x['words_added']} words, +{x['images_added']} figure(s)"))
                for x in r["problems"]:
                    print(red("  ✗ ") + x)
            return 0 if r["ok"] else 1
        else:
            r = G.draft(p, a.profile, getattr(a, "model", None), getattr(a, "strategy", None), ids or None, [a.section] if a.section else None)
            if a.json:
                out_json(r)
            elif r.get("nothing_to_draft"):
                print(green("✓ ") + r["message"])
            elif a.run:
                print(f"running {r['profile']}/{r['role']} (model {r['model']}) to draft {', '.join(r['targets']) or ', '.join(r['sections'])} "
                      f"within {r['limits']['max_words']} words/section", file=sys.stderr)
                import subprocess
                root = p.root
                p.close()
                return subprocess.call(r["argv"], cwd=root, env={**os.environ, **r["env"]})
            else:
                print(f"draft prepared for {', '.join(r['targets']) or ', '.join(r['sections'])} (≤ {r['limits']['max_words']} words per section). "
                      "Nothing is written until the agent runs; start it with:")
                print(r["shell"])
    elif act == "publish":
        r = G.publishing(p)
        if a.json:
            out_json(r)
        else:
            print((green("✓ ") if r["workflow"] else yellow("✗ ")) + r["path"] + ("" if r["workflow"] else "  (missing: `research guide init` adds it)"))
            print(dim("  " + r["site"]))
    else:
        r = G.status(p)
        if a.json:
            out_json(r)
        elif not r["exists"]:
            print(yellow("No Scientist Guide yet. ") + "`research guide init` creates it in " + r["path"] + ".")
        else:
            print(f"{r['path']}  " + ("" if r["enabled"] else "[disabled]"))
            for h in r["highlights"]:
                print((yellow if not h["drafted"] else green)(f"  {h['id']:6}") + f" {h['status']} {'★ ' if h['starred'] else ''}{h['title']}  "
                      + dim(("in guide" if h["in_guide"] else "not in guide") + (", drafted" if h["drafted"] else ", no draft")))
            pv = r["provenance"]
            for k, s in pv["sections"].items():
                col = green if s["state"] == "fresh" else red if s["state"] == "invalid" else yellow
                print(col(f"  {s['state']:8}") + f" {k} [{s['owner']}]" + "".join(dim("\n      " + w) for w in s["why"]))
            c = r["citations"]
            print(f"  references: {c['entries']} entr{'y' if c['entries'] == 1 else 'ies'}" + (red(f", {len(c['missing'])} cited but missing") if c["missing"] else ""))
            print(f"  provenance: {pv['errors']} error(s), {pv['warnings']} warning(s)  (research guide validate)")
            if not r["draft"]["ok"]:
                print(red("  last draft exceeds its limits: ") + "; ".join(r["draft"]["problems"][:2]))
    return 0


def cmd_setup(a, p) -> int:
    from research import setup_agents as SA
    r = SA.setup(p, a.provider, a.preset, apply=a.apply and not a.dry_run, approve=a.approve or [], probe=a.probe,
                 strategy=a.strategy)
    if a.json:
        out_json(r)
        return 0
    print(("applied" if r["applied"] else "dry run (add --apply)") + f" · claude {r['claude']['version'] or 'not found'}")
    for f in r["agent_files"]:
        print(f"  {f['state'].ljust(15)} {f['path']}  → {f['model']}")
    for k in ("installed", "skipped", "unavailable", "recommended"):
        for x in r[k]:
            print(f"  {k}: {x}")
    print(f"  routing: {r['routing_verified']}")
    return 0


def cmd_report(a, p) -> int:
    if getattr(a, "full", False):
        return _cmd_report_full(a, p)
    from research import report as RP
    r = RP.write_report(p, a.scope, a.format, a.out)
    _done(a, r, msg=f"report: {', '.join(r['paths'])}")
    return 0


def _cmd_report_full(a, p) -> int:
    """Deterministic, exhaustive Markdown export. Read-only: writes nothing but the output file."""
    from research import fullreport as FR
    if a.stdout:
        snap, text = FR.export(p, a.scope, stamp=a.stamp)
        res = {"scope": snap["scope"], "counts": snap["counts"], "bytes": len(text.encode("utf-8"))}
        if a.check:
            res["check"] = FR.coverage(snap, text)
        if a.json:
            out_json({**res, "markdown": text})
        else:
            sys.stdout.write(text)
        return 0 if not (a.check and not res["check"]["ok"]) else 1
    r = FR.write(p, a.scope, a.out, stamp=a.stamp, check=a.check)
    chk = r.get("check")
    if a.json:
        out_json(r)
    else:
        print(green("✓ ") + f"full context export: {r['path']}  {dim(human_size(r['bytes']))}")
        for c in FR.CATEGORIES:
            print(f"  {dim((c + ':').ljust(14))} {r['counts'][c]}")
        if chk:
            if chk["ok"]:
                print(green("✓ ") + "coverage verified: every in-scope record is represented exactly once")
            else:
                print(red("✗ ") + f"coverage FAILED: {len(chk['exporter_problems'])} problem(s)")
                for pb in chk["exporter_problems"][:20]:
                    print(red("  • ") + pb["detail"])
            src = {k: v for k, v in chk["source_incompleteness"].items() if k != "detail" and v}
            if src:
                print(yellow("⚠ ") + "source-data incompleteness (not an exporter bug): "
                      + ", ".join(f"{k}={v}" for k, v in sorted(src.items())))
    return 0 if not (chk and not chk["ok"]) else 1


def attach(sub, add, group) -> None:
    x = add(sub, "report", cmd_report, "write a readable report (project, or one Q-/PLAN-/EXP-) to .research/reports/")
    x.add_argument("scope", nargs="?")
    x.add_argument("--format", choices=["md", "html", "both"], default="both")
    x.add_argument("-o", "--out", help="output file (with --format md or html, or --full)")
    x.add_argument("--full", action="store_true",
                   help="exhaustive deterministic Markdown export of everything recorded (for an LLM hand-off)")
    x.add_argument("--check", action="store_true", help="with --full: verify coverage; nonzero exit if it fails")
    x.add_argument("--stdout", action="store_true", help="with --full: write to stdout instead of a file")
    x.add_argument("--stamp", action="store_true",
                   help="with --full: add a generation timestamp (output is then NOT byte-reproducible)")
    x = add(sub, "guide", cmd_guide, "Scientist Guide (Quarto book on main): init | status | highlights | star F-… | log | cite list|add|check | "
                                     "draft [done|check] | publish | claim | figure | section | validate | coverage | quarto … | chapter add | review …")
    x.add_argument("action", nargs="?", default="status", choices=["init", "status", "highlights", "star", "unstar", "log", "cite", "draft",
                                                                    "refresh", "publish", "claim", "figure", "section", "validate",
                                                                    "coverage", "quarto", "chapter", "review"])
    x.add_argument("ids", nargs="*", help="verb and arguments (cite add|list|check · draft done|check · star F-001 · claim/figure/section add|list|show|retire|pin|set)")
    x.add_argument("--bibtex", help="cite add: pasted BibTeX entry")
    x.add_argument("--key", help="cite add: citation key (made from author, year and title if omitted)")
    x.add_argument("--authors", help="cite add: 'Last, First and Last, First'")
    x.add_argument("--year")
    x.add_argument("--doi")
    x.add_argument("--journal")
    x.add_argument("--url")
    x.add_argument("--kind", help="claim kind: observation|derivation|interpretation|hypothesis|speculation|proposed")
    x.add_argument("--section", help="chapter file (relative to the guide directory)")
    x.add_argument("--source", action="append", metavar="[ROLE:]REF", help="cited record (repeatable); ROLE = supports|challenges|context")
    x.add_argument("--figure", action="append", metavar="FIG-001", help="claim: figure(s) that illustrate it")
    x.add_argument("--historical", action="store_true", help="claim: records an earlier belief, allowed to cite retracted evidence")
    x.add_argument("--fig-kind", choices=["archived", "recreated", "derived", "conceptual"])
    x.add_argument("--label", help="figure: Quarto id, e.g. fig-sensitivity")
    x.add_argument("--title")
    x.add_argument("--script", help="figure: generator script (repo-relative)")
    x.add_argument("--input", action="append", help="figure: generator input file (repeatable)")
    x.add_argument("--owner", choices=["human", "agent"], help="section: who may rewrite it")
    x.add_argument("--edition", choices=["scientific", "researcher", "both"], help="quarto build/preview: which edition (default scientific)")
    x.add_argument("--pdf", action="store_true", help="quarto build: also try a PDF (Typst); failure never affects the HTML")
    x.add_argument("--allow-invalid", action="store_true", help="quarto build: publish the scientific edition despite provenance errors")
    x.add_argument("--dry-run", action="store_true", help="quarto init: show what would be created")
    x.add_argument("--run", action="store_true", help="draft: start the agent now instead of printing the command")
    x.add_argument("--profile")
    x.add_argument("--strategy", help="refresh: use this allocation strategy for this call only")
    x.add_argument("--verdict", choices=["sound", "concerns", "unsound"], help="review record")
    x.add_argument("--reviewer", help="review record: who or which model reviewed")
    x.add_argument("--rating", action="append", metavar="CRITERION=RATING", help="review record: evidence_fidelity|scientific_validity|"
                   "explanatory_quality|uncertainty|historical_consistency|provenance = ok|concern|fail|not_assessed")
    x.add_argument("--concern", action="append", help="review record: a specific concern (repeatable)")
    x.add_argument("--notes", help="review record/approve")
    x.add_argument("--by", help="review approve: the researcher approving")
    x.add_argument("--force", action="store_true", help="review approve: approve despite provenance errors (recorded)")
    x = add(sub, "setup", cmd_setup, "optional setup: `setup agents --provider claude --preset scientific [--apply]`")
    x.add_argument("what", choices=["agents"])
    x.add_argument("--provider", default="claude")
    x.add_argument("--preset", default="scientific")
    x.add_argument("--apply", action="store_true")
    x.add_argument("--dry-run", action="store_true")
    x.add_argument("--approve", nargs="+", choices=["ponytail"], help="approve third-party skill imports")
    x.add_argument("--probe", action="store_true", help="also make one tiny live call per model")
    x.add_argument("--strategy", help="also select this allocation strategy for the project (e.g. balanced_first)")

