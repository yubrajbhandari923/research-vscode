"""Provider adapters: the only place that knows each agent CLI's flags.

An adapter turns (profile, model, effort, prompt, cwd, extra dirs) into an argv list (never a shell string).
`CAPABILITIES` states honestly what each tool supports so callers never assume parity.

Add a provider: write a function `(prof, model, effort, prompt, cwd, extra_dirs, headless) -> argv` and register it
in `ADAPTERS` with a capability entry. Profiles select an adapter with `provider: <name>` (default: the command's
basename if registered, else `custom`, which uses the profile's args/model_flag/prompt_flag/headless_args verbatim).
"""
from __future__ import annotations

import os
import shutil
from typing import Any, Callable, Dict, List, Optional

from .util import ResearchError, as_list

CAPABILITIES: Dict[str, Dict[str, Any]] = {
    "claude": {"interactive": True, "headless": True, "model": True, "effort": False, "structured_output": "json",
               "usage": True, "resume": False, "mcp": ".mcp.json", "instructions": "CLAUDE.md"},
    "codex": {"interactive": True, "headless": True, "model": True, "effort": True, "structured_output": "jsonl",
              "usage": False, "resume": True, "mcp": "codex mcp add", "instructions": "AGENTS.md"},
    "agy": {"interactive": True, "headless": True, "model": True, "effort": True, "structured_output": "json",
            "usage": False, "resume": True, "mcp": ".agents/mcp_config.json", "instructions": "AGENTS.md"},
    "custom": {"interactive": True, "headless": "if headless_args", "model": "if model_flag", "effort": False,
               "structured_output": None, "usage": "if usage_parser", "resume": False, "mcp": None, "instructions": None},
}


def _custom(prof, model, effort, prompt, cwd, dirs, headless) -> List[str]:
    argv = [prof.get("command"), *[str(a) for a in as_list(prof.get("args"))]]
    if headless:
        if not prof.get("headless_args"):
            raise ResearchError(f"Profile {prof.get('command')!r} has no headless_args, so headless execution is unsupported",
                                hint="Set agents.profiles.<name>.headless_args (and usage_parser) or use interactive mode.")
        argv += [str(a) for a in as_list(prof["headless_args"])]
    if model and prof.get("model_flag"):
        argv += [prof["model_flag"], str(model)]
    if prof.get("prompt_flag"):
        argv.append(prof["prompt_flag"])
    return argv + [prompt]


def _claude(prof, model, effort, prompt, cwd, dirs, headless) -> List[str]:
    argv = [prof.get("command") or "claude", *[str(a) for a in as_list(prof.get("args"))]]
    if headless:
        argv += ["-p", "--output-format", "json"]
    for d in dirs:
        argv += ["--add-dir", d]
    if headless:   # nobody can answer a permission prompt: allow edits and the research CLI only, never arbitrary Bash
        tools = prof.get("allowed_tools") or ["Bash(research *)", "Bash(./.research/bin/research *)", "Bash(*/.research/bin/research *)", "mcp__research"]
        argv += ["--allowedTools", ",".join(as_list(tools)), "--permission-mode", str(prof.get("permission_mode") or "acceptEdits")]
    if model:
        argv += ["--model", str(model)]
    return argv + [prompt]


def _codex(prof, model, effort, prompt, cwd, dirs, headless) -> List[str]:
    argv = [prof.get("command") or "codex", *[str(a) for a in as_list(prof.get("args"))]]
    if headless:
        argv.append("exec")
        argv += ["--json", "-s", prof.get("sandbox") or "workspace-write"]  # explicit sandbox; never danger-full-access
    argv += ["-C", cwd]
    for d in dirs:
        argv += ["--add-dir", d]
    if model:
        argv += ["-m", str(model)]
    if effort:
        argv += ["-c", f'model_reasoning_effort="{effort}"']
    return argv + [prompt]


def _agy(prof, model, effort, prompt, cwd, dirs, headless) -> List[str]:
    # agy has no -C: the subprocess cwd is the working directory.
    argv = [prof.get("command") or "agy", *[str(a) for a in as_list(prof.get("args"))]]
    for d in dirs:
        argv += ["--add-dir", d]
    if model:
        argv += ["--model", str(model)]
    if effort:
        argv += ["--effort", str(effort)]
    if headless:
        return argv + ["--output-format", "json", "-p", prompt]
    return argv + ["-i", prompt]


ADAPTERS: Dict[str, Callable[..., List[str]]] = {"claude": _claude, "codex": _codex, "agy": _agy, "custom": _custom}


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
    return ADAPTERS[kind](prof, model, effort or prof.get("effort"), prompt, cwd, dirs, headless)


def detect(prof: Dict[str, Any], name: str) -> Dict[str, Any]:
    """Installed? Capabilities. Authentication and MCP are not probed here (no credentials are read)."""
    kind = provider_of(prof, name)
    cmd = prof.get("command") or name
    return {"provider": kind, "command": cmd, "installed": bool(shutil.which(cmd)), "capabilities": CAPABILITIES[kind]}


# ------------------------------------------------------------------------------------------------ readiness
import json
import subprocess


def _run(argv: List[str], cwd: Optional[str] = None, timeout: float = 15.0) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def version(cmd: str) -> Optional[str]:
    r = _run([cmd, "--version"]) if shutil.which(cmd) else None
    return (r.stdout or r.stderr).strip().splitlines()[0] if r and r.returncode == 0 and (r.stdout or r.stderr).strip() else None


def available_models(kind: str, cmd: str, configured: Dict[str, Any]) -> Dict[str, Any]:
    """Models the CLI itself reports (agy), else the ones named by configured profiles. `source` says which."""
    if kind == "agy" and shutil.which(cmd):
        r = _run([cmd, "models"], timeout=30)
        if r and r.returncode == 0:
            rows = [l.split("\t", 1) for l in r.stdout.splitlines() if "\t" in l]
            if rows:
                return {"source": "cli", "models": [{"id": a.strip(), "label": b.strip()} for a, b in rows]}
    ms = sorted({str(p["model"]) for n, p in configured.items() if p.get("model") and provider_of(p, n) == kind})
    return {"source": "profiles", "models": [{"id": m, "label": m} for m in ms]}


def instruction_files(root: str) -> List[Dict[str, Any]]:
    """Every provider reads the one AGENTS.md; Claude Code only reads CLAUDE.md, so that file imports AGENTS.md."""
    a, c = os.path.join(root, "AGENTS.md"), os.path.join(root, "CLAUDE.md")
    claude_ok = os.path.isfile(c) and "@AGENTS.md" in open(c, encoding="utf-8").read()
    return [{"file": "AGENTS.md", "providers": ["codex", "agy", "claude (via CLAUDE.md)"], "ok": os.path.isfile(a)},
            {"file": "CLAUDE.md", "providers": ["claude"], "ok": claude_ok}]


def sync_instructions(root: str) -> List[str]:
    """Create CLAUDE.md as an `@AGENTS.md` import when absent. Never edits an existing CLAUDE.md (reports it instead)."""
    c = os.path.join(root, "CLAUDE.md")
    if not os.path.exists(c):
        with open(c, "w", encoding="utf-8") as f:
            f.write("@AGENTS.md\n")
        return ["CLAUDE.md"]
    return []


def _server_cmd(root: str) -> List[str]:
    if shutil.which("research"):
        return ["research", "mcp"]
    from . import localbin
    return [localbin.install(root)["path"], "mcp"]


def mcp_install(kind: str, root: str, cmd: Optional[str] = None) -> Dict[str, Any]:
    """Register the Research OS MCP server for one provider. Project files for claude/agy; Codex's user config via its CLI."""
    from . import mcp
    argv = _server_cmd(root)
    if kind == "claude":
        mcp.install(root, ["claude"], command=argv[0])
        return {"provider": kind, "where": ".mcp.json", "scope": "project"}
    if kind == "agy":
        mcp._merge_json(os.path.join(root, ".agents", "mcp_config.json"), "mcpServers", "research", {"command": argv[0], "args": argv[1:]})
        return {"provider": kind, "where": ".agents/mcp_config.json", "scope": "project"}
    if kind == "codex":
        codex = cmd or "codex"
        if not shutil.which(codex):
            raise ResearchError("codex is not installed, so its MCP registration cannot be written")
        r = _run([codex, "mcp", "add", "research", "--", *argv], timeout=30)
        if not r or r.returncode != 0:
            raise ResearchError(f"`codex mcp add` failed: {(r.stderr if r else 'could not run').strip()[:300]}")
        return {"provider": kind, "where": "~/.codex/config.toml", "scope": "user"}
    raise ResearchError(f"No MCP installer for provider {kind!r}")


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


def mcp_registered(kind: str, root: str) -> Optional[bool]:
    """Is the research server registered for this provider? None = cannot tell without side effects."""
    def has(rel: str, key: str) -> bool:
        try:
            return "research" in (json.load(open(os.path.join(root, rel), encoding="utf-8")).get(key) or {})
        except (OSError, ValueError):
            return False
    if kind == "claude":
        return has(".mcp.json", "mcpServers")
    if kind == "agy":
        return has(".agents/mcp_config.json", "mcpServers")
    if kind == "codex" and shutil.which("codex"):
        r = _run(["codex", "mcp", "list"])
        return bool(r and r.returncode == 0 and "research" in r.stdout)
    return None


def readiness(prof: Dict[str, Any], name: str, root: Optional[str] = None) -> Dict[str, Any]:
    """Everything the UI shows for one provider. Never reads credentials; authentication is reported as unknown unless the CLI says."""
    d = detect(prof, name)
    d["name"] = name
    d["version"] = version(d["command"]) if d["installed"] else None
    d["ready"] = d["installed"]
    d["auth"] = "unknown (the CLI reports it on first use; no credentials are read by Research OS)"
    d["mcp_registered"] = mcp_registered(d["provider"], root) if root else None
    d["instructions"] = CAPABILITIES[d["provider"]].get("instructions")
    if not d["installed"]:
        d["problem"] = f"`{d['command']}` is not on PATH. Install it, or set agents.profiles.{name}.command to its full path."
    return d


def register(name: str, build: Callable[..., List[str]], capabilities: Dict[str, Any]) -> None:
    """Add a provider adapter at runtime (see docs/PROVIDERS.md). `build` has the signature of `_codex`; capabilities must
    state honestly what the tool supports (the same keys as CAPABILITIES)."""
    missing = {"interactive", "headless", "model", "effort", "structured_output", "usage", "resume", "mcp", "instructions"} - set(capabilities)
    if missing:
        raise ResearchError(f"Provider {name!r} capabilities are missing: {', '.join(sorted(missing))}")
    ADAPTERS[name] = build
    CAPABILITIES[name] = capabilities
