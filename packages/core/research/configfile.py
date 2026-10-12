"""Layered, sparse, comment-preserving configuration.

    DEFAULTS (code)  <-  machine file (optional)  <-  .research/config.yaml  <-  per-command flags

What a project's `config.yaml` holds is only what it *changes*; everything else is inherited. The effective
dict every consumer reads is unchanged, so `p.config[...]` keeps working exactly as before.

Two properties matter more than brevity:

**Edits are surgical.** Writing one key rewrites that key's line (or inserts it under its block) and leaves
every other byte alone — comments, ordering, blank lines, line endings. Research OS owns the values it was
asked to change and nothing else. A full re-serialization happens only when the user explicitly migrates.

**Unknown keys are never discarded.** A key this version does not recognize is kept verbatim in the file and
reported by `check()`, because it may belong to a newer Research OS or to the user.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

from . import yamlio
from .util import ResearchError

MACHINE_ENV = "RESEARCH_CONFIG"          # explicit override: a file path
HOME_ENV = "XDG_CONFIG_HOME"
MACHINE_REL = os.path.join("research-os", "config.yaml")

HEADER = """# Research project configuration (human-editable).
# Only settings that differ from the inherited defaults are listed here.
#   research config show --effective    every value actually in force
#   research config show --sources      where each value comes from
#   research config check               validate this file
# Comments and ordering are preserved: Research OS rewrites only the keys it is asked to change.
"""

REFERENCE_BANNER = "# ── Commonly adjusted settings (uncomment a line to override it) ──"
REFERENCE_FOOTER = ("# Everything else keeps its default. `research config show --effective` prints every\n"
                    "# value in force, including the agent profiles and orchestration tiers not shown here.")

# The reference block exists so a reader can discover what is tunable without opening the docs. It is
# deliberately a shortlist: dumping all 75 defaults (agent profiles, orchestration tiers, bundle excludes)
# buries the handful of settings people actually change under a hundred commented lines.
REFERENCE_KEYS = (
    "agent_policy",
    "backends.slurm.partition", "backends.slurm.account", "backends.slurm.time",
    "backends.slurm.cpus", "backends.slurm.mem", "backends.slurm.gpus", "backends.slurm.setup",
    "context.max_chars",
    "coordination.stale_task_hours",
    "resume.stale_checkpoint_days",
    "verification.require_review_for_supported", "verification.independent_reviewer",
    "verification.checks_gate_task_completion", "verification.check_timeout_seconds",
    "guide.enabled", "guide.path",
    "git.commit_checkpoints",
    "artifacts.hash_max_bytes",
    "hpc.default_cluster",
    "orchestration.enabled",
)

# Keys that must stay in a project's file even when they equal the default, because they are identity
# rather than configuration: losing them would silently rename or re-date the project.
ALWAYS_KEEP = ("project.name", "project.created_at", "project.goal", "project.description",
               "project.git_repo")


# =============================================================================== paths

def machine_path() -> Optional[str]:
    """The optional machine-level config file. $RESEARCH_CONFIG wins; else the XDG location."""
    explicit = os.environ.get(MACHINE_ENV)
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    base = os.environ.get(HOME_ENV) or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, MACHINE_REL)


def machine_config() -> Tuple[Dict[str, Any], Optional[str]]:
    """(settings, path) from the machine file, or ({}, None) when there is none.

    A broken machine file must not make every project unusable, so it is reported, not raised.
    """
    path = machine_path()
    if not path or not os.path.isfile(path):
        return {}, None
    try:
        with open(path, encoding="utf-8") as f:
            raw = yamlio.load(f.read()) or {}
    except Exception as e:
        raise ResearchError(f"the machine config at {path} could not be parsed: {e}",
                            hint=f"Fix the YAML, or unset ${MACHINE_ENV} / move the file aside. "
                                 "Project configuration is unaffected.")
    if not isinstance(raw, dict):
        raise ResearchError(f"the machine config at {path} is not a mapping")
    raw.pop("project", None)   # project identity is never machine-wide
    return raw, path


# =============================================================================== flatten / merge

def flatten(d: Any, prefix: str = "") -> Iterator[Tuple[str, Any]]:
    """Dotted leaf paths. An empty dict is a leaf, so `clusters: {}` is a value rather than a branch."""
    if isinstance(d, dict) and d:
        for k, v in d.items():
            yield from flatten(v, f"{prefix}{k}.")
    else:
        yield prefix.rstrip("."), d


def _flat(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in flatten(d) if k}


def unflatten(items: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for dotted, v in items.items():
        cur = out
        parts = dotted.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
            if not isinstance(cur, dict):       # a scalar already occupies this path
                raise ResearchError(f"conflicting configuration keys around {dotted!r}")
        cur[parts[-1]] = v
    return out


def _get(d: Dict[str, Any], dotted: str) -> Tuple[bool, Any]:
    cur: Any = d
    for p in dotted.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return False, None
        cur = cur[p]
    return True, cur


# =============================================================================== layers

def layers(project_raw: Dict[str, Any], defaults: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The ordered layers, lowest precedence first. Each is {name, path, settings}."""
    machine, mpath = machine_config()
    return [
        {"name": "default", "path": None, "settings": defaults},
        {"name": "machine", "path": mpath, "settings": machine},
        {"name": "project", "path": ".research/config.yaml", "settings": project_raw},
    ]


def effective(project_raw: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    """The full dict every consumer reads. Identical in shape to the old `_deep_merge(DEFAULTS, raw)`."""
    from .store import _deep_merge
    out = defaults
    for layer in layers(project_raw, defaults)[1:]:
        if layer["settings"]:
            out = _deep_merge(out, layer["settings"])
    return _deep_merge(out, {}) if out is defaults else out


def sources(project_raw: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Per dotted key: the value in force and which layer supplied it (highest that defines it)."""
    ls = layers(project_raw, defaults)
    out: Dict[str, Dict[str, Any]] = {}
    for dotted, value in sorted(_flat(effective(project_raw, defaults)).items()):
        origin, overridden = "default", []
        for layer in ls:
            present, v = _get(layer["settings"], dotted)
            if present:
                if origin != "default" or layer["name"] != "default":
                    overridden.append(layer["name"])
                origin = layer["name"]
        out[dotted] = {"value": value, "source": origin,
                       "defined_in": [x for x in overridden if x != origin] or []}
    return out


def overrides(project_raw: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    """The project keys worth keeping in the file: those that change an inherited value, plus identity.

    Unknown keys always count as overrides — this version not understanding a key is no reason to drop it.
    """
    from .store import _deep_merge
    inherited = defaults
    machine, _ = machine_config()
    if machine:
        inherited = _deep_merge(defaults, machine)
    inherited_flat = _flat(inherited)
    keep: Dict[str, Any] = {}
    for dotted, v in _flat(project_raw).items():
        if dotted not in inherited_flat or inherited_flat[dotted] != v or dotted in ALWAYS_KEEP:
            keep[dotted] = v
    return keep


# =============================================================================== validation

def check(project_raw: Dict[str, Any], defaults: Dict[str, Any], text: Optional[str] = None) -> Dict[str, Any]:
    """Validate a project config. Reports problems; never rewrites anything."""
    out: Dict[str, Any] = {"ok": True, "errors": [], "warnings": [], "unknown": [], "redundant": []}
    from .store import _deep_merge
    machine, mpath = machine_config()
    # Compare against what is actually inherited, not the built-in defaults: a machine-level file is a
    # legitimate source of values, and treating its settings as "unknown" or "wrong" would be nonsense.
    inherited = _deep_merge(defaults, machine) if machine else defaults
    inherited_flat = _flat(inherited)
    out["machine_config"] = mpath

    for dotted, v in sorted(_flat(project_raw).items()):
        if dotted not in inherited_flat:
            # Cluster profiles are user-named, so their sub-keys are legitimately absent from the defaults.
            if re.match(r"^hpc\.clusters\.[^.]+\.", dotted):
                continue
            out["unknown"].append({"key": dotted, "value": v})
        elif inherited_flat[dotted] == v and dotted not in ALWAYS_KEEP:
            out["redundant"].append(dotted)

    for dotted, v in sorted(_flat(project_raw).items()):
        want = inherited_flat.get(dotted)
        if want is None or v is None:
            continue
        if isinstance(want, bool) != isinstance(v, bool) or (
                isinstance(want, (int, float)) and not isinstance(want, bool)
                and isinstance(v, str)):
            out["errors"].append({"key": dotted, "problem": f"expected {type(want).__name__}, got "
                                                            f"{type(v).__name__} ({v!r})"})

    # HPC profiles: structural checks only, and against the *effective* profile — a project that
    # inherits its cluster from the machine layer legitimately has almost nothing of its own here.
    # Paths and SSH auth are the user's and are never modified.
    eff_hpc = (effective(project_raw, defaults).get("hpc") or {})
    for name, cluster in sorted((eff_hpc.get("clusters") or {}).items()):
        if not isinstance(cluster, dict):
            out["errors"].append({"key": f"hpc.clusters.{name}", "problem": "must be a mapping"})
            continue
        if (cluster.get("transport") or "ssh") == "ssh" and not cluster.get("ssh_host"):
            out["errors"].append({"key": f"hpc.clusters.{name}.ssh_host",
                                  "problem": "an ssh cluster needs an ssh_host (an alias from ~/.ssh/config)"})
        if not ((cluster.get("paths") or {}).get("workspace_root")):
            out["errors"].append({"key": f"hpc.clusters.{name}.paths.workspace_root",
                                  "problem": "no approved workspace_root configured"})
    d = eff_hpc.get("default_cluster")
    known = set(eff_hpc.get("clusters") or {})
    if d and d not in known:
        out["errors"].append({"key": "hpc.default_cluster",
                              "problem": f"{d!r} is not a configured cluster ({', '.join(sorted(known)) or 'none'})"})

    if text is not None:
        out["edited_comments"] = edited_reference_lines(text, inherited)
        for ec in out["edited_comments"]:
            out["warnings"].append(
                f"line {ec['line']}: `{ec['key']}: {ec['value']}` is commented out, so it has no effect "
                f"(the value in force is {ec['default']!r}). Remove the leading `# ` to apply it.")
        out["duplicate_keys"] = duplicate_keys(text)
        for dk in out["duplicate_keys"]:
            out["errors"].append({"key": dk["key"],
                                  "problem": f"defined {dk['count']} times (lines {', '.join(map(str, dk['lines']))}); "
                                             "YAML silently keeps the last one"})
    out["ok"] = not out["errors"]
    return out


_KEY_RE = re.compile(r"^(?P<indent> *)(?P<key>[A-Za-z_][\w.-]*)\s*:(?P<rest>.*)$")


def edited_reference_lines(text: str, defaults: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Commented reference lines whose value no longer matches the default.

    The reference block is a convenience, but it invites a real mistake: a reader edits
    `#   max_runs_without_synthesis: 2`, saves, and nothing happens because the line is still a comment.
    Spotting a changed value inside a comment is the only way to tell them.
    """
    flat = _flat(defaults)
    by_leaf: Dict[str, List[Tuple[str, Any]]] = {}
    for dotted, v in flat.items():
        by_leaf.setdefault(dotted.split(".")[-1], []).append((dotted, v))
    out = []
    in_reference = False
    for n, line in enumerate(text.split("\n"), 1):
        if REFERENCE_BANNER.lstrip("# ").split("(")[0].strip() in line:
            in_reference = True
            continue
        if not in_reference or not line.lstrip().startswith("#"):
            continue
        m = _KEY_RE.match(line.lstrip()[1:].lstrip() if line.lstrip().startswith("#") else line)
        if not m:
            continue
        candidates = by_leaf.get(m.group("key"), [])
        if len(candidates) != 1:
            continue                      # ambiguous leaf name; not worth a guess
        dotted, default = candidates[0]
        raw = m.group("rest").strip()
        # strip the "← set for every project…" annotation the reference block appends
        mc = re.search(r"\s+#.*$", raw)
        if mc and not _looks_quoted(raw[:mc.start()]):
            raw = raw[:mc.start()].strip()
        if not raw:
            continue
        try:
            value = yamlio.load(f"k: {raw}\n")["k"]
        except Exception:
            continue
        if value != default:
            out.append({"line": n, "key": dotted, "value": raw, "default": default})
    return out


def duplicate_keys(text: str) -> List[Dict[str, Any]]:
    """Keys defined twice at the same path. YAML keeps the last silently, which hides a real mistake."""
    seen: Dict[str, List[int]] = {}
    stack: List[Tuple[int, str]] = []
    for n, line in enumerate(text.split("\n"), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _KEY_RE.match(line)
        if not m:
            continue
        indent = len(m.group("indent"))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = ".".join([k for _i, k in stack] + [m.group("key")])
        seen.setdefault(path, []).append(n)
        if not m.group("rest").strip():
            stack.append((indent, m.group("key")))
    return [{"key": k, "count": len(v), "lines": v} for k, v in sorted(seen.items()) if len(v) > 1]


# =============================================================================== surgical editing

def _render_value(v: Any) -> str:
    """One-line YAML for an inline value.

    Dumping a bare scalar yields a whole YAML document ("x\n...\n"), so the document-end marker has to go;
    wrapping the value in a throwaway mapping is the reliable way to get just the scalar's own spelling.
    """
    text = yamlio.dump({"_": v}, block=False).rstrip("\n")
    if text.startswith("{_:") and text.endswith("}"):
        return text[3:-1].strip()
    if text.startswith("_:"):
        return text[2:].strip()
    return text


def editable(text: str) -> bool:
    """Whether surgical editing is safe on this text.

    The line-anchored editor understands block style only. A legacy file written in flow style
    (`project: {name: x, goal: y}`) has no line to rewrite for `project.goal`, so editing it in place
    would corrupt it: such a file is re-serialized wholesale instead. `research config migrate` converts
    a project to block style once, after which every later edit is surgical.
    """
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _KEY_RE.match(line)
        if m and m.group("rest").strip().startswith("{") and m.group("rest").strip() != "{}":
            return False
    return True


def set_keys(text: str, updates: Dict[str, Any]) -> str:
    """Apply dotted-key updates to YAML text, touching only the lines that carry those keys.

    An existing key is rewritten in place, preserving its indentation and any trailing comment. A new key
    is inserted at the end of its parent block, creating parent blocks when needed. Everything else — other
    keys, blank lines, comments, line endings — is left byte-identical.
    """
    for dotted, value in sorted(updates.items()):
        text = _set_one(text, dotted, value)
    return text


def _find_key(lines: List[str], dotted: str) -> Tuple[Optional[int], int]:
    """(line index of the key, its indent) for a dotted path, searching only live (uncommented) keys."""
    parts = dotted.split(".")
    start, end, indent = 0, len(lines), 0
    for depth, part in enumerate(parts):
        found = None
        for i in range(start, end):
            line = lines[i]
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            m = _KEY_RE.match(line)
            if not m:
                continue
            ind = len(m.group("indent"))
            if ind < indent:
                break                      # left the parent block
            if ind == indent and m.group("key") == part:
                found = i
                break
        if found is None:
            return None, indent
        if depth == len(parts) - 1:
            return found, indent
        # descend: the child block is everything more-indented below this line
        start = found + 1
        child_indent = None
        j = start
        while j < len(lines):
            line = lines[j]
            if line.strip() and not line.lstrip().startswith("#"):
                m2 = _KEY_RE.match(line) or re.match(r"^(?P<indent> *)-", line)
                ind2 = len(m2.group("indent")) if m2 else len(line) - len(line.lstrip())
                if ind2 <= indent:
                    break
                if child_indent is None:
                    child_indent = ind2
            j += 1
        end = j
        indent = child_indent if child_indent is not None else indent + 2
    return None, indent


def _set_one(text: str, dotted: str, value: Any) -> str:
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    idx, indent = _find_key(lines, dotted)
    leaf = dotted.split(".")[-1]

    if idx is not None:
        m = _KEY_RE.match(lines[idx])
        assert m
        rest = m.group("rest")
        comment = ""
        mc = re.search(r"(\s+#.*)$", rest)
        if mc and not _looks_quoted(rest[:mc.start()]):
            comment = mc.group(1)
        if isinstance(value, dict) and value:
            # A nested block: replace the whole subtree rather than inline it, so formatting stays block-style.
            block = yamlio.dump({leaf: value}, block=True).rstrip("\n").split("\n")
            block = [(" " * indent) + b for b in block]
            end = _block_end(lines, idx, indent)
            lines[idx:end] = block
        else:
            lines[idx] = f"{' ' * indent}{leaf}: {_render_value(value)}{comment}"
        return newline.join(lines)

    # Not present: insert under the deepest existing parent, creating the rest.
    parts = dotted.split(".")
    for depth in range(len(parts) - 1, 0, -1):
        parent = ".".join(parts[:depth])
        pidx, pindent = _find_key(lines, parent)
        if pidx is None:
            continue
        child_indent = pindent + 2
        end = _block_end(lines, pidx, pindent)
        new = unflatten({".".join(parts[depth:]): value})
        block = [(" " * child_indent) + b
                 for b in yamlio.dump(new, block=True).rstrip("\n").split("\n")]
        # an empty parent (`key: {}` or `key:`) must become a real block first
        pm = _KEY_RE.match(lines[pidx])
        if pm and pm.group("rest").strip() in ("{}", ""):
            lines[pidx] = f"{' ' * pindent}{pm.group('key')}:"
        while end > pidx + 1 and not lines[end - 1].strip():
            end -= 1
        lines[end:end] = block
        return newline.join(lines)

    # No parent at all: append a fresh top-level block.
    block = yamlio.dump(unflatten({dotted: value}), block=True).rstrip("\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    lines += block + [""]
    return newline.join(lines)


def _looks_quoted(s: str) -> bool:
    """True when a '#' would fall inside a quoted scalar (so it is not a comment)."""
    t = s.strip()
    return (t.startswith("'") and not t.endswith("'")) or (t.startswith('"') and not t.endswith('"'))


def _block_end(lines: List[str], idx: int, indent: int) -> int:
    """Index just past the block that `lines[idx]` introduces."""
    j = idx + 1
    while j < len(lines):
        line = lines[j]
        if line.strip() and not line.lstrip().startswith("#"):
            ind = len(line) - len(line.lstrip())
            if ind <= indent:
                break
        j += 1
    return j


# =============================================================================== rendering a fresh file

def render(settings: Dict[str, Any], defaults: Dict[str, Any], *, reference: bool = True) -> str:
    """A complete sparse config file: the header, the given settings, then commented inherited defaults."""
    body = yamlio.dump(settings, block=True) if settings else ""
    out = HEADER + "\n" + body
    if reference:
        rest = _reference_block(settings, defaults)
        if rest:
            out = out.rstrip("\n") + "\n\n" + rest
    return out.rstrip("\n") + "\n"


def _reference_block(settings: Dict[str, Any], defaults: Dict[str, Any]) -> str:
    """The commonly adjusted settings this project has not overridden, commented out to stay discoverable.

    Shows the value actually **in force**, not the built-in default: when a machine-level file supplies a
    value, printing the built-in one would invite the reader to uncomment a line that silently undoes it.
    """
    from .store import _deep_merge
    machine, _ = machine_config()
    inherited = _deep_merge(defaults, machine) if machine else defaults
    live = set(_flat(settings))
    flat_inherited = _flat(inherited)
    from_machine = set(_flat(machine)) if machine else set()
    remaining, machine_keys = {}, set()
    for want in REFERENCE_KEYS:
        for k, v in flat_inherited.items():
            if (k == want or k.startswith(want + ".")) and k not in live:
                remaining[k] = v
                if k in from_machine:
                    machine_keys.add(k)
    if not remaining:
        return ""
    lines = [REFERENCE_BANNER]
    # Annotate the lines a machine-level file supplies, so it is obvious they are not built-in defaults.
    path_of: Dict[str, str] = {}
    for dotted in machine_keys:
        path_of[dotted.split(".")[-1]] = dotted
    for line in yamlio.dump(unflatten(remaining), block=True).rstrip("\n").split("\n"):
        if not line.strip():
            lines.append("#")
            continue
        m = _KEY_RE.match(line)
        note = ""
        if m and m.group("rest").strip() and m.group("key") in path_of:
            note = "   # ← set for every project in your machine config"
        lines.append("# " + line + note)
    footer = REFERENCE_FOOTER
    if machine_keys:
        _, mpath = machine_config()
        footer += (f"\n# Values marked ← come from {mpath}\n"
                   "# Uncommenting one here overrides it for this project only.")
    return "\n".join(lines) + "\n" + footer + "\n"


def changed_against(project_raw: Dict[str, Any], desired: Dict[str, Any],
                    defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Dotted keys to write so that `desired` becomes the effective config.

    Supports the in-place style (`p.config["x"]["y"] = z; p.save_config()`): the desired effective dict is
    compared against what the layers already provide, so only genuine changes are written. A key the
    project file already sets is rewritten whenever its value moved, even back to a default — removing a
    line is the user's business, not ours.
    """
    from .store import _deep_merge
    machine, _ = machine_config()
    inherited = _deep_merge(defaults, machine) if machine else defaults
    inherited_flat = _flat(inherited)
    own_flat = _flat(project_raw)
    out: Dict[str, Any] = {}
    for dotted, v in _flat(desired).items():
        if dotted in own_flat:
            if own_flat[dotted] != v:
                out[dotted] = v
        elif dotted not in inherited_flat or inherited_flat[dotted] != v:
            out[dotted] = v
    return out


def sparse(project_raw: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    """The settings a sparse file should contain for this project."""
    return unflatten(overrides(project_raw, defaults))


# =============================================================================== migration

def migrate_preview(path: str, defaults: Dict[str, Any]) -> Dict[str, Any]:
    """What `config migrate` would do to one file: the new text, a diff, and a safety verdict."""
    import difflib
    with open(path, encoding="utf-8", newline="") as f:
        old = f.read()
    raw = yamlio.load(old.replace("\r\n", "\n")) or {}
    if not isinstance(raw, dict):
        raise ResearchError(f"{path} is not a mapping")
    new = render(sparse(raw, defaults), defaults)
    before = effective(raw, defaults)
    after = effective(yamlio.load(new) or {}, defaults)
    out = {
        "path": path, "old": old, "new": new, "changed": old != new,
        "lines_before": old.count("\n"), "lines_after": new.count("\n"),
        "effective_identical": before == after,
        "dropped_keys": sorted(set(_flat(before)) - set(_flat(after))),
        "changed_values": sorted(k for k, v in _flat(before).items()
                                 if k in _flat(after) and _flat(after)[k] != v),
        "comments_before": sum(1 for l in old.split("\n") if l.strip().startswith("#")),
        "diff": "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                             fromfile="a/config.yaml", tofile="b/config.yaml", n=2)),
    }
    out["safe"] = bool(out["effective_identical"] and not out["dropped_keys"] and not out["changed_values"])
    return out
