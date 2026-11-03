"""Skill packs and the skills catalog.

The catalog (templates/catalog.yaml) lists every skill Research OS knows, with where it came from:

    first-party   written for Research OS; ships in templates/skills and is copied into every new project
    bundled       a curated, attributed copy of a third-party skill (templates/catalog/<name>/, with its LICENSE and SOURCE.yaml,
                  pinned to an upstream commit) so projects work offline
    external      listed but not shipped (its licence does not allow redistribution under ours, or it is large): installed from its
                  source at the pinned commit when you ask

A pack (templates/packs.yaml) is a named set of skills. Enabled packs are the config key `skills.packs` (machine or project file; the
project value wins). Enabling a pack installs its skills into .research/skills; disabling one only stops *enabling* it. Nothing you
changed is overwritten, and nothing is deleted: per-skill switches live in `skills.disabled`, activation (`applies_to`, `always`,
`level`) in .research/skills.yaml, which `research skills set` and the Agents page edit.

Activation, not injection: a skill reaches an agent's brief only when it is assigned, matches the task type or role (`applies_to`),
or is always-on; required skills are injected with their hash. Everything else is one line in the skill list.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional

from . import skills as SK
from . import yamlio
from .store import TEMPLATES, Project
from .util import NotFound, ResearchError, as_list, now_iso

_CACHE: Dict[str, Any] = {}


def _load(name: str) -> Dict[str, Any]:
    if name not in _CACHE:
        with open(TEMPLATES / name, encoding="utf-8") as f:
            _CACHE[name] = yamlio.load(f.read()) or {}
    return _CACHE[name]


def catalog() -> List[Dict[str, Any]]:
    return list(_load("catalog.yaml").get("skills") or [])


def pack_defs() -> List[Dict[str, Any]]:
    return list(_load("packs.yaml").get("packs") or [])


def entry(name: str) -> Dict[str, Any]:
    for e in catalog():
        if e["name"] == name:
            return e
    raise NotFound(f"No skill named {name!r} in the catalog", hint="`research skills catalog` lists them.")


def pack(pid: str) -> Dict[str, Any]:
    for x in pack_defs():
        if x["id"] == pid:
            return x
    raise NotFound(f"No skill pack {pid!r}", hint="`research skills packs` lists them.")


def matches(row: Dict[str, Any], query: str) -> bool:
    """Every word of the query appears in the skill's name, description, packs or activation (a word also matches by its stem: statistics ~ statistical)."""
    hay = " ".join([row["name"], row.get("description") or "", " ".join(row.get("packs") or []), " ".join(row.get("applies_to") or [])]).lower()
    for w in query.lower().split():
        stem = w[:-3] if len(w) > 6 else w[:-1] if len(w) > 4 else w
        if w not in hay and stem not in hay:
            return False
    return True


def default_pack_ids() -> List[str]:
    return [x["id"] for x in pack_defs() if x.get("default")]


# =============================================================================== settings (files are the source of truth)

def enabled_pack_ids(p: Project) -> List[str]:
    v = (p.config.get("skills") or {}).get("packs")
    ids = [str(x) for x in as_list(v)] if v is not None else default_pack_ids()
    known = {x["id"] for x in pack_defs()}
    out = [i for i in ids if i in known]
    return out or default_pack_ids()


def disabled_names(p: Project) -> List[str]:
    return [str(x) for x in as_list((p.config.get("skills") or {}).get("disabled"))]


def _save(p: Project, key: str, value: Any) -> None:
    p.save_config({f"skills.{key}": value})
    p.config = p.load_config()


# =============================================================================== installing

def digest_dir(path: str) -> str:
    h = hashlib.sha256()
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        for fn in sorted(files):
            fp = os.path.join(root, fn)
            h.update(os.path.relpath(fp, path).replace(os.sep, "/").encode())
            with open(fp, "rb") as f:
                h.update(f.read())
    return h.hexdigest()[:16]


def _src_dir(e: Dict[str, Any]) -> str:
    return str(TEMPLATES / e["path"])


def _record(p: Project, e: Dict[str, Any], pack_id: Optional[str], dest: str, extra: Optional[Dict[str, Any]] = None) -> None:
    m = SK.load_manifest(p)
    prev = m.get(e["name"]) or {}
    cur = {**prev, "catalog": e["name"], "origin": e["kind"], "license": e.get("license"), "installed_hash": digest_dir(dest), "installed_at": now_iso()}
    if e.get("commit"):
        cur["commit"] = e["commit"]
    if e.get("source"):
        cur["origin_url"] = e["source"]
    packs = sorted(set(as_list(prev.get("packs")) + ([pack_id] if pack_id else [])))
    if packs:
        cur["packs"] = packs
    if "applies_to" not in prev:                      # the project's own activation is never overwritten
        cur["applies_to"] = list(e.get("applies_to") or [])
    if e.get("always") and "always" not in prev:
        cur["always"] = True
    cur.update(extra or {})
    m[e["name"]] = cur
    SK.save_manifest(p, m)


def _clone_url(e: Dict[str, Any]) -> str:
    return e["source"] + ".git" if not e["source"].endswith(".git") else e["source"]


def _fetch_pinned(e: Dict[str, Any], tmp: str) -> str:
    """Shallow-fetch exactly the pinned commit of the source repository and return the skill folder. Reads a repository; runs nothing from it."""
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    for args in (["init", "-q"], ["remote", "add", "origin", _clone_url(e)], ["fetch", "-q", "--depth", "1", "origin", e["commit"]], ["checkout", "-q", "FETCH_HEAD"]):
        r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=env, timeout=300)
        if r.returncode:
            raise ResearchError(f"Could not fetch {e['name']} from {e['source']} at {e['commit'][:10]}: {(r.stderr or r.stdout).strip()[-300:]}",
                                hint="This one is installed from its source and needs network access.")
    d = os.path.join(repo, e["source_path"])
    if not os.path.isfile(os.path.join(d, "SKILL.md")):
        raise NotFound(f"{e['source_path']}/SKILL.md is not in {e['source']} at {e['commit'][:10]}")
    return d


def install_skill(p: Project, name: str, pack_id: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    """Install one catalog skill into .research/skills. An existing folder that you changed (or that is not ours) is kept."""
    e = entry(name)
    dest = p.path("skills", name)
    rec = SK.load_manifest(p).get(name) or {}
    if os.path.exists(dest):
        cur = digest_dir(dest)
        src = digest_dir(_src_dir(e)) if e["kind"] != "external" else None
        pristine = cur == rec.get("installed_hash") or (not rec.get("installed_hash") and src is not None and cur == src)
        if pristine and not force and (src is None or cur == src):
            _record(p, e, pack_id, dest)
            return {"name": name, "state": "present"}
        if pristine:
            shutil.rmtree(dest)                    # an untouched copy is replaced by this release's version
        elif force:
            SK._trash(p, name)                     # your changed copy is kept in .research/cache/removed-skills
        else:
            if pack_id and rec:
                _record_pack(p, name, pack_id)
            return {"name": name, "state": "kept", "why": "a folder with this name already exists and is yours (or was changed): left untouched"}
    if e["kind"] == "external":
        tmp = tempfile.mkdtemp(prefix="research-skill-")
        try:
            shutil.copytree(_fetch_pinned(e, tmp), dest, ignore=shutil.ignore_patterns(".git"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        state = "installed from source"
    else:
        shutil.copytree(_src_dir(e), dest, ignore=shutil.ignore_patterns(".git"))
        state = "installed"
    _record(p, e, pack_id, dest)
    p.event("skill", name, "installed", f"Skill '{name}' installed from the catalog ({e['kind']}, {e.get('license')})")
    return {"name": name, "state": state}


def _record_pack(p: Project, name: str, pack_id: str) -> None:
    m = SK.load_manifest(p)
    if name in m:
        m[name]["packs"] = sorted(set(as_list(m[name].get("packs")) + [pack_id]))
        SK.save_manifest(p, m)


def apply_defaults(p: Project) -> None:
    """Fill the manifest for skills copied at project creation (their activation comes from the catalog). Never changes an existing entry."""
    m = SK.load_manifest(p)
    changed = False
    for e in catalog():
        if e["kind"] != "first-party" or not os.path.isdir(p.path("skills", e["name"])):
            continue
        if e["name"] not in m:
            m[e["name"]] = {"catalog": e["name"], "origin": "first-party", "license": e.get("license"),
                            "installed_hash": digest_dir(p.path("skills", e["name"])), "applies_to": list(e.get("applies_to") or []),
                            "packs": ["core-research"], **({"always": True} if e.get("always") else {})}
            changed = True
    if changed:
        SK.save_manifest(p, m)


def install_pack(p: Project, pid: str, with_optional: bool = False, link: bool = True) -> Dict[str, Any]:
    pk = pack(pid)
    names = list(pk.get("skills") or []) + (list(pk.get("optional") or []) if with_optional else [])
    results = []
    for n in names:
        try:
            results.append(install_skill(p, n, pid))
        except ResearchError as err:
            results.append({"name": n, "state": "failed", "why": str(err)})
    links = SK.link_skills(p) if link else None
    return {"pack": pid, "results": results, "links": links}


def enable_pack(p: Project, pid: str, with_optional: bool = False) -> Dict[str, Any]:
    pack(pid)
    ids = enabled_pack_ids(p)
    if pid not in ids:
        _save(p, "packs", ids + [pid])
    names = set(pack(pid).get("skills") or [])
    off = [n for n in disabled_names(p) if n not in names]     # enabling a pack re-enables its skills
    if len(off) != len(disabled_names(p)):
        _save(p, "disabled", off)
    return install_pack(p, pid, with_optional)


def disable_pack(p: Project, pid: str) -> Dict[str, Any]:
    if pid == "core-research":
        raise ResearchError("Core Research cannot be turned off: it holds the skills the workflow itself refers to",
                            hint="Disable single skills with `research skills disable NAME`.")
    pack(pid)
    keep = [i for i in enabled_pack_ids(p) if i != pid]
    _save(p, "packs", keep)
    still = {n for i in keep for n in (pack(i).get("skills") or [])}
    off = sorted(set(disabled_names(p)) | {n for n in (pack(pid).get("skills") or []) if n not in still})
    _save(p, "disabled", off)
    return {"pack": pid, "disabled": [n for n in off if n in (pack(pid).get("skills") or [])], "links": SK.link_skills(p)}


def set_enabled(p: Project, name: str, enabled: bool) -> Dict[str, Any]:
    off = [n for n in disabled_names(p) if n != name]
    if not enabled:
        off.append(name)
    _save(p, "disabled", sorted(set(off)))
    return {"name": name, "enabled": enabled, "links": SK.link_skills(p)}


# =============================================================================== state for the CLI, the Agents page and the wizard

def state(p: Project) -> Dict[str, Any]:
    m = SK.load_manifest(p)
    off = set(disabled_names(p))
    on = enabled_pack_ids(p)
    out = []
    for e in catalog():
        d = p.path("skills", e["name"])
        have = os.path.isdir(d)
        rec = m.get(e["name"]) or {}
        modified = bool(have and rec.get("installed_hash") and digest_dir(d) != rec["installed_hash"])
        newer = bool(have and rec.get("commit") and e.get("commit") and rec["commit"] != e["commit"])
        applies = as_list(rec.get("applies_to")) if "applies_to" in rec else list(e.get("applies_to") or [])
        out.append({**{k: e.get(k) for k in ("name", "kind", "description", "license", "author", "source", "source_path", "commit", "files", "packs")},
                    "installed": have, "enabled": have and e["name"] not in off, "disabled": e["name"] in off, "modified": modified,
                    "update_available": newer and not modified, "installed_commit": rec.get("commit"),
                    "applies_to": applies, "always": bool(rec.get("always", e.get("always", False))), "level": rec.get("level", "recommended"),
                    "in_enabled_pack": any(i in on for i in as_list(e.get("packs"))), "network": e["kind"] == "external"})
    known = {e["name"] for e in catalog()}
    own = [s for s in SK.list_skills(p) if (s["dir"] or s["name"]) not in known]
    packs = []
    for x in pack_defs():
        names = list(x.get("skills") or [])
        packs.append({"id": x["id"], "title": x["title"], "description": x["description"], "default": bool(x.get("default")), "enabled": x["id"] in on,
                      "skills": names, "optional": list(x.get("optional") or []),
                      "installed": sum(1 for n in names if os.path.isdir(p.path("skills", n))), "total": len(names)})
    return {"catalog": out, "packs": packs, "enabled_packs": on, "disabled": sorted(off),
            "own": [{"name": s["name"], "dir": s["dir"], "description": s["description"], "source": (m.get(s["dir"] or s["name"]) or {}).get("source"),
                     "applies_to": (m.get(s["dir"] or s["name"]) or {}).get("applies_to") or [], "always": bool((m.get(s["dir"] or s["name"]) or {}).get("always"))}
                    for s in own]}


def update(p: Project) -> Dict[str, Any]:
    """Bring bundled and first-party skills to this release's version. A skill you changed is never replaced; a missing first-party
    skill (added after your project was created) is installed. External skills are updated explicitly with `skills install --force`."""
    rows = state(p)["catalog"]
    done, kept = [], []
    for r in rows:
        if r["kind"] == "external":
            continue
        if not r["installed"] and r["kind"] == "first-party":
            install_skill(p, r["name"], "core-research")
            done.append(r["name"] + " (new)")
        elif r["installed"] and not r["modified"]:
            e = entry(r["name"])
            if digest_dir(p.path("skills", r["name"])) != digest_dir(_src_dir(e)):
                install_skill(p, r["name"])
                done.append(r["name"])
        elif r["modified"]:
            kept.append(r["name"])
    for pid in enabled_pack_ids(p):                    # a project from before the packs existed gets the skills of its enabled packs
        for n in pack(pid).get("skills") or []:
            if not os.path.isdir(p.path("skills", n)) and n not in disabled_names(p):
                install_skill(p, n, pid)
                done.append(n + " (new)")
    apply_defaults(p)
    return {"updated": done, "kept_yours": kept, "links": SK.link_skills(p)}
