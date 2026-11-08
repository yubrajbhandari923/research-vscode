"""MCP server: the research tools for any MCP-capable agent (Claude Code, Codex, Copilot, Cursor, Gemini, …).

`research mcp` speaks the Model Context Protocol over stdio (newline-delimited JSON-RPC 2.0), standard library only.
`research mcp install` registers it in .mcp.json (Claude Code), .vscode/mcp.json (VS Code), .cursor/mcp.json and
.gemini/settings.json; for Codex it prints the `codex mcp add` command.

Every tool is an operation in `research/api/` (`mcp="<tool name>"`): the same core functions as the CLI, so policy (run budget,
verification gates, task claims) applies identically. This file holds the hand-written descriptions and schemas, the MCP framing and
the installer. Tool errors come back as `isError` results with the hint, so agents can react.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import traceback
from typing import Any, Dict, List, Optional

from . import __version__, api

PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]

INSTRUCTIONS = """Research memory and coordination for this project. Start with research_current (project state),
then task_next / task_context for your work. Register an experiment before computing; every run belongs to one
(run_start). Record results as findings with evidence; they stay preliminary until an independent review
(finding_review). task_done runs the task's checks; no verifier means unverified, never passed. Errors with code 3
are policy blocks: read the hint. MCP covers the core loop only: orchestration, hooks, tuning, strategy and most hpc
commands need the CLI (`.research/bin/research`)."""

S = {"type": "string"}
I = {"type": "integer"}
N = {"type": "number"}
B = {"type": "boolean"}
L = {"type": "array", "items": {"type": "string"}}
O = {"type": "object"}


class Tool:
    """A tool is its hand-written schema plus the op it calls: `fn` is the op handler (project first), `mutating` is the op's flag."""

    def __init__(self, name: str, description: str, props: Dict[str, Any], required: List[str], op: "api.Op"):
        self.name, self.description, self.op, self.fn, self.mutating = name, description, op, op.fn, op.mutates
        self.schema = {"type": "object", "properties": props, "required": required, "additionalProperties": False}


# name, description, properties, required. The order is the order of tools/list.
TOOLS = [
    ("research_current", "Project state for agents: goal, checkpoint, beliefs, failures, experiments, active plans and their "
             "next tasks, attention items, skills, policy. Read this first.",
     {}, []),
    ("research_search", "Keyword search over questions, experiments, runs, findings, decisions, plans, tasks, syntheses, "
             "notes and skills.",
     {"query": S, "type": S, "limit": I}, ["query"]),
    ("research_show", "Full detail of any id (Q-, EXP-, RUN-, F-, D-, CP-, A-, PLAN-, T-, note:…).",
     {"id": S}, ["id"]),
    ("task_next", "The next ready task (todo, dependencies done, unclaimed), optionally within one plan.",
     {"plan": S}, []),
    ("task_context", "Focused brief for one task: goal, acceptance criteria, checks, plan objective, dependency results, "
             "related experiment state, skills to load, progress notes, rules.",
     {"id": S}, ["id"]),
    ("agent_report", "Report on your supervised task: kind "
             "progress|milestone|artifact|metric|run|hpc_job|code|problem|report|finish. Only valid inside an "
             "execution started by the Research OS supervisor; you cannot mark the task verified.",
     {"task": S, "kind": S, "message": S, "summary": S, "path": S, "name": S, "value": N, "run": S, "job": S, "category": S, "commit": S}, ["task", "kind"]),
    ("agent_propose", "Propose a plan change as a YAML patch (same schema as plan YAML, scope partial). Operational "
             "additions may apply automatically; scientific changes wait for the researcher.",
     {"task": S, "yaml": S, "rationale": S}, ["task", "yaml", "rationale"]),
    ("plan_status", "Supervised status of a plan: task phases, attempts, budgets, pending proposals.",
     {"plan": S}, ["plan"]),
    ("task_start", "Claim a task and mark it running. Fails if another agent holds it.",
     {"id": S, "force": B}, ["id"]),
    ("task_note", "Append a progress note to a task (the hand-off trail for whoever continues).",
     {"id": S, "text": S}, ["id", "text"]),
    ("task_done", "Complete a task. Runs its checks first; failing checks block completion.",
     {"id": S, "result": S, "artifacts": L}, ["id"]),
    ("task_block", "Mark a task blocked, with the reason.",
     {"id": S, "reason": S}, ["id", "reason"]),
    ("task_release", "Give a claimed task back (status todo) with an optional hand-off note.",
     {"id": S, "note": S}, ["id"]),
    ("task_verify", "Run a task's checks (commands, files, metric thresholds) and record the result.",
     {"id": S}, ["id"]),
    ("task_create", "Add a task to a plan. checks: 'cmd:pytest -q', 'file:out/plot.png', 'metric:EXP-001:rmse<=0.05'. "
             "skills: project skill names.",
     {"plan": S, "title": S, "goal": S, "type": S, "role": S, "depends_on": L, "acceptance_criteria": S, "checks": L, "skills": L, "experiment": S, "question": S, "inputs": S, "expected_outputs": S}, ["plan", "title"]),
    ("plan_create", "Create a plan (a research objective that tasks break down).",
     {"title": S, "objective": S, "success_criteria": S, "question": S, "skills": L}, ["title"]),
    ("question_create", "Create a research question.",
     {"title": S, "description": S, "parent": S}, ["title"]),
    ("experiment_create", "Register an experiment BEFORE running it (humans only; agents use experiment_propose): hypothesis, "
             "parameters (lists = sweep), metrics, planned runs, success criteria, stop conditions.",
     {"title": S, "question": S, "hypothesis": S, "motivation": S, "method": S, "parameters": O, "metrics": L, "planned_runs": I, "success_criteria": S, "stop_conditions": S}, ["title"]),
    ("experiment_propose", "You may NOT create experiments. Submit a proposal; the researcher approves it with a PIN. All fields "
             "are required.",
     {"title": S, "hypothesis": S, "rationale": S, "method": S, "expected_outputs": S, "related": S}, ["title", "hypothesis", "rationale", "method", "expected_outputs", "related"]),
    ("run_start", "Start a tracked run of an experiment in the background (returns the run id; poll run_status). Use "
             "metric_log / artifact_register or research.log_metric() inside the code.",
     {"experiment": S, "command": S, "parameters": O, "label": S, "cwd": S}, ["experiment", "command"]),
    ("run_status", "Status, exit code, metrics and artifacts of a run (refreshes from the backend).",
     {"id": S}, ["id"]),
    ("run_logs", "Tail of a run's stdout or stderr.",
     {"id": S, "stream": {"type": "string", "enum": ["stdout", "stderr"]}, "tail": I}, ["id"]),
    ("runs_compare", "Side by side: parameter and metric differences, code changes, artifacts of two runs.",
     {"a": S, "b": S}, ["a", "b"]),
    ("metric_log", "Log a metric on a run or an experiment.",
     {"name": S, "value": {"type": ["number", "string"]}, "run": S, "experiment": S, "step": I, "unit": S}, ["name", "value"]),
    ("artifact_register", "Register a file or folder (by reference, never copied) on a run or experiment.",
     {"path": S, "run": S, "experiment": S, "description": S}, ["path"]),
    ("experiment_synthesize", "Interpret an experiment's finished runs. mark: completed | failed (optional).",
     {"id": S, "what_happened": S, "what_worked": S, "what_failed": S, "interpretation": S, "limitations": S, "unresolved": S, "next_experiment": S, "mark": S}, ["id"]),
    ("finding_create", "Record a reusable result with evidence ids (EXP-/RUN-/A-/F-). kind: result | failure. Stays "
             "preliminary until an independent review.",
     {"title": S, "statement": S, "kind": S, "confidence": S, "supports": L, "contradicts": L, "questions": L, "limitations": S}, ["title"]),
    ("finding_review", "Independent review of a finding: verdict supported | contradicted | needs_work, with reasoning. You "
             "can't review findings you wrote.",
     {"id": S, "verdict": S, "notes": S, "confidence": S}, ["id", "verdict"]),
    ("checkpoint_create", "Snapshot the project's understanding (pre-filled from current state). commit=true also commits "
             ".research/ to git.",
     {"understanding": S, "problem": S, "next": S, "commit": B}, []),
    ("skills_list", "Project skills (name, description, always-on, applies_to).",
     {}, []),
    ("skill_show", "The full SKILL.md of a project skill. Load the skills a task lists before working on it.",
     {"name": S}, ["name"]),
    ("dispatch_prepare", "Prepare a brief for another agent role (planner|implementer|verifier|analyst) on a target id or "
             "objective; returns the command a human can run.",
     {"role": S, "target": S, "objective": S, "profile": S}, []),
    ("report_full", "Deterministic, exhaustive Markdown export of everything recorded (optionally for one Q-/EXP-/PLAN-), "
             "with coverage verification. Use it for a complete context hand-off. It retrieves and renders records "
             "only — it is not a synthesis and interprets nothing.",
     {"scope": S, "check": B}, []),
    ("report", "Write the readable project report (goal, findings with evidence plots, failed directions, decisions, "
             "runs and syntheses per question) to .research/reports/<scope>.md and/or .html and return the paths. "
             "scope = Q-/EXP-/PLAN- id or omitted for the whole project; fmt = md|html|both. It covers everything; "
             "the Scientist Guide is the short curated story.",
     {"scope": S, "fmt": {"type": "string", "enum": ["md", "html", "both"]}}, []),
    ("guide_status", "Scientist Guide (Quarto book on main) state: important results and whether each has a draft, per-"
             "chapter freshness (fresh|stale|invalid), citations, the last draft's limit check, review/approval "
             "label per chapter. Read-only; a clean state is not scientific approval.",
     {}, []),
    ("guide_highlights", "The important results (findings) that belong in the Scientist Guide, with numbers status and whether "
             "each is logged/drafted. Read-only.",
     {}, []),
    ("guide_draft_check", "After writing a guide draft: did you stay within guide.draft.* (words per section, sections, "
             "figures) and out of human-owned chapters? Read-only; fix every problem it lists.",
     {}, []),
    ("guide_draft_done", "Record that the given important results (F- ids) now have a draft, then return the limit check. Call "
             "it only after the guide files are saved.",
     {"ids": L}, []),
    ("guide_validate", "Validate the Scientist Guide's claim/figure provenance against the live records (missing, stale, "
             "contradicted, superseded, inconsistent). Read-only; provenance only, not a scientific review.",
     {}, []),
    ("doctor", "Project health: records, shared ledger, workspaces, runs, agents, sandbox, context. Read-only; "
             "reports errors, warnings and optional capabilities that are simply not enabled.",
     {}, []),
    ("workspace_list", "Experiment workspaces (git worktrees) sharing this project's one research ledger: branch, base "
             "revision, dirty state, linked tasks, active runs. Read-only.",
     {}, []),
    ("hpc_doctor", "Whether remote execution is available on a configured cluster, and which modes are usable. Read-"
             "only.",
     {"cluster": S}, []),
    ("hpc_sync", "Reconcile outstanding remote runs: adopt uncertain submissions, update Slurm status, import "
             "available results, and list experiments now needing interpretation. Run this before proposing "
             "repeated work. Submits nothing and starts no agent.",
     {"cluster": S}, []),
    ("hpc_bundle", "Build a portable execution bundle for an experiment, for manual execution on a cluster (no agent or "
             "connection needed there). Returns the archive path and the run id it is tied to.",
     {"experiment": S, "command": S}, ["experiment"]),
]


def _tools() -> List[Tool]:
    by_tool = {o.mcp: o for o in api.OPS.values() if o.mcp}
    return [Tool(n, d, props, req, by_tool[n]) for n, d, props, req in TOOLS]


class McpServer:
    def __init__(self, root: str, author: Optional[Dict[str, Any]] = None):
        self.session = api.Session(root, author)
        self.explicit_author = author is not None
        self.tools = {t.name: t for t in _tools()}

    def handle(self, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if (not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0"
                or not isinstance(msg.get("method"), str)):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params", {})
        if isinstance(mid, (dict, list, bool)):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request id"}}
        if mid is None:  # notification (e.g. notifications/initialized): never answered
            return None
        try:
            if not isinstance(params, dict):
                raise _RpcError(-32602, "params must be an object")
            return {"jsonrpc": "2.0", "id": mid, "result": self._dispatch(method, params)}
        except _RpcError as e:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": e.code, "message": str(e)}}

    def _dispatch(self, method: str, params: Dict[str, Any]) -> Any:
        if method == "initialize":
            want = params.get("protocolVersion")
            info = params.get("clientInfo") or {}
            if not isinstance(info, dict):
                raise _RpcError(-32602, "clientInfo must be an object")
            client = info.get("name")
            if client and not self.explicit_author and self.session.author.get("author_type") == "human":
                from .util import detect_author
                self.session.author = detect_author(agent=str(client).strip().lower().replace(" ", "-"))
            return {"protocolVersion": want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[1],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "research", "title": "Research Panel", "version": __version__},
                    "instructions": INSTRUCTIONS}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [{"name": t.name, "description": t.description, "inputSchema": t.schema,
                               "annotations": {"readOnlyHint": not t.mutating}} for t in self.tools.values()]}
        if method in ("resources/list", "prompts/list"):
            return {method.split("/")[0]: []}
        if method == "tools/call":
            args = params.get("arguments", {})
            if not isinstance(params.get("name"), str) or not isinstance(args, dict):
                raise _RpcError(-32602, "tools/call needs a string name and object arguments")
            return self._call(params["name"], args)
        raise _RpcError(-32601, f"Method not found: {method}")

    def _call(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        from .util import ResearchError
        tool = self.tools.get(name)
        if not tool:
            raise _RpcError(-32602, f"Unknown tool: {name}")
        try:
            if not self.session.initialized():
                raise ResearchError("No research project here", hint="Run `research init` in the project root.")
            with contextlib.redirect_stdout(sys.stderr):  # stdout belongs to the protocol
                out = api.call(tool.op.name, self.session, args, surface="mcp")
            text = out if isinstance(out, str) else json.dumps(out, indent=1, default=str, ensure_ascii=False)
            return {"content": [{"type": "text", "text": text}], "isError": False}
        except api.BadParams as e:
            return {"content": [{"type": "text", "text": f"Bad arguments for {name}: {e.detail}"}], "isError": True}
        except ResearchError as e:
            text = f"Error (code {e.exit_code}): {e}" + (f"\nHint: {e.hint}" if e.hint else "")
            return {"content": [{"type": "text", "text": text}], "isError": True}
        except Exception as e:  # pragma: no cover
            traceback.print_exc(file=sys.stderr)
            return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}


class _RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def serve(root: str, stdin=None, stdout=None, author: Optional[Dict[str, Any]] = None) -> None:
    stdin = stdin or sys.stdin
    out = stdout or sys.stdout
    server = McpServer(root, author)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            if isinstance(msg, list) and msg:  # JSON-RPC batch (older protocol versions)
                resps = [r for r in (server.handle(m) for m in msg) if r]
                resp = resps if resps else None
            else:
                resp = server.handle(msg)
        if resp is not None:
            out.write(json.dumps(resp, default=str, ensure_ascii=False) + "\n")
            out.flush()


# =============================================================================== install

CLIENTS = ["claude", "vscode", "cursor", "gemini", "agy", "codex"]
# Editor clients are plain JSON files written here; agent CLIs (claude, agy, codex) are written by their provider adapters.
_EDITORS = {"vscode": (".vscode/mcp.json", "servers", lambda argv: {"type": "stdio", "command": argv[0], "args": argv[1:]}),
            "cursor": (".cursor/mcp.json", "mcpServers", lambda argv: {"command": argv[0], "args": argv[1:]}),
            "gemini": (".gemini/settings.json", "mcpServers", lambda argv: {"command": argv[0], "args": argv[1:], "cwd": "."})}


def install(root: str, clients: Optional[List[str]] = None) -> Dict[str, Any]:
    """Register `research mcp` with agent clients (project-level config files). Codex is configured per user: the command to run is
    returned instead. Uses `research` when it is on PATH, otherwise the project-local launcher `.research/bin/research`."""
    from . import providers as PR
    from .providers.base import merge_json, server_cmd
    from .util import ResearchError
    argv = server_cmd(root)
    done: Dict[str, Any] = {}
    for c in clients or ["claude", "vscode"]:
        if c == "codex":
            done[c] = f"run once: codex mcp add research -- {' '.join(argv)}"
        elif c in _EDITORS:
            rel, key, entry = _EDITORS[c]
            done[c] = os.path.relpath(merge_json(os.path.join(root, rel), key, "research", entry(argv)), root)
        elif c in PR.ADAPTERS and PR.ADAPTERS[c].mcp_install:
            done[c] = PR.mcp_install(c, root)["where"]
        else:
            raise ResearchError(f"Unknown client {c!r} (one of {', '.join(CLIENTS)})")
    return {"installed": done, "command": " ".join(argv)}


def status(root: str) -> Dict[str, Any]:
    """Which agent clients have the `research` server registered, and every other server they list. Read-only."""
    spec = [("claude", ".mcp.json", "mcpServers"), ("vscode", ".vscode/mcp.json", "servers"),
            ("cursor", ".cursor/mcp.json", "mcpServers"), ("gemini", ".gemini/settings.json", "mcpServers")]
    clients, others = [], []
    for name, rel, key in spec:
        path, servers, err = os.path.join(root, rel), {}, None
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    servers = (json.load(f) or {}).get(key) or {}
            except (ValueError, OSError) as e:
                err = f"unreadable: {e}"
        clients.append({"client": name, "file": rel, "exists": os.path.isfile(path), "registered": "research" in servers, "error": err})
        others += [{"client": name, "name": n, "command": " ".join([str(s.get("command") or s.get("url") or "")] + [str(a) for a in s.get("args") or []]).strip()}
                   for n, s in servers.items() if n != "research" and isinstance(s, dict)]
    return {"clients": clients, "others": others, "codex": "per-user: `codex mcp add research -- research mcp`"}
