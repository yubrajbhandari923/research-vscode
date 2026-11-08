"""Provider adapters: the only place that knows each agent CLI's flags.

One module per provider (`claude.py`, `codex.py`, `agy.py`, `custom.py`), each exporting an `ADAPTER` record (`base.Adapter`). This
file holds the ONE registry (`ADAPTERS`, `KNOWN`, `LABELS`) and the provider-neutral operations built on it. An adapter turns
(profile, model, effort, prompt, cwd, extra dirs) into an argv list (never a shell string); `caps` states honestly what each tool
supports so callers never assume parity; `levels` is its level pack (fast/balanced/deep -> model + effort).

Add a provider: write `providers/<name>.py` with an `ADAPTER`, add it to `_MODULES` below. Nothing else needs an edit: default
profiles (`<name>`, `<name>-fast|balanced|deep`), the provider table, the fallback order and the setup wizard all derive from here.
Profiles select an adapter with `provider: <name>` (default: the command's basename if registered, else `custom`, which uses the
profile's args/model_flag/prompt_flag/headless_args verbatim).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from ..util import ResearchError
from . import agy, claude, codex, custom
from .base import LEVELS, Adapter, run as _run, server_cmd as _server_cmd

_MODULES = (claude, codex, agy, custom)           # `custom` last: it is the fallback, never a "known" provider
ADAPTERS: Dict[str, Adapter] = {m.ADAPTER.name: m.ADAPTER for m in _MODULES}
KNOWN: Tuple[str, ...] = tuple(n for n in ADAPTERS if n != "custom")   # providers with a real CLI; the order is the default fallback order
LABELS: Dict[str, str] = {n: a.label for n, a in ADAPTERS.items()}
CAPABILITIES: Dict[str, Dict[str, Any]] = {n: a.caps for n, a in ADAPTERS.items()}   # read-only view of the capability table
# opencode slot (WP13): add `opencode` to _MODULES; its level pack lives in providers/opencode.py.


def level_profile_name(provider: str, level: str) -> str:
    return f"{provider}-{level}"


def default_profiles() -> Dict[str, Dict[str, Any]]:
    """agents.profiles defaults, generated: the plain profile of every known provider, its three level profiles, and `gemini`."""
    out: Dict[str, Dict[str, Any]] = {n: dict(ADAPTERS[n].profile, args=[]) for n in KNOWN}
    out["gemini"] = {"command": "gemini", "args": [], "model": None, "model_flag": "-m", "prompt_flag": "-i"}
    for n in KNOWN:
        a = ADAPTERS[n]
        for lv in LEVELS:
            pack = a.levels[lv]
            prof: Dict[str, Any] = {"command": a.profile["command"], "args": [], **{k: (list(v) if isinstance(v, list) else v) for k, v in a.level_extra.items()},
                                    "model": pack.get("model")}
            if pack.get("effort"):
                prof["effort"] = pack["effort"]
            out[level_profile_name(n, lv)] = prof
    return out


def levels_for(provider: str) -> Dict[str, str]:
    """strategy levels -> profile names for one provider (mixed-provider levels are written as config overrides)."""
    return {lv: level_profile_name(provider, lv) for lv in LEVELS}


def provider_of(prof: Dict[str, Any], name: str = "") -> str:
    p = prof.get("provider")
    if p:
        if p not in ADAPTERS:
            raise ResearchError(f"Unknown provider {p!r}", hint=f"Registered providers: {', '.join(ADAPTERS)}.")
        return p
    base = os.path.basename(str(prof.get("command") or name))
    return base if base in ADAPTERS and base != "custom" else "custom"


def build_argv(prof: Dict[str, Any], name: str, model: Optional[str], prompt: str, cwd: str,
               extra_dirs: Optional[List[str]] = None, headless: bool = False, effort: Optional[str] = None) -> List[str]:
    kind = provider_of(prof, name)
    dirs = [d for d in (extra_dirs or []) if os.path.abspath(d) != os.path.abspath(cwd)]
    return ADAPTERS[kind].build(prof, model, effort or prof.get("effort"), prompt, cwd, dirs, headless)


def detect(prof: Dict[str, Any], name: str) -> Dict[str, Any]:
    """Installed? Capabilities. Authentication and MCP are not probed here (no credentials are read)."""
    kind = provider_of(prof, name)
    cmd = prof.get("command") or name
    return {"provider": kind, "command": cmd, "installed": bool(shutil.which(cmd)), "capabilities": ADAPTERS[kind].caps}


# ------------------------------------------------------------------------------------------------ readiness


def version(cmd: str) -> Optional[str]:
    r = _run([cmd, "--version"]) if shutil.which(cmd) else None
    return (r.stdout or r.stderr).strip().splitlines()[0] if r and r.returncode == 0 and (r.stdout or r.stderr).strip() else None


def available_models(kind: str, cmd: str, configured: Dict[str, Any]) -> Dict[str, Any]:
    """Models the CLI itself reports (when the adapter can ask), else the ones named by configured profiles. `source` says which."""
    lister = ADAPTERS[kind].list_models if kind in ADAPTERS else None
    rows = lister(cmd) if lister else None
    if rows:
        return {"source": "cli", "models": rows}
    ms = sorted({str(p["model"]) for n, p in configured.items() if p.get("model") and provider_of(p, n) == kind})
    return {"source": "profiles", "models": [{"id": m, "label": m} for m in ms]}


def table(p) -> List[Dict[str, Any]]:
    """One readiness row per provider profile of the project (the Control Center, `research provider list` and init share it).
    Level profiles (`claude-fast`...) are model choices, not providers, so they are not rows."""
    profs = (p.config.get("agents") or {}).get("profiles") or {}
    rows = []
    for name, prof in profs.items():
        if name in KNOWN or prof.get("provider"):
            r = readiness(prof, name, p.root)
            r["models"] = available_models(r["provider"], r["command"], profs)
            r["profiles"] = sorted(n for n, x in profs.items() if provider_of(x, n) == r["provider"] and n != name)
            rows.append(r)
    return rows


def instruction_files(root: str) -> List[Dict[str, Any]]:
    """Every provider reads the one AGENTS.md; Claude Code only reads CLAUDE.md, so that file imports AGENTS.md."""
    a, c = os.path.join(root, "AGENTS.md"), os.path.join(root, "CLAUDE.md")
    claude_ok = os.path.isfile(c) and "@AGENTS.md" in open(c, encoding="utf-8").read()
    reads = lambda f: [n for n in KNOWN if ADAPTERS[n].caps.get("instructions") == f]
    return [{"file": "AGENTS.md", "providers": reads("AGENTS.md") + [f"{n} (via CLAUDE.md)" for n in reads("CLAUDE.md")], "ok": os.path.isfile(a)},
            {"file": "CLAUDE.md", "providers": reads("CLAUDE.md"), "ok": claude_ok}]


def sync_instructions(root: str) -> List[str]:
    """Create CLAUDE.md as an `@AGENTS.md` import when absent. Never edits an existing CLAUDE.md (reports it instead)."""
    c = os.path.join(root, "CLAUDE.md")
    if not os.path.exists(c):
        with open(c, "w", encoding="utf-8") as f:
            f.write("@AGENTS.md\n")
        return ["CLAUDE.md"]
    return []


def mcp_install(kind: str, root: str, cmd: Optional[str] = None) -> Dict[str, Any]:
    """Register the Research OS MCP server for one provider. Project files for claude/agy; Codex's user config via its CLI."""
    a = ADAPTERS.get(kind)
    if not a or not a.mcp_install:
        raise ResearchError(f"No MCP installer for provider {kind!r}")
    return a.mcp_install(root, cmd)


def mcp_registered(kind: str, root: str) -> Optional[bool]:
    """Is the research server registered for this provider? None = cannot tell without side effects."""
    a = ADAPTERS.get(kind)
    return a.mcp_registered(root) if a and a.mcp_registered else None


def mcp_verify(root: str, cwd: str, timeout: float = 20.0) -> Dict[str, Any]:
    """Prove the server is reachable from the directory an agent runs in: start it there and list its tools over stdio."""
    argv = _server_cmd(root)
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                                                        "clientInfo": {"name": "research-verify", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    r = None
    try:
        r = subprocess.run(argv, cwd=cwd, input="".join(json.dumps(m) + "\n" for m in msgs), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "cwd": cwd, "problem": f"could not start `{' '.join(argv)}` in {cwd}: {e}"}
    tools: List[str] = []
    for line in r.stdout.splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("id") == 2:
            tools = [t["name"] for t in (m.get("result") or {}).get("tools", [])]
    return {"ok": bool(tools), "cwd": cwd, "tools": len(tools),
            "problem": None if tools else f"server started in {cwd} but listed no tools: {(r.stderr or r.stdout).strip()[:200]}"}


def readiness(prof: Dict[str, Any], name: str, root: Optional[str] = None) -> Dict[str, Any]:
    """Everything the UI shows for one provider. Never reads credentials; authentication is reported as unknown unless the CLI says."""
    d = detect(prof, name)
    d["name"] = name
    d["label"] = ADAPTERS[d["provider"]].label
    d["version"] = version(d["command"]) if d["installed"] else None
    d["ready"] = d["installed"]
    d["auth"] = "unknown (the CLI reports it on first use; no credentials are read by Research OS)"
    d["mcp_registered"] = mcp_registered(d["provider"], root) if root else None
    d["instructions"] = ADAPTERS[d["provider"]].caps.get("instructions")
    if not d["installed"]:
        d["problem"] = f"`{d['command']}` is not on PATH. Install it, or set agents.profiles.{name}.command to its full path."
    return d



# ------------------------------------------------------------------------------------------------ session continuity
_RESUME_PROBE: Dict[Any, bool] = {}


def native_resume_supported(kind: str, command: str) -> bool:
    """True only when the *installed* CLI proves it: its own --help lists the resume interface. Nothing is assumed from the
    provider's name, and an unprobeable provider falls back to a context handoff."""
    key = (kind, command)
    if key not in _RESUME_PROBE:
        a = ADAPTERS.get(kind)
        _RESUME_PROBE[key] = bool(a and a.resume_probe and shutil.which(command) and a.resume_probe(command))
    return _RESUME_PROBE[key]


def resume_argv(kind: str, argv: List[str], session_id: str) -> Optional[List[str]]:
    """The provider command line that continues a native session, or None when this adapter has no verified resume shape."""
    a = ADAPTERS.get(kind)
    return a.resume_argv(argv, session_id) if a and a.resume_argv else None


def session_id_of(kind: str, output: str) -> Optional[str]:
    """The provider's own session id from headless output, if the format reports one."""
    a = ADAPTERS.get(kind)
    return a.session_id(output) if a and a.session_id else None
