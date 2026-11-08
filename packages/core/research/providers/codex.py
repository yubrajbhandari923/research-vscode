"""OpenAI Codex CLI adapter."""
from __future__ import annotations

import json
import os
import shutil
from typing import Any, Dict, List, Optional

from ..util import ResearchError, as_list
from .base import Adapter, LimitInfo, last_json_value, run, server_cmd

# Level pack. Source: developers.openai.com/codex/config-reference (`model_reasoning_effort`: minimal|low|medium|high).
# UNVERIFIED defaults: no model id is pinned (the CLI's own configured model is used, which can never name a model that does not
# exist) and the levels differ by reasoning effort only. Pin `model` in agents.profiles.codex-<level> to taste.
LEVELS = {"fast": {"model": None, "effort": "low"},
          "balanced": {"model": None, "effort": "medium"},
          "deep": {"model": None, "effort": "high"}}


def build(prof, model, effort, prompt, cwd, dirs, headless) -> List[str]:
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


def resume_probe(command: str) -> bool:
    r = run([command, "exec", "resume", "--help"], timeout=20) if shutil.which(command) else None
    return bool(r and r.returncode == 0)


def resume_argv(argv: List[str], session_id: str) -> Optional[List[str]]:
    """Verified against codex-cli 0.163: `codex exec resume --json [-m M] [-c k=v …] ID PROMPT`; resume accepts neither -s, -C nor
    --add-dir, so the sandbox and writable roots go through -c and the working directory is the process's own."""
    if "exec" not in argv:
        return None
    i = argv.index("exec")
    rest, prompt = argv[i + 1:-1], argv[-1]
    out = [argv[0], "exec", "resume", "--json"]
    extra_dirs: List[str] = []
    j = 0
    while j < len(rest):
        a = rest[j]
        if a == "-s" and j + 1 < len(rest):
            out += ["-c", f'sandbox_mode="{rest[j + 1]}"']
            j += 2
        elif a == "--add-dir" and j + 1 < len(rest):
            extra_dirs.append(rest[j + 1])
            j += 2
        elif a in ("-m", "-c") and j + 1 < len(rest):
            out += [a, rest[j + 1]]
            j += 2
        else:                        # --json, -C (the process cwd already is the workspace) and anything resume does not take
            j += 2 if a == "-C" else 1
    if extra_dirs:
        out += ["-c", "sandbox_workspace_write.writable_roots=" + json.dumps(extra_dirs)]
    return out + [session_id, prompt]


def mcp_install(root: str, cmd: Optional[str] = None) -> Dict[str, Any]:
    codex = cmd or "codex"
    if not shutil.which(codex):
        raise ResearchError("codex is not installed, so its MCP registration cannot be written")
    r = run([codex, "mcp", "add", "research", "--", *server_cmd(root)], timeout=30)
    if not r or r.returncode != 0:
        raise ResearchError(f"`codex mcp add` failed: {(r.stderr if r else 'could not run').strip()[:300]}")
    return {"provider": "codex", "where": "~/.codex/config.toml", "scope": "user"}


def mcp_registered(root: str) -> Optional[bool]:
    if not shutil.which("codex"):
        return None
    r = run(["codex", "mcp", "list"])
    return bool(r and r.returncode == 0 and "research" in r.stdout)


def limit_info(out: str, err: str, exit_code: Optional[int]) -> Optional[LimitInfo]:
    """Source of truth: codex-rs/protocol/src/error.rs (UsageLimitReachedError) and exec's JSONL events (`turn.failed.error.message`, `error.message`).
    'Try again at 3:41 PM.' is host-local with no zone; 'Try again later.' means unknown. Plain stderr only counts for a failed run.
    UNVERIFIED: whether `exec --json` also exposes `resets_at` epoch seconds, and the exact JSON form of the per-minute 429."""
    from . import limits as L
    c: List[Optional[LimitInfo]] = []
    for d in L.json_lines(out):
        msg = (d.get("error") or {}).get("message") if d.get("type") == "turn.failed" else d.get("message") if d.get("type") == "error" else None
        if msg:
            c.append(L.info(str(msg)))
    if L.failing(exit_code):
        c += [L.info(line) for line in L.plain_lines(err, out)]
    return L.best(c)


ADAPTER = Adapter(
    name="codex", label="OpenAI Codex", build=build, levels=LEVELS,
    blurb="OpenAI Codex CLI. Interactive and headless (`codex exec`), model and reasoning effort, runs in a sandbox.",
    caps={"interactive": True, "headless": True, "model": True, "effort": True, "structured_output": "jsonl",
          "usage": False, "resume": True, "mcp": "codex mcp add", "instructions": "AGENTS.md", "watchdog": False},
    profile={"command": "codex", "args": [], "model": None, "model_flag": "-m", "prompt_flag": None},
    resume_probe=resume_probe, resume_argv=resume_argv,
    session_id=lambda out: last_json_value(out, "thread_id", reverse=False),   # codex --json: {"type":"thread.started","thread_id":…}
    mcp_install=mcp_install, mcp_registered=mcp_registered, limit_info=limit_info)
