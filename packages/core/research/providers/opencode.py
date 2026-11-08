"""OpenCode adapter (opencode.ai, `opencode run --format json`). Design: docs/PHASE_CLEANUP_2_DESIGN.md D; verification: docs/PROVIDERS.md.

OpenCode 1 and 2 differ in config schema (`permission` object vs `permissions` rule array; v1 REJECTS the v2 key), so every config is
generated for the major version read from `opencode --version`. Headless runs never use --auto/--yolo: permissions are injected
inline (`OPENCODE_CONFIG_CONTENT`) and anything unmatched is `ask`, which `run` auto-rejects.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from typing import Any, Dict, List, Optional

from ..util import ResearchError, as_list
from .base import Adapter, merge_json, run, server_cmd

# Level pack. UNVERIFIED defaults (no account was available to run a model): anthropic/* ids follow the claude pack and `--variant high`
# is Anthropic's documented reasoning variant. Override with agents.profiles.opencode-<level>.model, e.g. `opencode/big-pickle`
# (`opencode models` lists what this machine can use).
LEVELS = {"fast": {"model": "anthropic/claude-haiku-5-5", "effort": None},
          "balanced": {"model": "anthropic/claude-sonnet-5-5", "effort": None},
          "deep": {"model": "anthropic/claude-opus-5-5", "effort": "high"}}

FINAL_REASONS = ("stop", "end_turn", "length")   # `step_finish.part.reason`; "tool-calls" means the model will continue
_REFUSED_ARGS = ("--auto", "--yolo", "--dangerously-skip-permissions")
_VERSION_CACHE: Dict[Any, Dict[str, Any]] = {}
_HELP_CACHE: Dict[Any, str] = {}


def version_info(cmd: str) -> Dict[str, Any]:
    """{"raw", "major", "minor", "patch", "line": "v1"|"v2"|None}; cached by (realpath, mtime). Unparseable: major None."""
    exe = shutil.which(cmd) or cmd
    try:
        key = (os.path.realpath(exe), os.stat(exe).st_mtime)
    except OSError:
        key = (exe, 0)
    if key not in _VERSION_CACHE:
        r = run([exe, "--version"], timeout=15)
        raw = ((r.stdout or r.stderr) if r and r.returncode == 0 else "").strip()
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", raw)
        major = int(m.group(1)) if m else None
        _VERSION_CACHE[key] = {"raw": raw, "major": major, "minor": int(m.group(2)) if m else None, "patch": int(m.group(3)) if m else None,
                               "line": None if major is None else "v2" if major >= 2 else "v1"}
    return _VERSION_CACHE[key]


def _help(cmd: str, *sub: str) -> str:
    exe = shutil.which(cmd) or cmd
    key = (os.path.realpath(exe), sub)
    if key not in _HELP_CACHE:
        r = run([exe, *sub, "--help"], timeout=20)
        _HELP_CACHE[key] = (r.stdout + r.stderr) if r else ""
    return _HELP_CACHE[key]


def build(prof, model, effort, prompt, cwd, dirs, headless) -> List[str]:
    args = [str(a) for a in as_list(prof.get("args"))]
    bad = [a for a in args if a in _REFUSED_ARGS]
    if bad:
        raise ResearchError(f"Profile args {bad} would disable opencode's permission checks",
                            hint="Research OS never auto-approves for an agent. Remove them; headless runs allow edits and the research CLI only.")
    if model and "/" not in str(model):
        raise ResearchError(f"opencode needs provider/model, e.g. anthropic/claude-sonnet-5-5 (got {model!r})")
    argv = [prof.get("command") or "opencode", *args]
    if not headless:
        if model:
            argv += ["-m", str(model)]
        if "--prompt" in _help(argv[0]):   # verified on opencode 1.19.5; an unprobeable CLI just opens the TUI
            argv += ["--prompt", prompt]
        return argv
    argv += ["run", "--format", "json"]
    if model:
        argv += ["-m", str(model)]
    if effort:
        argv += ["--variant", str(effort)]
    argv.append(prompt)
    # `-f` is an array option: it swallows a following positional, so it goes AFTER the prompt (verified on 1.19.5). The brief lives
    # in the ledger, outside the worktree, so attaching it avoids an `external_directory` permission the headless run would refuse.
    m = re.search(r"brief at (\S+?\.md)\b", prompt)
    if m and os.path.isabs(m.group(1)):
        argv += ["-f", m.group(1)]
    return argv


# ------------------------------------------------------------------------------------------------ config injection
def config_content(major: Optional[int], headless: bool, env: Dict[str, str]) -> Dict[str, Any]:
    """The inline config for one run: the research MCP server always; permissions only when headless (interactive keeps the user's)."""
    cfg: Dict[str, Any] = {"mcp": {"research": {
        "type": "local", "command": ["research", "mcp"], "enabled": True,
        "environment": {k: env[k] for k in ("RESEARCH_AGENT", "RESEARCH_AGENT_ROLE", "RESEARCH_ROOT", "RESEARCH_EXECUTION", "PATH") if env.get(k)}}}}
    if not headless:
        return cfg
    shell = ["research *", ".research/bin/research *", "*/.research/bin/research *"]
    if major is not None and major >= 2:
        # No `*` deny: in v2 a deny wins within an operation, so `shell * deny` would also block `research ...`. Unmatched = ask = rejected.
        cfg["permissions"] = ([{"action": "edit", "resource": "*", "effect": "allow"}]
                              + [{"action": "shell", "resource": s, "effect": "allow"} for s in shell]
                              + [{"action": "mcp", "resource": "research_*", "effect": "allow"}])
    else:
        cfg["permission"] = {"edit": "allow", "bash": {"*": "ask", **{s: "allow" for s in shell}}, "webfetch": "ask"}
    return cfg


def extra_env(prof: Dict[str, Any], env: Dict[str, str], headless: bool) -> Dict[str, str]:
    vi = version_info(prof.get("command") or "opencode")
    if headless and vi["major"] is None:   # better to refuse than to guess a config schema (v1 rejects v2 keys and vice versa)
        raise ResearchError("could not read the opencode version (`opencode --version`), so headless dispatch is refused",
                            hint="Check that `opencode --version` prints a version like 1.19.5, or set agents.profiles.<name>.command.")
    return {"OPENCODE_CONFIG_CONTENT": json.dumps(config_content(vi["major"], headless, env))}


# ------------------------------------------------------------------------------------------------ session, resume, models
def resume_probe(command: str) -> bool:
    return "--session" in _help(command, "run")


def resume_argv(argv: List[str], session_id: str) -> Optional[List[str]]:
    """`opencode run --session ID …`: right after `run`, so --format json, model, variant and the attachment are kept."""
    if "run" not in argv:
        return None
    i = argv.index("run")
    return argv[:i + 1] + ["--session", session_id] + argv[i + 1:]


def session_id_of(out: str) -> Optional[str]:
    for line in (out or "").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("sessionID"):
            return str(d["sessionID"])
    return None


def is_final_event(line: str) -> bool:
    try:
        d = json.loads(line)
    except ValueError:
        return False
    return isinstance(d, dict) and d.get("type") == "step_finish" and (d.get("part") or {}).get("reason") in FINAL_REASONS


def list_models(command: str) -> Optional[List[Dict[str, str]]]:
    r = run([command, "models"], timeout=30) if shutil.which(command) else None
    rows = [l.strip() for l in r.stdout.splitlines() if "/" in l] if r and r.returncode == 0 else []
    return [{"id": m, "label": m} for m in rows] or None


# ------------------------------------------------------------------------------------------------ MCP
def _strip_jsonc(text: str) -> str:
    """Drop // and /* */ comments outside strings. shortcut: trailing commas are not handled, upgrade if a real config needs it."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1]); i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True; out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _entry(root: str) -> Dict[str, Any]:
    return {"type": "local", "command": server_cmd(root), "enabled": True, "environment": {"RESEARCH_AGENT": "opencode"}}


def mcp_install(root: str, cmd: Optional[str] = None) -> Dict[str, Any]:
    """Merge `mcp.research` into opencode.json. opencode.jsonc cannot be round-tripped by the stdlib, so it is left untouched and the snippet returned."""
    if os.path.exists(os.path.join(root, "opencode.jsonc")) and not os.path.exists(os.path.join(root, "opencode.json")):
        return {"provider": "opencode", "where": "opencode.jsonc", "scope": "project", "manual": json.dumps({"mcp": {"research": _entry(root)}}, indent=2)}
    merge_json(os.path.join(root, "opencode.json"), "mcp", "research", _entry(root))
    return {"provider": "opencode", "where": "opencode.json", "scope": "project"}


def mcp_registered(root: str) -> Optional[bool]:
    for rel in ("opencode.json", "opencode.jsonc"):
        try:
            with open(os.path.join(root, rel), encoding="utf-8") as f:
                return "research" in ((json.loads(_strip_jsonc(f.read())).get("mcp")) or {})
        except OSError:
            continue
        except ValueError:
            return None
    return False


# ------------------------------------------------------------------------------------------------ doctor
def checks(root: str, command: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    vi = version_info(command)
    if vi["major"] is None:
        rows.append({"id": "agents.opencode.version", "level": "warn", "title": "Could not read the opencode version",
                     "detail": "`opencode --version` printed nothing parseable; headless dispatch is refused until it does."})
    elif vi["major"] < 2:
        rows.append({"id": "agents.opencode.eol", "level": "warn", "title": f"OpenCode {vi['raw']} (v1) is end-of-life upstream",
                     "detail": "Still supported here; v2 uses a different permission schema.", "fix": {"text": "Run `opencode upgrade --major`."}})
    if not os.path.isfile(os.path.join(root, "AGENTS.md")):
        rows.append({"id": "agents.opencode.agents_md", "level": "warn", "title": "AGENTS.md is missing (opencode's instruction file)",
                     "fix": {"command": "research agent instructions"}})
    if os.environ.get("OPENCODE_DISABLE_CLAUDE_CODE_SKILLS"):
        rows.append({"id": "agents.opencode.skills_env", "level": "warn", "title": "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS is set",
                     "detail": ".claude/skills are invisible to opencode.", "fix": {"command": "research skills link --target agents"}})
    bad = []
    for d in (".claude/skills", ".agents/skills"):
        base = os.path.join(root, d)
        bad += [n for n in (sorted(os.listdir(base)) if os.path.isdir(base) else []) if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", n)]
    if bad:
        rows.append({"id": "agents.opencode.skill_names", "level": "warn", "title": "Skill names opencode skips silently",
                     "detail": ", ".join(bad[:8]) + " (names must match ^[a-z0-9]+(-[a-z0-9]+)*$)"})
    rows.append({"id": "agents.opencode.attribution", "level": "info", "title": "Interactive opencode sessions are attributed to you unless started by Research OS",
                 "detail": "opencode passes no env var to its shell; dispatched runs and MCP calls carry RESEARCH_AGENT. PIN-gated actions stay human-only either way."})
    return rows


ADAPTER = Adapter(
    name="opencode", label="OpenCode", build=build, levels=LEVELS,
    blurb="OpenCode (opencode.ai). Many model providers behind one CLI; headless `run --format json`, variants as effort, reports tokens and cost.",
    caps={"interactive": True, "headless": True, "model": True, "effort": True, "structured_output": "jsonl",
          "usage": True, "resume": True, "mcp": "opencode.json", "instructions": "AGENTS.md", "watchdog": True},
    profile={"provider": "opencode", "command": "opencode", "args": [], "model": None, "effort": None},
    usage_parser="opencode_jsonl", resume_probe=resume_probe, resume_argv=resume_argv, session_id=session_id_of, list_models=list_models,
    mcp_install=mcp_install, mcp_registered=mcp_registered, version_info=version_info, final_event=is_final_event, env=extra_env, checks=checks)
