"""The research website: the Scientist Guide (one Quarto project, one Pages workflow) plus a page per published experiment.

Nothing here is a second publishing system. `guide_quarto` scaffolds, renders and previews the Quarto project and writes the one GitHub
Pages workflow; this module adds the website-shaped parts around it:

    pages     about / research-question pages written from the project record (TODOs where the record is empty)
    add       publish ONE verified experiment: its committed report chapters and figures (from `reports/` at the verified commit) are
              copied into `<guide>/experiments/<slug>/`, with an editable draft page (metadata, methods, results, limitations, TODOs)
    remove    unpublish: the experiment's folder moves to `<guide>/_unpublished/<slug>/` (Quarto never renders `_` folders) and its chapters
              leave the book. Nothing is deleted, and `add` restores it.
    status    quarto, workflow, remote, GitHub Pages URL, what is published and the exact next commands

Safety rules, in one place:
  * Only what is committed at the experiment's verified commit (or the commit you name) is read; the working tree is never published.
  * Only `reports/**` files of a small allow-list of types, under a size cap, with no credential-like names or contents, never ledgers,
    logs, scratch, HPC output or agent files.
  * Research OS only ADDS. A generated file that you edited is never overwritten (it is reported); the draft page is yours after the
    first write; no command deletes anything. Publishing means `git push`, which only you do.

State (committed with the guide): `<guide>/provenance/site.json`: slug, status, commit and a hash per generated file (to tell your edits from ours).
"""
from __future__ import annotations

import hashlib
import html.parser
import os
import re
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from . import fork as FK, guide as G, guide_quarto as GQ, provenance as PV, workspace as W
from .schema import normalize_id
from .store import Project, _atomic_write
from .util import NotFound, ResearchError

STATE = "site.json"
SOURCE_DIR = "reports"
ALLOWED = (".qmd", ".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".csv")
MAX_BYTES, MAX_CSV_BYTES = 5_000_000, 1_000_000
DENY_DIRS = {".git", ".research", ".claude", "ledger", "logs", "log", "scratch", "tmp", "temp", "hpc", "slurm", "agent", "agents", "secrets",
             "credentials", "private", "unpublished", "node_modules", "__pycache__"}
DENY_NAME = re.compile(r"(^\.env|\.pem$|\.key$|id_rsa|id_ed25519|secret|token|credential|passw|\.log$|^slurm-|\.ipynb$)", re.I)
SECRET_TEXT = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[bp]-[A-Za-z0-9-]{10,}")
_CELL_OPEN = re.compile(r"^(`{3,})\{\.?(\w+)[^}\n]*\}", re.M)


# =============================================================================== state

def _state(p: Project) -> Dict[str, Any]:
    d = G._state(p, STATE)
    d.setdefault("experiments", {})
    return d


def _save(p: Project, d: Dict[str, Any]) -> None:
    G._save_state(p, STATE, d)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")[:48] or "experiment"


def _guide(p: Project) -> str:
    return G._need_guide(p)


def _rel(p: Project, path: str) -> str:
    return os.path.relpath(path, PV.main_root(p)).replace(os.sep, "/")


# =============================================================================== pages (about, research question)

def _page_about(p: Project) -> str:
    proj = p.config.get("project") or {}
    goal, desc = proj.get("goal"), proj.get("description")
    lines = ["# About this project", "", f"**{p.name}**" + (f": {goal}" if goal else ""), ""]
    if desc:
        lines += [str(desc), ""]
    lines += ["<!-- Written from the project record by `research site init`. Edit it freely: Research OS never rewrites this page. -->", "",
              "_TODO: who is working on this, how to cite it, how to get in touch, funding and acknowledgements._"]
    return "\n".join(lines) + "\n"


def _page_question(p: Project) -> str:
    qs = p.list("question")
    out = ["# Research question", ""]
    if not qs:
        out += ["_TODO: no question is recorded yet. State the question this project tries to answer, and record it in the project._"]
    else:
        root = [q for q in qs if not q.get("parent_id")] or qs
        out += [f"**{root[0]['title']}**", ""]
        if root[0].get("description"):
            out += [str(root[0]["description"]), ""]
        rest = [q for q in qs if q["id"] != root[0]["id"]]
        if rest:
            out += ["## Related questions", ""] + [f"- {q['title']} ({q['status']})" for q in rest]
    out += ["", "<!-- Written from the project record by `research site init`. Edit it freely: Research OS never rewrites this page. -->"]
    return "\n".join(out) + "\n"


PAGES = {"about.qmd": ("About this project", _page_about), "research-question.qmd": ("Research question", _page_question)}


def ensure_pages(p: Project, dry_run: bool = False) -> Dict[str, Any]:
    """Create the about / research-question pages that are missing and list them in the book. Existing files are never touched."""
    gd = _guide(p)
    created, kept = [], []
    for name, (title, body) in PAGES.items():
        path = os.path.join(gd, name)
        if os.path.exists(path):
            kept.append(name)
            continue
        created.append(name)
        if not dry_run:
            _atomic_write(path, body(p))
            GQ.chapter_add(p, name, title, owner="human")
    if not dry_run:
        listed = GQ.listed_chapters(p)
        for name in kept:                         # a page that exists but is not in the book is listed, never rewritten
            if name not in listed:
                GQ.chapter_add(p, name, owner=None)
    return {"created": created, "kept": kept}


# =============================================================================== init / status

def init(p: Project, apply: bool = False) -> Dict[str, Any]:
    """Preview (default) or create the website scaffold: the guide (chapters, theme, bibliography, .gitignore, Pages workflow) and the
    about / research-question pages. Existing files are never overwritten, so it doubles as a repair."""
    exists = G.exists(p)
    if not apply:
        base = GQ.init_quarto(p, dry_run=True)
        pages = [{"path": f"{base['guide_dir']}/{n}", "exists": exists and os.path.exists(os.path.join(PV.guide_dir(p), n))} for n in PAGES]
        return {"dry_run": True, "guide_dir": base["guide_dir"], "files": base["files"] + pages,
                "next": ["research site init --apply"]}
    base = G.init(p)
    pages = ensure_pages(p)
    return {**base, "pages": pages, "next": ["research site preview", "research site status"]}


def _git(p: Project, *args: str) -> Optional[str]:
    return W._git(PV.main_root(p), *args)


def _remote(p: Project) -> Dict[str, Any]:
    url = _git(p, "remote", "get-url", "origin")
    if not url:
        return {"url": None}
    m = re.match(r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+)/([^/]+?)(?:\.git)?/?$", url.strip())
    if not m:
        return {"url": url, "github": False}
    owner, repo = m.group(1), m.group(2)
    site = f"https://{owner}.github.io/" + ("" if repo.lower() == f"{owner}.github.io".lower() else repo + "/")
    return {"url": url, "github": True, "owner": owner, "repo": repo, "pages_settings": f"https://github.com/{owner}/{repo}/settings/pages", "site_url": site}


def _workflows(p: Project) -> Dict[str, Any]:
    d = os.path.join(PV.main_root(p), ".github", "workflows")
    ours = GQ.workflow_path(p)
    deploys = []
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        try:
            if fn.endswith((".yml", ".yaml")) and "deploy-pages" in open(os.path.join(d, fn), encoding="utf-8").read():
                deploys.append(f".github/workflows/{fn}")
        except OSError:
            pass
    branches = None
    if os.path.exists(ours):
        m = re.search(r"push:\s*\n\s*branches:\s*\[([^\]]*)\]", open(ours, encoding="utf-8").read())
        branches = [b.strip().strip("'\"") for b in m.group(1).split(",")] if m else None
    return {"path": _rel(p, ours), "exists": os.path.exists(ours), "branches": branches, "deploying_workflows": deploys}


def _dirty(p: Project) -> List[str]:
    gd, wf = _rel(p, PV.guide_dir(p)), ".github"
    out = _git(p, "status", "--porcelain", "--", gd, wf) or ""
    return [l[3:] for l in out.splitlines() if l.strip()]


def _output_dir(p: Project) -> str:
    return os.path.join(PV.guide_dir(p), "_output", "scientific")


def status(p: Project) -> Dict[str, Any]:
    exists = G.exists(p)
    q = GQ.doctor(p)
    branch = GQ.default_branch(p)
    wf, rem = _workflows(p), _remote(p)
    st = _state(p)["experiments"]
    out_dir = _output_dir(p)
    built = os.path.exists(os.path.join(out_dir, "index.html"))
    exps = []
    for e, r in sorted(st.items()):
        edited = [f for f, h in (r.get("files") or {}).items() if _disk_hash(p, r, f) not in (None, h)]
        exps.append({"experiment": e, "slug": r["slug"], "status": r["status"], "commit": (r.get("commit") or "")[:10],
                     "pages": len(r.get("chapters") or []), "edited_by_you": edited})
    nxt: List[str] = []
    if not exists:
        nxt.append("research site init --apply        (creates the website; previews without --apply)")
    else:
        if not q["ok"]:
            nxt += q["hints"] or q["problems"]
        elif not built:
            nxt.append("research site preview            (live local site with hot reload) or: research site build")
        if wf["deploying_workflows"] and len(wf["deploying_workflows"]) > 1:
            nxt.append("Several workflows deploy to Pages (" + ", ".join(wf["deploying_workflows"]) + "): keep one, they cancel each other")
        if not wf["exists"]:
            nxt.append("research site init --apply        (restores the missing Pages workflow; nothing existing is overwritten)")
        elif wf["branches"] and branch not in wf["branches"]:
            nxt.append(f"{wf['path']} deploys on {wf['branches']} but your default branch is {branch!r}: edit its `branches:`")
        dirty = _dirty(p)
        if dirty:
            nxt.append(f"git -C {PV.main_root(p)} add {_rel(p, PV.guide_dir(p))} .github && git -C {PV.main_root(p)} commit -m \"Research website\"   "
                       f"({len(dirty)} uncommitted file(s))")
        if not rem["url"]:
            nxt.append("No remote yet. Create an empty repository on GitHub, then: "
                       f"git remote add origin git@github.com:<you>/<repo>.git && git push -u origin {branch}")
        elif rem.get("github"):
            nxt.append(f"git push origin {branch}        (Research OS never pushes; the Pages workflow runs after you do)")
            nxt.append(f"One time on GitHub: {rem['pages_settings']} → Build and deployment → Source: GitHub Actions. The site will be {rem['site_url']}")
    return {"guide": exists, "guide_dir": _rel(p, PV.guide_dir(p)) if exists else None, "quarto": {"ok": q["ok"], "path": q.get("quarto"), "problems": q["problems"]},
            "workflow": wf, "branch": branch, "remote": rem, "output": {"dir": _rel(p, out_dir), "built": built}, "experiments": exps, "next": nxt,
            "pushes_automatically": False}


def _disk_hash(p: Project, rec: Dict[str, Any], rel: str) -> Optional[str]:
    base = _exp_dir(p, rec)
    try:
        return _sha(open(os.path.join(base, rel), "rb").read())
    except OSError:
        return None


def _exp_dir(p: Project, rec: Dict[str, Any]) -> str:
    return os.path.join(PV.guide_dir(p), "_unpublished" if rec["status"] == "unpublished" else "experiments", rec["slug"])


# =============================================================================== preview / build

def preview_command(p: Project, port: int = 4200, host: str = "127.0.0.1", browse: Optional[bool] = None) -> Dict[str, Any]:
    """The argv to run and what to tell the user: URL, and the port-forward line when this looks like an SSH/headless session."""
    argv = GQ.preview_argv(p) + ["--port", str(port), "--host", host]
    ssh = bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))
    headless = ssh or (os.name == "posix" and not os.environ.get("DISPLAY") and os.uname().sysname != "Darwin")
    if browse is False or (browse is None and headless):
        argv.append("--no-browser")
    notes = [f"Open http://localhost:{port}/ (it reloads when you save a file). Stop with Ctrl-C."]
    if ssh or host != "127.0.0.1":
        notes.append(f"On a remote machine: from your laptop run  ssh -L {port}:localhost:{port} <user>@<this-host>  and open http://localhost:{port}/")
    return {"argv": argv, "url": f"http://localhost:{port}/", "notes": notes}


class _Links(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refs: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        for k, v in attrs:
            if v and k in ("href", "src") and tag in ("a", "img", "link", "script", "source", "video", "audio"):
                self.refs.append(v)


def check_links(out_dir: str) -> List[Dict[str, str]]:
    """Local links and images in the rendered site that point at nothing. External URLs and in-page anchors are not followed."""
    broken: List[Dict[str, str]] = []
    for root, _, files in os.walk(out_dir):
        for fn in files:
            if not fn.endswith(".html"):
                continue
            path = os.path.join(root, fn)
            try:
                text = open(path, encoding="utf-8").read()
            except (OSError, UnicodeDecodeError):
                continue
            pr = _Links()
            pr.feed(text)
            for ref in pr.refs:
                if re.match(r"^(?:[a-z][a-z0-9+.-]*:|//|#)", ref, re.I):
                    continue
                target = os.path.normpath(os.path.join(root, ref.split("#")[0].split("?")[0]))
                if not os.path.exists(target) and not os.path.exists(os.path.join(target, "index.html")):
                    broken.append({"page": os.path.relpath(path, out_dir), "link": ref})
    return broken


def build(p: Project) -> Dict[str, Any]:
    """Render the site (the guide's scientific edition, never executing code) and check its local links and figures."""
    _guide(p)
    r = GQ.build(p, "scientific")
    out = _output_dir(p)
    r["links"] = check_links(out) if r.get("ok") else []
    if r["links"]:
        r["ok"] = False
    r["site_dir"] = _rel(p, out)
    return r


# =============================================================================== publishing one experiment

def _discover(p: Project, sha: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """(publishable, skipped-with-reason) among the files committed under reports/ at `sha`."""
    top = FK._top(p)
    r = subprocess.run(["git", "ls-tree", "-r", "-l", "-z", "--full-tree", sha, SOURCE_DIR], cwd=top, capture_output=True)
    ok, skipped = [], []
    for rec in r.stdout.decode("utf-8", "replace").split("\0"):
        if not rec.strip():
            continue
        meta, _, path = rec.partition("\t")
        mode, kind, _, size = meta.split(None, 3)
        size = int(size.strip() or 0) if size.strip() != "-" else 0
        parts = [x.lower() for x in path.split("/")]
        why = None
        if kind != "blob" or mode == "120000":
            why = "not a regular file"
        elif any(x in DENY_DIRS for x in parts[:-1]) or DENY_NAME.search(parts[-1]):
            why = "excluded by default (credentials, logs, scratch, HPC output or internal files)"
        elif not parts[-1].endswith(ALLOWED):
            continue
        elif size > (MAX_CSV_BYTES if parts[-1].endswith(".csv") else MAX_BYTES):
            why = f"larger than {(MAX_CSV_BYTES if parts[-1].endswith('.csv') else MAX_BYTES) // 1000} kB"
        elif parts[-1].startswith("_"):
            why = "underscore files are Quarto internals"
        (skipped if why else ok).append({"path": path, "why": why} if why else {"path": path, "size": size})
    return ok, skipped


def _blob(p: Project, sha: str, path: str) -> bytes:
    r = subprocess.run(["git", "cat-file", "blob", f"{sha}:{path}"], cwd=FK._top(p), capture_output=True)
    if r.returncode:
        raise ResearchError(f"cannot read {path} at {sha[:8]}")
    return r.stdout


def _static(text: str) -> Tuple[str, int]:
    """Executable cells become plain code blocks: the website renders committed results and never runs code."""
    return _CELL_OPEN.subn(lambda m: f"{m.group(1)}{m.group(2)}", text)


def _draft_page(p: Project, exp: str, rec: Dict[str, Any], chapters: List[str], figures: List[str]) -> str:
    e = p.get("experiment", exp)
    todo = lambda what: f"_TODO: {what}_"                                               # noqa: E731
    q = None
    if e.get("question_id") and p.exists("question", e["question_id"]):
        q = p.get("question", e["question_id"])["title"]
    tasks = [t for t in p.list("task", "related_experiment_id=?", (exp,)) if not str(t.get("blockers") or "").startswith("retired by plan")]
    lines = [f"# {e['title']}", "",
             f"<!-- research:site {exp} commit {rec['commit'][:10]}: a generated first draft from the project record. It is yours now: Research OS never rewrites this page. -->", "",
             "::: {.callout-note}", "## Draft", "Written from the recorded experiment. Check it, fill in the TODOs, and delete this note when it is ready.", ":::", "",
             "## Summary", "",
             f"**Question.** {q or todo('which question does this experiment address?')}", "",
             f"**Hypothesis.** {e.get('hypothesis') or todo('state the hypothesis')}", "",
             f"**Verified at** commit `{rec['commit'][:10]}`" + (" (named explicitly, not checked by the supervisor)." if rec.get("source") == "commit" else "."), ""]
    if e.get("motivation"):
        lines += [f"**Motivation.** {e['motivation']}", ""]
    lines += ["## Methods", ""]
    lines += [e["method"], ""] if e.get("method") else [todo("describe the method; the recorded steps are below"), ""]
    if tasks:
        lines += ["Recorded steps:", ""] + [f"- {t['title']}" for t in tasks] + [""]
    lines += ["## Results", "", todo("say what the figures and tables show, in your own words and only what the evidence supports")]
    if e.get("success_criteria"):
        lines += ["", f"Success criteria recorded before the run: {e['success_criteria']}"]
    if figures:
        lines += ["", "Included figures: " + ", ".join(f"`{os.path.basename(f)}`" for f in figures[:12]) + (" …" if len(figures) > 12 else "")]
    lines += ["", "## Limitations", ""]
    lines += [e["limitations"], ""] if e.get("limitations") else [todo("what this experiment cannot show"), ""]
    lines += ["## Report", ""]
    lines += [f"- [{os.path.basename(c)[:-4]}]({c})" for c in chapters] if chapters else [todo("no report chapter was found; add one under reports/ in the experiment")]
    lines += ["", "## Evidence still missing", "",
              "- " + todo("citations for any claim that rests on the literature (add to references.bib; none are invented here)"),
              "- " + todo("what a reader needs to reproduce this (data, commands, environment)")]
    return "\n".join(lines) + "\n"


def _list_in_book(p: Project, keys: List[str]) -> None:
    for k in keys:
        GQ.chapter_add(p, k, owner="human")


def _delist(p: Project, keys: List[str]) -> List[str]:
    """Remove chapters from the book, only between the managed markers. Returns what could not be removed (markers edited away)."""
    qy = os.path.join(PV.guide_dir(p), "_quarto.yml")
    text = open(qy, encoding="utf-8").read()
    if GQ.CHAPTER_MARK_BEGIN not in text or GQ.CHAPTER_MARK_END not in text:
        return keys
    head, rest = text.split(GQ.CHAPTER_MARK_BEGIN, 1)
    block, tail = rest.split(GQ.CHAPTER_MARK_END, 1)
    block = "\n".join(l for l in block.split("\n") if l.strip().lstrip("- ").strip() not in keys)
    if block and not block.startswith("\n"):
        block = "\n" + block
    _atomic_write(qy, head + GQ.CHAPTER_MARK_BEGIN + block + ("\n" if not block.endswith("\n") else "") + GQ.CHAPTER_MARK_END + tail)
    return []


def add(p: Project, exp: str, at: str = "verified", dry_run: bool = False) -> Dict[str, Any]:
    """Publish (or refresh, or restore) one experiment on the website. See the module docstring for the rules."""
    _guide(p)
    exp = str(normalize_id(exp, "EXP"))
    if not p.exists("experiment", exp):
        raise NotFound(f"Experiment {exp} not found")
    st = _state(p)
    rec = st["experiments"].get(exp)
    if rec and rec["status"] == "unpublished":
        return restore(p, exp, dry_run)
    sha = FK.resolve(p, exp, at)                                   # fails closed: not verified, or no verified commit, says so
    files, skipped = _discover(p, sha)
    chapters_src = sorted(f["path"] for f in files if f["path"].endswith(".qmd"))
    if not files:
        raise ResearchError(f"{exp} has no publishable files under {SOURCE_DIR}/ at {sha[:8]}",
                            hint=f"Commit a Quarto report and its figures under {SOURCE_DIR}/ (for example {SOURCE_DIR}/quarto/chapters/00-smoke.qmd) in the experiment, "
                                 "then run this again. Only committed files are published.")
    slug = (rec or {}).get("slug") or _unique_slug(st, _slug(p.get("experiment", exp)["title"]), exp)
    base = os.path.join(PV.guide_dir(p), "experiments", slug)
    plan = {"experiment": exp, "slug": slug, "commit": sha, "dir": _rel(p, base), "files": [f["path"] for f in files],
            "skipped": skipped, "dry_run": dry_run}
    if dry_run:
        return plan
    rec = rec or {"slug": slug, "status": "published", "files": {}, "chapters": []}
    rec.update(commit=sha, source="verified" if at == "verified" else "commit", status="published")
    written, refreshed, kept, warnings, present = [], [], [], [], []
    for f in files:
        rel = f["path"]
        data = _blob(p, sha, rel)
        if rel.endswith(".qmd"):
            text = data.decode("utf-8", "replace")
            if SECRET_TEXT.search(text):
                skipped.append({"path": rel, "why": "looks like it contains a credential"})
                continue
            text, n = _static(text)
            if n:
                warnings.append(f"{rel}: {n} code cell(s) are shown as code and not executed (the site never runs code; results come from committed figures)")
            data = text.encode("utf-8")
        dest = os.path.join(base, rel)
        new = _sha(data)
        old = rec["files"].get(rel)
        if not os.path.exists(dest):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            open(dest, "wb").write(data)
            written.append(rel)
        elif old is not None and _sha(open(dest, "rb").read()) == old:
            if old != new:
                open(dest, "wb").write(data)
                refreshed.append(rel)
        elif _sha(open(dest, "rb").read()) != new:
            kept.append(rel)
            warnings.append(f"{rel}: you edited it, so it was left as it is (the source changed or differs)")
        present.append(rel)
        if rel in written or rel in refreshed:
            rec["files"][rel] = new                                # only what Research OS wrote is tracked: a kept file stays yours
    chapters = [f"experiments/{slug}/{c}" for c in chapters_src if c in present]
    figures = [rel for rel in present if rel not in chapters_src]
    page = os.path.join(base, "index.qmd")
    if not os.path.exists(page):
        os.makedirs(base, exist_ok=True)
        text = _draft_page(p, exp, rec, [os.path.relpath(os.path.join(PV.guide_dir(p), c), base).replace(os.sep, "/") for c in chapters], figures)
        _atomic_write(page, text)
        rec["files"]["index.qmd"] = _sha(text.encode("utf-8"))        # tracked only to tell your later edits from ours; the page is never rewritten
        written.append("index.qmd")
    keys = [f"experiments/{slug}/index.qmd"] + chapters
    _list_in_book(p, keys)
    rec["chapters"] = keys
    st["experiments"][exp] = rec
    _save(p, st)
    p.event("experiment", exp, "site_published", f"{exp} published on the website at {sha[:8]} ({len(written)} new, {len(refreshed)} refreshed, {len(kept)} kept)")
    issues = [i for i in GQ.source_issues(p) if any(k in i.get("message", "") for k in keys)]
    return {**plan, "dry_run": False, "source": rec["source"], "written": written, "refreshed": refreshed, "kept_yours": kept, "warnings": warnings,
            "skipped": skipped, "issues": issues, "pages": keys,
            "next": ["research site preview", "edit " + _rel(p, page) + " (fill in the TODOs)", "git add + commit, then git push when you are ready"]}


def _unique_slug(st: Dict[str, Any], slug: str, exp: str) -> str:
    used = {r["slug"] for e, r in st["experiments"].items() if e != exp}
    s, n = slug, 2
    while s in used:
        s, n = f"{slug}-{n}", n + 1
    return s


def remove(p: Project, exp: str, apply: bool = False) -> Dict[str, Any]:
    """Unpublish: move the experiment's site folder to `_unpublished/` and take its pages out of the book. Deletes nothing."""
    _guide(p)
    exp = str(normalize_id(exp, "EXP"))
    st = _state(p)
    rec = st["experiments"].get(exp)
    if not rec:
        raise NotFound(f"{exp} is not on the website")
    if rec["status"] == "unpublished":
        return {"experiment": exp, "already": True, "dir": _rel(p, _exp_dir(p, rec))}
    src = os.path.join(PV.guide_dir(p), "experiments", rec["slug"])
    dst = os.path.join(PV.guide_dir(p), "_unpublished", rec["slug"])
    edited = [f for f, h in rec["files"].items() if _disk_hash(p, rec, f) not in (None, h)]
    plan = {"experiment": exp, "dry_run": not apply, "from": _rel(p, src), "to": _rel(p, dst), "unlist": rec["chapters"], "edited_by_you": edited,
            "note": "Nothing is deleted: the folder (including your edits) moves to _unpublished/, which Quarto never renders. "
                    f"`research site add {exp}` puts it back. The site changes only after you commit and push."}
    if not apply:
        return plan
    if os.path.exists(dst):
        raise ResearchError(f"{_rel(p, dst)} already exists; nothing was moved", hint="Rename or remove that folder yourself, then run this again.")
    stuck = _delist(p, rec["chapters"])
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.replace(src, dst)
    secs = PV.load_sections(p)                    # a registered chapter whose file moved is a provenance error: park the entries (kept, restorable)
    rec["sections"] = {k: secs.pop(k) for k in rec["chapters"] if k in secs}
    PV._save(p, "sections.json", "sections", secs)
    rec["status"] = "unpublished"
    _save(p, st)
    p.event("experiment", exp, "site_unpublished", f"{exp} unpublished from the website (kept in {_rel(p, dst)})")
    return {**plan, "dry_run": False, "still_listed": stuck,
            "warnings": ([f"Could not edit _quarto.yml (its research:chapters markers were removed); delete these lines yourself: {', '.join(stuck)}"] if stuck else [])}


def restore(p: Project, exp: str, dry_run: bool = False) -> Dict[str, Any]:
    st = _state(p)
    rec = st["experiments"][exp]
    src = os.path.join(PV.guide_dir(p), "_unpublished", rec["slug"])
    dst = os.path.join(PV.guide_dir(p), "experiments", rec["slug"])
    if dry_run:
        return {"experiment": exp, "restored": False, "dry_run": True, "from": _rel(p, src), "to": _rel(p, dst)}
    if os.path.exists(dst):
        raise ResearchError(f"{_rel(p, dst)} already exists; nothing was moved")
    if not os.path.isdir(src):
        raise ResearchError(f"{_rel(p, src)} is missing, so {exp} cannot be restored from it", hint="Publish it again from its verified commit by removing it from provenance/site.json.")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.replace(src, dst)
    secs = PV.load_sections(p)
    secs.update(rec.pop("sections", None) or {})
    PV._save(p, "sections.json", "sections", secs)
    rec["status"] = "published"
    _list_in_book(p, rec["chapters"])
    _save(p, st)
    p.event("experiment", exp, "site_published", f"{exp} restored on the website")
    return {"experiment": exp, "restored": True, "pages": rec["chapters"], "next": ["research site preview"]}


def listing(p: Project) -> List[Dict[str, Any]]:
    return status(p)["experiments"]


# =============================================================================== automatic inclusion (opt-in)

def auto_enabled(p: Project) -> bool:
    return bool(((p.config.get("guide") or {}).get("site") or {}).get("auto_include", False))


def sync(p: Project) -> Dict[str, Any]:
    """Publish every verified experiment that has committed report files and is not on the website yet. An experiment you unpublished stays
    unpublished; one already published is not refreshed (use `site add` for that). Adds only."""
    _guide(p)
    st = _state(p)["experiments"]
    added, skipped = [], []
    for e in p.list("experiment"):
        if e["id"] in st:
            continue
        try:
            r = add(p, e["id"])
            added.append(e["id"])
            skipped += [{"experiment": e["id"], **s} for s in r["skipped"]]
        except ResearchError as ex:
            skipped.append({"experiment": e["id"], "why": str(ex)})
    return {"added": added, "skipped": skipped}
