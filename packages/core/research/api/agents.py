"""Agents, providers, MCP registration, skills, packs, hooks, strategy and the agent-setup page."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .. import services as S
from .. import skills as SK
from ..store import Project
from ..util import ResearchError
from . import op


@op(sync=False)
def agents(p) -> Dict[str, Any]:
    from .. import agents as AG
    return AG.list_profiles(p)


@op(sync=False)
def providers(p) -> Dict[str, Any]:
    """Agent providers for the Control Center: readiness, models, MCP, default, strategy, recent executions."""
    from .. import execution as EX, providers as PR, strategy as ST
    p.config = p.load_config()
    return {"default": (p.config.get("agents") or {}).get("default_profile"), "strategy": ST.active_name(p),
            "providers": PR.table(p), "recent": [{**{k: e.get(k) for k in ("id", "task", "state", "mode", "exit_code", "prepared_at", "cwd")},
                "profile": (e.get("request") or {}).get("profile"), "model": (e.get("request") or {}).get("model")} for e in EX.list_all(p)[:15]],
            "instructions": PR.instruction_files(p.root)}


@op()
def provider_default(p, name: str) -> Dict[str, Any]:
    """Change the project default agent (same setting as the YAML key agents.default_profile). Future dispatches only."""
    from .. import registry
    return registry.apply(p, {"agents.default_profile": name}, reason="provider default")


@op(sync=False)
def provider_mcp(p, name: str, apply: bool = False) -> Dict[str, Any]:
    from .. import providers as PR, workspace as W
    prof = ((p.config.get("agents") or {}).get("profiles") or {}).get(name) or {}
    kind = PR.provider_of(prof, name)
    done = PR.mcp_install(kind, p.root) if apply else None
    ws = W.list_workspaces(p)
    return {"installed": done, "verify": PR.mcp_verify(p.root, ws[0]["path"] if ws else p.root)}


@op(sync=False)
def mcp_install(p, clients: Any = None) -> Dict[str, Any]:
    from .. import mcp as MCP
    return MCP.install(p.root, clients)


@op(sync=False)
def agent_setup(p) -> Dict[str, Any]:
    """Strategy, harness, skills, MCP, orchestration: one read for the UI."""
    from .. import agents as AG, harness as H, hooks as HK, mcp as MCP, orchestrate as O, setup_agents as SA, strategy as ST, usage as U
    p.config = p.load_config()
    cur = ST.active_name(p)
    files = [{k: v for k, v in f.items() if k != "_text"} for f in SA.plan_agent_files(p)]
    hr = H.resolve(p)
    return {"strategy": ST.show(p) if cur else None, "strategies": [
                {**s, "ok": ST.validate(p, s["name"])["ok"], "routes": ST.definition(p, s["name"])["route"]} for s in ST.listing(p)],
            "harness": hr, "harness_warnings": H.warnings(p),
            "skills": [{**{k: s.get(k) for k in ("name", "dir", "description", "path", "source", "always", "applies_to", "level")},
                        "in_harness": s["name"] in hr["skills"]} for s in SK.list_skills(p)],
            "mcp": MCP.status(p.root),
            "orchestration": {"enabled": bool(O.cfg(p).get("enabled")), "limits": O.limits(p), "tiers": O.cfg(p).get("tiers"),
                              "available": O.cfg(p).get("available"), "agent_files": files},
            "hooks": HK.listing(p), "usage": U.summary(p, "strategy"), "tasks": _task_rows(p, cur),
            "experiments": _experiment_rows(p),
            "profiles": [{"name": x["name"], "model": x["model"], "available": x["available"]} for x in AG.list_profiles(p)["profiles"]]}


def _task_rows(p: Project, strat: Optional[str]) -> List[Dict[str, Any]]:
    from .. import orchestrate as O, strategy as ST
    rows = []
    for t in p.q("SELECT * FROM tasks WHERE status IN ('todo','running','verify','blocked') ORDER BY id LIMIT 40"):
        t = p.get("task", t["id"])
        led = O.ledger(p, t["id"])
        r = None
        if strat:
            try:
                x = ST.route_task(p, t, led)
                r = {"level": x["level"], "model": x["model"], "why": x["why"]}
            except ResearchError:
                pass
        rows.append({"id": t["id"], "title": t["title"], "status": t["status"], "override": led.get("override"), "route": r,
                     "contract": led.get("contract") or {}, "checks": len(t.get("checks") or []),
                     "frozen": sorted(led.get("frozen") or {}), "attempts": len(led.get("attempts") or [])})
    return rows


def _experiment_rows(p: Project) -> List[Dict[str, Any]]:
    out = []
    for e in p.q("SELECT id, title, status FROM experiments WHERE status != 'abandoned' ORDER BY id DESC LIMIT 15"):
        ev = p.q1("SELECT ts, summary FROM events WHERE entity_id=? AND action='validated' ORDER BY id DESC LIMIT 1", (e["id"],))
        out.append({**e, "state": S.experiment_state(p, e["id"]), "validation": ev})
    return out


@op(sync=False)
def tools(p) -> Dict[str, Any]:
    """What the agents get from Research OS besides records: the skills catalog and packs (with install, enable and update state), the
    links into the agent CLIs, hooks (installed and recommended for this project) and the MCP command. Read-only."""
    from .. import configfile, hooks as HK, packs as PK, providers as PR
    p.config = p.load_config()
    st = PK.state(p)
    installed = [s for s in SK.list_skills(p) if s.get("enabled", True)]
    links = {}
    for key, rel in SK.LINK_TARGETS.items():
        d = os.path.join(p.root, rel)
        links[key] = {"dir": rel, "linked": sorted(n["name"] for n in installed if os.path.exists(os.path.join(d, n["dir"] or n["name"])))}
    rec = HK.recommended(p)
    return {"catalog": st["catalog"], "packs": st["packs"], "own": st["own"], "enabled_packs": st["enabled_packs"], "disabled": st["disabled"],
            "skills": [{"name": s["name"], "dir": s["dir"], "description": s.get("description"), "level": s["level"], "always": s["always"],
                        "applies_to": s["applies_to"], "source": s.get("source"), "enabled": s.get("enabled", True)} for s in SK.list_skills(p)],
            "enabled_skill_count": len(installed), "links": links, "link_targets": list((p.config.get("skills") or {}).get("link") or []),
            "hooks": HK.listing(p), "recommended_hooks": rec["hooks"], "stacks": rec["stacks"],
            "starters": {n: {"stage": s["stage"], "command": s["command"], "description": s["description"], "installed": n in HK.specs(p)}
                         for n, s in HK.STARTERS.items()},
            "config_files": {"project": p.rel(p.config_path), "machine": configfile.machine_path()},
            "mcp_command": PR._server_cmd(p.root)}


@op(sync=False, mutates=True)
def pack_set(p, id: str, enabled: bool = True, optional: bool = False) -> Dict[str, Any]:
    from .. import packs as PK
    return PK.enable_pack(p, id, optional) if enabled else PK.disable_pack(p, id)


@op(sync=False, mutates=True)
def skill_install(p, names: Any, force: bool = False) -> Dict[str, Any]:
    from .. import packs as PK
    out = [PK.install_skill(p, n, None, force) for n in (names if isinstance(names, list) else [names])]
    SK.link_skills(p)
    return {"results": out}


@op(sync=False, mutates=True)
def skill_toggle(p, name: str, enabled: bool) -> Dict[str, Any]:
    from .. import packs as PK
    return PK.set_enabled(p, name, enabled)


@op(sync=False, mutates=True)
def skills_upgrade(p) -> Dict[str, Any]:
    from .. import packs as PK
    return PK.update(p)


@op(sync=False, mutates=True)
def hook_install(p, names: Any = None, approve: bool = False) -> Dict[str, Any]:
    from .. import hooks as HK
    return HK.install(p, names, approve)


@op(sync=False, mutates=True)
def hook_install_starters(p, approve: bool = True) -> Dict[str, Any]:
    from .. import hooks as HK
    return HK.install_starters(p, approve)


@op(sync=False, mutates=True)
def hook_approve(p, names: Any = None) -> Dict[str, Any]:
    from .. import hooks as HK
    return HK.approve(p, names)


@op(sync=False, mutates=True)
def hook_toggle(p, name: str, enabled: bool) -> Dict[str, Any]:
    from .. import hooks as HK
    return HK.set_enabled(p, name, enabled)


@op(sync=False, mutates=True)
def hook_run(p, name: str) -> Dict[str, Any]:
    from .. import hooks as HK
    return HK.run(p, HK.specs(p)[name]["stage"], names=[name])


@op(sync=False, mutates=True)
def strategy_use(p, name: str, dry_run: bool = False) -> Dict[str, Any]:
    from .. import strategy as ST
    p.config = p.load_config()
    return ST.use(p, name, dry_run)


@op(sync=False, mutates=True)
def setup_agents(p, apply: bool = False, strategy: Optional[str] = None) -> Dict[str, Any]:
    from .. import setup_agents as SA
    return SA.setup(p, apply=apply, strategy=strategy)


@op(mutates=True)
def skills_add(p, source: str, only: Any = None, force: bool = False, always: Optional[bool] = None,
               applies_to: Any = None, list_only: bool = False) -> Dict[str, Any]:
    return SK.add_skills(p, source, only, force, always, applies_to, list_only=list_only)


@op(mutates=True)
def skills_remove(p, name: str) -> Dict[str, Any]:
    return SK.remove_skill(p, name)


@op(mutates=True)
def skills_update(p, names: Any = None) -> Dict[str, Any]:
    return SK.update_skills(p, names)


@op(mutates=True)
def skills_set(p, name: str, always: Optional[bool] = None, applies_to: Any = None, level: Optional[str] = None,
               checks: Any = None) -> Dict[str, Any]:
    return SK.set_skill(p, name, always, applies_to, level, checks)


@op(sync=False, mutates=True)
def skills_link(p) -> Dict[str, Any]:
    return SK.link_skills(p)
