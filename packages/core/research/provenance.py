"""Scientist Guide provenance: which Research OS records each scientific statement and figure rests on.

Research OS records stay the only source of truth. Three small JSON files next to the Quarto prose
(`<guide dir>/provenance/`) hold only *links* and a *pinned basis* (the state of each cited record when a person or
agent last read it). Nothing numeric is stored beyond that pin; values are always re-read from the live records.

    claims.json    CLM-0001 → {statement, kind, section, sources:[{ref, role, basis}], figures, historical}
    figures.json   FIG-001  → {kind, file, file_sha256, section, label, sources, generator, input_hashes}
    sections.json  chapter  → {owner: human|agent, depends_on:[{ref, basis}], pinned_at, prose_sha256}

A claim has a *kind* so that observation, derivation, interpretation, hypothesis, speculation and proposed work are
never blurred. A figure has a *kind* so that an archived experimental figure, a recreation, a newly derived analysis and
an illustration are never confused (a conceptual figure can never be evidence for an observation).

`validate` is deterministic and read-only. It re-reads live state and reports missing, stale, contradicted, superseded,
unreviewed and inconsistent evidence. It checks provenance, not science: a clean validation is not scientific approval.

A *ref* names a record: F-003 · EXP-001 · EXP-001/S1 (synthesis) · F-003/B1 (binding) · F-003/R1 (review) · RUN-0007 ·
RUN-0007#loss@last (a recorded metric, optional step number or first|last|min|max) · A-0012 · D-004 · Q-002 ·
SRC-001 (literature source) · doc:path/in/repo (a tracked document, hashed) · CLM-0003 (another claim).
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .schema import normalize_id
from .store import Project, _atomic_write
from .util import NotFound, ResearchError, as_list, now_iso, sha256_file

KINDS = ["observation", "derivation", "interpretation", "hypothesis", "speculation", "proposed"]
EVIDENTIAL = ("observation", "derivation", "interpretation")  # these must rest on something recorded
FIG_KINDS = ["archived", "recreated", "derived", "conceptual"]
ROLES = ["supports", "challenges", "context"]
OWNERS = ["human", "agent"]
DEFAULT_DIR = "docs/scientist-guide"

_CLM = re.compile(r"^CLM-\d{4,}$")
_FIG = re.compile(r"^FIG-\d{3,}$")
_METRIC = re.compile(r"^(RUN-\d+)#([^@\s]+)(?:@(\S+))?$", re.I)
_SUB = re.compile(r"^((?:EXP|F)-\d+)/([SBRV])(\d+)$", re.I)
_PLAIN = re.compile(r"^(EXP|RUN|F|D|Q|A|T|PLAN|CP)-\d+$", re.I)


# =============================================================================== files

def guide_dir(p: Project) -> str:
    return os.path.join(p.root, ((p.config.get("guide") or {}).get("dir") or DEFAULT_DIR))


def prov_path(p: Project, name: str) -> str:
    return os.path.join(guide_dir(p), "provenance", name)


def _load(p: Project, name: str, key: str) -> Dict[str, Any]:
    path = prov_path(p, name)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        got = data.get(key)
        if not isinstance(got, dict):
            raise ValueError(f"missing {key!r} object")
        return got
    except (OSError, ValueError, AttributeError) as e:  # never discard or overwrite a file we cannot read
        raise ResearchError(f"Cannot read {p.rel(path)}: {e}", hint="Fix or restore the file; Research OS will not overwrite it.")


def _save(p: Project, name: str, key: str, data: Dict[str, Any]) -> None:
    path = prov_path(p, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _atomic_write(path, json.dumps({"version": 1, key: data}, indent=1, sort_keys=True, ensure_ascii=False) + "\n")


def load_claims(p: Project) -> Dict[str, Any]:
    return _load(p, "claims.json", "claims")


def load_figures(p: Project) -> Dict[str, Any]:
    return _load(p, "figures.json", "figures")


def load_sections(p: Project) -> Dict[str, Any]:
    return _load(p, "sections.json", "sections")


def _section_key(p: Project, section: str) -> str:
    """Chapter path relative to the guide directory, POSIX style. Accepts a repo-relative or guide-relative path."""
    s = str(section).replace(os.sep, "/").strip().lstrip("./")
    prefix = os.path.relpath(guide_dir(p), p.root).replace(os.sep, "/") + "/"
    return s[len(prefix):] if s.startswith(prefix) else s


def _sha_text(*parts: Any) -> str:
    h = hashlib.sha256()
    for x in parts:
        h.update(str(x if x is not None else "").encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:16]


def _sha_file(path: str) -> Optional[str]:
    try:
        return sha256_file(path)
    except OSError:
        return None


# =============================================================================== live state of a ref

def ref_type(ref: str) -> str:
    r = str(ref).strip()
    if _CLM.match(r):
        return "claim"
    if r.startswith("doc:"):
        return "doc"
    if r.upper().startswith("SRC-"):
        return "source"
    if _METRIC.match(r):
        return "metric"
    m = _SUB.match(r)
    if m:
        return {"S": "synthesis", "B": "binding", "R": "review", "V": "check"}[m.group(2).upper()]
    if _PLAIN.match(r):
        return {"EXP": "experiment", "RUN": "run", "F": "finding", "D": "decision", "Q": "question", "A": "artifact",
                "T": "task", "PLAN": "plan", "CP": "checkpoint"}[r.split("-")[0].upper()]
    raise ResearchError(f"Not a recognised provenance reference: {ref!r}",
                        hint="Use a Research OS id (F-003, EXP-001/S1, RUN-0007#loss@last, A-0012…), SRC-001, doc:path or CLM-0001.")


def norm_ref(ref: str) -> str:
    r = str(ref).strip()
    t = ref_type(r)
    if t == "doc":
        return "doc:" + r[4:].strip().replace(os.sep, "/")
    if t == "claim" or t == "source":
        return r.upper()
    if t == "metric":
        m = _METRIC.match(r)
        return f"{normalize_id(m.group(1), 'RUN')}#{m.group(2)}" + (f"@{m.group(3)}" if m.group(3) else "")
    return normalize_id(r.split("/")[0]) + (("/" + r.split("/", 1)[1].upper()) if "/" in r else "")


def ref_state(p: Project, ref: str) -> Dict[str, Any]:
    """The facts about a cited record that a statement could depend on. {'missing': True} when it does not exist."""
    from . import evidence as ev
    from . import verification as vf
    t = ref_type(ref)
    try:
        if t == "claim":
            c = load_claims(p).get(ref)
            return {"missing": True} if not c else {"text": _sha_text(c.get("statement"), c.get("status"))}
        if t == "doc":
            h = _sha_file(os.path.join(p.root, ref[4:]))
            return {"missing": True} if h is None else {"sha256": h[:16]}
        if t == "source":
            s = ev._sources(p).get(ref.upper())
            return {"missing": True} if not s else {"identity": s["identity"], "examined": bool(s["examined"])}
        if t == "metric":
            m = _METRIC.match(ref)
            step, select = m.group(3), None
            if step and not re.fullmatch(r"-?\d+", step):
                step, select = None, step
            r = ev.resolve_metric(p, m.group(1).upper(), m.group(2), int(step) if step else None, select)
            if r["status"] == "missing" and r["issues"] and "not found" in r["issues"][0]:
                return {"missing": True}
            return {"status": r["status"], "value": None if r["value"] is None else float(f"{r['value']:.10g}"), "unit": r["unit"]}
        if t == "synthesis":
            from .services import get_synthesis
            s = get_synthesis(p, ref.upper())
            return {"text": _sha_text(*(s.get(k) for k in ("what_happened", "what_worked", "what_failed", "interpretation",
                                                           "limitations", "unresolved")))}
        if t == "binding":
            b = next((x for x in ev._all_bindings(p) if x["id"] == ref.upper()), None)
            if not b:
                return {"missing": True}
            dead = {x["supersedes"] for x in ev._all_bindings(p) if x.get("supersedes")}
            return {"status": ev.verify_binding(p, b)["status"], "superseded": b["id"] in dead}
        if t in ("review", "check"):
            r = p.q1("SELECT verdict FROM reviews WHERE id=?", (ref.upper(),))
            return {"missing": True} if not r else {"verdict": r["verdict"]}
        if t == "finding":
            f = p.get("finding", ref.upper())
            last = vf.latest(p, f["id"], "review")
            return {"status": f["status"], "superseded_by": f.get("superseded_by"), "confidence": f.get("confidence"),
                    "review": f"{last['id']}:{last['verdict']}" if last else None,
                    "numerics": ev.verify_finding(p, f["id"])["status"],
                    "text": _sha_text(f.get("statement"), f.get("limitations"))}
        if t == "experiment":
            e = p.get("experiment", ref.upper())
            n = (p.q1("SELECT COUNT(*) AS n FROM syntheses WHERE experiment_id=?", (e["id"],)) or {"n": 0})["n"]
            return {"status": e["status"], "syntheses": n}
        if t == "run":
            r = p.q1("SELECT status FROM runs WHERE id=?", (ref.upper(),))
            return {"missing": True} if not r else {"status": r["status"]}
        if t == "artifact":
            a = ev._art(p, ref.upper())
            return {"hash": (a.get("hash") or "")[:16] or None, "integrity": ev.artifact_integrity(p, a["id"])["status"]}
        if t in ("decision", "question"):
            d = p.get(t, ref.upper())
            return {"status": d.get("status"), "superseded_by": d.get("superseded_by")}
        d = p.get(t, ref.upper())
        return {"status": d.get("status")}
    except NotFound:
        return {"missing": True}


def _diff(old: Dict[str, Any], new: Dict[str, Any]) -> str:
    keys = sorted(set(old) | set(new))
    return "; ".join(f"{k}: {old.get(k)} → {new.get(k)}" for k in keys if old.get(k) != new.get(k))


# =============================================================================== registration

def _next(items: Dict[str, Any], prefix: str, width: int) -> str:
    n = max([int(k.split("-")[1]) for k in items if k.startswith(prefix + "-")] or [0]) + 1
    return f"{prefix}-{n:0{width}d}"


def _source_entries(p: Project, sources: Any) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for s in as_list(sources):
        ref, role = (s["ref"], s.get("role", "supports")) if isinstance(s, dict) else (s, "supports")
        if isinstance(s, str) and ":" in s and s.split(":", 1)[0] in ROLES:  # "challenges:F-004"
            role, ref = s.split(":", 1)
        if role not in ROLES:
            raise ResearchError(f"source role must be one of {', '.join(ROLES)}")
        ref = norm_ref(ref)
        if (ref, role) in seen:
            continue
        seen.add((ref, role))
        st = ref_state(p, ref)
        if st.get("missing"):
            raise NotFound(f"{ref} does not exist in this project", hint="Cite only records that exist; never an invented id.")
        out.append({"ref": ref, "role": role, "basis": st})
    return out


def claim_add(p: Project, statement: str, kind: str, section: str, sources: Any = None, figures: Any = None,
              historical: bool = False, note: Optional[str] = None) -> Dict[str, Any]:
    if kind not in KINDS:
        raise ResearchError(f"kind must be one of {', '.join(KINDS)}")
    if not (statement or "").strip():
        raise ResearchError("A claim needs a statement (a short plain-language paraphrase of what the text asserts)")
    claims = load_claims(p)
    cid = _next(claims, "CLM", 4)
    figs = [str(x).upper() for x in as_list(figures)]
    known = load_figures(p)
    for f in figs:
        if f not in known:
            raise NotFound(f"Figure {f} is not registered", hint="Register it first with `research guide figure add`.")
    claims[cid] = {"statement": statement.strip(), "kind": kind, "section": _section_key(p, section),
                   "sources": _source_entries(p, sources), "figures": figs, "historical": bool(historical),
                   "status": "active", "created_at": now_iso(), **({"note": note} if note else {})}
    _save(p, "claims.json", "claims", claims)
    return {"id": cid, **claims[cid]}


def claim_update(p: Project, cid: str, **fields: Any) -> Dict[str, Any]:
    """Change a claim; re-pins the basis of every source it lists. A retired claim keeps its id forever."""
    claims = load_claims(p)
    c = claims.get(cid.upper())
    if not c:
        raise NotFound(f"Claim {cid} not found")
    for k in ("statement", "kind", "historical", "note", "status"):
        if fields.get(k) is not None:
            if k == "kind" and fields[k] not in KINDS:
                raise ResearchError(f"kind must be one of {', '.join(KINDS)}")
            if k == "status" and fields[k] not in ("active", "retired"):
                raise ResearchError("status must be active or retired")
            c[k] = fields[k]
    if fields.get("section") is not None:
        c["section"] = _section_key(p, fields["section"])
    if fields.get("sources") is not None:
        c["sources"] = _source_entries(p, fields["sources"])
    if fields.get("figures") is not None:
        c["figures"] = [str(x).upper() for x in as_list(fields["figures"])]
    c["updated_at"] = now_iso()
    _save(p, "claims.json", "claims", claims)
    return {"id": cid.upper(), **c}


def figure_add(p: Project, kind: str, file: str, section: str, label: str, title: str, sources: Any = None,
               generator: Optional[Dict[str, Any]] = None, note: Optional[str] = None) -> Dict[str, Any]:
    """Register a figure file (path relative to the guide directory). `generator` = {script, inputs:[paths], args}."""
    if kind not in FIG_KINDS:
        raise ResearchError(f"kind must be one of {', '.join(FIG_KINDS)}")
    if not re.fullmatch(r"fig-[A-Za-z0-9][\w-]*", label or ""):
        raise ResearchError("label must look like fig-sensitivity (Quarto cross-reference id)")
    path = os.path.join(guide_dir(p), file)
    h = _sha_file(path)
    if h is None:
        raise NotFound(f"Figure file not found: {p.rel(path)}")
    figs = load_figures(p)
    if any(f["label"] == label for f in figs.values()):
        raise ResearchError(f"Label {label} is already registered")
    fid = _next(figs, "FIG", 3)
    gen = None
    if generator:
        ins = [str(x) for x in as_list(generator.get("inputs"))]
        gen = {"script": generator.get("script"), "args": as_list(generator.get("args")), "inputs": ins}
    figs[fid] = {"kind": kind, "file": file.replace(os.sep, "/"), "file_sha256": h, "section": _section_key(p, section),
                 "label": label, "title": title, "sources": _source_entries(p, sources), "generator": gen,
                 "input_hashes": {i: _sha_file(os.path.join(p.root, i)) for i in (gen or {}).get("inputs", [])},
                 "created_at": now_iso(), **({"note": note} if note else {})}
    _save(p, "figures.json", "figures", figs)
    return {"id": fid, **figs[fid]}


def figure_refresh(p: Project, fid: str, note: Optional[str] = None) -> Dict[str, Any]:
    """After a figure was regenerated on purpose: re-pin its file hash, its generator inputs and the basis of its sources.
    The previous hash is kept in `history`, so a figure replaced without notice is still visible. It does not clear errors
    about the sources (retractions, mismatches)."""
    figs = load_figures(p)
    fid = fid.upper()
    if fid not in figs:
        raise NotFound(f"Figure {fid} is not registered")
    f = figs[fid]
    h = _sha_file(os.path.join(guide_dir(p), f["file"]))
    if h is None:
        raise NotFound(f"Figure file not found: {f['file']}")
    f.setdefault("history", []).append({"at": now_iso(), "file_sha256": f["file_sha256"], "input_hashes": f.get("input_hashes", {}),
                                        **({"note": note} if note else {})})
    f["file_sha256"] = h
    f["input_hashes"] = {i: _sha_file(os.path.join(p.root, i)) for i in (f.get("generator") or {}).get("inputs", [])}
    for e in f.get("sources", []):
        st = ref_state(p, e["ref"])
        if not st.get("missing"):
            e["basis"] = st
    _save(p, "figures.json", "figures", figs)
    return {"id": fid, **f}


def section_set(p: Project, section: str, owner: Optional[str] = None, depends_on: Any = None) -> Dict[str, Any]:
    if owner is not None and owner not in OWNERS:
        raise ResearchError(f"owner must be one of {', '.join(OWNERS)}")
    secs = load_sections(p)
    key = _section_key(p, section)
    s = secs.setdefault(key, {"owner": "agent", "depends_on": []})
    if owner:
        s["owner"] = owner
    if depends_on is not None:
        old = {d["ref"]: d for d in s.get("depends_on", [])}
        s["depends_on"] = [old.get(e["ref"]) or e for e in _source_entries(p, depends_on)]
    _save(p, "sections.json", "sections", secs)
    return {"section": key, **s}


# =============================================================================== validation

def _issue(sev: str, code: str, msg: str, ref: Optional[str] = None) -> Dict[str, Any]:
    return {"severity": sev, "code": code, "message": msg, **({"ref": ref} if ref else {})}


def _source_issues(p: Project, src: Dict[str, Any], historical: bool, evidential: bool, live: Dict[str, Dict[str, Any]]
                   ) -> List[Dict[str, Any]]:
    ref, role = src["ref"], src.get("role", "supports")
    st = live.get(ref)
    if st is None:
        st = live[ref] = ref_state(p, ref)
    if st.get("missing"):
        return [_issue("error", "missing_ref", f"{ref} no longer exists", ref)]
    out = []
    t = ref_type(ref)
    if role == "supports":
        if t == "finding":
            if st["status"] == "contradicted":
                out.append(_issue("info" if historical else "error", "contradicted_support",
                                  f"{ref} is contradicted" + (" (kept as history)" if historical else
                                                              ": the statement still rests on it; revise or mark it historical"), ref))
            elif st["status"] == "superseded":
                by = st.get("superseded_by")
                out.append(_issue("info" if historical else "error", "superseded_support",
                                  f"{ref} was superseded" + (f" by {by}" if by else "") +
                                  ("" if historical else ": cite the current finding or mark the statement historical"), ref))
            elif st["status"] == "preliminary" and evidential:
                out.append(_issue("warning", "unreviewed_support", f"{ref} is preliminary (no supporting independent review)", ref))
            if st["numerics"] in ("mismatch", "missing", "stale", "unverifiable") and not historical:
                out.append(_issue("error", "numerics_" + st["numerics"], f"{ref}: its numerical evidence is {st['numerics']}", ref))
        elif t == "binding" and st["status"] != "verified" and not historical:
            out.append(_issue("error", "numerics_" + str(st["status"]), f"{ref} is {st['status']}", ref))
        elif t == "binding" and st.get("superseded"):
            out.append(_issue("warning", "superseded_binding", f"{ref} was replaced by a corrected binding", ref))
        elif t == "metric" and st["status"] != "verified":
            out.append(_issue("error", "metric_" + st["status"], f"{ref} cannot be read from the records ({st['status']})", ref))
        elif t == "run" and st["status"] != "completed":
            out.append(_issue("error", "run_not_completed", f"{ref} is {st['status']}; a statement cannot rest on it", ref))
        elif t == "artifact" and st["integrity"] in ("modified", "missing"):
            out.append(_issue("error", "artifact_" + st["integrity"], f"{ref} is {st['integrity']} on disk", ref))
        elif t in ("decision",) and st.get("status") == "superseded":
            out.append(_issue("warning", "superseded_decision", f"{ref} was superseded", ref))
        elif t == "review" and st["verdict"] == "contradicted" and not historical:
            out.append(_issue("error", "contradicted_support", f"{ref} is a contradicting review", ref))
    elif role == "challenges" and t == "finding" and st["status"] in ("superseded",):
        out.append(_issue("info", "superseded_challenge", f"{ref} (a challenge) was superseded", ref))
    return out


def _stale(src: Dict[str, Any], live: Dict[str, Dict[str, Any]]) -> Optional[str]:
    now = live.get(src["ref"])
    old = src.get("basis")
    if now is None or now.get("missing") or old is None:
        return None
    d = _diff(old, now)
    return f"{src['ref']} changed since it was last read ({d})" if d else None


def _claim_report(p: Project, cid: str, c: Dict[str, Any], figs: Dict[str, Any], claims: Dict[str, Any],
                  live: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    issues: List[Dict[str, Any]] = []
    hist = bool(c.get("historical"))
    srcs = c.get("sources") or []
    if c.get("status") == "retired":
        return {"id": cid, "status": "retired", "issues": [], "stale": []}
    if c["kind"] in EVIDENTIAL and not [s for s in srcs if s.get("role") == "supports"]:
        issues.append(_issue("error", "unsupported_claim", f"a{'n' if c['kind'][0] in 'aeiou' else ''} {c['kind']} must cite at least "
                                                          "one supporting record; label it a hypothesis or speculation if it has none"))
    direct = {"metric", "run", "artifact", "binding", "finding", "synthesis"}
    kinds = [ref_type(s["ref"]) for s in srcs if s.get("role") == "supports"]
    if c["kind"] == "observation" and srcs and not (direct & set(kinds)):
        issues.append(_issue("error", "observation_without_data", "an observation must cite recorded data "
                                                                  "(run, metric, artifact, binding, synthesis or finding)"))
    if c["kind"] == "interpretation" and srcs and not ({"finding", "synthesis", "claim", "review"} & set(kinds)):
        issues.append(_issue("warning", "interpretation_without_basis", "an interpretation should cite the finding, synthesis "
                                                                        "or observation it interprets"))
    stale = []
    for s in srcs:
        issues += _source_issues(p, s, hist, c["kind"] in EVIDENTIAL, live)
        if s["ref"].startswith("CLM-"):
            dep = claims.get(s["ref"])
            if dep and dep.get("status") == "retired":
                issues.append(_issue("error", "retired_dependency", f"{s['ref']} was retired", s["ref"]))
        msg = None if hist else _stale(s, live)  # a historical statement records what was believed; it is not re-read
        if msg:
            stale.append(msg)
    for f in c.get("figures") or []:
        fg = figs.get(f)
        if not fg:
            issues.append(_issue("error", "missing_figure", f"{f} is not registered", f))
        elif fg["kind"] == "conceptual" and c["kind"] in ("observation", "derivation"):
            issues.append(_issue("error", "illustration_as_evidence", f"{f} is a conceptual figure; it cannot illustrate "
                                                                      f"an {c['kind']}", f))
    if not c.get("section"):
        issues.append(_issue("warning", "no_section", "claim is not attached to a chapter"))
    errs = [i for i in issues if i["severity"] == "error"]
    if any(i["code"] in ("contradicted_support", "superseded_support") for i in errs):
        label = "contradicted"
    elif any(i["code"] == "unsupported_claim" for i in errs):
        label = "unsupported"
    elif errs:
        label = "invalid"
    elif stale:
        label = "stale"
    elif c["kind"] in ("hypothesis", "speculation", "proposed"):
        label = c["kind"]
    elif any(i["code"] == "unreviewed_support" for i in issues):
        label = "preliminary"
    else:
        label = "historical" if hist else "traceable"
    return {"id": cid, "status": label, "kind": c["kind"], "section": c.get("section"), "issues": issues, "stale": stale}


def _figure_report(p: Project, fid: str, f: Dict[str, Any], live: Dict[str, Dict[str, Any]], claims: Dict[str, Any]
                   ) -> Dict[str, Any]:
    issues: List[Dict[str, Any]] = []
    path = os.path.join(guide_dir(p), f["file"])
    h = _sha_file(path)
    if h is None:
        issues.append(_issue("error", "figure_missing", f"{f['file']} does not exist"))
    elif h != f["file_sha256"]:
        issues.append(_issue("error", "figure_changed", f"{f['file']} differs from the registered file (rebuilt or edited); "
                                                        "if the change is intended, check it against its sources and run `research guide figure refresh`"))
    srcs = f.get("sources") or []
    data = [s for s in srcs if ref_type(s["ref"]) in ("metric", "run", "artifact", "binding", "synthesis", "finding")]
    gen = f.get("generator")
    if f["kind"] == "archived":
        arts = [s for s in srcs if ref_type(s["ref"]) == "artifact"]
        if not arts:
            issues.append(_issue("error", "archived_without_artifact", "an archived figure must cite the registered artifact it is"))
        for s in arts:
            st = live.setdefault(s["ref"], ref_state(p, s["ref"]))
            if not st.get("missing") and h and st.get("hash") and not (f["file_sha256"].startswith(st["hash"])):
                issues.append(_issue("error", "archive_mismatch", f"the figure file is not byte-identical to {s['ref']}", s["ref"]))
    elif f["kind"] in ("recreated", "derived"):
        if not data:
            issues.append(_issue("error", "figure_without_data", f"a {f['kind']} figure must cite the records it plots"))
        if not gen:
            issues.append(_issue("error", "figure_without_generator", f"a {f['kind']} figure needs a generator (script + inputs) "
                                                                      "so it can be reproduced"))
    else:  # conceptual
        if data:
            issues.append(_issue("error", "conceptual_with_data", "a conceptual figure cites recorded data: decide whether it is "
                                                                  "illustration or a recreated/derived figure"))
    stale = []
    for s in srcs:
        issues += _source_issues(p, s, False, True, live)
        msg = _stale(s, live)
        if msg:
            stale.append(msg)
    if gen:
        for i, old in (f.get("input_hashes") or {}).items():
            now = _sha_file(os.path.join(p.root, i))
            if now is None:
                issues.append(_issue("error", "generator_input_missing", f"generator input {i} does not exist"))
            elif old and now != old:
                stale.append(f"generator input {i} changed since the figure was built")
        if gen.get("script") and not os.path.exists(os.path.join(p.root, gen["script"])):
            issues.append(_issue("error", "generator_missing", f"generator script {gen['script']} does not exist"))
    uses = [cid for cid, c in claims.items() if fid in (c.get("figures") or []) and c.get("status") != "retired"]
    errs = [i for i in issues if i["severity"] == "error"]
    return {"id": fid, "kind": f["kind"], "label": f["label"], "section": f.get("section"), "claims": uses,
            "status": "invalid" if errs else ("stale" if stale else "ok"), "issues": issues, "stale": stale}


_CLM_ANCHOR = re.compile(r"#(clm-\d{4,})")
_FIG_ANCHOR = re.compile(r"#(fig-[A-Za-z0-9][\w-]*)")


def _scan_sources(p: Project) -> Dict[str, Dict[str, List[str]]]:
    """{chapter: {'claims': [CLM…], 'figures': [label…]}} from the .qmd files that exist. Read-only."""
    gd = guide_dir(p)
    out: Dict[str, Dict[str, List[str]]] = {}
    for path in sorted(glob.glob(os.path.join(gd, "**", "*.qmd"), recursive=True)):
        rel = os.path.relpath(path, gd).replace(os.sep, "/")
        if rel.startswith(("_output", "_generated")):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                t = f.read()
        except OSError:
            continue
        out[rel] = {"claims": sorted({m.upper() for m in _CLM_ANCHOR.findall(t)}), "figures": sorted(set(_FIG_ANCHOR.findall(t)))}
    return out


def _section_report(p: Project, key: str, s: Dict[str, Any], claim_reports: Dict[str, Any], fig_reports: Dict[str, Any],
                    live: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    path = os.path.join(guide_dir(p), key)
    reasons, errors, child_errors = [], [], []
    exists = os.path.exists(path)
    if not exists:
        errors.append("chapter file does not exist")
    for d in s.get("depends_on", []):
        st = live.setdefault(d["ref"], ref_state(p, d["ref"]))
        if st.get("missing"):
            errors.append(f"{d['ref']} no longer exists")
        else:
            msg = _stale(d, live)
            if msg:
                reasons.append(msg)
    for r in list(claim_reports.values()) + list(fig_reports.values()):
        if r.get("section") != key:
            continue
        for m in r["stale"]:
            reasons.append(f"{r['id']}: {m}")
        for i in r["issues"]:
            if i["severity"] == "error":
                child_errors.append(f"{r['id']}: {i['message']}")
    edited = bool(exists and s.get("prose_sha256") and _sha_file(path) != s["prose_sha256"])
    state = "invalid" if (errors or child_errors) else ("stale" if reasons else "fresh")
    return {"section": key, "owner": s.get("owner", "agent"), "state": state, "stale_because": reasons, "errors": errors,
            "contains_errors": child_errors,
            "edited_since_pin": edited, "pinned_at": s.get("pinned_at")}


def _refs_in_use(claims: Dict[str, Any], figs: Dict[str, Any], secs: Dict[str, Any]) -> List[str]:
    refs = []
    for c in claims.values():
        if c.get("status") != "retired":
            refs += [s["ref"] for s in c.get("sources", [])]
    for f in figs.values():
        refs += [s["ref"] for s in f.get("sources", [])]
    for s in secs.values():
        refs += [d["ref"] for d in s.get("depends_on", [])]
    return refs


def coverage(p: Project) -> Dict[str, Any]:
    """Synthesized experiments and findings the Guide does not cite anywhere yet (what an incremental refresh must add)."""
    refs = _refs_in_use(load_claims(p), load_figures(p), load_sections(p))
    exps, finds = set(), set()
    for r in refs:
        try:
            t = ref_type(r)
        except ResearchError:
            continue
        base = r.split("#")[0].split("/")[0]
        if t in ("experiment", "synthesis"):
            exps.add(base)
        elif t in ("run", "metric"):
            row = p.q1("SELECT experiment_id FROM runs WHERE id=?", (base.upper(),))
            if row:
                exps.add(row["experiment_id"])
        elif t in ("finding", "binding", "review"):
            finds.add(base)
            for l in p.q("SELECT dst_id FROM links WHERE src_id=? AND relation IN ('supports','contradicts')", (base,)):
                if l["dst_id"].startswith("EXP-"):
                    exps.add(l["dst_id"])
                elif l["dst_id"].startswith("RUN-"):
                    row = p.q1("SELECT experiment_id FROM runs WHERE id=?", (l["dst_id"],))
                    if row:
                        exps.add(row["experiment_id"])
    syn = p.q("SELECT DISTINCT experiment_id FROM syntheses ORDER BY experiment_id")
    return {"uncited_experiments": [r["experiment_id"] for r in syn if r["experiment_id"] not in exps],
            "uncited_findings": [r["id"] for r in p.q("SELECT id FROM findings WHERE status!='superseded' ORDER BY id")
                                 if r["id"] not in finds]}


def validate(p: Project) -> Dict[str, Any]:
    """Read-only. Never writes, logs an event, or modifies a record."""
    claims, figs, secs = load_claims(p), load_figures(p), load_sections(p)
    live: Dict[str, Dict[str, Any]] = {}
    cr = {cid: _claim_report(p, cid, c, figs, claims, live) for cid, c in sorted(claims.items())}
    fr = {fid: _figure_report(p, fid, f, live, claims) for fid, f in sorted(figs.items())}
    # cycles among claims that cite each other
    for cid, c in claims.items():
        seen, stack = set(), [s["ref"] for s in c.get("sources", []) if s["ref"].startswith("CLM-")]
        while stack:
            x = stack.pop()
            if x == cid:
                cr[cid]["issues"].append(_issue("error", "claim_cycle", f"{cid} depends on itself"))
                break
            if x in seen or x not in claims:
                continue
            seen.add(x)
            stack += [s["ref"] for s in claims[x].get("sources", []) if s["ref"].startswith("CLM-")]
    # prose ↔ manifest consistency (only for chapters that exist)
    scan = _scan_sources(p)
    placed_claims = {c for v in scan.values() for c in v["claims"]}
    labels = {v for x in scan.values() for v in x["figures"]}
    prose: List[Dict[str, Any]] = []
    for cid, c in claims.items():
        if c.get("status") == "retired":
            continue
        sec = c.get("section")
        if sec in scan and cid not in scan[sec]["claims"]:
            prose.append(_issue("error", "claim_not_in_text", f"{cid} is registered for {sec} but is not marked in the text", cid))
    for sec, v in scan.items():
        for m in v["claims"]:
            if m not in claims:
                prose.append(_issue("error", "unregistered_claim", f"{sec} marks {m}, which is not registered", m))
    for fid, f in figs.items():
        if f.get("section") in scan and f["label"] not in scan[f["section"]]["figures"]:
            prose.append(_issue("error", "figure_not_in_text", f"{fid} ({f['label']}) is not placed in {f['section']}", fid))
    from . import guide_quarto as GQ  # source-level checks on the .qmd files (no Quarto needed)
    prose += GQ.source_issues(p) if os.path.isdir(guide_dir(p)) else []
    sr = {k: _section_report(p, k, s, cr, fr, live) for k, s in sorted(secs.items())}
    for k, v in scan.items():  # chapters with claims/figures but never registered as sections
        if k not in sr and (v["claims"] or v["figures"]):
            sr[k] = _section_report(p, k, {"owner": "agent", "depends_on": []}, cr, fr, live)
            sr[k]["unregistered_section"] = True
    n_err = (sum(1 for r in list(cr.values()) + list(fr.values()) for i in r["issues"] if i["severity"] == "error")
             + len([i for i in prose if i["severity"] == "error"])
             + sum(len(x["errors"]) for x in sr.values()))
    n_warn = (sum(1 for r in list(cr.values()) + list(fr.values()) for i in r["issues"] if i["severity"] == "warning")
              + len([i for i in prose if i["severity"] == "warning"]))
    n_stale = len([s for s in sr.values() if s["state"] == "stale"]) + len([r for r in cr.values() if r["status"] == "stale"])
    return {"ok": n_err == 0, "errors": n_err, "warnings": n_warn, "stale": n_stale, "claims": cr, "figures": fr, "sections": sr,
            "prose": prose, "coverage": coverage(p),
            "note": "Provenance and freshness only. This is neither an independent scientific review nor human approval."}


def pin_section(p: Project, section: str) -> Dict[str, Any]:
    """Record that the section's text was reconciled with the current state of everything it cites: re-pins the basis of its
    claims, figures and dependencies and stores the prose hash. It does NOT clear errors (retractions, mismatches)."""
    key = _section_key(p, section)
    path = os.path.join(guide_dir(p), key)
    if not os.path.exists(path):
        raise NotFound(f"Chapter {key} does not exist")
    claims, figs, secs = load_claims(p), load_figures(p), load_sections(p)
    s = secs.setdefault(key, {"owner": "agent", "depends_on": []})

    def repin(entries):
        for e in entries:
            st = ref_state(p, e["ref"])
            if not st.get("missing"):
                e["basis"] = st
    n = 0
    for c in claims.values():
        if c.get("section") == key and c.get("status") != "retired":
            repin(c.get("sources", []))
            n += 1
    for f in figs.values():
        if f.get("section") == key:
            repin(f.get("sources", []))
            f["input_hashes"] = {i: _sha_file(os.path.join(p.root, i)) for i in (f.get("generator") or {}).get("inputs", [])}
    repin(s.get("depends_on", []))
    s["pinned_at"] = now_iso()
    s["prose_sha256"] = _sha_file(path)
    _save(p, "claims.json", "claims", claims)
    _save(p, "figures.json", "figures", figs)
    _save(p, "sections.json", "sections", secs)
    return {"section": key, "claims_repinned": n, "pinned_at": s["pinned_at"]}
