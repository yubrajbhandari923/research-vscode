"""Claude Code adapter."""
from __future__ import annotations

import os
import shutil
from typing import Any, Dict, List, Optional

from ..util import as_list
from .base import Adapter, json_has_research, last_json_value, run, server_cmd

# Level pack: the models the built-in claude-fast/balanced/deep profiles have always used. Claude Code has no effort flag here.
LEVELS = {"fast": {"model": "claude-haiku-5-5", "effort": None},
          "balanced": {"model": "claude-sonnet-5-5", "effort": None},
          "deep": {"model": "claude-opus-5-5", "effort": None}}


def build(prof, model, effort, prompt, cwd, dirs, headless) -> List[str]:
    argv = [prof.get("command") or "claude", *[str(a) for a in as_list(prof.get("args"))]]
    if headless:
        argv += ["-p", "--output-format", "json"]
    for d in dirs:
        argv += ["--add-dir", d]
    if headless:   # nobody can answer a permission prompt: allow edits and the research CLI only, never arbitrary Bash
        tools = prof.get("allowed_tools") or ["Bash(research *)", "Bash(.research/bin/research *)", "Bash(./.research/bin/research *)", "Bash(*/.research/bin/research *)",
                 "Bash(cd * && research *)", "Bash(cd * && .research/bin/research *)", "Bash(cd * && ./.research/bin/research *)", "mcp__research"]
        argv += ["--allowedTools", ",".join(as_list(tools)), "--permission-mode", str(prof.get("permission_mode") or "acceptEdits")]
    if model:
        argv += ["--model", str(model)]
    return argv + [prompt]


def resume_probe(command: str) -> bool:
    r = run([command, "--help"], timeout=20) if shutil.which(command) else None
    return bool(r and r.returncode == 0 and "--resume" in (r.stdout + r.stderr))


def resume_argv(argv: List[str], session_id: str) -> Optional[List[str]]:
    """`claude -p --resume ID …`."""
    if "-p" not in argv:
        return None
    i = argv.index("-p")
    return argv[:i + 1] + ["--resume", session_id] + argv[i + 1:]


def mcp_install(root: str, cmd: Optional[str] = None) -> Dict[str, Any]:
    from .. import mcp
    mcp.install(root, ["claude"], command=server_cmd(root)[0])
    return {"provider": "claude", "where": ".mcp.json", "scope": "project"}


ADAPTER = Adapter(
    name="claude", label="Claude Code", build=build, levels=LEVELS,
    blurb="Anthropic’s Claude Code. Default. Interactive and headless, per-task model choice, reports token usage.",
    caps={"interactive": True, "headless": True, "model": True, "effort": False, "structured_output": "json",
          "usage": True, "resume": False, "mcp": ".mcp.json", "instructions": "CLAUDE.md"},
    profile={"command": "claude", "args": [], "model": None, "model_flag": "--model", "prompt_flag": None},
    level_extra={"headless_args": ["-p", "--output-format", "json"], "usage_parser": "claude_json", "model_flag": "--model", "prompt_flag": None},
    usage_parser="claude_json", resume_probe=resume_probe, resume_argv=resume_argv,
    session_id=lambda out: last_json_value(out, "session_id"),
    mcp_install=mcp_install, mcp_registered=lambda root: json_has_research(root, ".mcp.json", "mcpServers"))
