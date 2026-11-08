"""Google Antigravity CLI (`agy`) adapter."""
from __future__ import annotations

import json
import os
import shutil
from typing import Any, Dict, List, Optional

from ..util import as_list
from .base import Adapter, LimitInfo, json_has_research, last_json_value, merge_json, run, server_cmd

# Level pack. Source: `agy --help` (agy 1.5.0): `--effort` takes low|medium|high. UNVERIFIED defaults: no model id is pinned (`agy models`
# lists what the account may use) and the levels differ by effort only. Pin `model` in agents.profiles.agy-<level> to taste.
LEVELS = {"fast": {"model": None, "effort": "low"},
          "balanced": {"model": None, "effort": "medium"},
          "deep": {"model": None, "effort": "high"}}


def build(prof, model, effort, prompt, cwd, dirs, headless) -> List[str]:
    # agy has no -C: the subprocess cwd is the working directory.
    argv = [prof.get("command") or "agy", *[str(a) for a in as_list(prof.get("args"))]]
    for d in dirs:
        argv += ["--add-dir", d]
    if model:
        argv += ["--model", str(model)]
    if effort:
        argv += ["--effort", str(effort)]
    if headless:
        # nobody can answer a permission prompt in print mode: accept edits (the same stance as claude's acceptEdits), never --dangerously-skip-permissions
        return argv + ["--mode", str(prof.get("permission_mode") or "accept-edits"), "--output-format", "json", "-p", prompt]
    return argv + ["-i", prompt]


def resume_probe(command: str) -> bool:
    r = run([command, "--help"], timeout=20) if shutil.which(command) else None
    return bool(r and "--conversation" in (r.stdout + r.stderr))   # agy prints its usage on stderr / non-zero


def resume_argv(argv: List[str], session_id: str) -> Optional[List[str]]:
    """Verified against agy 1.5.0: `agy --conversation ID --output-format json -p PROMPT`."""
    if "-p" not in argv:
        return None
    i = argv.index("-p")
    return argv[:i] + ["--conversation", session_id] + argv[i:]


def list_models(command: str) -> Optional[List[Dict[str, str]]]:
    r = run([command, "models"], timeout=30) if shutil.which(command) else None
    if r and r.returncode == 0:
        rows = [l.split("\t", 1) for l in r.stdout.splitlines() if "\t" in l]
        if rows:
            return [{"id": a.strip(), "label": b.strip()} for a, b in rows]
    return None


def mcp_install(root: str, cmd: Optional[str] = None) -> Dict[str, Any]:
    argv = server_cmd(root)
    merge_json(os.path.join(root, ".agents", "mcp_config.json"), "mcpServers", "research", {"command": argv[0], "args": argv[1:]})
    return {"provider": "agy", "where": ".agents/mcp_config.json", "scope": "project"}


def limit_info(out: str, err: str, exit_code: Optional[int]) -> Optional[LimitInfo]:
    """agy 1.2.2 prints a stdout `result` event with status ERROR (exit 0 after --print-timeout); 1.2.14 prints `AGY_ERROR: {...}` on stderr
    (exit 3). The reset is relative ('Resets in 2h39m52s'). Source: antigravity-cli issue 1018. UNVERIFIED: the json-mode (non-stream) shape,
    shorter or longer durations, and anything else agy prints; a timeout with zero tokens is NOT treated as a limit (issue 1278 says it can
    hide one, but a guess would wait for nothing). `retryable: true` is ignored (it is true even for a multi-hour reset)."""
    from . import limits as L
    c: List[Optional[LimitInfo]] = []
    for line in (err or "").splitlines():
        if line.startswith("AGY_ERROR:"):
            try:
                c.append(L.info(str(json.loads(line[len("AGY_ERROR:"):]).get("short_error") or "")))
            except ValueError:
                pass
    for d in L.json_lines(out):
        r = d.get("result") if d.get("event") == "result" else d
        if isinstance(r, dict) and r.get("status") == "ERROR" and r.get("error"):
            c.append(L.info(str(r["error"])))
    if L.failing(exit_code):
        c += [L.info(line) for line in L.plain_lines(err, out) if not line.startswith("AGY_ERROR:")]
    return L.best(c)


ADAPTER = Adapter(
    name="agy", label="Google Antigravity", build=build, levels=LEVELS,
    blurb="Google Antigravity CLI (`agy`). Interactive and headless, model and effort, JSON output. Not the same tool as Gemini CLI.",
    caps={"interactive": True, "headless": True, "model": True, "effort": True, "structured_output": "json",
          "usage": False, "resume": True, "mcp": ".agents/mcp_config.json", "instructions": "AGENTS.md", "watchdog": False},
    profile={"provider": "agy", "command": "agy", "args": [], "model": None, "effort": None},
    resume_probe=resume_probe, resume_argv=resume_argv, session_id=lambda out: last_json_value(out, "conversation_id"),
    list_models=list_models, mcp_install=mcp_install, limit_info=limit_info,
    mcp_registered=lambda root: json_has_research(root, ".agents/mcp_config.json", "mcpServers"))
