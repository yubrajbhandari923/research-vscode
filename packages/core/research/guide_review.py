"""Scientist Guide review: three different statements, recorded separately and never merged.

    deterministic   `provenance.validate`: do the links resolve, is anything stale, retracted, mismatched. Always current.
    model review    an independent agent's judgement of one chapter against the rubric below (`record`). Not approval.
    human approval  the researcher's own sign-off (`approve`). The only thing that makes a chapter "approved".

A model review or approval is bound to the chapter's *fingerprint*: its prose plus the state of every record its claims and
figures cite. When either changes, the review is reported as outdated, never silently carried over.

    <guide dir>/provenance/reviews.json   chapter → {model: [review…], human: [approval…]}   (appended, never rewritten)
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from . import provenance as PV
from .store import Project
from .util import NotFound, PolicyBlocked, ResearchError, as_list, now_iso

CRITERIA = {
    "evidence_fidelity": "Do the numbers and figures agree with the records they cite? Spot-check with `research evidence value`.",
    "scientific_validity": "Do the conclusions follow from the design and observations? Are confounds, scope and sample size respected?",
    "explanatory_quality": "Are the motivation, method, mathematical intuition and results explained to a reader outside the project?",
    "uncertainty": "Are limitations and alternative interpretations stated, and kept proportionate to the evidence?",
    "historical_consistency": "Are contradicted, superseded or retracted findings treated as history, never as current conclusions?",
    "provenance": "Can each important conclusion and figure be traced to its evidence (`research guide validate`)?",
}
VERDICTS = ["sound", "concerns", "unsound"]
RATINGS = ["ok", "concern", "fail", "not_assessed"]


def _all(p: Project) -> Dict[str, Any]:
    return PV._load(p, "reviews.json", "reviews")


def _chapter(p: Project, section: str) -> str:
    key = PV._section_key(p, section)
    if not key.endswith(".qmd"):
        key += ".qmd"
    if not os.path.exists(os.path.join(PV.guide_dir(p), key)):
        raise NotFound(f"Chapter {key} does not exist")
    return key


def fingerprint(p: Project, key: str) -> str:
    """Identity of what a reviewer saw: the chapter text and the live state of everything its claims and figures rest on."""
    claims, figs = PV.load_claims(p), PV.load_figures(p)
    parts: List[Any] = [PV._sha_file(os.path.join(PV.guide_dir(p), key))]
    for coll in (claims, figs):
        for k, v in sorted(coll.items()):
            if v.get("section") == key and v.get("status") != "retired":
                parts.append([k, v.get("statement") or v.get("file_sha256"), v.get("kind"),
                              [[s["ref"], json.dumps(PV.ref_state(p, s["ref"]), sort_keys=True, default=str)] for s in v.get("sources", [])]])
    return PV._sha_text(json.dumps(parts, sort_keys=True, default=str))[:24]


def record(p: Project, section: str, verdict: str, reviewer: str, ratings: Optional[Dict[str, str]] = None,
           notes: Optional[str] = None, concerns: Any = None, model: Optional[str] = None) -> Dict[str, Any]:
    """Store an independent model review of one chapter. It never changes the chapter, a claim or any Research OS record."""
    if verdict not in VERDICTS:
        raise ResearchError(f"verdict must be one of {', '.join(VERDICTS)}")
    bad = {k: v for k, v in (ratings or {}).items() if k not in CRITERIA or v not in RATINGS}
    if bad:
        raise ResearchError(f"Unknown criterion or rating: {bad}", hint=f"criteria: {', '.join(CRITERIA)}; ratings: {', '.join(RATINGS)}")
    if not reviewer:
        raise ResearchError("A review names its reviewer (--reviewer)")
    key = _chapter(p, section)
    if verdict == "sound" and any(v in ("fail", "concern") for v in (ratings or {}).values()):
        raise ResearchError("A 'sound' verdict cannot carry a failed or concerning criterion", hint="Use verdict `concerns`.")
    allr = _all(p)
    entry = {"at": now_iso(), "reviewer": reviewer, "model": model, "verdict": verdict, "ratings": dict(ratings or {}),
             "concerns": [str(c) for c in as_list(concerns)], "notes": notes or "", "fingerprint": fingerprint(p, key),
             "provenance_errors": PV.validate(p)["sections"].get(key, {}).get("contains_errors", [])}
    allr.setdefault(key, {"model": [], "human": []})["model"].append(entry)
    PV._save(p, "reviews.json", "reviews", allr)
    return {"section": key, **entry}


def approve(p: Project, section: str, who: str, notes: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    """The researcher's sign-off on the chapter as it is now. Refused over provenance errors or an unresolved model review."""
    if not who:
        raise ResearchError("Approval names the person approving (--by)")
    key = _chapter(p, section)
    rep = PV.validate(p)["sections"].get(key)
    problems = list((rep or {}).get("errors", [])) + list((rep or {}).get("contains_errors", []))
    last = (_all(p).get(key) or {}).get("model", [])
    lastm = last[-1] if last else None
    if lastm and lastm["fingerprint"] == fingerprint(p, key) and lastm["verdict"] == "unsound":
        problems.append("the latest independent review judged this chapter unsound")
    if problems and not force:
        raise PolicyBlocked("Not approved: " + "; ".join(problems[:3]),
                            hint="Resolve them, or pass --force to record an approval despite them (the override is recorded).")
    allr = _all(p)
    entry = {"at": now_iso(), "by": who, "notes": notes or "", "fingerprint": fingerprint(p, key),
             "reviewed_by_model": bool(lastm and lastm["fingerprint"] == fingerprint(p, key)), "overrode": problems if problems else []}
    allr.setdefault(key, {"model": [], "human": []})["human"].append(entry)
    PV._save(p, "reviews.json", "reviews", allr)
    return {"section": key, **entry}


def status(p: Project) -> Dict[str, Dict[str, Any]]:
    """Per chapter: the deterministic state plus the latest model review and human approval and whether each is current."""
    rep = PV.validate(p)
    allr = _all(p)
    out: Dict[str, Dict[str, Any]] = {}
    for key, sec in rep["sections"].items():
        if not os.path.exists(os.path.join(PV.guide_dir(p), key)):
            continue
        fp = fingerprint(p, key)
        r = allr.get(key) or {}

        def latest(kind):
            e = (r.get(kind) or [None])[-1]
            return None if not e else {**{k: e[k] for k in e if k != "fingerprint"}, "current": e["fingerprint"] == fp}
        m, h = latest("model"), latest("human")
        out[key] = {"deterministic": {"state": sec["state"], "errors": len(sec["errors"]) + len(sec["contains_errors"]),
                                      "stale": len(sec["stale_because"])},
                    "model_review": m, "human_approval": h,
                    "label": ("approved" if h and h["current"] else
                              "reviewed, awaiting approval" if m and m["current"] and m["verdict"] == "sound" else
                              "reviewed with concerns" if m and m["current"] else
                              "review outdated" if (m or h) else "unreviewed")}
    return out


def brief(p: Project, section: Optional[str] = None) -> str:
    """The reviewer's instructions. The reviewer reads the chapter and checks it against the live records itself."""
    keys = [_chapter(p, section)] if section else sorted(k for k in status(p))
    L = ["# Independent scientific review of the Scientist Guide", "",
         "You are reviewing, not writing. Do not edit any chapter, claim, figure or Research OS record. You are not the "
         "author; if you wrote this chapter, say so and stop.", "",
         "Chapters: " + ", ".join(f"`{k}`" for k in keys), "",
         f"Read each chapter under `{os.path.relpath(PV.guide_dir(p), p.root)}/`, then `research guide validate` and "
         "`research guide claim show` for what it cites. Verify numbers yourself with `research evidence value` / "
         "`research evidence verify`. Assess:", ""]
    L += [f"- **{k}**: {v}" for k, v in CRITERIA.items()]
    L += ["", "Record one review per chapter:", "",
          "`research guide review record CHAPTER.qmd --verdict sound|concerns|unsound --reviewer <your name> "
          "--rating evidence_fidelity=ok --rating uncertainty=concern … --concern \"…\" --notes \"…\"`", "",
          "`sound` means you found no problem on any criterion. Use `not_assessed` for a criterion you could not check; do "
          "not guess. Your review is not approval: only the researcher approves (`research guide review approve`)."]
    return "\n".join(L)


def request(p: Project, section: Optional[str] = None, profile: Optional[str] = None, model: Optional[str] = None,
            strategy: Optional[str] = None) -> Dict[str, Any]:
    """Prepare (never run) an independent reviewer session. Uses the verifier role and its routing."""
    from . import agents
    from . import strategy as S
    policy = None
    if S.active_name(p, strategy):
        policy = S.select(p, "verifier", "verification", "scientific briefing review", strategy=strategy, profile=profile, model=model)
        model = policy["model"]
    d = agents.dispatch(p, "verifier", None, brief(p, section), profile, model, policy=policy,
                        require_skills=["scientific-briefing"])
    return {**d, "chapters": [section] if section else sorted(status(p))}
