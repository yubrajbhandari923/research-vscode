"""MCP server: the research tools for any MCP-capable agent (Claude Code, Codex, Copilot, Cursor, Gemini, …).

`research mcp` speaks the Model Context Protocol over stdio (newline-delimited JSON-RPC 2.0), standard library only.
`research mcp install` registers it in .mcp.json (Claude Code), .vscode/mcp.json (VS Code), .cursor/mcp.json and
.gemini/settings.json; for Codex it prints the `codex mcp add` command.

Every tool is a thin wrapper over the same core functions as the CLI, so policy (run budget, verification gates,
task claims) applies identically. Tool errors come back as `isError` results with the hint, so agents can react.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import traceback
from typing import Any, Callable, Dict, List, Optional

from . import __version__

PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]

INSTRUCTIONS = """Research memory and coordination for this project. Start with research_current (project state),
then task_next / task_context for your work. Register an experiment before computing; every run belongs to one
(run_start). Record results as findings with evidence; they stay preliminary until an independent review
(finding_review). task_done runs the task's checks. Errors with code 3 are policy blocks: read the hint."""

S = {"type": "string"}
I = {"type": "integer"}
N = {"type": "number"}
B = {"type": "boolean"}
L = {"type": "array", "items": {"type": "string"}}
O = {"type": "object"}


class Tool:
    def __init__(self, name: str, description: str, props: Dict[str, Any], required: List[str],
                 fn: Callable[..., Any], mutating: bool = True):
        self.name, self.description, self.fn, self.mutating = name, description, fn, mutating
        self.schema = {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def _tools() -> List[Tool]:
    from . import agents as A
    from . import compare as CMP
    from . import context as C
    from . import plans as P
    from . import runs as R
    from .search import search as _search_records
    from . import services as SV
    from . import skills as SK
    from . import verification as V
    from . import views as VW

    def current(p):
        C.write_current_md(p)
        return C.render_current_md(p)

    def task_ctx(p, id):
        c = P.task_context(p, id)
        return A.task_brief_md(c)

    def run_status(p, id):
        r = R.reconcile_run(p, id, force=True)
        d = VW.run_detail(p, r["id"])
        out = {k: d.get(k) for k in ("id", "experiment_id", "status", "exit_code", "started_at", "ended_at",
                                     "duration_s", "label", "command", "metrics", "slurm_job_id", "run_dir")}
        out["artifacts"] = [{"id": a["id"], "path": a["path"]} for a in d["artifacts"]]
        return out

    def run_logs(p, id, stream="stdout", tail=80):
        r = R.get_run(p, id)
        path = p.abspath(r["stderr_path"] if stream == "stderr" else r["stdout_path"])
        if not path or not os.path.exists(path):
            return "(no log yet)"
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        return "\n".join(lines[-int(tail):])

    def checkpoint(p, understanding=None, problem=None, next=None, commit=False):
        c = C.create_checkpoint(p, understanding=understanding, current_problem=problem, next_experiment=next,
                                commit=commit)
        return {"id": c["id"], "path": c.get("path"), "snapshot_commit": c.get("snapshot_commit")}

    return [
        Tool("research_current", "Project state for agents: goal, checkpoint, beliefs, failures, experiments, active plans "
             "and their next tasks, attention items, skills, policy. Read this first.", {}, [], current, False),
        Tool("research_search", "Keyword search over questions, experiments, runs, findings, decisions, plans, tasks, "
             "syntheses, notes and skills.", {"query": S, "type": S, "limit": I}, ["query"],
             lambda p, query, type=None, limit=20: _search_records(p, query, type, limit), False),
        Tool("research_show", "Full detail of any id (Q-, EXP-, RUN-, F-, D-, CP-, A-, PLAN-, T-, note:…).", {"id": S}, ["id"],
             lambda p, id: VW.show(p, id), False),
        Tool("task_next", "The next ready task (todo, dependencies done, unclaimed), optionally within one plan.",
             {"plan": S}, [], lambda p, plan=None: P.get_next_ready_task(p, plan), False),
        Tool("task_context", "Focused brief for one task: goal, acceptance criteria, checks, plan objective, "
             "dependency results, related experiment state, skills to load, progress notes, rules.", {"id": S}, ["id"],
             task_ctx, False),
        Tool("task_start", "Claim a task and mark it running. Fails if another agent holds it.",
             {"id": S, "force": B}, ["id"], lambda p, id, force=False: P.start_task(p, id, force=force)),
        Tool("task_note", "Append a progress note to a task (the hand-off trail for whoever continues).",
             {"id": S, "text": S}, ["id", "text"], lambda p, id, text: P.add_task_note(p, id, text)),
        Tool("task_done", "Complete a task. Runs its checks first; failing checks block completion.",
             {"id": S, "result": S, "artifacts": L}, ["id"],
             lambda p, id, result=None, artifacts=None: P.complete_task(p, id, result=result, artifacts=artifacts)),
        Tool("task_block", "Mark a task blocked, with the reason.", {"id": S, "reason": S}, ["id", "reason"],
             lambda p, id, reason: P.block_task(p, id, reason)),
        Tool("task_release", "Give a claimed task back (status todo) with an optional hand-off note.",
             {"id": S, "note": S}, ["id"], lambda p, id, note=None: P.release_task(p, id, note)),
        Tool("task_verify", "Run a task's checks (commands, files, metric thresholds) and record the result.",
             {"id": S}, ["id"], lambda p, id: {k: v for k, v in V.verify_task(p, id).items() if k != "task"}),
        Tool("task_create", "Add a task to a plan. checks: 'cmd:pytest -q', 'file:out/plot.png', "
             "'metric:EXP-001:rmse<=0.05'. skills: project skill names.",
             {"plan": S, "title": S, "goal": S, "type": S, "role": S, "depends_on": L, "acceptance_criteria": S,
              "checks": L, "skills": L, "experiment": S, "question": S, "inputs": S, "expected_outputs": S},
             ["plan", "title"],
             lambda p, plan, title, goal=None, type=None, role=None, depends_on=None, acceptance_criteria=None, checks=None,
             skills=None, experiment=None, question=None, inputs=None, expected_outputs=None: P.create_task(
                 p, plan, title, goal=goal, task_type=type, assigned_role=role, depends_on=depends_on,
                 acceptance_criteria=acceptance_criteria, checks=checks, skills=skills, related_experiment_id=experiment,
                 related_question_id=question, inputs=inputs, expected_outputs=expected_outputs)),
        Tool("plan_create", "Create a plan (a research objective that tasks break down).",
             {"title": S, "objective": S, "success_criteria": S, "question": S, "skills": L}, ["title"],
             lambda p, title, objective=None, success_criteria=None, question=None, skills=None: P.create_plan(
                 p, title, objective=objective, success_criteria=success_criteria, question=question, skills=skills)),
        Tool("question_create", "Create a research question.", {"title": S, "description": S, "parent": S}, ["title"],
             lambda p, title, description=None, parent=None: SV.create_question(p, title, description, parent)),
        Tool("experiment_create", "Register an experiment BEFORE running it: hypothesis, parameters (lists = sweep), "
             "metrics, planned runs, success criteria, stop conditions.",
             {"title": S, "question": S, "hypothesis": S, "motivation": S, "method": S, "parameters": O, "metrics": L,
              "planned_runs": I, "success_criteria": S, "stop_conditions": S}, ["title"],
             lambda p, title, **kw: SV.create_experiment(p, title, **kw)),
        Tool("run_start", "Start a tracked run of an experiment in the background (returns the run id; poll run_status). "
             "Use metric_log / artifact_register or research.log_metric() inside the code.",
             {"experiment": S, "command": S, "parameters": O, "label": S, "cwd": S}, ["experiment", "command"],
             lambda p, experiment, command, parameters=None, label=None, cwd=None: R.exec_run(
                 p, experiment, command, detach=True, parameters=parameters, label=label,
                 working_dir=os.path.join(p.root, cwd) if cwd else p.root, tee=False)),
        Tool("run_status", "Status, exit code, metrics and artifacts of a run (refreshes from the backend).",
             {"id": S}, ["id"], run_status, False),
        Tool("run_logs", "Tail of a run's stdout or stderr.", {"id": S, "stream": {"type": "string", "enum": ["stdout", "stderr"]},
                                                               "tail": I}, ["id"], run_logs, False),
        Tool("runs_compare", "Side by side: parameter and metric differences, code changes, artifacts of two runs.",
             {"a": S, "b": S}, ["a", "b"], lambda p, a, b: CMP.compare_runs(p, a, b), False),
        Tool("metric_log", "Log a metric on a run or an experiment.",
             {"name": S, "value": {"type": ["number", "string"]}, "run": S, "experiment": S, "step": I, "unit": S},
             ["name", "value"], lambda p, name, value, run=None, experiment=None, step=None, unit=None: R.log_metric(
                 p, name, value, step, unit, run=run, experiment=experiment)),
        Tool("artifact_register", "Register a file or folder (by reference, never copied) on a run or experiment.",
             {"path": S, "run": S, "experiment": S, "description": S}, ["path"],
             lambda p, path, run=None, experiment=None, description=None: R.register_artifact(
                 p, path, run=run, experiment=experiment, description=description)),
        Tool("experiment_synthesize", "Interpret an experiment's finished runs. mark: completed | failed (optional).",
             {"id": S, "what_happened": S, "what_worked": S, "what_failed": S, "interpretation": S, "limitations": S,
              "unresolved": S, "next_experiment": S, "mark": S}, ["id"],
             lambda p, id, **kw: SV.synthesize(p, id, **kw)),
        Tool("finding_create", "Record a reusable result with evidence ids (EXP-/RUN-/A-/F-). kind: result | failure. "
             "Stays preliminary until an independent review.",
             {"title": S, "statement": S, "kind": S, "confidence": S, "supports": L, "contradicts": L, "questions": L,
              "limitations": S}, ["title"],
             lambda p, title, statement=None, kind="result", confidence=None, supports=None, contradicts=None,
             questions=None, limitations=None: SV.create_finding(
                 p, title, statement, kind, "preliminary", confidence, supports, contradicts, None, questions, limitations)),
        Tool("finding_review", "Independent review of a finding: verdict supported | contradicted | needs_work, with "
             "reasoning. You can't review findings you wrote.", {"id": S, "verdict": S, "notes": S, "confidence": S},
             ["id", "verdict"], lambda p, id, verdict, notes=None, confidence=None: V.review_finding(p, id, verdict, notes, confidence)),
        Tool("checkpoint_create", "Snapshot the project's understanding (pre-filled from current state). "
             "commit=true also commits .research/ to git.", {"understanding": S, "problem": S, "next": S, "commit": B},
             [], checkpoint),
        Tool("skills_list", "Project skills (name, description, always-on, applies_to).", {}, [],
             lambda p: [{k: s[k] for k in ("name", "description", "always", "applies_to")} for s in SK.list_skills(p)], False),
        Tool("skill_show", "The full SKILL.md of a project skill. Load the skills a task lists before working on it.",
             {"name": S}, ["name"], lambda p, name: SK.get_skill(p, name)["markdown"], False),
        Tool("dispatch_prepare", "Prepare a brief for another agent role (planner|implementer|verifier|analyst) on a "
             "target id or objective; returns the command a human can run.", {"role": S, "target": S, "objective": S, "profile": S},
             [], lambda p, role=None, target=None, objective=None, profile=None: A.dispatch(p, role, target, objective, profile)),
    ]


class McpServer:
    def __init__(self, root: str, author: Optional[Dict[str, Any]] = None):
        from .rpc import Server
        self.srv = Server(root)
        self.explicit_author = author is not None
        if author:
            self.srv.author = author
        self.tools = {t.name: t for t in _tools()}

    def handle(self, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if mid is None:  # notification (e.g. notifications/initialized): never answered
            return None
        try:
            return {"jsonrpc": "2.0", "id": mid, "result": self._dispatch(method, params)}
        except _RpcError as e:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": e.code, "message": str(e)}}

    def _dispatch(self, method: str, params: Dict[str, Any]) -> Any:
        if method == "initialize":
            want = params.get("protocolVersion")
            client = (params.get("clientInfo") or {}).get("name")
            if client and not self.explicit_author and self.srv.author.get("author_type") == "human":
                from .util import detect_author
                self.srv.author = detect_author(agent=str(client).strip().lower().replace(" ", "-"))
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
            return self._call(params.get("name"), params.get("arguments") or {})
        raise _RpcError(-32601, f"Method not found: {method}")

    def _call(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        from . import context as C
        from .util import ResearchError
        tool = self.tools.get(name)
        if not tool:
            raise _RpcError(-32602, f"Unknown tool: {name}")
        try:
            if not self.srv.initialized():
                raise ResearchError("No research project here", hint="Run `research init` in the project root.")
            p = self.srv.project()
            p.author = self.srv.author
            with contextlib.redirect_stdout(sys.stderr):  # stdout belongs to the protocol
                out = tool.fn(p, **args)
                if tool.mutating:
                    try:
                        C.write_current_md(p)
                    except Exception:
                        pass
            text = out if isinstance(out, str) else json.dumps(out, indent=1, default=str, ensure_ascii=False)
            return {"content": [{"type": "text", "text": text}], "isError": False}
        except ResearchError as e:
            text = f"Error (code {e.exit_code}): {e}" + (f"\nHint: {e.hint}" if e.hint else "")
            return {"content": [{"type": "text", "text": text}], "isError": True}
        except TypeError as e:
            return {"content": [{"type": "text", "text": f"Bad arguments for {name}: {e}"}], "isError": True}
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
            if isinstance(msg, list):  # JSON-RPC batch (older protocol versions)
                resps = [r for r in (server.handle(m) for m in msg) if r]
                resp = resps if resps else None
            else:
                resp = server.handle(msg)
        if resp is not None:
            out.write(json.dumps(resp, default=str, ensure_ascii=False) + "\n")
            out.flush()


# =============================================================================== install

def _merge_json(path: str, key: str, name: str, entry: Dict[str, Any]) -> str:
    data: Dict[str, Any] = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            try:
                data = json.load(f)
            except ValueError:
                raise ValueError(f"{path} is not valid JSON; fix or remove it first")
    data.setdefault(key, {})[name] = entry
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    return path


CLIENTS = ["claude", "vscode", "cursor", "gemini", "codex"]


def install(root: str, clients: Optional[List[str]] = None, command: Optional[str] = None) -> Dict[str, Any]:
    """Register `research mcp` with agent clients (project-level config files). Codex is configured per user:
    the command to run is returned instead. Uses `research` when it is on PATH, otherwise the project-local
    launcher `.research/bin/research` (created if missing)."""
    import shutil
    from .util import ResearchError
    if not command:
        if shutil.which("research"):
            command = "research"
        else:
            from . import localbin
            command = localbin.install(root)["path"]
    clients = clients or ["claude", "vscode"]
    done: Dict[str, Any] = {}
    entry = {"command": command, "args": ["mcp"]}
    for c in clients:
        try:
            if c == "claude":
                done[c] = os.path.relpath(_merge_json(os.path.join(root, ".mcp.json"), "mcpServers", "research",
                                                      {"type": "stdio", **entry}), root)
            elif c == "vscode":
                done[c] = os.path.relpath(_merge_json(os.path.join(root, ".vscode", "mcp.json"), "servers", "research",
                                                      {"type": "stdio", **entry}), root)
            elif c == "cursor":
                done[c] = os.path.relpath(_merge_json(os.path.join(root, ".cursor", "mcp.json"), "mcpServers", "research", entry), root)
            elif c == "gemini":
                done[c] = os.path.relpath(_merge_json(os.path.join(root, ".gemini", "settings.json"), "mcpServers", "research",
                                                      {**entry, "cwd": "."}), root)
            elif c == "codex":
                done[c] = f"run once: codex mcp add research -- {command} mcp"
            else:
                raise ResearchError(f"Unknown client {c!r} (one of {', '.join(CLIENTS)})")
        except ValueError as e:
            raise ResearchError(str(e))
    return {"installed": done, "command": f"{command} mcp"}
