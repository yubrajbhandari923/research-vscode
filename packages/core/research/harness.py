"""Project harness: references to how *this project* prepares and verifies agent work.

Research OS only carries the references into a dispatch brief; it never interprets or executes them. A project fills
`harness:` in .research/config.yaml with pointers to things it owns (skills, context files, an instruction file,
verification commands). It is independent of the allocation strategy: switch either without touching the other.

    harness: {name: numerical_research, skills: [pde-conventions], context: [docs/conventions.md],
              instructions: docs/AGENT_NOTES.md, checks: ["pytest tests/oracle -q"]}
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List

from .util import as_list


def resolve(p) -> Dict[str, Any]:
    h = p.config.get("harness") or {}
    from .skills import list_skills
    have = {s["name"] for s in list_skills(p)} | {s["dir"] for s in list_skills(p) if s.get("dir")}
    skills = [{"name": n, "missing": n not in have} for n in as_list(h.get("skills"))]
    ctx = [{"path": c, "missing": not os.path.exists(os.path.join(p.root, c))} for c in as_list(h.get("context"))]
    ins = h.get("instructions")
    cfg = {"name": h.get("name"), "skills": [s["name"] for s in skills], "context": [c["path"] for c in ctx],
           "instructions": ins, "checks": as_list(h.get("checks"))}
    fp = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:12]
    configured = any(h.get(k) for k in ("name", "skills", "context", "instructions", "checks", "hooks"))
    return {**cfg, "fingerprint": fp if configured else None, "configured": configured, "hooks": sorted(h.get("hooks") or {}), "skills_detail": skills, "context_detail": ctx,
            "instructions_missing": bool(ins) and not os.path.exists(os.path.join(p.root, ins))}


def warnings(p) -> List[str]:
    r = resolve(p)
    w = [f"harness skill {s['name']!r} is not installed" for s in r["skills_detail"] if s["missing"]]
    w += [f"harness context {c['path']!r} does not exist" for c in r["context_detail"] if c["missing"]]
    return w + ([f"harness instructions {r['instructions']!r} does not exist"] if r["instructions_missing"] else [])


def brief_md(p) -> str:
    r = resolve(p)
    if not r["configured"]:
        return ""
    L = [f"## Project harness{': ' + r['name'] if r['name'] else ''}", "",
         "Supplied by this project; follow it alongside the role guide. Task checks remain authoritative."]
    if r["instructions"]:
        L.append(f"- Read `{r['instructions']}` first" + (" (MISSING)" if r["instructions_missing"] else "") + ".")
    L += [f"- Skill: `research skill show {s['name']}`" + (" (NOT INSTALLED)" if s["missing"] else "") for s in r["skills_detail"]]
    L += [f"- Context: `{c['path']}`" + (" (MISSING)" if c["missing"] else "") for c in r["context_detail"]]
    L += [f"- Project verification command (informational; run it, do not replace task checks): `{c}`" for c in r["checks"]]
    return "\n".join(L)
