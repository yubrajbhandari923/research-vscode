"""Scientist Guide v2 publishing: a Quarto book with two editions built from the same prose.

    <guide dir>/                 default docs/scientist-guide/ (human + agent prose, tracked in git)
      _quarto.yml                shared book config (chapters list, bibliography, execute disabled)
      _quarto-scientific.yml     Quarto *profile*: the readable monograph, no Research OS identifiers
      _quarto-researcher.yml     Quarto profile: same prose + evidence panels, status, legacy guide
      filters/edition.lua        scientific: unwraps claim spans · researcher: links them to evidence, appends the panel
      index.qmd briefing.qmd theory.qmd storyline.qmd open-questions.qmd investigations/*.qmd
      provenance/*.json          see provenance.py
      _generated/                evidence panels written by `prepare` from the live records (git-ignored, rebuilt)
      _output/<edition>/         rendered site (git-ignored)

The two editions share every `.qmd`; only Quarto's own `content-visible when-profile` blocks, the profile files and one
small filter differ. Research OS imports no Quarto code: this module only locates the executable and shells out to
`quarto render|preview`, and only when you ask. Rendering never executes code (`execute: enabled: false` plus
`--no-execute`; a chapter containing an executable cell fails validation).
"""
from __future__ import annotations

import html.parser
import json
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from . import provenance as PV
from .store import Project, _atomic_write
from .util import NotFound, ResearchError

MIN_QUARTO = (1, 4)
EDITIONS = ["scientific", "researcher"]
_ID = re.compile(r"\b(?:EXP|RUN|CLM|FIG|SRC|PLAN|CP|[FADQT])-\d+(?:/[A-Z]\d+)?\b|\bclm-\d{4}\b")
CHAPTER_MARK_BEGIN = "    # research:chapters:begin (investigations are listed here; `research guide chapter add` edits only this block)"
CHAPTER_MARK_END = "    # research:chapters:end"


# =============================================================================== scaffold text

_QUARTO_YML = """# Shared by both editions. Edition-specific settings live in _quarto-scientific.yml / _quarto-researcher.yml.
project:
  type: book
  output-dir: _output/scientific

book:
  title: "{title}"
  author: "{author}"
  chapters:
    - index.qmd
    - briefing.qmd
    - results.qmd
    - theory.qmd
    - storyline.qmd
{begin}
{end}
    - open-questions.qmd
    - ideas.qmd
    - references.qmd

# Your sources: add entries to references.bib (`research guide cite add`), cite them in any chapter as [@key].
bibliography: references.bib
link-citations: true

# Rendering must never run simulations or any other code.
execute:
  enabled: false

format:
  html:
    # The project's own look: edit theme.scss (colours, fonts) or swap the theme list for any Quarto theme.
    theme: [cosmo, theme.scss]
    toc: true
    number-sections: true
    citations-hover: true
    html-math-method: mathjax
    code-fold: false
    filters:
      - filters/edition.lua
  typst:
    toc: true
    number-sections: true
    filters:
      - filters/edition.lua
"""

_PROFILE_SCIENTIFIC = """# Scientific edition: the monograph. No Research OS identifiers, evidence panels or legacy appendix.
edition: scientific
project:
  output-dir: _output/scientific
"""

_PROFILE_RESEARCHER = """# Researcher edition: the same chapters plus exact provenance (written by `research guide quarto prepare`).
edition: researcher
project:
  output-dir: _output/researcher
book:
  appendices:
    - _generated/evidence-status.qmd
"""

_THEME = """/*-- scss:defaults --*/
// This project's look. Edit freely: it is yours, Research OS never rewrites it.
// Any Quarto theme works too: change `theme:` in _quarto.yml (for example [flatly, theme.scss]).
$primary: #2a5d8f;
$link-color: #2a5d8f;
$font-family-sans-serif: "Source Sans 3", "Helvetica Neue", Arial, sans-serif;
$headings-font-weight: 600;
$body-color: #1f2328;

/*-- scss:rules --*/
.quarto-title-block .quarto-title-banner { background: $primary; }
blockquote { border-left: 4px solid $primary; color: #444; }
figure figcaption { font-size: 0.9em; color: #555; }
"""

_PUBLISH_YML = """# Builds the research website (the guide's scientific edition) and publishes it with GitHub Pages on every push to {branch}.
# This is the ONE workflow that deploys to Pages; keep it single (two deploying workflows cancel each other).
# One-time setup on GitHub: Settings -> Pages -> Build and deployment -> Source: "GitHub Actions".
# Written once by Research OS; it is yours to edit (add the researcher edition, a custom domain, a PDF, ...).
name: Publish scientist guide

on:
  push:
    branches: [{branch}]
    paths:
      - "{path}/**"
      - ".github/workflows/publish-guide.yml"
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: true

jobs:
  publish:
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{{{ steps.deployment.outputs.page_url }}}}
    steps:
      - uses: actions/checkout@v4
      - uses: quarto-dev/quarto-actions/setup@v2
      - name: Render (never executes code)
        run: quarto render {path} --profile scientific --to html --no-execute
      - name: Check the site was built
        run: test -f {path}/_output/scientific/index.html
      - uses: actions/configure-pages@v5
      - uses: actions/upload-pages-artifact@v3
        with:
          path: {path}/_output/scientific
      - id: deployment
        uses: actions/deploy-pages@v4
"""

_FILTER = r"""-- Edition filter. Same prose, two readings.
--   scientific: [text]{#clm-0001 .claim} becomes plain text.
--   researcher: the span links to its evidence entry, and each chapter gets its generated evidence panel appended.
--   both: HTML comments in the source are dropped from the output.
local edition = "scientific"

local function slug(path)
  local dir = quarto.project.directory or ""
  local rel = path
  if dir ~= "" and path:sub(1, #dir) == dir then rel = path:sub(#dir + 2) end
  rel = rel:gsub("%.qmd$", ""):gsub("/", "__")
  return rel
end

return {
  { Meta = function(m)
      if m.edition then edition = pandoc.utils.stringify(m.edition) end
    end },
  { RawBlock = function(el)  -- source comments may name internal records; they must not ship in either edition
      if el.format == "html" and el.text:match("^%s*<!%-%-") then return {} end
    end,
    RawInline = function(el)
      if el.format == "html" and el.text:match("^%s*<!%-%-") then return {} end
    end },
  { Span = function(el)
      if not el.classes:includes("claim") then return nil end
      if edition == "researcher" then
        local id = el.identifier
        local out = el.content
        if FORMAT:match("html") then  -- panels are per-file HTML pages; a merged PDF has neither them nor their anchors
          table.insert(out, pandoc.Superscript({ pandoc.Link(id:upper(), "#ev-" .. id) }))
        end
        return out
      end
      return el.content
    end },
  { Pandoc = function(doc)
      if edition ~= "researcher" then return nil end
      local input = quarto.doc.input_file
      if not input then return nil end
      local dir = quarto.project.directory or ""
      local f = io.open(dir .. "/_generated/evidence/" .. slug(input) .. ".md", "r")
      if not f then return nil end
      local text = f:read("*a")
      f:close()
      for _, b in ipairs(pandoc.read(text, "markdown").blocks) do doc.blocks:insert(b) end
      return doc
    end },
}
"""

_CHAPTERS = {
    "index.qmd": ("Preface", "This is the evolving scientific account of the project: what we are trying to understand, why, and what the "
                  "evidence so far supports. It is rewritten as understanding changes; superseded interpretations are explained, "
                  "not hidden."),
    "briefing.qmd": ("Where the science stands", "_Not yet written._ This chapter answers, for a reader who has not followed the "
                     "work: the current goal, the established findings, the significant negative results, the unresolved questions, "
                     "the main limitations, the working hypotheses, the decisions that need human judgment, and the reasonable next "
                     "experiments."),
    "results.qmd": ("Key results", "The results that matter most, each with its evidence. Research OS adds a short entry here when a "
                    "finding becomes an important result; entries are yours to edit (add the scientific context, cite the "
                    "literature, rewrite them) and are never rewritten. Delete anything you do not want."),
    "theory.qmd": ("Mathematical and physical formulation", "_Not yet written._ The model, its assumptions, and the equations the "
                   "investigations rely on."),
    "storyline.qmd": ("How the investigation developed", "_Not yet written._ The questions, hypotheses, approaches, surprises, "
                      "corrections and failed directions, and why the direction changed."),
    "open-questions.qmd": ("Open questions and future directions", "_Not yet written._"),
    "ideas.qmd": ("Notes and ideas", "Your scratch space: half-formed ideas, things to check, quotes and links to papers. Agents never "
                  "edit this chapter. Cite a source with `[@key]` once it is in references.bib. Move text into other chapters "
                  "when it is ready."),
}
HUMAN_CHAPTERS = ("ideas.qmd", "references.qmd")

_GITIGNORE = "_output/\n_generated/\n.quarto/\n/.quarto/\n*.quarto_ipynb\n"
_BIB = """% References for the guide. Cite one in any chapter with [@key]. Add entries with `research guide cite add`, or paste BibTeX here
% (from Zotero, a publisher page, Google Scholar...). Cite only sources you have actually examined.
%
% @article{smith2020example,
%   author  = {Smith, Jane and Lee, Wei},
%   title   = {An example article},
%   journal = {Journal of Examples},
%   year    = {2020},
%   doi     = {10.0000/example}
% }
"""
_REFERENCES = "# References {.unnumbered}\n\n::: {#refs}\n:::\n"


def _qmd(title: str, body: str) -> str:
    return f"# {title}\n\n{body}\n"


# =============================================================================== locate / doctor

def find_quarto(p: Optional[Project] = None) -> Optional[str]:
    cfg = ((p.config.get("guide") or {}).get("quarto") if p else None) or os.environ.get("RESEARCH_QUARTO")
    for c in (cfg, shutil.which("quarto"), os.path.expanduser("~/.local/bin/quarto"), os.path.expanduser("~/.local/opt/quarto/bin/quarto")):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def _run(argv: List[str], cwd: Optional[str] = None, timeout: int = 600) -> Tuple[int, str]:
    try:
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "QUARTO_PRINT_STACK": "false", "NO_COLOR": "1"})
        return r.returncode, re.sub(r"\x1b\[[0-9;]*m", "", (r.stdout + "\n" + r.stderr)).strip()
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except OSError as e:
        return 127, str(e)


_DOCTOR: Dict[Tuple[str, float], Dict[str, Any]] = {}  # a working install does not change while the process lives


def doctor(p: Optional[Project] = None) -> Dict[str, Any]:
    q = find_quarto(p)
    key = (q, os.path.getmtime(q)) if q and os.path.exists(q) else None
    if key and key in _DOCTOR:
        return dict(_DOCTOR[key])
    out = _doctor(q)
    if key and out["ok"]:
        _DOCTOR[key] = dict(out)
    return out


def _doctor(q: Optional[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"quarto": q, "version": None, "ok": False, "html": False, "pdf": False, "problems": [], "hints": []}
    if not q:
        out["problems"].append("Quarto is not installed (it is needed only to render or preview the guide)")
        out["hints"].append("Install from https://quarto.org/docs/get-started/ (a user-local install under ~/.local/opt is enough), "
                            "then put it on PATH or set guide.quarto in .research/config.yaml. Everything else in Research OS "
                            "works without it.")
        return out
    code, text = _run([q, "--version"], timeout=60)
    out["version"] = text.splitlines()[-1].strip() if code == 0 and text else None
    try:
        ver = tuple(int(x) for x in (out["version"] or "0").split(".")[:2])
    except ValueError:
        ver = (0, 0)
    if ver < MIN_QUARTO:
        out["problems"].append(f"Quarto {out['version']} is older than {'.'.join(map(str, MIN_QUARTO))}")
        return out
    out["html"] = True
    code, text = _run([q, "typst", "--version"], timeout=60)
    out["pdf"] = code == 0
    if not out["pdf"]:
        out["hints"].append("PDF output uses Quarto's bundled Typst, which is not available here; HTML still builds.")
    out["ok"] = True
    return out


# =============================================================================== scaffold

def slug_of(section: str) -> str:
    return re.sub(r"\.qmd$", "", section).replace("/", "__")


def _write_new(path: str, text: str, created: List[str], skipped: List[str], root: str) -> None:
    rel = os.path.relpath(path, root)
    if os.path.exists(path):
        skipped.append(rel)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _atomic_write(path, text)
    created.append(rel)


def default_branch(p: Project) -> str:
    """The branch the Pages workflow deploys: `promotion.main_branch` when set, else the remote's default branch, else the branch checked out
    on the main checkout (so a repository whose default is `trunk` or `master` is not given a workflow for `main`)."""
    from . import promotion as PR, registry, workspace as W
    explicit = (p.config.get("promotion") or {}).get("main_branch")
    if explicit and explicit != registry.default("promotion.main_branch"):      # the default "main" is not a choice; a changed value is
        return str(explicit)
    top = PV.main_root(p)
    ref = W._git(top, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if ref and "/" in ref:
        return ref.split("/", 1)[1]
    return W._git(top, "symbolic-ref", "--short", "HEAD") or PR._main_branch(p)


def workflow_path(p: Project) -> str:
    return os.path.join(PV.main_root(p), ".github", "workflows", "publish-guide.yml")


def init_quarto(p: Project, dry_run: bool = False, workflow: Optional[bool] = None) -> Dict[str, Any]:
    """Create the Quarto Scientist Guide on the main branch's checkout, with the publish workflow. Never overwrites or edits an
    existing file; safe to repeat after an interruption (each file is written atomically, so a retry creates what is missing)."""
    gd, root = PV.guide_dir(p), PV.main_root(p)
    proj = p.config.get("project") or {}
    guide_rel = os.path.relpath(gd, root).replace(os.sep, "/")
    files = {
        "_quarto.yml": _QUARTO_YML.format(title=f"{p.name}: scientific guide".replace('"', "'"), author=(p.name or "Research").replace('"', "'"),
                                          begin=CHAPTER_MARK_BEGIN, end=CHAPTER_MARK_END),
        "_quarto-scientific.yml": _PROFILE_SCIENTIFIC, "_quarto-researcher.yml": _PROFILE_RESEARCHER,
        "filters/edition.lua": _FILTER, "references.bib": _BIB, "references.qmd": _REFERENCES, "theme.scss": _THEME, ".gitignore": _GITIGNORE,
    }
    for name, (title, body) in _CHAPTERS.items():
        if name == "index.qmd" and proj.get("goal"):
            body += f"\n\n**Goal.** {proj['goal']}"
        files[name] = _qmd(title, body)
    want_wf = (p.config.get("guide") or {}).get("publish", {}).get("workflow", True) if workflow is None else workflow
    paths = {n: os.path.join(gd, n) for n in files}
    if want_wf:
        files[os.path.relpath(workflow_path(p), gd).replace(os.sep, "/")] = _PUBLISH_YML.format(path=guide_rel, branch=default_branch(p))
        paths[os.path.relpath(workflow_path(p), gd).replace(os.sep, "/")] = workflow_path(p)
    plan = [{"path": os.path.relpath(paths[n], root), "exists": os.path.exists(paths[n])} for n in files]
    if dry_run:
        return {"dry_run": True, "guide_dir": os.path.relpath(gd, root), "files": plan}
    created: List[str] = []
    skipped: List[str] = []
    for n, t in files.items():
        _write_new(paths[n], t, created, skipped, root)
    secs = PV.load_sections(p)
    for n in list(_CHAPTERS) + ["references.qmd"]:  # register chapters as sections (never changes an existing registration)
        if n not in secs:
            PV.section_set(p, n, owner="human" if n in HUMAN_CHAPTERS else "agent")
    return {"guide_dir": os.path.relpath(gd, root), "created": created, "left_untouched": skipped, "workflow": bool(want_wf),
            "next": ["git add " + guide_rel + (" .github" if want_wf else "") + " && git commit  (on main)",
                     "research guide quarto doctor", "research guide quarto build --edition scientific"]}


def chapter_add(p: Project, path: str, title: Optional[str] = None, owner: Optional[str] = None) -> Dict[str, Any]:
    """Create an investigation chapter (if absent) and list it in _quarto.yml between the managed markers."""
    key = PV._section_key(p, path)
    if not key.endswith(".qmd"):
        key += ".qmd"
    full = os.path.join(PV.guide_dir(p), key)
    qy = os.path.join(PV.guide_dir(p), "_quarto.yml")
    if not os.path.exists(qy):
        raise NotFound("No Quarto guide here", hint="Run `research guide quarto init` first.")
    with open(qy, encoding="utf-8") as f:
        text = f.read()
    if CHAPTER_MARK_END not in text:
        raise ResearchError("_quarto.yml no longer has the research:chapters markers, so it will not be edited",
                            hint=f"Add `- {key}` to book.chapters yourself.")
    created = False
    if not os.path.exists(full):
        os.makedirs(os.path.dirname(full), exist_ok=True)
        _atomic_write(full, _qmd(title or os.path.basename(key)[:-4].replace("-", " ").capitalize(), "_Not yet written._"))
        created = True
    listed = re.search(rf"^\s*-\s*{re.escape(key)}\s*$", text, re.M) is not None
    if not listed:
        text = text.replace(CHAPTER_MARK_END, f"    - {key}\n{CHAPTER_MARK_END}", 1)
        _atomic_write(qy, text)
    if owner or key not in PV.load_sections(p):  # an existing registration (and its owner) is kept unless you say otherwise
        PV.section_set(p, key, owner=owner or "agent")
    return {"chapter": key, "file_created": created, "added_to_book": not listed}


def listed_chapters(p: Project) -> List[str]:
    try:
        with open(os.path.join(PV.guide_dir(p), "_quarto.yml"), encoding="utf-8") as f:
            return re.findall(r"^\s*-\s*([\w./-]+\.qmd)\s*$", f.read(), re.M)
    except OSError:
        return []


# =============================================================================== evidence panels (researcher edition)

def _md(s: Any) -> str:
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def describe_ref(p: Project, ref: str) -> str:
    """One line of exact, live detail about a cited record, for the researcher edition."""
    from . import evidence as ev
    from . import verification as vf
    t = PV.ref_type(ref)
    try:
        if t == "finding":
            f = p.get("finding", ref)
            last = vf.latest(p, ref, "review")
            rev = f"; review {last['id']} {last['verdict']} by {last.get('author_name') or last.get('author_type')}" if last else "; not yet reviewed"
            sup = f"; superseded by {f['superseded_by']}" if f.get("superseded_by") else ""
            return f"{f['title']} — {f['status']}{', ' + f['confidence'] + ' confidence' if f.get('confidence') else ''}{rev}; " \
                   f"numbers {ev.verify_finding(p, ref)['status']}{sup}"
        if t == "metric":
            m = PV._METRIC.match(ref)
            r = ev.resolve_metric(p, m.group(1), m.group(2), *(((int(m.group(3)), None) if m.group(3) and re.fullmatch(r"-?\d+", m.group(3))
                                                                  else (None, m.group(3)))))
            row = p.q1("SELECT git_commit, git_dirty FROM runs WHERE id=?", (m.group(1),)) or {}
            git = f"; code {str(row['git_commit'])[:8]}{' (dirty)' if row.get('git_dirty') else ''}" if row.get("git_commit") else "; code revision not recorded"
            return f"{m.group(2)} = {r['value']:.6g} {r['unit'] or ''}".rstrip() + f" ({r['status']}){git}" if r["value"] is not None \
                else f"{m.group(2)}: {r['status']} — {'; '.join(r['issues'])}"
        if t == "artifact":
            a = ev._art(p, ref)
            i = ev.artifact_integrity(p, ref)
            return f"{a['path']} — integrity {i['status']}" + (f", sha256 {a['hash'][:12]}" if a.get("hash") else "")
        if t == "synthesis":
            s = p.q1("SELECT experiment_id, interpretation FROM syntheses WHERE id=?", (ref,))
            return f"synthesis of {s['experiment_id']}: {(s.get('interpretation') or '')[:140]}" if s else "synthesis (missing)"
        if t == "experiment":
            e = p.get("experiment", ref)
            return f"{e['title']} — {e['status']}"
        if t == "run":
            r = p.q1("SELECT status, experiment_id, git_commit, git_dirty FROM runs WHERE id=?", (ref,))
            return f"{r['status']} (experiment {r['experiment_id']}, code {str(r['git_commit'] or 'unrecorded')[:8]}{', dirty' if r['git_dirty'] else ''})"
        if t == "binding":
            b = next((x for x in ev._all_bindings(p) if x["id"] == ref), None)
            v = ev.verify_binding(p, b)
            return f"{v['status']}: claimed {v['claimed']} vs recorded {v['actual']} {v['unit'] or ''}".strip()
        if t in ("decision", "question"):
            d = p.get(t, ref)
            return f"{(d.get('title') or d.get('statement') or '')[:120]} — {d.get('status')}"
        if t == "review":
            r = p.q1("SELECT verdict, summary, author_name FROM reviews WHERE id=?", (ref,))
            return f"{r['verdict']} by {r['author_name']}: {(r['summary'] or '')[:140]}"
        if t == "source":
            s = ev._sources(p).get(ref)
            return f"{s.get('title') or s.get('doi') or s.get('arxiv') or s.get('url')} — identity {s['identity']}, examined {bool(s['examined'])}"
        if t == "doc":
            return f"document {ref[4:]}"
    except (NotFound, ResearchError, TypeError, KeyError):
        pass
    return "(unavailable)"


def _review_line(rv: Optional[Dict[str, Any]]) -> str:
    """One line on the guide's own review state: deterministic validation, model review, human approval (never merged)."""
    if not rv:
        return "**Guide review:** none recorded · **human approval:** none"
    m, h = rv.get("model_review"), rv.get("human_approval")
    ms = (f"{m['verdict']} by {m['reviewer'].split(' (')[0]} ({m['at'][:10]}, {'current' if m['current'] else 'OUTDATED: text or evidence changed since'})"
          if m else "none")
    hs = f"approved by {h['by']} ({h['at'][:10]}, {'current' if h['current'] else 'OUTDATED'})" if h else "none"
    return f"**Independent model review:** {ms} · **human approval:** {hs} · overall: {rv['label']}"


def _panel(p: Project, section: str, rep: Dict[str, Any], claims: Dict[str, Any], figs: Dict[str, Any], secs: Dict[str, Any],
           rv: Optional[Dict[str, Any]] = None) -> str:
    L = ["", "::: {.callout-note collapse=\"false\" appearance=\"minimal\"}", "## Evidence for this chapter {.unnumbered}", ""]
    st = rep["sections"].get(section)
    if st:
        L.append(f"**Source freshness:** {st['state']}" + (f" · pinned {st['pinned_at']}" if st.get("pinned_at") else " · never pinned")
                 + (" · prose edited since last pin" if st.get("edited_since_pin") else "") + f" · owner: {st['owner']}")
        for m in st["stale_because"] + st["errors"] + st["contains_errors"]:
            L.append(f"- ⚠ {_md(m)}")
        L += ["", _review_line(rv), ""]
    mine = {k: c for k, c in claims.items() if c.get("section") == section}
    if mine:
        L += ["| Claim | Kind | Status | Statement |", "|---|---|---|---|"]
        for k, c in mine.items():
            L.append(f"| {k} | {c['kind']}{' (historical)' if c.get('historical') else ''} | {rep['claims'][k]['status']} | {_md(c['statement'])} |")
        for k, c in mine.items():
            L += ["", f"### {k} {{#ev-{k.lower()} .unnumbered}}", ""]
            for s in c["sources"]:
                L.append(f"- *{s['role']}* **{s['ref']}** — {_md(describe_ref(p, s['ref']))}")
            for i in rep["claims"][k]["issues"]:
                if i["severity"] != "info":
                    L.append(f"- **{i['severity']}**: {_md(i['message'])}")
            for m in rep["claims"][k]["stale"]:
                L.append(f"- *stale*: {_md(m)}")
    myfigs = {k: f for k, f in figs.items() if f.get("section") == section}
    if myfigs:
        L += ["", "### Figures {.unnumbered}", ""]
        for k, f in myfigs.items():
            fr = rep["figures"][k]
            L.append(f"- **{k}** `{f['label']}` — {f['kind']} · {fr['status']} · file `{f['file']}` sha256 {f['file_sha256'][:12]}")
            for s in f["sources"]:
                L.append(f"    - data: **{s['ref']}** — {_md(describe_ref(p, s['ref']))}")
            if f.get("generator"):
                g = f["generator"]
                L.append(f"    - generator: `{g.get('script')}` inputs: {', '.join('`' + i + '`' for i in g.get('inputs', [])) or 'none'}")
            for i in fr["issues"]:
                L.append(f"    - **{i['severity']}**: {_md(i['message'])}")
            for m in fr["stale"]:
                L.append(f"    - *stale*: {_md(m)}")
    deps = (secs.get(section) or {}).get("depends_on") or []
    if deps:
        L += ["", "### Also depends on {.unnumbered}", ""] + [f"- **{d['ref']}** — {_md(describe_ref(p, d['ref']))}" for d in deps]
    if not (mine or myfigs or deps):
        L.append("_No claims, figures or dependencies are registered for this chapter yet._")
    L += [":::", ""]
    return "\n".join(L)


def _status_page(p: Project, rep: Dict[str, Any], rvs: Optional[Dict[str, Any]] = None) -> str:
    L = ["# Evidence status {.unnumbered}", "", f"Validation: **{'passed' if rep['ok'] else 'FAILED'}** — {rep['errors']} error(s), "
         f"{rep['warnings']} warning(s), {rep['stale']} stale. Provenance and freshness only; this is neither an independent "
         "scientific review nor human approval.", ""]
    bad = [(k, v) for k, v in list(rep["claims"].items()) + list(rep["figures"].items()) if v["status"] not in ("traceable", "ok", "historical", "retired")]
    if bad:
        L += ["| Item | Status | Detail |", "|---|---|---|"]
        for k, v in bad:
            detail = "; ".join([i["message"] for i in v["issues"] if i["severity"] != "info"] + v["stale"])
            L.append(f"| {k} | {v['status']} | {_md(detail)[:300]} |")
        L.append("")
    for i in rep["prose"]:
        L.append(f"- **{i['severity']}**: {_md(i['message'])}")
    cov = rep["coverage"]
    L += ["", "## Not yet in the guide", "", f"- Experiments: {', '.join(cov['uncited_experiments']) or 'none'}",
          f"- Findings: {', '.join(cov['uncited_findings']) or 'none'}", "", "## Chapters", "",
          "| Chapter | Owner | Provenance | Review and approval |", "|---|---|---|---|"]
    L += [f"| {k} | {s['owner']} | {s['state']} | {(rvs or {}).get(k, {}).get('label', 'unreviewed')} |" for k, s in rep["sections"].items()]
    return "\n".join(L) + "\n"


def _write_if_changed(path: str, text: str) -> bool:
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _atomic_write(path, text)
    return True


def prepare(p: Project) -> Dict[str, Any]:
    """Write the researcher edition's generated evidence panels from the live records. Deterministic and idempotent; touches only
    <guide dir>/_generated/ (git-ignored). Never modifies a Research OS record or any prose."""
    gd = PV.guide_dir(p)
    if not os.path.exists(os.path.join(gd, "_quarto.yml")):
        raise NotFound("No Quarto guide here", hint="Run `research guide quarto init` first.")
    rep = PV.validate(p)
    claims, figs, secs = PV.load_claims(p), PV.load_figures(p), PV.load_sections(p)
    from . import guide_review as GR
    rvs = GR.status(p)
    chapters = sorted(set(listed_chapters(p)) | set(rep["sections"]))
    wrote = 0
    for ch in chapters:
        wrote += _write_if_changed(os.path.join(gd, "_generated", "evidence", slug_of(ch) + ".md"), _panel(p, ch, rep, claims, figs, secs, rvs.get(ch)))
    wrote += _write_if_changed(os.path.join(gd, "_generated", "evidence-status.qmd"), _status_page(p, rep, rvs))
    return {"generated": os.path.relpath(os.path.join(gd, "_generated"), PV.main_root(p)), "files_written": wrote, "chapters": chapters,
            "validation": {"ok": rep["ok"], "errors": rep["errors"], "warnings": rep["warnings"], "stale": rep["stale"]}}


# =============================================================================== source-level checks (no Quarto needed)

_FM = re.compile(r"\A---\n.*?\n---\n", re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_CELL = re.compile(r"^```+\s*\{\s*(?:python|r|julia|ojs|bash|sh|sql|stata|matlab|\.python)\b", re.I | re.M)
_IMG = re.compile(r"!\[(?P<cap>[^\]]*)\]\((?P<src>[^)]*)\)\s*\{(?P<attrs>[^}]*)\}", re.S)
_FIGDIV_CAP = re.compile(r":::\s*\{[^}]*#(fig-[\w-]+)[^}]*\}(.*?)\n:::", re.S)


def visible_in_scientific(text: str) -> str:
    """The chapter text a scientific-edition reader can see: no front matter, comments or researcher-only blocks."""
    t = _COMMENT.sub("", _FM.sub("", text, count=1))
    out, depth, skip = [], 0, None
    for line in t.split("\n"):
        s = line.strip()
        if skip is None and s.startswith(":::") and "content-visible" in s and "researcher" in s and "unless" not in s:
            skip, depth = 1, 1
            continue
        if skip is not None:
            if s.startswith(":::"):
                depth += 1 if re.match(r":::+\s*[{\w]", s) else -1
                if depth <= 0:
                    skip = None
            continue
        out.append(line)
    return "\n".join(out)


def _chapter_files(p: Project) -> List[Tuple[str, str]]:
    gd = PV.guide_dir(p)
    out = []
    for root, dirs, files in os.walk(gd):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "_")) and d not in ("provenance", "filters", "figures"))
        for fn in sorted(files):
            if fn.endswith(".qmd") and not fn.startswith("_"):
                out.append((os.path.relpath(os.path.join(root, fn), gd).replace(os.sep, "/"), os.path.join(root, fn)))
    return out


_REF = re.compile(r"\b(?:EXP|RUN|[FADQ])-\d+(?:#[\w.@-]+|/[A-Z]\d+)?")


def attribution_issues(p: Project, rel: str, text: str) -> List[Dict[str, Any]]:
    """Every record a chapter cites, anywhere (researcher-only blocks included), must exist in THIS project. A report generator that types RUN-0003 or
    EXP-004 into its output attributes results to records it never read: on other data the identifier is stale or invented."""
    body = _FM.sub("", text, count=1)
    missing = sorted({m.group(0) for m in _REF.finditer(body) if PV.ref_state(p, m.group(0)).get("missing")})
    if not missing:
        return []
    return [PV._issue("error", "stale_attribution",
                      f"{rel} cites {', '.join(missing[:6])}{' …' if len(missing) > 6 else ''}, which {'is' if len(missing) == 1 else 'are'} not a record of this project: "
                      "attribution must be read from the data the report is generated from (the run, experiment and artifact it actually used), never typed in")]


def text_issues(p: Project, rel: str, text: str, figs: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The read-only checks for ONE chapter's text (shared by `source_issues` and the promotion gate): identifier leaks into the scientific reading,
    stale or invented attribution, executable cells, figure captions."""
    issues: List[Dict[str, Any]] = []
    figs = PV.load_figures(p) if figs is None else figs
    if _CELL.search(text):
        issues.append(PV._issue("error", "executable_cell", f"{rel} contains an executable code cell; rendering never runs code "
                                                            "(prepare figures with `research guide figure add` instead)"))
    vis = visible_in_scientific(text)
    seen = sorted({m.group(0) for m in _ID.finditer(re.sub(r"\{[^{}\n]*#clm-\d+[^{}\n]*\}", "", vis))})
    if seen:
        issues.append(PV._issue("error", "identifier_in_scientific_text",
                                f"{rel} shows Research OS identifiers to scientific readers: {', '.join(seen[:6])}"
                                + (" …" if len(seen) > 6 else "") + " (move them to provenance, or into a researcher-only block)"))
    issues += attribution_issues(p, rel, text)
    for m in _IMG.finditer(vis):
        label = re.search(r"#(fig-[\w-]+)", m.group("attrs"))
        if not label:
            continue
        cap = m.group("cap").strip()
        if len(cap) < 20:
            issues.append(PV._issue("error", "figure_caption_missing", f"{rel}: {label.group(1)} has no explanatory caption"))
        reg = next((f for f in figs.values() if f["label"] == label.group(1)), None)
        if reg and reg["kind"] == "conceptual" and not re.search(r"illustrat|schemat|conceptual|cartoon|toy", cap, re.I):
            issues.append(PV._issue("warning", "conceptual_not_disclosed", f"{rel}: {label.group(1)} is conceptual but its caption "
                                                                          "does not say it is illustrative"))
        if "fig-alt" not in m.group("attrs"):
            issues.append(PV._issue("warning", "figure_alt_missing", f"{rel}: {label.group(1)} has no fig-alt text"))
    return issues


def source_issues(p: Project) -> List[Dict[str, Any]]:
    """Read-only checks on the .qmd sources: identifier leaks into the scientific reading, stale attribution, executable cells, figure captions,
    chapters missing from the book. Cheap and independent of Quarto."""
    issues: List[Dict[str, Any]] = []
    figs = PV.load_figures(p)
    listed = set(listed_chapters(p))
    for rel, path in _chapter_files(p):
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        issues += text_issues(p, rel, text, figs)
        if listed and rel not in listed and rel != "_quarto.yml":
            issues.append(PV._issue("warning", "chapter_not_in_book", f"{rel} is not listed in _quarto.yml (`research guide chapter add {rel}`)"))
    from . import guide as G
    return issues + G.citation_issues(p)


class _Visible(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        for k, v in attrs:
            if k in ("alt", "title", "aria-label", "data-bs-original-title", "fig-alt") and v:
                self.parts.append(v)

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

    def handle_comment(self, data):  # comments ship in page source, so they count
        self.parts.append(data)


def leak_check_output(out_dir: str) -> List[Dict[str, str]]:
    """Scan a rendered scientific edition (visible text, alt/title attributes, the search index) for Research OS identifiers."""
    found = []
    for root, _d, files in os.walk(out_dir):
        if "site_libs" in root.split(os.sep):
            continue
        for fn in files:
            path = os.path.join(root, fn)
            try:
                if fn.endswith(".html"):
                    v = _Visible()
                    with open(path, encoding="utf-8", errors="replace") as f:
                        v.feed(f.read())
                    text = "\n".join(v.parts)
                elif fn == "search.json":
                    with open(path, encoding="utf-8", errors="replace") as f:
                        text = "\n".join(str(x.get("text", "")) + " " + str(x.get("title", "")) for x in json.load(f))
                else:
                    continue
            except (OSError, ValueError):
                continue
            for m in sorted({x.group(0) for x in _ID.finditer(text)}):
                found.append({"file": os.path.relpath(path, out_dir), "identifier": m})
    return found


# =============================================================================== build / preview

def _preflight(p: Project) -> Tuple[str, str]:
    gd = PV.guide_dir(p)
    if not os.path.exists(os.path.join(gd, "_quarto.yml")):
        raise NotFound("No Quarto guide here", hint="Run `research guide quarto init` first.")
    d = doctor(p)
    if not d["ok"]:
        raise ResearchError("; ".join(d["problems"]), hint=" ".join(d["hints"]))
    return d["quarto"], gd


def build(p: Project, edition: str = "scientific", pdf: bool = False, allow_invalid: bool = False) -> Dict[str, Any]:
    """Render one edition to HTML (and optionally PDF). Provenance is validated first; the scientific edition refuses to publish
    over validation errors (a retracted claim presented as current) unless allow_invalid. A PDF failure never affects the HTML."""
    if edition not in EDITIONS:
        raise ResearchError(f"edition must be one of {', '.join(EDITIONS)}")
    q, gd = _preflight(p)
    prep = prepare(p)
    errs = prep["validation"]["errors"]
    if errs and edition == "scientific" and not allow_invalid:
        raise ResearchError(f"Provenance validation has {errs} error(s); the scientific edition is not built over them",
                            hint="`research guide validate` lists them. The researcher edition still builds so you can inspect them; "
                                 "or pass --allow-invalid.")
    out: Dict[str, Any] = {"edition": edition, "validation": prep["validation"], "warnings": []}
    code, text = _run([q, "render", gd, "--profile", edition, "--to", "html", "--no-execute"], timeout=1800)
    out["html"] = {"ok": code == 0, "dir": os.path.relpath(os.path.join(gd, "_output", edition), PV.main_root(p)), "log": text[-1500:]}
    if code != 0:
        out["ok"] = False
        return out
    out["ok"] = True
    if edition == "scientific":
        leaks = leak_check_output(os.path.join(gd, "_output", "scientific"))
        out["leaks"] = leaks
        if leaks:
            out["ok"] = False
    if pdf and edition != "scientific":
        out["warnings"].append("PDF skipped: the researcher edition's evidence panels are per-chapter web pages; "
                               "build the PDF from the scientific edition")
    elif pdf:
        d = doctor(p)
        if not d["pdf"]:
            out["warnings"].append("PDF skipped: Typst is not available")
        else:
            # Quarto empties the output directory when it renders another format, so the PDF goes to its own directory
            # and is copied next to the HTML afterwards; a failed PDF can then never remove the HTML.
            tmp_rel = f"_output/_pdf-{edition}"
            c2, t2 = _run([q, "render", gd, "--profile", edition, "--to", "typst", "--no-execute", "--output-dir", tmp_rel], timeout=1800)
            out["pdf"] = {"ok": c2 == 0, "log": t2[-800:]}
            if c2 != 0:
                out["warnings"].append("PDF failed (HTML is unaffected): " + (t2.splitlines()[-1] if t2 else ""))
            else:
                tmp = os.path.join(gd, tmp_rel)
                for fn in (os.listdir(tmp) if os.path.isdir(tmp) else []):
                    if fn.endswith(".pdf"):
                        shutil.copy2(os.path.join(tmp, fn), os.path.join(gd, "_output", edition, fn))
                        out["pdf"]["file"] = os.path.relpath(os.path.join(gd, "_output", edition, fn), PV.main_root(p))
                shutil.rmtree(tmp, ignore_errors=True)
    return out


def preview_argv(p: Project, edition: str = "scientific") -> List[str]:
    q, gd = _preflight(p)
    prepare(p)
    return [q, "preview", gd, "--profile", edition, "--no-execute"]
