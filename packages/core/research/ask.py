"""`/ask`: a read-only agent that looks things up and answers, and the optional agent-drafted experiment summary. Both use the agent configured under
`ask.profile` / `ask.model` (any provider: claude, codex, agy, opencode) and share one set of rules.

Read-only is enforced by the provider where its CLI can enforce it, and always stated in the prompt:
    claude   plan permission mode, and only read tools, read-only shell commands and read-only `research` commands are allowed
    codex    the `read-only` sandbox (it cannot write anywhere)
    agy, opencode, custom   their headless modes accept edits and cannot be restricted from here, so they are REFUSED unless a human sets
             `ask.allow_unenforced: true` (then only the instruction stops them from writing)
The agent runs as an agent (RESEARCH_AGENT is set), so every protected action (approvals, promotion, PIN-gated changes) is refused to it as always.
"""
from __future__ import annotations

import os
import shutil
from typing import Any, Dict, List, Optional, Tuple

from . import agents, providers as PV
from .util import ResearchError

READ_TOOLS = ["Read", "Grep", "Glob", "Bash(ls *)", "Bash(cat *)", "Bash(head *)", "Bash(tail *)", "Bash(grep *)", "Bash(rg *)", "Bash(find *)", "Bash(wc *)",
              "Bash(git log*)", "Bash(git status*)", "Bash(git diff*)", "Bash(git show*)", "Bash(git branch*)",
              "Bash(research current)", "Bash(research show *)", "Bash(research search *)", "Bash(research status*)", "Bash(research list*)",
              "Bash(research run status *)", "Bash(research run logs *)", "Bash(research experiment state *)", "Bash(.research/bin/research show *)",
              "Bash(.research/bin/research search *)", "Bash(.research/bin/research current)"]
READONLY = {"claude": {"permission_mode": "plan", "allowed_tools": READ_TOOLS}, "codex": {"sandbox": "read-only"}}    # provider -> the profile settings that enforce it
RULES = """You are a READ-ONLY research assistant for this project (Research OS). Answer the researcher's question from the project's records and files.

RULES, no exceptions:
- Read only. Do NOT create, edit, move, copy over or delete any file. Do NOT run anything that writes: no rm, mv, redirects (> or >>), tee, sed -i, git commit/checkout/reset/clean/stash/push, pip/uv/npm install, no job submission (sbatch), no experiment runs, no `research` command that creates or changes records.
- You may read files, search, and run read-only commands (ls, cat, head, tail, grep, find, wc, git log/status/diff/show, `research current|show|search|status|list`).
- If the answer needs a change, describe it and let the researcher decide. Never print secrets, tokens or the contents of credential files.
- Say what you checked, and what you could not check. Do not state a scientific conclusion the records do not support; say when evidence is missing or unverified.
- Be brief: short paragraphs, plain language, at most about 250 words unless asked for more. The answer is read on a phone.
"""


def cfg(p) -> Dict[str, Any]:
    return {"profile": None, "model": None, "timeout_seconds": 300, "allow_unenforced": False, **(p.config.get("ask") or {})}


def readonly_profile(prof: Dict[str, Any], provider: str, allow_unenforced: bool) -> Tuple[Dict[str, Any], bool]:
    """(profile with the read-only settings applied, whether the provider enforces them). Refuses a provider that cannot, unless allowed."""
    if provider in READONLY:
        return {**prof, **READONLY[provider]}, True
    if not allow_unenforced:
        raise ResearchError(f"{provider} cannot be restricted to read-only from here, so it is not used for /ask or summaries",
                            hint="Use claude or codex (`research config set ask.profile claude`), or a human can set ask.allow_unenforced: true to rely on the instruction alone.")
    return prof, False


def describe(p) -> Dict[str, Any]:
    c = cfg(p)
    name, prof = agents.profile_for(p, "analyst", c["profile"])
    kind = PV.provider_of(prof, name)
    return {"profile": name, "provider": kind, "model": c["model"] or prof.get("model"), "read_only_enforced": kind in READONLY,
            "allowed": kind in READONLY or bool(c["allow_unenforced"]), "installed": bool(shutil.which(prof.get("command") or name)),
            "timeout_seconds": c["timeout_seconds"]}


def _context(p, limit: int = 9000) -> str:
    try:
        with open(p.path("current.md"), encoding="utf-8") as f:
            return f.read()[:limit]
    except OSError:
        return ""


def run(p, question: str, facts: str = "", style: str = "answer") -> Dict[str, Any]:
    """One headless read-only agent turn. Returns {answer, profile, provider, model, read_only_enforced, usage}. Raises ResearchError with a reason."""
    c = cfg(p)
    if not (question or "").strip():
        raise ResearchError("Ask a question: /ask <what you want to know>")
    name, base = agents.profile_for(p, "analyst", c["profile"])
    kind = PV.provider_of(base, name)
    prof, enforced = readonly_profile(base, kind, bool(c["allow_unenforced"]))
    cmd = prof.get("command") or name
    if not shutil.which(cmd):
        raise ResearchError(f"`{cmd}` is not installed or not on PATH", hint="Pick another agent: `research config set ask.profile <name>`.")
    model = c["model"] or prof.get("model")
    parts = [RULES]
    if style == "summary":
        parts.append("TASK: Write a short message for the researcher about the experiment below: what was done, what the recorded results are, and what they should know "
                     "(problems, unverified evidence, what needs their decision). Use ONLY the facts and records given or found; do not invent numbers or claims; "
                     "mark anything uncertain. At most 120 words. No greeting.\n")
    ctx = _context(p)
    if ctx:
        parts.append("PROJECT CONTEXT (current.md):\n" + ctx)
    if facts:
        parts.append("FACTS:\n" + facts[:8000])
    parts.append(("EXPERIMENT TO SUMMARISE: " if style == "summary" else "QUESTION: ") + question.strip()[:4000])
    prompt = "\n\n".join(parts)
    cwd = p.root
    argv = PV.build_argv(prof, name, model, prompt, cwd, [os.path.dirname(os.path.dirname(p.rdir))], True, None)
    env = {"RESEARCH_AGENT": name, "RESEARCH_AGENT_ROLE": "ask", "RESEARCH_ROOT": cwd}
    shim = os.path.join(p.rdir, "bin")
    env["PATH"] = (shim + os.pathsep if os.path.isdir(shim) else "") + os.environ.get("PATH", "")
    adapter = PV.ADAPTERS[kind]
    if model:
        env["RESEARCH_AGENT_MODEL"] = str(model)
    if adapter.env:
        env.update(adapter.env(prof, env, True))
    d = {"headless": True, "argv": argv, "env": env, "cwd": cwd, "usage_parser": prof.get("usage_parser") or adapter.usage_parser, "role": "ask", "profile": name,
         "model": model, "target": None, "policy": {}}
    r = agents.run_headless(p, d, timeout=float(c["timeout_seconds"]))
    if r["timed_out"] or r["exit_code"] not in (0, None):
        raise ResearchError(f"the agent failed (exit {r['exit_code']}{', timed out' if r['timed_out'] else ''}): {(r.get('stderr') or '')[-200:]}")
    ans = (r.get("result") or "").strip()
    if not ans:
        raise ResearchError("the agent returned no answer")
    return {"answer": ans, "profile": name, "provider": kind, "model": model, "read_only_enforced": enforced, "usage": r.get("usage")}


def footer(r: Dict[str, Any]) -> str:
    return f"\n\n-- {r['profile']}" + (f" / {r['model']}" if r.get("model") else "") + (", read-only" if r["read_only_enforced"] else ", read-only by instruction only")
