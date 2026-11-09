"""`research site`: the research website (the Scientist Guide as one Quarto site, plus a page per published experiment)."""
from __future__ import annotations

import subprocess

from research.util import ResearchError

from . import mut
from ..output import bold, dim, green, out_json, yellow


def _emit(a, obj, lines) -> int:
    if a.json:
        out_json(obj)
    else:
        for l in lines(obj):
            print(l)
    return 0


@mut
def cmd_site_init(a, p) -> int:
    from research import site
    r = site.init(p, apply=a.apply)
    if r.get("dry_run"):
        return _emit(a, r, lambda d: [bold(f"Research website, proposed files in {d['guide_dir']}/") + dim("   (nothing is written without --apply)")]
                     + [f"  {'exists ' if f['exists'] else 'create '} {f['path']}" for f in d["files"]]
                     + [dim("Existing files are never overwritten, so this also repairs a half-created website."), "Then: research site init --apply"])
    return _emit(a, r, lambda d: [green("✓ ") + f"website ready in {d['guide_dir']}/ ({len(d['created'])} file(s) created, {len(d['left_untouched'])} left untouched)"]
                 + ([green("✓ ") + "pages: " + ", ".join(d["pages"]["created"])] if d["pages"]["created"] else [])
                 + [dim("  " + n) for n in d["next"]])


def cmd_site_status(a, p) -> int:
    from research import site
    r = site.status(p)

    def lines(d):
        out = [bold("Research website") + (f" in {d['guide_dir']}/" if d["guide"] else ": not created yet")]
        if d["guide"]:
            out.append(f"  quarto:    {'ok ' + str(d['quarto']['path']) if d['quarto']['ok'] else yellow('; '.join(d['quarto']['problems']))}")
            wf = d["workflow"]
            out.append(f"  workflow:  {wf['path'] if wf['exists'] else yellow('missing')}" + (f" (deploys on {', '.join(wf['branches'])})" if wf["branches"] else ""))
            rem = d["remote"]
            out.append("  remote:    " + (rem["url"] or "none (the site works locally without one)"))
            if rem.get("site_url"):
                out.append(f"  site URL:  {rem['site_url']}")
            out.append(f"  built:     {d['output']['dir']}" + ("" if d["output"]["built"] else dim(" (not built yet)")))
            out.append("  published experiments: " + (", ".join(f"{e['experiment']} ({e['status']})" for e in d["experiments"]) or "none"))
            for e in d["experiments"]:
                if e["edited_by_you"]:
                    out.append(dim(f"    {e['experiment']}: your edits are kept in {len(e['edited_by_you'])} file(s)"))
        out.append(bold("Next"))
        out += [f"  {n}" for n in d["next"]] or ["  nothing: it is built and committed; push when you are ready"]
        out.append(dim("Research OS never pushes, commits or changes GitHub settings."))
        return out
    return _emit(a, r, lines)


def cmd_site_preview(a, p) -> int:
    from research import site
    r = site.preview_command(p, port=a.port, host=a.host, browse=False if a.no_browser else None)
    if a.json:
        out_json(r)
        return 0
    for n in r["notes"]:
        print(n)
    try:
        return subprocess.run(r["argv"]).returncode
    except KeyboardInterrupt:
        return 0


def cmd_site_build(a, p) -> int:
    from research import site
    r = site.build(p)
    if a.json:
        out_json(r)
    else:
        print((green("✓ ") + f"site built in {r['site_dir']}/") if r.get("ok") else yellow("✗ the site did not build cleanly"))
        for k in ("log",):
            if not r.get("ok") and r.get("html", {}).get(k):
                print(dim(r["html"][k][-600:]))
        for l in (r.get("leaks") or [])[:10]:
            print(yellow(f"  Research OS identifier shown to readers: {l['identifier']} in {l['file']}"))
        for l in r.get("links", [])[:15]:
            print(yellow(f"  broken link in {l['page']}: {l['link']}"))
        if r.get("ok"):
            print(dim("  open it: research site preview   ·   publish: commit, then git push (the workflow deploys it)"))
    return 0 if r.get("ok") else 1


@mut
def cmd_site_add(a, p) -> int:
    from research import site
    r = site.add(p, a.experiment, at=a.at, dry_run=a.dry_run)
    if a.dry_run:
        return _emit(a, r, lambda d: [bold(f"{d['experiment']} would be published at {d['commit'][:10]} as {d['dir']}/")]
                     + [f"  {f}" for f in d["files"]] + [yellow(f"  skipped {s['path']}: {s['why']}") for s in d["skipped"]])
    if r.get("restored"):
        return _emit(a, r, lambda d: [green("✓ ") + f"{d['experiment']} is back on the website"] + [dim("  " + n) for n in d["next"]])

    def lines(d):
        out = [green("✓ ") + f"{d['experiment']} published at {d['commit'][:10]} in {d['dir']}/: {len(d['written'])} new, {len(d['refreshed'])} refreshed, "
                                f"{len(d['kept_yours'])} of yours kept"]
        out += [yellow("⚠ " + w) for w in d["warnings"]]
        out += [yellow(f"  skipped {s['path']}: {s['why']}") for s in d["skipped"]]
        out += [yellow(f"  {i['severity']}: {i['message']}") for i in d["issues"]]
        return out + [dim("  " + n) for n in d["next"]]
    return _emit(a, r, lines)


@mut
def cmd_site_remove(a, p) -> int:
    from research import site
    r = site.remove(p, a.experiment, apply=a.apply)
    if r.get("already"):
        return _emit(a, r, lambda d: [f"{d['experiment']} is already unpublished (kept in {d['dir']}/)"])

    def lines(d):
        out = [bold(("Unpublished " if not d["dry_run"] else "Would unpublish ") + d["experiment"])
               + f": {d['from']}/  →  {d['to']}/", f"  pages leaving the book: {len(d['unlist'])}"]
        if d["edited_by_you"]:
            out.append(yellow(f"  {len(d['edited_by_you'])} file(s) contain your edits: they move with the folder, nothing is lost"))
        out += [yellow("⚠ " + w) for w in d.get("warnings", [])]
        out.append(dim(d["note"]))
        if d["dry_run"]:
            out.append(f"Run again with --apply to do it:  research site remove {d['experiment']} --apply")
        return out
    return _emit(a, r, lines)


@mut
def cmd_site_sync(a, p) -> int:
    from research import site
    r = site.sync(p)
    return _emit(a, r, lambda d: [green("✓ ") + (f"added: {', '.join(d['added'])}" if d["added"] else "nothing new to publish")]
                 + [dim(f"  skipped {s.get('experiment')}: {s.get('why') or s.get('path')}") for s in d["skipped"][:10]])


def cmd_site_list(a, p) -> int:
    from research import site
    return _emit(a, site.listing(p), lambda d: [f"{e['experiment']}  {e['status']:<11} {e['pages']} page(s)  commit {e['commit']}  /{e['slug']}/" for e in d] or ["No experiment is on the website."])


def attach(sub, add, group) -> None:
    g = group("site", "research website (Quarto, GitHub Pages): init | preview | build | status | add EXP | remove EXP | list | sync")
    x = add(g, "init", cmd_site_init, "preview the website files; --apply creates them (never overwrites; also repairs)")
    x.add_argument("--apply", action="store_true", help="write the files (without it nothing changes)")
    add(g, "status", cmd_site_status, "quarto, workflow, remote, Pages URL, published experiments and the exact next commands")
    x = add(g, "preview", cmd_site_preview, "live local website with hot reload (no GitHub needed)")
    x.add_argument("--port", type=int, default=4200)
    x.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to reach it from another machine (prefer an SSH port-forward)")
    x.add_argument("--no-browser", action="store_true", help="do not open a browser (the default over SSH or without a display)")
    add(g, "build", cmd_site_build, "render the static site locally and check its links (never runs code)")
    x = add(g, "add", cmd_site_add, "publish a verified experiment's committed report and figures as a draft page you can edit")
    x.add_argument("experiment", help="EXP-001")
    x.add_argument("--at", default="verified", help="verified (default), RUN-NNNN or a git commit")
    x.add_argument("--dry-run", action="store_true", help="show what would be published and what is excluded")
    x = add(g, "remove", cmd_site_remove, "unpublish an experiment: move it out of the site (nothing is deleted); previews unless --apply")
    x.add_argument("experiment", help="EXP-001")
    x.add_argument("--apply", action="store_true", help="do it")
    add(g, "list", cmd_site_list, "experiments on the website and their state")
    add(g, "sync", cmd_site_sync, "publish verified experiments that have committed reports and are not on the site yet (adds only)")
