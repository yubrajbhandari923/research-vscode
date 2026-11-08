"""Claude Code adapter."""
from __future__ import annotations

import json
import os
import shutil
from typing import Any, Dict, List, Optional

from ..util import as_list
from .base import Adapter, LimitInfo, json_has_research, last_json_value, merge_json, run, server_cmd

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
    argv = server_cmd(root)
    merge_json(os.path.join(root, ".mcp.json"), "mcpServers", "research", {"type": "stdio", "command": argv[0], "args": argv[1:]})
    return {"provider": "claude", "where": ".mcp.json", "scope": "project"}


def limit_info(out: str, err: str, exit_code: Optional[int]) -> Optional[LimitInfo]:
    """Formats from docs/PHASE_CLEANUP_R_LIMITS.md §1. Structured: `result` with is_error / terminal_reason api_error (key on those, never on
    `subtype`, which says "success" even for a limit) and stream-json `rate_limit_event` status rejected (epoch `resetsAt`, exact; not when
    overage covers it). Plain banner text only counts for a failed run. UNVERIFIED: the exact `-p --output-format json` stdout at a real
    5-hour limit (the clock time in the text is all it may carry)."""
    from . import limits as L
    c: List[Optional[LimitInfo]] = []
    event = None
    for d in L.json_lines(out):
        t = d.get("type")
        ri = d.get("rate_limit_info") or {}
        if t == "rate_limit_event" and ri.get("status") == "rejected" and not ri.get("isUsingOverage") and L.failing(exit_code):
            event = LimitInfo("credit" if ri.get("rateLimitType") == "overage" else "usage", L.from_epoch(ri.get("resetsAt")), json.dumps(ri)[:300])
        elif t == "result" and (d.get("is_error") or d.get("terminal_reason") == "api_error"):
            text = " ".join(str(d.get(k) or "") for k in ("api_error", "result", "error"))
            c.append(L.info(text) or (LimitInfo("rate", None, text.strip()[:300]) if d.get("api_error_status") == 429 else None))
        elif t == "error":
            c.append(L.info(json.dumps(d)))
    if L.failing(exit_code):
        c += [L.info(line) for line in L.plain_lines(out, err)] + [L.info("\n".join(L.plain_lines(out, err)))]   # headers arrive on separate lines
    got = L.best(c)
    if event and got:   # the epoch is exact; the message text is only a clock time
        return LimitInfo(event.kind if event.kind == "credit" else got.kind, event.reset_at or got.reset_at, got.raw)
    return event or got


ADAPTER = Adapter(
    name="claude", label="Claude Code", build=build, levels=LEVELS,
    blurb="Anthropic’s Claude Code. Default. Interactive and headless, per-task model choice, reports token usage.",
    caps={"interactive": True, "headless": True, "model": True, "effort": False, "structured_output": "json",
          "usage": True, "resume": False, "mcp": ".mcp.json", "instructions": "CLAUDE.md", "watchdog": False},
    profile={"command": "claude", "args": [], "model": None, "model_flag": "--model", "prompt_flag": None},
    level_extra={"headless_args": ["-p", "--output-format", "json"], "usage_parser": "claude_json", "model_flag": "--model", "prompt_flag": None},
    usage_parser="claude_json", resume_probe=resume_probe, resume_argv=resume_argv,
    session_id=lambda out: last_json_value(out, "session_id"),
    mcp_install=mcp_install, mcp_registered=lambda root: json_has_research(root, ".mcp.json", "mcpServers"), limit_info=limit_info)
