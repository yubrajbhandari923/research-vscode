"""`research upgrade`: bring an existing project up to the installed Research OS, losing nothing.

Four kinds of change, all previewable, all idempotent:
  * **schema**     — the existing versioned SQLite migrations (`schema.migrate`); the index is rebuildable anyway
  * **filesystem** — create missing managed directories
  * **templates**  — copy missing default skills/prompts/templates; never overwrite a customized file
  * **config**     — add missing default keys by deep merge; never remove or rewrite an existing value
  * **agents-md**  — only the Research OS-owned block (see `research.agentsmd`)
  * **bin**        — refresh the per-machine project-local CLI

Deliberately not a migration framework: the schema migrations already exist, and everything else here is a
handful of idempotent functions. `--dry-run` opens the project read-only and writes nothing at all.
"""
from __future__ import annotations

import os
import shutil
import time
from typing import Any, Dict, List, Optional, Tuple

from . import agentsmd, schema, yamlio
from .store import CONFIG_HEADER, DEFAULT_CONFIG, RESEARCH_DIR, SUBDIRS, TEMPLATES, Project, _atomic_write, \
    _deep_merge, find_root
from .util import NotInitialized, ResearchError

# Managed directories. `hpc` is created only when HPC is configured (the features are opt-in).
MANAGED_DIRS = list(SUBDIRS) + ["reports"]
TEMPLATE_DIRS = ("skills", "prompts", "templates")
BACKUP_MAX_BYTES = 4 * 1024 * 1024   # metadata files are cheap to back up; research histories are not copied


def _rdir(root: str) -> str:
    return os.path.join(root, RESEARCH_DIR)


def project_schema_version(root: str) -> Optional[int]:
    """The project's schema version, read without opening (and thus without migrating) the database."""
    import sqlite3
    db = os.path.join(_rdir(root), "research.db")
    if not os.path.exists(db):
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error:
        return None
    try:
        r = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(r[0]) if r else 0
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def _missing_dirs(root: str) -> List[str]:
    rd = _rdir(root)
    return [d for d in MANAGED_DIRS if not os.path.isdir(os.path.join(rd, d))]


def _missing_templates(root: str) -> List[Tuple[str, str]]:
    """(source, destination-relative) for default files this project does not have.

    A file that exists is left alone even if it differs: that difference is the user's customization.
    """
    out = []
    rd = _rdir(root)
    for sub in TEMPLATE_DIRS:
        src = TEMPLATES / sub
        if not src.is_dir():
            continue
        for path in sorted(src.rglob("*")):
            if path.is_file():
                rel = os.path.join(sub, str(path.relative_to(src)))
                if not os.path.exists(os.path.join(rd, rel)):
                    out.append((str(path), rel))
    return out


def _stale_templates(root: str) -> List[Tuple[str, str, str]]:
    """(source, destination-relative, recorded hash) for first-party files the project has NOT edited.

    A shipped skill carries guidance that changes between versions, and a project that never touched it
    should get the new text. Customization is detected by hash: the hash of the version we installed is
    recorded, so a file the user edited is left alone and reported instead of being overwritten.
    """
    from .util import sha256_bytes
    rd = _rdir(root)
    installed = _template_hashes(root)
    out = []
    for sub in TEMPLATE_DIRS:
        src = TEMPLATES / sub
        if not src.is_dir():
            continue
        for path in sorted(src.rglob("*")):
            if not path.is_file():
                continue
            rel = os.path.join(sub, str(path.relative_to(src)))
            dst = os.path.join(rd, rel)
            if not os.path.exists(dst):
                continue
            with open(dst, "rb") as f:
                current = sha256_bytes(f.read())
            with open(path, "rb") as f:
                shipped = sha256_bytes(f.read())
            if current == shipped:
                continue                      # already current
            known = installed.get(rel)
            if known is None or known != current:
                continue                      # never recorded, or edited by the user: leave it alone
            out.append((str(path), rel, shipped))
    return out


def _hash_file(root: str) -> str:
    return os.path.join(_rdir(root), "cache", "template-hashes.json")


def _template_hashes(root: str) -> Dict[str, str]:
    import json
    try:
        with open(_hash_file(root), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _record_template_hash(root: str, rel: str, digest: str) -> None:
    import json
    d = _template_hashes(root)
    d[rel] = digest
    os.makedirs(os.path.dirname(_hash_file(root)), exist_ok=True)
    _atomic_write(_hash_file(root), json.dumps(d, indent=2, sort_keys=True) + "\n")


def _legacy_flow_config(root: str) -> bool:
    """True when config.yaml uses flow style (`project: {name: x}`).

    Such a file cannot be edited surgically and breaks outright if a human adds an inline comment, so the
    upgrade offers to reformat it. Sparse configs need no "add the missing defaults" step at all: a key
    absent from the file simply resolves to its inherited value.
    """
    from . import configfile
    path = os.path.join(_rdir(root), "config.yaml")
    try:
        with open(path, encoding="utf-8") as f:
            return not configfile.editable(f.read())
    except OSError:
        return False


def _config_additions(raw: Dict[str, Any], defaults: Dict[str, Any], prefix: str = "") -> List[str]:
    """Dotted keys present in the defaults but absent from the project's own config.yaml."""
    out = []
    for k, v in sorted(defaults.items()):
        key = f"{prefix}{k}"
        if k not in raw:
            out.append(key)
        elif isinstance(v, dict) and isinstance(raw.get(k), dict):
            out += _config_additions(raw[k], v, key + ".")
    return out


def _raw_config(root: str) -> Dict[str, Any]:
    path = os.path.join(_rdir(root), "config.yaml")
    with open(path, encoding="utf-8") as f:
        raw = yamlio.load(f.read()) or {}
    if not isinstance(raw, dict):
        raise ResearchError(".research/config.yaml is not a mapping", hint="Fix it by hand, then re-run.")
    return raw


# Lines the generated .gitignore should contain. An upgrade appends only what is missing; it never
# rewrites or reorders a project's own .gitignore.
GITIGNORE_WANT = ["reports/", "hpc/bundles/", "hpc/incoming/"]


def _missing_gitignore_lines(root: str) -> List[str]:
    path = os.path.join(_rdir(root), ".gitignore")
    if not os.path.isfile(path):
        return []           # no file of ours to extend; `init` writes it for new projects
    try:
        with open(path, encoding="utf-8") as f:
            have = {ln.strip() for ln in f}
    except OSError:
        return []
    return [ln for ln in GITIGNORE_WANT if ln not in have and ln.rstrip("/") not in have]


def _bin_state(root: str) -> Dict[str, Any]:
    from . import localbin
    st = localbin.status(root)
    if not st["exists"]:
        return {"needs": True, "reason": "not installed"}
    from . import __version__
    stamp = st.get("stamp") or ""
    return {"needs": not stamp.startswith(__version__ + ":"),
            "reason": f"stamped {stamp.split(':')[0] or 'unknown'}, installed {__version__}"}


def plan(where: Optional[str] = None) -> Dict[str, Any]:
    """What an upgrade would change. Read-only: opens no database, writes nothing, logs nothing."""
    root = os.path.abspath(where or find_root() or ".")
    if not os.path.isfile(os.path.join(_rdir(root), "config.yaml")):
        raise NotInitialized(f"No research project at {root}", hint="Run `research init` to create one.")
    from . import __version__
    cur = project_schema_version(root)
    raw = _raw_config(root)

    if cur is not None and cur > schema.SCHEMA_VERSION:
        downgrade = (f"This project uses schema v{cur}; this Research OS only understands v{schema.SCHEMA_VERSION}. "
                     "Upgrade the research package / VS Code extension instead — an older tool must never "
                     "write to a newer project.")
    else:
        downgrade = None

    dirs = _missing_dirs(root)
    tmpl = _missing_templates(root)
    cfg_flow = _legacy_flow_config(root)
    try:
        am = agentsmd.state_for_root(root, project_name=raw.get("project", {}).get("name") or os.path.basename(root))
    except ResearchError as e:
        am = {"action": "refuse", "problem": str(e)}
    binst = _bin_state(root)

    steps: List[Dict[str, Any]] = []
    if cur is None:
        steps.append({"kind": "schema", "change": "build",
                      "detail": f"research.db is absent; it will be built from the text files at v{schema.SCHEMA_VERSION}"})
    elif cur < schema.SCHEMA_VERSION:
        steps.append({"kind": "schema", "change": "migrate",
                      "detail": f"schema v{cur} → v{schema.SCHEMA_VERSION} "
                                f"(migrations {', '.join(str(v) for v in sorted(schema.MIGRATIONS) if v > cur)})"})
    for d in dirs:
        steps.append({"kind": "filesystem", "change": "create", "path": f"{RESEARCH_DIR}/{d}/"})
    for _src, rel in tmpl:
        steps.append({"kind": "template", "change": "add", "path": f"{RESEARCH_DIR}/{rel}"})
    for _src, rel, _h in _stale_templates(root):
        steps.append({"kind": "template", "change": "refresh", "path": f"{RESEARCH_DIR}/{rel}",
                      "detail": "updated guidance from this Research OS version; your copy is unmodified "
                                "(an edited file is never overwritten)"})
    if cfg_flow:
        steps.append({"kind": "config", "change": "reformat", "path": f"{RESEARCH_DIR}/config.yaml",
                      "detail": "rewrite in block style, keeping only your overrides (inherited defaults "
                                "become a commented reference). Needed because the current flow style "
                                "breaks if you add an inline comment, and prevents comment-preserving edits"})
    if am["action"] == "refuse":
        steps.append({"kind": "agents_md", "change": "refuse", "path": "AGENTS.md", "detail": am["problem"]})
    elif am["action"] != "none":
        steps.append({"kind": "agents_md", "change": am["action"], "path": "AGENTS.md",
                      "detail": "the Research OS-managed block only; bytes outside it are untouched"})
    if binst["needs"]:
        steps.append({"kind": "bin", "change": "refresh", "path": f"{RESEARCH_DIR}/bin/",
                      "detail": binst["reason"] + " (per-machine, gitignored)"})
    gi = _missing_gitignore_lines(root)
    if gi:
        steps.append({"kind": "gitignore", "change": "append", "path": f"{RESEARCH_DIR}/.gitignore",
                      "keys": gi, "detail": "append " + ", ".join(gi)
                                            + " (generated output; your own lines are untouched)"})

    preserved = [f"{RESEARCH_DIR}/{d}/" for d in sorted(MANAGED_DIRS)
                 if os.path.isdir(os.path.join(_rdir(root), d))]
    return {
        "root": root, "tool_version": __version__,
        "project_schema": cur, "target_schema": schema.SCHEMA_VERSION,
        "downgrade_refused": downgrade,
        "steps": steps, "changes": len([s for s in steps if s["change"] != "refuse"]),
        "up_to_date": not steps,
        "preserved": preserved,
        "agents_md": {k: am.get(k) for k in ("action", "problem", "up_to_date")},
        "note": "No research records are removed or rewritten by an upgrade.",
    }


def _backup(root: str, rel_paths: List[str]) -> Optional[str]:
    """Copy the small metadata files an upgrade modifies. Research histories are never copied."""
    kept = []
    for rel in rel_paths:
        src = os.path.join(root, rel)
        if os.path.isfile(src) and os.path.getsize(src) <= BACKUP_MAX_BYTES:
            kept.append(rel)
    if not kept:
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(_rdir(root), "cache", f"upgrade-{stamp}")
    for rel in kept:
        dst = os.path.join(bdir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(root, rel), dst)
    return os.path.relpath(bdir, root)


def apply(where: Optional[str] = None) -> Dict[str, Any]:
    """Apply the plan. Validates preconditions first, backs up what it edits, and is safe to re-run."""
    pl = plan(where)
    root: str = pl["root"]
    if pl["downgrade_refused"]:
        raise ResearchError(pl["downgrade_refused"],
                            hint="Upgrade the research package / VS Code extension; nothing was changed.")
    refused = [s for s in pl["steps"] if s["change"] == "refuse"]
    if pl["up_to_date"]:
        return {**pl, "applied": [], "failed": [], "backup": None, "changed": False}

    edited = [s["path"] for s in pl["steps"]
              if s["kind"] in ("config", "agents_md", "gitignore") and s["change"] != "refuse"]
    backup = _backup(root, edited)
    applied: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []

    def step(s: Dict[str, Any], fn) -> None:
        try:
            fn()
            applied.append(s)
        except Exception as e:  # one failed step must not hide the others or half-finish silently
            failed.append({**s, "error": str(e)})

    rd = _rdir(root)
    for s in pl["steps"]:
        if s["change"] == "refuse":
            continue
        if s["kind"] == "filesystem":
            step(s, lambda s=s: os.makedirs(os.path.join(root, s["path"]), exist_ok=True))
        elif s["kind"] == "template":
            def copy(s=s):
                from .util import sha256_bytes
                rel = s["path"][len(RESEARCH_DIR) + 1:]
                src = TEMPLATES / rel
                dst = os.path.join(rd, rel)
                if s["change"] == "add" and os.path.exists(dst):
                    return                   # re-checked: another process may have created it
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(str(src), dst)
                with open(dst, "rb") as f:
                    _record_template_hash(root, rel, sha256_bytes(f.read()))
            step(s, copy)
        elif s["kind"] == "config":
            def reformat(s=s):
                from . import configfile
                path = os.path.join(rd, "config.yaml")
                prev = configfile.migrate_preview(path, DEFAULT_CONFIG)
                if not prev["safe"]:
                    raise ResearchError(
                        "refusing to reformat config.yaml: the effective settings would change "
                        f"(dropped {prev['dropped_keys']}, changed {prev['changed_values']})")
                _atomic_write(path, prev["new"])
            step(s, reformat)
        elif s["kind"] == "agents_md":
            step(s, lambda: agentsmd.apply_for_root(
                root, project_name=_raw_config(root).get("project", {}).get("name") or os.path.basename(root)))
        elif s["kind"] == "gitignore":
            def gitignore(s=s):
                path = os.path.join(rd, ".gitignore")
                missing = _missing_gitignore_lines(root)      # re-checked under the current state
                if not missing:
                    return
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                with open(path, "a", encoding="utf-8") as f:
                    f.write(("" if not text or text.endswith("\n") else "\n")
                            + "# generated reports and HPC staging (added by `research upgrade`)\n"
                            + "".join(ln + "\n" for ln in missing))
            step(s, gitignore)
        elif s["kind"] == "bin":
            def bin_(s=s):
                from . import localbin
                try:
                    localbin.install(root)
                except ResearchError as e:
                    # Running from .research/bin/research, which cannot replace itself. Everything else
                    # in the upgrade still applies; report this one step rather than failing the upgrade.
                    raise ResearchError(f"{e} (the rest of the upgrade was applied)", hint=e.hint)
            step(s, bin_)

    # Schema last: opening the Project runs the migrations, and by then the directories exist.
    schema_step = next((s for s in pl["steps"] if s["kind"] == "schema"), None)
    final_schema = pl["project_schema"]
    if schema_step:
        try:
            with Project(root, sync=False) as p:
                final_schema = schema.current_version(p.conn)
                p.event("project", None, "upgraded",
                        f"Upgraded to research {pl['tool_version']} (schema v{final_schema}); "
                        f"{len(applied) + 1} step(s) applied")
            applied.append(schema_step)
        except Exception as e:
            failed.append({**schema_step, "error": str(e)})
    else:
        try:
            with Project(root, sync=False) as p:
                final_schema = schema.current_version(p.conn)
                if applied:
                    p.event("project", None, "upgraded",
                            f"Upgraded to research {pl['tool_version']}; {len(applied)} step(s) applied")
        except Exception as e:
            failed.append({"kind": "verify", "change": "open", "error": str(e)})

    return {**pl, "project_schema": final_schema, "applied": applied, "failed": failed,
            "refused": refused, "backup": backup, "changed": bool(applied),
            "ok": not failed, "up_to_date": False}


def status(where: Optional[str] = None) -> Dict[str, Any]:
    """Version/compatibility summary without changing anything."""
    pl = plan(where)
    out = {k: pl[k] for k in ("root", "tool_version", "project_schema", "target_schema", "up_to_date",
                              "changes", "downgrade_refused", "agents_md")}
    out["pending"] = [{k: s.get(k) for k in ("kind", "change", "path", "detail")} for s in pl["steps"]]
    return out
