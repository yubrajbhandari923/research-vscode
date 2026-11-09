"""Scientist Guide: a Quarto book on the main branch (default docs/scientist-guide/) for communicating the science.

It is NOT a second report. `research report` covers every experiment and run; the guide holds the few results worth explaining, and the
researcher's own writing. The division of labour:

    Research OS (deterministic)   scaffolds the book, picks the important results ("highlights"), logs a short entry for each into
                                  results.qmd, keeps provenance, and checks an agent's draft against the limits you set.
    an agent (only when you ask)  writes a SHORT first draft of the main idea, within guide.draft.* limits.
    you                           edit anything, add citations (references.bib), put the results in their scientific context.

Files (all under the guide directory, tracked on main):
    provenance/highlights.json    logged / drafted results: which findings already have an entry, which have a draft
    provenance/draft.json         the baseline of the last prepared draft, so `draft check` can bound what the agent changed
    results.qmd                   one `<!-- research:result F-001 -->` entry per important result (never rewritten once logged)
    references.bib, ideas.qmd     your sources and scratch space (agents never edit them)

Configuration (.research/config.yaml, `guide:`): see registry.py (Settings -> Scientist Guide).
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil
from typing import Any, Dict, List, Optional, Tuple

from . import guide_quarto as GQ
from . import provenance as PV
from .durability import atomic
from .store import Project, _atomic_write
from .util import NotFound, ResearchError, as_list, now_iso

STAR_TAG = "highlight"
_XREF = ("fig", "tbl", "eq", "sec", "lst", "thm", "lem", "cor", "prp", "cnj", "def", "exm", "exr", "nte", "wrn", "tip", "cau", "imp", "clm")
_CITE = re.compile(r"(?<![\w@./-])@([A-Za-z_][\w:.-]*[\w])")
_BIBKEY = re.compile(r"^\s*@(\w+)\s*\{\s*([^,\s]+)\s*,", re.M)


# =============================================================================== settings and files

def settings(p: Project) -> Dict[str, Any]:
    g = dict(p.config.get("guide") or {})
    d = dict(g.get("draft") or {})
    return {"enabled": g.get("enabled", True), "auto_log": g.get("auto_log", True), "highlights": g.get("highlights", "supported"),
            "model": g.get("model"), "profile": g.get("profile"),
            "draft": {"trigger": d.get("trigger", "result"), "max_words": int(d.get("max_words", 150)),
                      "max_sections": int(d.get("max_sections", 3)), "max_figures": int(d.get("max_figures", 1))},
            "publish": {"workflow": (g.get("publish") or {}).get("workflow", True)}}


def exists(p: Project) -> bool:
    return os.path.exists(os.path.join(PV.guide_dir(p), "_quarto.yml"))


def _need_guide(p: Project) -> str:
    if not exists(p):
        raise NotFound("No Scientist Guide here", hint="Create it with `research guide init` (Guide page → Create the guide).")
    return PV.guide_dir(p)


def _state(p: Project, name: str) -> Dict[str, Any]:
    try:
        with open(PV.prov_path(p, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(p: Project, name: str, data: Dict[str, Any]) -> None:
    path = PV.prov_path(p, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _atomic_write(path, json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n")


def init(p: Project, dry_run: bool = False, workflow: Optional[bool] = None) -> Dict[str, Any]:
    """Create the guide (chapters, theme, references.bib, publish workflow). Never overwrites a file."""
    r = GQ.init_quarto(p, dry_run=dry_run, workflow=workflow)
    if not dry_run:
        from . import site
        r["site_pages"] = site.ensure_pages(p)["created"]          # about / research-question pages of the website; existing files untouched
    return r


# =============================================================================== important results

def _numbers(p: Project, fid: str) -> str:
    from .evidence import verify_finding
    try:
        return verify_finding(p, fid)["status"]
    except (NotFound, ResearchError):
        return "unverifiable"


def highlights(p: Project) -> List[Dict[str, Any]]:
    """Findings that count as important results under `guide.highlights`, plus every starred finding. Superseded findings never do;
    a contradicted finding only if starred (the researcher decided it is worth telling)."""
    rule = settings(p)["highlights"]
    hl = _state(p, "highlights.json")
    logged, drafted = hl.get("logged") or {}, hl.get("drafted") or {}
    cited = {s["ref"] for c in PV.load_claims(p).values() if c.get("status") != "retired" for s in c.get("sources", [])}
    out = []
    for r in p.q("SELECT id, title, status, confidence, tags FROM findings WHERE status != 'superseded' ORDER BY id"):
        try:
            tags = json.loads(r["tags"]) if isinstance(r.get("tags"), str) else (r.get("tags") or [])
        except ValueError:
            tags = []
        starred = STAR_TAG in tags
        if not (starred or rule == "all" or (rule == "supported" and r["status"] == "supported")):
            continue
        if r["status"] == "contradicted" and not starred:
            continue
        out.append({"id": r["id"], "title": r["title"], "status": r["status"], "confidence": r["confidence"], "starred": starred,
                    "numbers": _numbers(p, r["id"]), "logged": r["id"] in logged, "drafted": r["id"] in drafted,
                    "in_guide": r["id"] in logged or r["id"] in cited})
    return out


def star(p: Project, fid: str, on: bool = True) -> Dict[str, Any]:
    """Mark (or unmark) a finding as an important result. The mark is the finding's `highlight` tag, so it is part of the record."""
    from .schema import normalize_id
    fid = normalize_id(fid, "F")
    f = p.get("finding", fid)
    tags = [t for t in as_list(f.get("tags")) if t != STAR_TAG] + ([STAR_TAG] if on else [])
    p.update_entity("finding", fid, {"tags": tags}, action="starred" if on else "unstarred",
                    summary=f"{fid} {'marked' if on else 'no longer marked'} as an important result")
    logged = None
    if on and settings(p)["auto_log"] and exists(p):
        logged = log(p, [fid])
    return {"id": fid, "starred": on, "logged": logged}


# =============================================================================== logging results into the guide

_ID = GQ._ID


_DANGLING = re.compile(r"(?:\b(?:in|of|from|with|to|by|for|on|at|and|or|the|a|an|see|per|via|vs\.?)\s*)+[.,;:]*$", re.I)


def _clean(text: Any, words: int, period: bool = True) -> str:
    """Plain, identifier-free text of at most `words` words. Identifiers must not reach the scientific edition; removing one can leave a
    dangling preposition or empty brackets, which are trimmed so the sentence still reads."""
    t = _ID.sub("", re.sub(r"\s+", " ", str(text or "")))
    t = re.sub(r"\(\s*[,;]?\s*\)|\[\s*[,;]?\s*\]", "", t)
    t = re.sub(r"\s+([,.;:)])", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    w = t.split(" ")
    if len(w) > words:
        t = " ".join(w[:words]).rstrip(",;:") + " …"
    t = t.strip(" -—:;,")
    while True:
        u = _DANGLING.sub("", t).rstrip(" ,;:")
        if u == t:
            break
        t = u
    return t + ("." if period and t and t[-1] not in ".!?…)" else "")


def _image_for(p: Project, fid: str) -> Optional[Dict[str, Any]]:
    from .runs import list_artifacts
    from .views import finding_detail
    d = finding_detail(p, fid)
    arts = list(d["artifacts"])
    for x in d["evidence"]["supports"]:
        if x["type"] == "run":
            arts += list_artifacts(p, run=x["id"])
    return next((a for a in arts if a.get("type") == "image" and a.get("exists") and a.get("abspath")), None)


def _entry(p: Project, h: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """The text of one results.qmd entry, with its claim (and figure) registered in the provenance manifest."""
    fid, words = h["id"], cfg["draft"]["max_words"]
    f = p.get("finding", fid)
    title = _clean(f["title"], 14, period=False) or "Result"
    statement = _clean(f.get("statement") or f["title"], min(45, words))
    claim = PV.claim_add(p, statement, "interpretation", "results.qmd", sources=[fid])
    lines = [f"<!-- research:result {fid} -->", f"## {title}", "",
             f"[{statement}]{{#{claim['id'].lower()} .claim}}"
             + ("" if f["status"] == "supported" else f" This result is {f['status']}, not yet independently reviewed."),
             "", "<!-- DRAFT: replace this paragraph with the main idea in your words: what was done, what was found, what it means. "
                 "Cite sources as [@key]. -->"]
    info: Dict[str, Any] = {"claim": claim["id"]}
    art = _image_for(p, fid) if cfg["draft"]["max_figures"] > 0 else None
    if art:
        gd = PV.guide_dir(p)
        name = f"{fid.replace('-', '').lower()}-{re.sub(r'[^A-Za-z0-9._-]+', '-', os.path.basename(art['abspath']))}"  # no "F-001" in the visible path
        rel = f"figures/{name}"
        os.makedirs(os.path.join(gd, "figures"), exist_ok=True)
        shutil.copy2(art["abspath"], os.path.join(gd, rel))
        label = f"fig-{fid.lower().replace('-', '')}-{len(PV.load_figures(p)) + 1}"
        stem = os.path.splitext(os.path.basename(art["abspath"]))[0].replace("_", " ").replace("-", " ")
        fig = PV.figure_add(p, "archived", rel, "results.qmd", label, stem, sources=[art["id"]])
        caption = (f"Plot recorded with this result ({stem}). Replace this caption with what is plotted, the axes and units, "
                   f"and the uncertainty.")
        lines += ["", f"![{caption}]({rel}){{#{label} fig-alt=\"{stem}\"}}"]
        info["figure"] = fig["id"]
    lines += ["", "<!-- /research:result -->", ""]
    return "\n".join(lines), info


@atomic
def log(p: Project, ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Add a short entry to results.qmd for every important result that has none yet. Deterministic, no agent. An entry is created
    once and then belongs to you: it is never rewritten, and deleting it does not bring it back."""
    if not exists(p):
        return {"skipped": "no guide"}
    cfg = settings(p)
    hl = _state(p, "highlights.json")
    logged = dict(hl.get("logged") or {})
    todo = [h for h in highlights(p) if h["id"] not in logged and (not ids or h["id"] in ids)]
    if not todo:
        return {"logged": [], "already": sorted(logged)}
    gd = PV.guide_dir(p)
    GQ.chapter_add(p, "results.qmd", title="Key results")  # creates and lists the chapter when an older guide lacks it
    owner = (PV.load_sections(p).get("results.qmd") or {}).get("owner", "agent")
    target = os.path.join(gd, "results.qmd") if owner != "human" else os.path.join(gd, "_proposals", "results.md")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    try:
        with open(target, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = "# Key results\n\n"
    added = []
    for h in todo:
        block, info = _entry(p, h, cfg)
        text = text.rstrip("\n") + "\n\n" + block
        logged[h["id"]] = {"at": now_iso(), **info, **({"proposal": True} if owner == "human" else {})}
        added.append(h["id"])
    _atomic_write(target, text)
    hl["logged"] = logged
    _save_state(p, "highlights.json", hl)
    return {"logged": added, "chapter": os.path.relpath(target, PV.main_root(p)), "proposal": owner == "human"}


def auto_log(p: Project) -> None:
    """Called when a finding becomes supported. Never raises: logging is a convenience, not part of the review."""
    try:
        if settings(p)["enabled"] and settings(p)["auto_log"] and exists(p):
            log(p)
            from . import site
            if site.auto_enabled(p):
                site.sync(p)
    except Exception:  # noqa: BLE001
        pass


# =============================================================================== agent drafts, bounded

def _words(text: str) -> int:
    t = GQ.visible_in_scientific(text)
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"^#+ .*$", " ", t, flags=re.M)
    t = re.sub(r"\{[^{}\n]*\}", " ", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"\$\$.*?\$\$|\$[^$\n]*\$", " ", t, flags=re.S)
    return len(re.findall(r"[A-Za-z0-9][\w'’-]*", t))


def _images(text: str) -> int:
    return len(re.findall(r"!\[[^\]]*\]\([^)]*\)", GQ.visible_in_scientific(text)))


def _snapshot(p: Project) -> Dict[str, Dict[str, Any]]:
    snap = {}
    for rel, path in GQ._chapter_files(p):
        with open(path, encoding="utf-8") as f:
            t = f.read()
        snap[rel] = {"words": _words(t), "images": _images(t), "sha": hashlib.sha256(t.encode("utf-8")).hexdigest()[:16]}
    return snap


def undrafted(p: Project) -> List[Dict[str, Any]]:
    return [h for h in highlights(p) if not h["drafted"]]


def _guide_level(p: Project, strategy: Optional[str] = None) -> str:
    from . import strategy as S
    return S.definition(p, S.active_name(p, strategy))["guide"]


def draft(p: Project, profile: Optional[str] = None, model: Optional[str] = None, strategy: Optional[str] = None,
          results: Optional[List[str]] = None, sections: Optional[List[str]] = None) -> Dict[str, Any]:
    """Prepare (never run) an agent session that writes a SHORT first draft within the configured limits, and record the baseline that
    `check` compares against afterwards. Targets: the given results / chapters, else the important results with no draft yet."""
    from . import agents
    from . import strategy as S
    cfg = settings(p)
    if not cfg["enabled"]:
        raise ResearchError("The Scientist Guide is disabled", hint="Set guide.enabled: true (Settings → Scientist Guide).")
    _need_guide(p)
    log(p)  # make sure every important result has its entry before an agent is pointed at it
    lim = cfg["draft"]
    from .schema import normalize_id
    want = [normalize_id(x, "F") for x in as_list(results)]
    cand = [h for h in highlights(p) if (h["id"] in want if want else not h["drafted"])]
    targets = [h["id"] for h in cand][: lim["max_sections"]]
    secs = [PV._section_key(p, s) for s in as_list(sections)]
    if not targets and not secs:
        return {"nothing_to_draft": True, "message": "Every important result already has a draft. Star a finding or pass --section to draft a chapter."}
    avail = as_list((p.config.get("orchestration") or {}).get("available"))
    policy = None
    if S.active_name(p, strategy):
        policy = S.select(p, "analyst", "scientific", "scientist guide draft", level=_guide_level(p, strategy),
                          strategy=strategy, profile=profile, model=model)
        model = policy["model"]
    else:
        model = model or cfg["model"]
    if model and avail and model not in avail:
        raise ResearchError(f"Guide model {model} is not available; the draft is not prepared",
                            hint="Fix orchestration.available / guide.model (Settings → Scientist Guide).")
    guide_rel = os.path.relpath(PV.guide_dir(p), PV.main_root(p)).replace(os.sep, "/")
    obj = (f"Write a short FIRST DRAFT in the Scientist Guide ({guide_rel}/). The researcher will rewrite it, add citations and place the "
           f"results in their scientific context, so give the main idea and stop. Follow `research skill show scientific-briefing`.\n"
           f"Limits (checked afterwards with `research guide draft check`): at most {lim['max_words']} words per section; at most "
           f"{lim['max_sections']} section(s); at most {lim['max_figures']} figure(s) per section. Do not touch human-owned chapters "
           f"(ideas.qmd, references.qmd) or references.bib; invent no citation (leave `<!-- cite: what is needed -->` instead).\n"
           + (f"Results to draft, each in its `<!-- research:result F-… -->` entry of results.qmd (replace the DRAFT comment; keep the "
              f"marked claim): {', '.join(targets)}.\n" if targets else "")
           + (f"Chapters to draft: {', '.join(secs)}.\n" if secs else "")
           + (f"When finished run `research guide draft done {' '.join(targets)}`, then `research guide draft check`." if targets
              else "When finished run `research guide draft check`."))
    _save_state(p, "draft.json", {"at": now_iso(), "limits": lim, "targets": targets, "sections": secs, "baseline": _snapshot(p)})
    d = agents.dispatch(p, "analyst", None, obj, profile or cfg["profile"], model, policy=policy,
                        require_skills=["scientific-briefing"])
    return {**d, "targets": targets, "sections": secs, "limits": lim, "path": guide_rel}


def draft_check(p: Project) -> Dict[str, Any]:
    """Compare the guide with the baseline recorded by the last `draft`: did the agent stay within the limits and out of human-owned
    chapters? Read-only."""
    st = _state(p, "draft.json")
    if not st:
        return {"ok": True, "prepared": False, "problems": [], "note": "No draft has been prepared yet."}
    lim, base, now = st["limits"], st.get("baseline") or {}, _snapshot(p)
    owners = {k: v["owner"] for k, v in PV.load_sections(p).items()}
    targets = st.get("targets") or []
    changed = [k for k, v in now.items() if v["sha"] != (base.get(k) or {}).get("sha")]
    problems = []
    if len(changed) > max(lim["max_sections"], len(targets), 1):
        problems.append(f"{len(changed)} chapters changed; the limit is {lim['max_sections']} section(s) per draft ({', '.join(changed)})")
    per = {}
    for k in changed:
        added = now[k]["words"] - (base.get(k) or {}).get("words", 0)
        imgs = now[k]["images"] - (base.get(k) or {}).get("images", 0)
        n = max(1, len(targets)) if k == "results.qmd" else 1  # each result entry is one section of results.qmd
        per[k] = {"words_added": added, "images_added": imgs}
        if owners.get(k) == "human":
            problems.append(f"{k} is human-owned but changed; the agent must propose changes in _proposals/ instead")
        if added > lim["max_words"] * n:
            problems.append(f"{k}: {added} words added; the limit is {lim['max_words'] * n} ({lim['max_words']} per section)")
        if imgs > lim["max_figures"] * n:
            problems.append(f"{k}: {imgs} figure(s) added; the limit is {lim['max_figures'] * n}")
    return {"ok": not problems, "prepared": True, "at": st.get("at"), "changed": changed, "per_chapter": per, "problems": problems,
            "limits": lim}


@atomic
def draft_done(p: Project, ids: Any = None) -> Dict[str, Any]:
    """The agent (or you) records which results now have a draft. Only this clears them from the 'needs a draft' list."""
    hl = _state(p, "highlights.json")
    drafted = dict(hl.get("drafted") or {})
    ids = [str(i).upper() for i in as_list(ids)] or [h["id"] for h in highlights(p) if not h["drafted"]]
    known = {h["id"] for h in highlights(p)}
    bad = [i for i in ids if i not in known]
    if bad:
        raise NotFound(f"Not an important result: {', '.join(bad)}", hint="`research guide highlights` lists them.")
    for i in ids:
        drafted.setdefault(i, now_iso())
    hl["drafted"] = drafted
    _save_state(p, "highlights.json", hl)
    return {"drafted": ids, "check": draft_check(p)}


# =============================================================================== citations

def bib_path(p: Project) -> str:
    return os.path.join(PV.guide_dir(p), "references.bib")


def _bib_text(p: Project) -> str:
    try:
        with open(bib_path(p), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def bib_entries(text: str) -> Dict[str, Dict[str, Any]]:
    """Entries of a BibTeX file: key -> {type, title, authors, year, doi}. Commented lines (`%`) are ignored."""
    clean = "\n".join(l for l in text.split("\n") if not l.lstrip().startswith("%"))
    out: Dict[str, Dict[str, Any]] = {}
    marks = list(_BIBKEY.finditer(clean))
    for i, m in enumerate(marks):
        body = clean[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(clean)]

        def field(name: str) -> Optional[str]:
            r = re.search(rf"\b{name}\s*=\s*(?:\{{((?:[^{{}}]|\{{[^{{}}]*\}})*)\}}|\"([^\"]*)\"|(\w+))", body, re.I)
            return next((g for g in r.groups() if g), None) if r else None
        out[m.group(2)] = {"type": m.group(1).lower(), "title": field("title"), "authors": field("author"), "year": field("year"),
                           "doi": field("doi")}
    return out


def _used_keys(p: Project) -> Dict[str, List[str]]:
    used: Dict[str, List[str]] = {}
    for rel, path in GQ._chapter_files(p):
        try:
            with open(path, encoding="utf-8") as f:
                text = GQ._COMMENT.sub("", GQ._FM.sub("", f.read(), count=1))
        except OSError:
            continue
        text = re.sub(r"`[^`\n]*`", "", re.sub(r"```.*?```", "", text, flags=re.S))  # code is not a citation
        for m in _CITE.finditer(text):
            k = m.group(1)
            if k.split("-", 1)[0] in _XREF and "-" in k:
                continue
            used.setdefault(k, []).append(rel)
    return used


def cite_list(p: Project) -> Dict[str, Any]:
    """Entries of references.bib, which are cited where, which cited keys are missing, which entries are unused."""
    _need_guide(p)
    ent, used = bib_entries(_bib_text(p)), _used_keys(p)
    return {"file": os.path.relpath(bib_path(p), PV.main_root(p)),
            "entries": [{"key": k, **v, "cited_in": sorted(set(used.get(k, [])))} for k, v in sorted(ent.items())],
            "missing": {k: sorted(set(v)) for k, v in sorted(used.items()) if k not in ent},
            "unused": sorted(k for k in ent if k not in used)}


def _key_for(authors: Optional[str], year: Optional[str], title: Optional[str]) -> str:
    first = (authors or "").split(" and ")[0]
    last = (first.split(",")[0] if "," in first else first.split(" ")[-1]).strip()
    word = next((w for w in re.findall(r"[A-Za-z]{4,}", title or "") if w.lower() != last.lower()), "")
    return re.sub(r"[^A-Za-z0-9]", "", (last or "ref")).lower() + str(year or "") + word.lower()


def cite_add(p: Project, bibtex: Optional[str] = None, key: Optional[str] = None, title: Optional[str] = None,
             authors: Optional[str] = None, year: Optional[Any] = None, doi: Optional[str] = None, journal: Optional[str] = None,
             url: Optional[str] = None) -> Dict[str, Any]:
    """Append to references.bib: pasted BibTeX (validated, keys must be new) or a minimal entry from fields. Nothing existing is edited."""
    _need_guide(p)
    existing = bib_entries(_bib_text(p))
    if bibtex and bibtex.strip():
        new = bib_entries(bibtex)
        if not new:
            raise ResearchError("That does not look like BibTeX", hint="It should start like `@article{key, author = {...}, title = {...}}`.")
        if bibtex.count("{") != bibtex.count("}"):
            raise ResearchError("The BibTeX has unbalanced braces", hint="Copy the whole entry, including its closing brace.")
        dup = [k for k in new if k in existing]
        if dup:
            raise ResearchError(f"Already in references.bib: {', '.join(dup)}", hint="Cite the existing key, or pick a different key.")
        text, keys = bibtex.strip() + "\n", list(new)
    else:
        if not (title or "").strip():
            raise ResearchError("Give BibTeX, or at least a title (plus authors and year if you know them)")
        key = (key or _key_for(authors, year, title)).strip()
        if not re.fullmatch(r"[A-Za-z][\w:.-]*", key):
            raise ResearchError("A citation key starts with a letter and uses letters, digits and - _ : .")
        if key in existing:
            raise ResearchError(f"Already in references.bib: {key}", hint="Cite the existing key, or pick a different key.")
        fields = [("author", authors), ("title", title), ("journal", journal), ("year", year), ("doi", doi), ("url", url)]
        text = f"@{'article' if journal else 'misc'}{{{key},\n" + ",\n".join(f"  {k} = {{{v}}}" for k, v in fields if v) + "\n}\n"
        keys = [key]
    cur = _bib_text(p)
    _atomic_write(bib_path(p), cur.rstrip("\n") + "\n\n" + text if cur.strip() else text)
    return {"added": keys, "file": os.path.relpath(bib_path(p), PV.main_root(p)), "cite_as": ", ".join(f"[@{k}]" for k in keys)}


def citation_issues(p: Project) -> List[Dict[str, Any]]:
    """Cited keys that are not in references.bib render as `?key` in the book, so they are errors."""
    ent, used = bib_entries(_bib_text(p)), _used_keys(p)
    return [PV._issue("error", "citation_missing", f"{', '.join(sorted(set(v)))} cites [@{k}], which is not in references.bib "
                                                  "(`research guide cite add`)") for k, v in sorted(used.items()) if k not in ent]


# =============================================================================== status

def publishing(p: Project) -> Dict[str, Any]:
    wf = GQ.workflow_path(p)
    return {"workflow": os.path.exists(wf), "path": os.path.relpath(wf, PV.main_root(p)),
            "site": "GitHub → Settings → Pages → Source: GitHub Actions (once). Every push to main then publishes the scientific edition."}


def _prov(p: Project, v: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    v = v or PV.validate(p)
    return {"ok": v["ok"], "errors": v["errors"], "warnings": v["warnings"],
            "sections": {k: {"state": s["state"], "owner": s["owner"], "why": (s["stale_because"] + s["errors"] + s["contains_errors"])[:5]}
                         for k, s in v["sections"].items()},
            "proposals": sorted(os.path.relpath(x, PV.guide_dir(p)).replace(os.sep, "/")
                                for x in glob.glob(os.path.join(PV.guide_dir(p), "_proposals", "**", "*.md"), recursive=True)),
            "issues": [i["message"] for i in v["prose"] if i["severity"] == "error"][:8]}


def status(p: Project, _rep: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Everything the Guide page, `research guide` and the agent context need. Read-only."""
    cfg = settings(p)
    gd = PV.guide_dir(p)
    out: Dict[str, Any] = {"enabled": cfg["enabled"], "exists": exists(p), "settings": cfg, "abs": gd,
                           "path": os.path.relpath(gd, PV.main_root(p)).replace(os.sep, "/"),
                           "built": {e: os.path.join(gd, "_output", e, "index.html") for e in GQ.EDITIONS
                                     if os.path.exists(os.path.join(gd, "_output", e, "index.html"))}}
    if not out["exists"]:
        return {**out, "highlights": [], "undrafted": 0, "provenance": None, "citations": None, "draft": None, "publishing": publishing(p)}
    hl = highlights(p)
    cl = cite_list(p)
    out.update({"highlights": hl, "undrafted": len([h for h in hl if not h["drafted"]]), "provenance": _prov(p, _rep),
                "citations": {"entries": len(cl["entries"]), "missing": cl["missing"], "unused": cl["unused"]},
                "draft": draft_check(p), "publishing": publishing(p)})
    return out


def overview(p: Project) -> Dict[str, Any]:
    """`status` plus the review/approval label of each chapter and Quarto availability. Read-only (callers open without sync)."""
    from . import guide_review as GR
    rep = PV.validate(p) if exists(p) else None  # once: both the status and the review labels are built from it
    st = status(p, rep)
    return {**st, "review": GR.status(p, rep) if st["exists"] else None, "quarto": GQ.doctor(p)}


def attention(p: Project) -> List[Dict[str, Any]]:
    """Reminders for Research Home, shaped by `guide.draft.trigger`. They only remind: an agent never writes until you start a draft."""
    cfg = settings(p)
    if not (cfg["enabled"] and exists(p)) or cfg["draft"]["trigger"] == "manual":
        return []
    todo = undrafted(p)
    if not todo:
        return []
    if cfg["draft"]["trigger"] == "checkpoint":
        since = min((_state(p, "highlights.json").get("logged") or {}).get(h["id"], {}).get("at", "") for h in todo) or ""
        last = p.q1("SELECT MAX(created_at) AS t FROM checkpoints") or {}
        if not last.get("t") or last["t"] < since:
            return []
    return [{"kind": "guide_draft", "severity": "low", "id": todo[0]["id"], "title": "Important results need a draft in the guide",
             "message": f"{len(todo)} result(s) have no draft: Guide → Draft with an agent (or `research guide draft`)"}]
