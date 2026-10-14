"""Scientist Guide: an optional, agent-maintained scientific narrative (default docs/SCIENTIST_GUIDE.md).

Freshness is derived, not queued: a synthesis is *pending* until `mark_done` records it in .research/guide.json.
So every synthesis is automatically due, nothing can be lost, and queued is never mistaken for updated — only an
explicit `research guide done SYN-ID…` (run by the agent after it edited the guide) clears an entry. Retries are safe.

config.yaml:
    guide: {enabled: true, path: docs/SCIENTIST_GUIDE.md, profile: null, model: opus}
"""
from __future__ import annotations

from .durability import atomic

import json
import os
from typing import Any, Dict, List

from .store import Project, _atomic_write
from .util import NotFound, ResearchError, as_list, now_iso

BEGIN, END = "<!-- research:guide:begin — narrative maintained by the scientist-guide skill -->", "<!-- research:guide:end -->"


def settings(p: Project) -> Dict[str, Any]:
    return p.config.get("guide") or {}


def guide_path(p: Project) -> str:
    return os.path.join(p.root, settings(p).get("path") or "docs/SCIENTIST_GUIDE.md")


def _ledger(p: Project) -> Dict[str, str]:
    try:
        with open(p.path("guide.json"), encoding="utf-8") as f:
            return dict(json.load(f).get("processed") or {})
    except (OSError, ValueError, AttributeError):
        return {}


def _findings(p: Project, exp_id: str) -> List[Dict[str, Any]]:
    from .evidence import verify_finding
    rows = p.q("SELECT DISTINCT f.id, f.title, f.status, f.confidence, l.relation FROM findings f JOIN links l "
               "ON l.src_id=f.id WHERE l.dst_id=? AND l.relation IN ('supports','contradicts') ORDER BY f.id", (exp_id,))
    for r in rows:  # tells the guide writer which numbers may be quoted as verified
        r["numerical"] = verify_finding(p, r["id"])["status"]
    return rows


def status(p: Project) -> Dict[str, Any]:
    path, done = guide_path(p), _ledger(p)
    pending = []
    for s in p.q("SELECT id, experiment_id, created_at FROM syntheses ORDER BY created_at, id"):
        if s["id"] not in done:
            pending.append({**s, "findings": _findings(p, s["experiment_id"])})
    return {"enabled": bool(settings(p).get("enabled", True)), "path": p.rel(path), "exists": os.path.exists(path),
            "pending": pending, "up_to_date": not pending, "processed": len(done)}


def init_guide(p: Project) -> Dict[str, Any]:
    """Create a bare-bones guide from what the project already records. Never overwrites an existing file."""
    path = guide_path(p)
    if os.path.exists(path):
        return {"path": p.rel(path), "created": False}
    proj = p.config["project"]
    qs = p.q("SELECT id, title FROM questions ORDER BY id")
    body = [f"# {p.name} — Scientist Guide", "", BEGIN, ""]
    body.append(proj.get("goal") or proj.get("description")
                or "_The scientific motivation has not been recorded yet. Add it with `research project set --goal`, "
                   "then refresh this guide._")
    if qs:
        body += ["", "The questions currently driving the work:", ""] + [f"- {q['id']}: {q['title']}" for q in qs]
    body += ["", "_No experiments have been synthesized yet, so there is no story beyond the question. This guide grows "
             "as experiments are synthesized._", "", END, ""]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _atomic_write(path, "\n".join(body))
    return {"path": p.rel(path), "created": True}


@atomic
def mark_done(p: Project, ids: Any = None) -> Dict[str, Any]:
    """Record syntheses as reflected in the guide (default: all currently pending). Idempotent."""
    if not os.path.exists(guide_path(p)):
        raise ResearchError("The guide file does not exist", hint="Run `research guide init` first.")
    known = {r["id"] for r in p.q("SELECT id FROM syntheses")}
    ids = as_list(ids) or [s["id"] for s in status(p)["pending"]]
    bad = [i for i in ids if i not in known]
    if bad:
        raise NotFound(f"Unknown synthesis: {', '.join(bad)}")
    led = _ledger(p)
    for i in ids:
        led.setdefault(i, now_iso())
    _atomic_write(p.path("guide.json"), json.dumps({"processed": led}, indent=1, sort_keys=True) + "\n")
    return status(p)


def _guide_level(p: Project, strategy: str = None) -> str:
    from . import strategy as S
    return S.definition(p, S.active_name(p, strategy))["guide"]


def refresh(p: Project, profile: str = None, model: str = None, strategy: str = None) -> Dict[str, Any]:
    """Prepare (never run) an agent session for the configured guide model. Does not mark anything done."""
    from . import agents
    cfg = settings(p)
    if not cfg.get("enabled", True):
        raise ResearchError("The Scientist Guide is disabled", hint="Set guide.enabled: true in .research/config.yaml.")
    from . import strategy as S
    policy = None
    if S.active_name(p, strategy):  # the strategy owns the guide's model; explicit --profile/--model still win
        policy = S.select(p, "analyst", "scientific", "scientist guide", level=_guide_level(p, strategy), strategy=strategy,
                          profile=profile, model=model)
        model = policy["model"]
    else:
        model = model or cfg.get("model")
    if not model:
        raise ResearchError("No guide model configured", hint="Set guide.model (e.g. opus) or pass --model.")
    avail = as_list((p.config.get("orchestration") or {}).get("available"))
    if avail and model not in avail:  # never run the narrative on a lesser model: leave it visibly pending
        raise ResearchError(f"Guide model {model} is not available; the guide stays pending",
                            hint="Fix orchestration.available / guide.model in .research/config.yaml.")
    init_guide(p)
    st = status(p)
    if st["up_to_date"]:
        return {"up_to_date": True, "path": st["path"]}
    ids = [s["id"] for s in st["pending"]]
    obj = (f"Refresh the Scientist Guide at {st['path']} with `research skill show scientist-guide`. Pending syntheses: "
           f"{', '.join(ids)}. Process them together. Only after the guide is edited, run "
           f"`research guide done {' '.join(ids)}`; if you cannot finish, leave them pending and say why.")
    d = agents.dispatch(p, "analyst", None, obj, profile or cfg.get("profile"), model, policy=policy)
    return {**d, "pending": ids, "path": st["path"]}
