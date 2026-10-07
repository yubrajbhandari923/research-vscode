"""`research search`: keyword retrieval over everything recorded (tier 3 of agent context).

Every term must match (case-insensitive substring). Title hits weigh more than body hits. Small projects only need
a scan; there is no separate full-text index to keep in sync.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from .schema import ENTITIES
from .store import Project
from .util import as_list

# (type, sql, title column, body columns)
_SOURCES = [(et, f"SELECT * FROM {spec['table']}", "title", [c for c, _ in spec["sections"]])
            for et, spec in ENTITIES.items()]
_SOURCES += [
    ("synthesis", "SELECT * FROM syntheses", "id",
     ["what_happened", "what_worked", "what_failed", "interpretation", "limitations", "unresolved", "next_experiment"]),
    ("run", "SELECT * FROM runs", "label", ["command", "notes", "parameters"]),
    ("artifact", "SELECT * FROM artifacts", "name", ["path", "description"]),
    ("review", "SELECT * FROM reviews", "id", ["summary"]),
]


def _snippet(text: str, terms: List[str], width: int = 160) -> str:
    low = text.lower()
    pos = min((low.find(t) for t in terms if low.find(t) >= 0), default=0)
    start = max(0, pos - width // 3)
    s = re.sub(r"\s+", " ", text[start:start + width]).strip()
    return ("…" if start else "") + s + ("…" if start + width < len(text) else "")


def search(p: Project, query: str, types: Any = None, limit: int = 20) -> List[Dict[str, Any]]:
    terms = [t for t in re.split(r"\s+", (query or "").lower()) if t]
    if not terms:
        return []
    want = {str(t).lower() for t in as_list(types)}
    hits: List[Dict[str, Any]] = []
    for etype, sql, tcol, bcols in _SOURCES:
        if want and etype not in want:
            continue
        for r in p.q(sql):
            title = str(r.get(tcol) or r.get("command") or r.get("id") or "")
            body = "\n".join(str(r.get(c)) for c in bcols if r.get(c))
            tl, bl = title.lower(), body.lower()
            if not all(t in tl or t in bl or t in str(r.get("id", "")).lower() for t in terms):
                continue
            score = sum(3 * tl.count(t) + bl.count(t) for t in terms)
            hits.append({"id": r["id"], "type": etype, "title": title, "status": r.get("status") or r.get("verdict"),
                         "snippet": _snippet(body or title, terms), "score": score,
                         "updated_at": r.get("updated_at") or r.get("created_at")})
    if not want or "note" in want:
        from .services import list_notes
        for n in list_notes(p):
            try:
                with open(p.abspath(n["path"]), encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                continue
            tl, bl = (n.get("title") or "").lower(), text.lower()
            if all(t in tl or t in bl for t in terms):
                hits.append({"id": n["id"], "type": "note", "title": n.get("title"), "status": None,
                             "snippet": _snippet(text, terms), "score": sum(3 * tl.count(t) + bl.count(t) for t in terms),
                             "updated_at": None})
    if not want or "skill" in want:
        from .skills import list_skills
        for s in list_skills(p):
            text = f"{s['name']} {s['description']}".lower()
            if all(t in text for t in terms):
                hits.append({"id": f"skill:{s['name']}", "type": "skill", "title": s["name"], "status": None,
                             "snippet": s["description"], "score": 2, "updated_at": None})
    hits.sort(key=lambda h: str(h.get("updated_at") or ""), reverse=True)  # newest first among equal scores
    hits.sort(key=lambda h: -h["score"])
    return hits[:limit]
