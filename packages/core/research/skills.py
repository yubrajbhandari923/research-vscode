"""Project skills: import (folder or git), update, remove, match to tasks, expose to agents.

A skill is a folder with a SKILL.md (Agent Skills format: front-matter `name`, `description`, then instructions;
optional scripts/, references/, assets/). Skills live in .research/skills/<name>/. Import metadata and project
settings live in .research/skills.yaml, so imported SKILL.md files are never modified:

    ponytail:
      source: https://github.com/DietrichGebert/ponytail
      subpath: skills/ponytail
      ref: 3f2c…            # commit at import time (git sources)
      added_at: '…'
      always: true          # list in current.md as always-on for every agent session
      applies_to: []        # task types / roles / tags it is suggested for in `research task context`

Skills are exposed to agents by per-skill symlinks in .claude/skills/ (Claude Code) and .agents/skills/ (Codex and
other Agent Skills tools), controlled by config `skills.link`.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

from . import yamlio
from .store import Project, _atomic_write
from .util import NotFound, ResearchError, as_list, now_iso, slugify

LINK_TARGETS = {"claude": ".claude/skills", "agents": ".agents/skills"}
_SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv"}
_GH_TREE = re.compile(r"^(https?://github\.com/[^/]+/[^/]+?)(?:\.git)?/tree/([^/]+)/(.+)$")
_SHORT = re.compile(r"^(?:gh:|github:)?([\w.-]+)/([\w.-]+)$")


# =============================================================================== manifest

def _manifest_path(p: Project) -> str:
    return p.path("skills.yaml")


def load_manifest(p: Project) -> Dict[str, Dict[str, Any]]:
    path = _manifest_path(p)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = yamlio.load(f.read()) or {}
    except Exception:
        return {}
    return {str(k): (v or {}) for k, v in data.items()} if isinstance(data, dict) else {}


def save_manifest(p: Project, m: Dict[str, Dict[str, Any]]) -> None:
    header = ("# Project skills: where each imported skill came from, and project settings for it.\n"
              "# always: list as always-on in current.md · applies_to: task types/roles/tags it is suggested for.\n")
    _atomic_write(_manifest_path(p), header + yamlio.dump(dict(sorted(m.items()))))


def _read_fm(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            fm, _ = yamlio.split_front_matter(f.read())
        return fm if isinstance(fm, dict) else {}
    except Exception:
        return {}


def list_skills(p: Project) -> List[Dict[str, Any]]:
    from .services import list_skills as _base
    m = load_manifest(p)
    out = []
    for s in _base(p):
        meta = m.get(s["dir"] or s["name"]) or m.get(s["name"]) or {}
        out.append({**s, "source": meta.get("source"), "subpath": meta.get("subpath"), "ref": meta.get("ref"),
                    "added_at": meta.get("added_at"), "always": bool(meta.get("always")),
                    "applies_to": as_list(meta.get("applies_to"))})
    return out


def get_skill(p: Project, name: str) -> Dict[str, Any]:
    want = name.strip().lower()
    for s in list_skills(p):
        if want in (s["name"].lower(), (s["dir"] or "").lower()):
            with open(p.abspath(s["path"]), encoding="utf-8") as f:
                return {**s, "markdown": f.read()}
    names = ", ".join(s["name"] for s in list_skills(p)) or "none"
    raise NotFound(f"No skill named {name!r}", hint=f"Available: {names}")


# =============================================================================== import

def _resolve_source(source: str) -> Tuple[str, Optional[str], Optional[str], bool]:
    """→ (git url or local path, ref, subpath, is_git)."""
    s = source.strip()
    m = _GH_TREE.match(s)
    if m:
        return m.group(1) + ".git", m.group(2), m.group(3).strip("/"), True
    if os.path.exists(os.path.expanduser(s)):
        return os.path.abspath(os.path.expanduser(s)), None, None, False
    if s.startswith(("http://", "https://", "git@", "ssh://", "file://")) or s.endswith(".git"):
        return s, None, None, True
    m = _SHORT.match(s)
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}.git", None, None, True
    raise ResearchError(f"Skill source not found: {source}",
                        hint="Give a folder containing SKILL.md (or a repo with skills/), a git URL, or owner/repo.")


def _git(args: List[str], cwd: Optional[str] = None, timeout: int = 180) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise ResearchError("git is not installed", hint="Install git, or download the skill and pass its folder.")
    except subprocess.TimeoutExpired:
        raise ResearchError(f"git {' '.join(args[:2])} timed out")
    if r.returncode != 0:
        raise ResearchError(f"git {' '.join(args[:2])} failed: {(r.stderr or r.stdout).strip()[-400:]}")
    return r.stdout.strip()


def discover(root: str) -> List[Dict[str, str]]:
    """Skill folders under `root`: root itself if it has SKILL.md, else every SKILL.md folder outside hidden dirs.
    Duplicates (same name) prefer the copy under a `skills/` folder, e.g. ponytail's skills/ponytail/."""
    if os.path.isfile(os.path.join(root, "SKILL.md")):
        found = [root]
    else:
        found = []
        for d, dirs, files in os.walk(root):
            dirs[:] = sorted(x for x in dirs if not x.startswith(".") and x not in _SKIP_DIRS)
            if "SKILL.md" in files and d != root:
                found.append(d)
                dirs[:] = []
    best: Dict[str, Dict[str, str]] = {}
    for d in found:
        fm = _read_fm(os.path.join(d, "SKILL.md"))
        name = slugify(str(fm.get("name") or os.path.basename(d.rstrip("/"))))
        rel = os.path.relpath(d, root).replace(os.sep, "/")
        cand = {"name": name, "dir": d, "subpath": "" if rel == "." else rel,
                "description": str(fm.get("description") or "").strip()}
        prev = best.get(name)
        if prev is None or ("skills/" in "/" + cand["subpath"] and "skills/" not in "/" + prev["subpath"]):
            best[name] = cand
    return sorted(best.values(), key=lambda x: x["name"])


def _trash(p: Project, name: str) -> Optional[str]:
    src = p.path("skills", name)
    if not os.path.exists(src):
        return None
    dst = p.path("cache", "removed-skills", f"{name}-{time.strftime('%Y%m%d-%H%M%S')}")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    return dst


def add_skills(p: Project, source: str, only: Any = None, force: bool = False, always: Optional[bool] = None,
               applies_to: Any = None, link: bool = True, list_only: bool = False) -> Dict[str, Any]:
    """Import skill(s) from a folder or git repo into .research/skills/. Returns {added, available, links}."""
    where, ref, subpath, is_git = _resolve_source(source)
    return _import(p, where, ref, subpath, is_git, source, only, force, always, applies_to, link, list_only)


def _import(p: Project, where: str, ref: Optional[str], subpath: Optional[str], is_git: bool, label: str,
            only: Any, force: bool, always: Optional[bool], applies_to: Any, link: bool, list_only: bool) -> Dict[str, Any]:
    source = label
    tmp = None
    try:
        if is_git:
            tmp = tempfile.mkdtemp(prefix="research-skill-")
            clone = os.path.join(tmp, "repo")
            _git(["clone", "--depth", "1"] + (["--branch", ref] if ref else []) + [where, clone])
            ref = _git(["rev-parse", "HEAD"], cwd=clone)
            root = os.path.join(clone, subpath) if subpath else clone
            if not os.path.isdir(root):
                raise NotFound(f"{subpath} not found in {where}")
        else:
            root = where
        avail = discover(root)
        if not avail:
            raise NotFound(f"No SKILL.md found in {source}")
        if list_only:
            return {"added": [], "available": [{k: a[k] for k in ("name", "subpath", "description")} for a in avail]}
        wanted = {slugify(x) for x in as_list(only)}
        if wanted:
            missing = wanted - {a["name"] for a in avail}
            if missing:
                raise NotFound(f"Not in {source}: {', '.join(sorted(missing))}",
                               hint="Available: " + ", ".join(a["name"] for a in avail))
            avail = [a for a in avail if a["name"] in wanted]
        clash = [a["name"] for a in avail if os.path.exists(p.path("skills", a["name"]))]
        if clash and not force:
            raise ResearchError(f"Skill already exists: {', '.join(clash)}",
                                hint="Use --force to replace (the old copy is kept in .research/cache/removed-skills/), "
                                     "or `research skills update NAME`.")
        m = load_manifest(p)
        added = []
        for a in avail:
            if a["name"] in clash:
                _trash(p, a["name"])
            shutil.copytree(a["dir"], p.path("skills", a["name"]), ignore=shutil.ignore_patterns(".git"))
            prev = m.get(a["name"]) or {}
            entry = {
                "source": (where[:-4] if where.endswith(".git") else where) if is_git else p.rel(where),
                "subpath": "/".join(x for x in (subpath, a["subpath"]) if x) or None,
                "ref": ref, "added_at": now_iso(),
                "always": prev.get("always", False) if always is None else bool(always),
                "applies_to": as_list(applies_to) if applies_to is not None else as_list(prev.get("applies_to")),
            }
            m[a["name"]] = entry
            added.append(a["name"])
            p.event("skill", a["name"], "imported", f"Skill '{a['name']}' imported from {source}"
                    + (f" @ {ref[:10]}" if ref else ""))
        save_manifest(p, m)
        links = link_skills(p) if link else None
        return {"added": added, "available": [a["name"] for a in discover(root)], "links": links}
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def update_skills(p: Project, names: Any = None) -> Dict[str, Any]:
    """Re-import skills from their recorded source (all imported skills if none named)."""
    m = load_manifest(p)
    targets = [slugify(n) for n in as_list(names)] or [n for n, e in m.items() if e.get("source")]
    updated = []
    for n in targets:
        e = m.get(n)
        if not e or not e.get("source"):
            raise ResearchError(f"Skill '{n}' has no recorded source", hint="Only imported skills can be updated.")
        src, sub = e["source"], e.get("subpath")
        local = p.abspath(src)
        if local and os.path.isdir(local):
            _import(p, os.path.join(local, sub) if sub else local, None, None, False, src, [n], True, None, None, False, False)
        else:
            _import(p, src, None, sub, True, src, [n], True, None, None, False, False)
        updated.append(n)
    return {"updated": updated, "links": link_skills(p)}


def remove_skill(p: Project, name: str) -> Dict[str, Any]:
    n = slugify(name)
    moved = _trash(p, n)
    if not moved:
        raise NotFound(f"Skill '{n}' not found")
    m = load_manifest(p)
    m.pop(n, None)
    save_manifest(p, m)
    p.event("skill", n, "removed", f"Skill '{n}' removed (kept in {p.rel(moved)})")
    return {"removed": n, "kept_at": p.rel(moved), "links": link_skills(p)}


def set_skill(p: Project, name: str, always: Optional[bool] = None, applies_to: Any = None) -> Dict[str, Any]:
    n = get_skill(p, name)["dir"] or slugify(name)
    m = load_manifest(p)
    e = m.get(n) or {}
    if always is not None:
        e["always"] = bool(always)
    if applies_to is not None:
        e["applies_to"] = [str(x).strip() for x in as_list(applies_to) if str(x).strip()]
    m[n] = e
    save_manifest(p, m)
    p.event("skill", n, "updated", f"Skill '{n}' settings: always={e.get('always', False)}, "
            f"applies_to={e.get('applies_to') or []}")
    return next(s for s in list_skills(p) if (s["dir"] or s["name"]) == n)


# =============================================================================== expose to agents

def link_skills(p: Project, targets: Any = None) -> Dict[str, Any]:
    """Per-skill symlinks <root>/.claude/skills/<name> and <root>/.agents/skills/<name> → .research/skills/<name>.
    Never touches entries that aren't ours; removes our links whose skill is gone."""
    keys = as_list(targets) if targets is not None else as_list((p.config.get("skills") or {}).get("link"))
    report: Dict[str, Any] = {}
    skills_dir = p.path("skills")
    names = sorted(d for d in os.listdir(skills_dir) if os.path.isfile(os.path.join(skills_dir, d, "SKILL.md")))
    for key in keys:
        rel_dir = LINK_TARGETS.get(key, key)
        tdir = os.path.join(p.root, rel_dir)
        os.makedirs(tdir, exist_ok=True)
        done, skipped, removed = [], [], []
        for n in names:
            dst = os.path.join(tdir, n)
            src_rel = os.path.relpath(os.path.join(skills_dir, n), tdir)
            if os.path.islink(dst):
                if os.readlink(dst) == src_rel:
                    done.append(n)
                    continue
                if _ours(dst, skills_dir):
                    os.unlink(dst)
                else:
                    skipped.append(n)
                    continue
            elif os.path.exists(dst):
                skipped.append(n)  # a real folder the user (or another tool) owns
                continue
            try:
                os.symlink(src_rel, dst, target_is_directory=True)
            except OSError:  # e.g. Windows without symlink rights: copy instead
                shutil.copytree(os.path.join(skills_dir, n), dst)
            done.append(n)
        for entry in os.listdir(tdir):
            path = os.path.join(tdir, entry)
            if os.path.islink(path) and _ours(path, skills_dir) and entry not in names:
                os.unlink(path)
                removed.append(entry)
        report[rel_dir] = {"linked": done, "skipped": skipped, "removed": removed}
    return report


def _ours(link: str, skills_dir: str) -> bool:
    target = os.path.normpath(os.path.join(os.path.dirname(link), os.readlink(link)))
    return os.path.dirname(target) == os.path.normpath(skills_dir)


# =============================================================================== matching

def skills_for(p: Project, task: Optional[Dict[str, Any]] = None, plan: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Skills relevant to a task: assigned on the task or plan, matching its type/role, or always-on."""
    out: Dict[str, Dict[str, Any]] = {}
    allsk = list_skills(p)
    by = {s["name"].lower(): s for s in allsk}
    by.update({(s["dir"] or "").lower(): s for s in allsk if s["dir"]})

    def add(name: str, why: str) -> None:
        s = by.get(name.lower())
        key = (s["name"] if s else name).lower()
        if key in out:
            return
        out[key] = {"name": s["name"] if s else name, "description": s["description"] if s else "",
                    "path": s["path"] if s else None, "why": why, "missing": s is None}

    for n in as_list((task or {}).get("skills")):
        add(n, "assigned to this task")
    for n in as_list((plan or {}).get("skills")):
        add(n, "assigned to the plan")
    if task:
        keys = {str(x).lower() for x in (task.get("task_type"), task.get("assigned_role")) if x}
        for s in allsk:
            hit = keys & {str(a).lower() for a in s["applies_to"]}
            if hit:
                add(s["name"], f"applies to {', '.join(sorted(hit))}")
    for s in allsk:
        if s["always"]:
            add(s["name"], "always on")
    return list(out.values())
