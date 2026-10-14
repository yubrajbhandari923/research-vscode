"""`research setup agents`: idempotent, optional Claude Code orchestration setup. Plans first; --apply writes.

Automatic: first-party skills, managed `.claude/agents/research-*.md` (only files carrying our marker), the
`orchestration` config block. Approval needed (`approve=[…]`): third-party skills (ponytail). Never installs MCP servers,
plugins or hooks; never touches permissions. Never launches a model unless `probe=True`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any, Dict, List

from . import orchestrate as O
from .store import TEMPLATES, Project, _atomic_write
from .util import ResearchError

MARK = "research:managed"
PONYTAIL = "https://github.com/DietrichGebert/ponytail"
FIRST_PARTY = ["engineering-loop", "parameter-tuning", "scientist-guide", "scientific-handoff", "synthesize-experiment", "run-experiment"]
OPTIONAL_MCP = {"literature": "Crossref / OpenAlex / Semantic Scholar MCP (network; not installed automatically)"}


def _claude() -> Dict[str, Any]:
    exe = shutil.which("claude")
    if not exe:
        return {"available": False, "version": None}
    try:
        v = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        v = None
    return {"available": True, "version": v}


def _probe(model: str) -> bool:
    try:
        r = subprocess.run(["claude", "-p", "--model", model, "--max-turns", "1", "reply with just: ok"],
                           capture_output=True, text=True, timeout=120)
        return r.returncode == 0 and "ok" in r.stdout.lower()
    except Exception:
        return False


def _recommend(p: Project) -> List[str]:
    r, out = p.root, []
    has = lambda *n: any(os.path.exists(os.path.join(r, x)) for x in n)
    if not has(".mcp.json") or "scholar" not in open(os.path.join(r, ".mcp.json")).read().lower():
        out.append("literature: " + OPTIONAL_MCP["literature"])
    if has("pyproject.toml", "requirements.txt", "setup.py"):
        out.append("python: use the project's existing numpy/scipy/pytest; prefer scipy.optimize or optuna for tuning")
    if (p.config.get("backends") or {}).get("slurm", {}).get("partition") or has("slurm", "job.sbatch"):
        out.append("hpc: SLURM backend already integrated (`research run submit`)")
    if any(f.endswith(".tex") for _, _, fs in os.walk(r) for f in fs[:200]):
        out.append("writing: LaTeX sources found; keep bibliography tooling as-is")
    return out


def _state(path: str, text: str) -> str:
    if not os.path.exists(path):
        return "create"
    cur = open(path, encoding="utf-8").read()
    return "unchanged" if cur == text else ("update" if MARK in cur else "skip-user-owned")


def plan_agent_files(p: Project) -> List[Dict[str, Any]]:
    """Agent files for the active strategy's levels (or the legacy tiers). Legacy managed files left over from before a
    strategy was chosen are reported as `retire`; user-owned files are never touched."""
    from . import strategy as S
    name = S.active_name(p)
    if name:
        sd, res = S.definition(p, name), []
        for lv in sd["levels"]:
            text = S.agent_doc(p, name, lv, MARK)
            path = os.path.join(p.root, ".claude", "agents", f"research-{lv}.md")
            res.append({"tier": lv, "path": os.path.relpath(path, p.root), "state": _state(path, text),
                        "model": S.profile_for_level(p, sd, lv)["model"], "_text": text})
        for tier in O.TIERS:
            path = os.path.join(p.root, ".claude", "agents", f"research-{tier}.md")
            if tier not in sd["levels"] and os.path.exists(path) and MARK in open(path, encoding="utf-8").read():
                res.append({"tier": tier, "path": os.path.relpath(path, p.root), "state": "retire", "model": None, "_text": ""})
        return res
    res = []
    for tier in O.TIERS:
        model = O.resolve(p, tier)["model"] or O.cfg(p)["tiers"][tier]["model"]
        text = (TEMPLATES / "claude-agents" / f"research-{tier}.md").read_text(encoding="utf-8").replace("{{model}}", model)
        path = os.path.join(p.root, ".claude", "agents", f"research-{tier}.md")
        if not os.path.exists(path):
            state = "create"
        else:
            cur = open(path, encoding="utf-8").read()
            state = "unchanged" if cur == text else ("update" if MARK in cur else "skip-user-owned")
        res.append({"tier": tier, "path": os.path.relpath(path, p.root), "state": state, "model": model, "_text": text})
    return res


def setup(p: Project, provider: str = "claude", preset: str = "scientific", apply: bool = False,
          approve: List[str] = (), probe: bool = False, strategy: str = None) -> Dict[str, Any]:
    prev = O.cfg(p).get("active_strategy")
    try:
        if strategy:   # plan against the strategy being requested; saved only on --apply (below)
            p.config.setdefault("orchestration", {})["active_strategy"] = strategy
        return _setup(p, provider, preset, apply, approve, probe, strategy, prev)
    finally:
        if not (apply and strategy):
            p.config.setdefault("orchestration", {})["active_strategy"] = prev


def _setup(p: Project, provider, preset, apply, approve, probe, strategy, prev) -> Dict[str, Any]:
    if provider != "claude" or preset != "scientific":
        raise ResearchError("Only --provider claude --preset scientific is implemented",
                            hint="Other providers keep using explicit `research dispatch --profile …`.")
    cli = _claude()
    if strategy:
        from . import strategy as S
        v = S.validate(p, strategy)
        if not v["ok"]:
            raise ResearchError(f"Strategy {strategy} is invalid: {v['errors'][0]}", hint="`research strategy validate`.")
    files = plan_agent_files(p)
    from .skills import list_skills
    have = {s["name"] for s in list_skills(p)}
    r: Dict[str, Any] = {"applied": apply, "claude": cli, "agent_files": [{k: v for k, v in f.items() if k != "_text"} for f in files],
                         "first_party_skills": {s: ("installed" if s in have else "will install") for s in FIRST_PARTY},
                         "config": "orchestration.enabled=true" if not O.cfg(p).get("enabled") else "already enabled",
                         "installed": [], "skipped": [], "unavailable": [], "recommended": _recommend(p)}
    if not cli["available"]:
        r["unavailable"].append("claude CLI not on PATH (files are still prepared; routing cannot be verified)")
    for nm in ("ponytail", "ponytail-review"):
        if nm in have:
            r["skipped"].append(f"{nm}: already installed")
        elif "ponytail" in approve:
            r["installed"].append(f"{nm}: pending import from {PONYTAIL}")
        else:
            r["skipped"].append(f"{nm}: third-party, not approved (offer: --approve ponytail imports {PONYTAIL})")
    mcp = os.path.join(p.root, ".mcp.json")
    try:
        r["mcp_has_research"] = "research" in (json.load(open(mcp)).get("mcpServers") or {}) if os.path.exists(mcp) else False
    except ValueError:
        r["mcp_has_research"] = None
    for f in files:
        if f["state"] == "skip-user-owned":
            r["skipped"].append(f"{f['path']}: user-owned, left untouched")
    disp = {}
    from . import strategy as S
    sname = S.active_name(p)
    names = list(S.definition(p, sname)["levels"]) if sname else O.TIERS
    model_of = (lambda t: S.select(p, "implementer", level=t)["model"]) if sname else (lambda t: O.resolve(p, t)["model"])
    for t in names:  # verify routing offline: dispatch prepares argv only
        try:
            from . import agents
            m = model_of(t)
            d = agents.dispatch(p, "analyst", None, "routing check", None, m, None) if apply else None
            disp[t] = bool(d and m in d["provider_argv"]) if d else "dry-run"
        except ResearchError as e:
            disp[t] = f"failed: {e}"
    r["routing_verified"] = disp
    if probe and cli["available"]:
        r["live_probe"] = {t: _probe(model_of(t)) for t in names}
    if not apply:
        return r
    for f in files:
        if f["state"] == "retire":   # a managed legacy file: moved aside (not deleted) so the old tier names stop competing
            dst = p.path("cache", "removed-agents", os.path.basename(f["path"]))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(os.path.join(p.root, f["path"]), dst)
            r["installed"].append(f"{f['path']} (retired to {p.rel(dst)})")
        if f["state"] in ("create", "update"):
            os.makedirs(os.path.dirname(os.path.join(p.root, f["path"])), exist_ok=True)
            _atomic_write(os.path.join(p.root, f["path"]), f["_text"])
            r["installed"].append(f"{f['path']} ({f['state']}d)")
    from .skills import add_skills, link_skills
    for s in FIRST_PARTY:  # first-party: copy only if missing
        src, dst = TEMPLATES / "skills" / s, p.path("skills", s)
        if src.is_dir() and not os.path.exists(dst):
            shutil.copytree(src, dst)
            r["installed"].append(f"skill {s}")
    if "ponytail" in approve and "ponytail" not in have:
        add_skills(p, PONYTAIL, only=["ponytail", "ponytail-review"], link=True)
    link_skills(p)
    if not O.cfg(p).get("enabled"):
        p.config.setdefault("orchestration", {})["enabled"] = True
        p.save_config()
    if strategy and prev != strategy:  # only on request; never changes an existing choice silently
        p.save_config({"orchestration.active_strategy": strategy})
    p.event("project", None, "agents_setup", f"agent setup applied ({provider}/{preset})")
    return r
