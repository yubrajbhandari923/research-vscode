"""Settings and config, doctor, AGENTS.md, workspaces and the project-initialization wizard."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..util import ResearchError
from . import op

_NOINIT = dict(needs_init=False, needs_project=False)


@op(sync=False)
def settings(p, category: Optional[str] = None, search: Optional[str] = None) -> Dict[str, Any]:
    from .. import registry
    return registry.describe(p, category, search)


@op(sync=False)
def settings_preview(p, changes: Dict[str, Any], scope: str = "project", remove: bool = False, expected: Optional[str] = None) -> Dict[str, Any]:
    from .. import registry
    return registry.preview(p, changes, scope, remove, expected)


@op()
def settings_apply(p, changes: Dict[str, Any], scope: str = "project", reason: Optional[str] = None, confirm: bool = False,
                   remove: bool = False, expected: Optional[str] = None, pin: Optional[str] = None) -> Dict[str, Any]:
    from .. import registry
    return registry.apply(p, changes, scope, reason, confirm, remove, expected, pin)


@op(sync=False)
def config_check(p) -> Dict[str, Any]:
    """Validate this project's config. Read-only."""
    from .. import configfile
    from ..store import DEFAULT_CONFIG
    with open(p.config_path, encoding="utf-8") as f:
        text = f.read()
    return configfile.check(p.raw_config, DEFAULT_CONFIG, text)


@op(sync=False)
def config_show(p, effective: bool = False, sources: bool = False) -> Dict[str, Any]:
    """This project's own settings, every effective value, or per-key provenance."""
    from .. import configfile
    from ..store import DEFAULT_CONFIG
    if sources:
        return {"sources": configfile.sources(p.raw_config, DEFAULT_CONFIG),
                "machine_config": configfile.machine_path(),
                "project_config": p.rel(p.config_path)}
    return {"settings": p.config if effective else p.raw_config, "effective": effective}


@op(needs_init=False, needs_project=False, mcp="doctor")
def doctor(s, fix: Optional[str] = None) -> Dict[str, Any]:
    """The Project Doctor. Without `fix` it is read-only; a fix touches only generated files."""
    from .. import doctor as DR
    return DR.apply_fix(s.root, fix) if fix else DR.run(s.root)


@op(sync=False)
def agents_md_state(p) -> Dict[str, Any]:
    """The managed-block action, the block itself and a unified diff. Touches no file."""
    from .. import agentsmd as AM
    st = AM.state(p)
    out = {k: st[k] for k in ("path", "action", "problem", "up_to_date", "new")}
    out["diff"] = "" if st["problem"] else AM.diff(p)
    return out


@op(sync=False)
def agents_md_apply(p) -> Dict[str, Any]:
    from .. import agentsmd as AM
    return AM.apply(p)


# workspaces ------------------------------------------------------------------------------------------
@op(sync=False)
def workspace_info(p) -> Dict[str, Any]:
    from .. import workspace as W
    return W.info(p)


@op(sync=False, mcp="workspace_list")
def workspaces(p, archived: bool = False) -> List[Dict[str, Any]]:
    from .. import workspace as W
    return W.list_workspaces(p, include_archived=archived)


@op()
def workspace_create(p, experiment: str, name: Optional[str] = None, base: Optional[str] = None) -> Dict[str, Any]:
    from .. import workspace as W
    return W.create(p, experiment, name=name, base=base)


@op()
def experiment_fork(p, id: str, title: str, at: str = "verified") -> Dict[str, Any]:
    from .. import fork
    return fork.fork(p, id, title, at=at)


@op()
def experiment_sync(p, id: str, source: str = "main") -> Dict[str, Any]:
    from .. import fork
    return fork.sync(p, id, source)


@op()
def workspace_archive(p, id: str, dry_run: bool = True, reason: Optional[str] = None) -> Dict[str, Any]:
    """Archival removes a directory, so the impact preview is the default."""
    from .. import workspace as W
    return W.archive_impact(p, id) if dry_run else W.archive(p, id, reason=reason)


# project initialization wizard -----------------------------------------------------------------------
@op(**_NOINIT)
def init_environment(s) -> Dict[str, Any]:
    from .. import initproject
    return initproject.environment(s.root)


@op(**_NOINIT)
def init_preview(s, spec: Dict[str, Any]) -> Dict[str, Any]:
    from .. import initproject
    return initproject.plan(s.root, spec)


@op(**_NOINIT)
def init_yaml_template(s) -> Dict[str, Any]:
    from .. import bootstrap
    return {"text": bootstrap.TEMPLATE}


@op(**_NOINIT)
def init_yaml_check(s, text: str) -> Dict[str, Any]:
    from .. import bootstrap
    return bootstrap.check(text, s.root)


@op(mutates=True, **_NOINIT)
def init_yaml_apply(s, text: str) -> Dict[str, Any]:
    from .. import bootstrap
    return bootstrap.apply(text, s.root, author=s.author)


@op(sync=False)
def init_yaml_export(p) -> Dict[str, Any]:
    from .. import bootstrap
    return {"text": bootstrap.export(p)}


@op(**_NOINIT)
def init_apply(s, spec: Dict[str, Any]) -> Dict[str, Any]:
    from .. import initproject
    return initproject.apply(s.root, spec, author=s.author)


# settings profiles (machine-level bundles, applied per project) ------------------------------------------------
@op(sync=False)
def profiles_list(p) -> Dict[str, Any]:
    from .. import profiles
    return {"profiles": profiles.list_profiles(), "status": profiles.status(p)}


@op(sync=False)
def profile_preview(p, profile: str, other: Optional[str] = None, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """`other` set: the difference between two profiles; else what applying `name` would change in this project."""
    from .. import profiles
    return profiles.diff(p, profile, other) if other else profiles.preview(p, profile, overrides)


@op(mutates=True)
def profile_apply(p, profile: str, overrides: Optional[Dict[str, Any]] = None, pin: Optional[str] = None, reason: Optional[str] = None) -> Dict[str, Any]:
    from .. import profiles
    return profiles.apply(p, profile, overrides, confirm=True, pin=pin, reason=reason)


@op(mutates=True)
def profile_revert(p, pin: Optional[str] = None) -> Dict[str, Any]:
    from .. import profiles
    return profiles.revert(p, confirm=True, pin=pin)


@op(sync=False)
def profile_check(p, profile: str = "overnight") -> Dict[str, Any]:
    from .. import profiles
    return profiles.check(p, profile)
