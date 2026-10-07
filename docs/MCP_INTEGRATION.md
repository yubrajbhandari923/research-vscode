# MCP Integration for Research OS

**Status:** Recommended for Phase 2
**Priority:** High (enables agent portability)
**Effort:** ~3 days implementation + 1 day testing

---

## Executive Summary

✅ **Recommendation: Implement MCP server as interface layer to Research OS tools**

**Why:**
- Material improvement in agent portability (Claude Code, Cursor, any MCP client)
- Lightweight Python SDK available
- Non-invasive (MCP server is one additional interface, doesn't change core)
- Can be disabled/removed without affecting Research OS functionality
- Aligns with Research OS principle: tools > prompts

**What it enables:**
- Any MCP-compatible agent can use Research OS tools immediately
- Tools are discoverable (no need to paste full docs into prompts)
- Provider-agnostic (not locked to Claude/OpenAI/etc.)

---

## 1. What is MCP?

### 1.1 Model Context Protocol Overview

MCP is an open protocol created by Anthropic for:
1. **Tool exposure** — servers provide callable functions to LLMs
2. **Resource access** — servers expose readable resources (files, data)
3. **Prompt templates** — reusable prompts

**Transport:** stdio (default), HTTP, or SSE

**Key insight:** MCP separates "what tools exist" from "how to call them"

Agents query:
```json
// Discovery
→ tools/list
← {
  "tools": [
    {
      "name": "research.task.context",
      "description": "Get focused context for a task",
      "inputSchema": {...}
    }
  ]
}

// Invocation
→ tools/call {
  "name": "research.task.context",
  "arguments": {"task_id": "T-042"}
}
← {
  "content": [{"type": "text", "text": "..."}]
}
```

### 1.2 MCP for Research OS

**Perfect fit because:**
- Research OS tools are **already deterministic** (`run_experiment`, `register_artifact`, etc.)
- Research OS has **clear tool boundaries** (no magic, just functions)
- Research OS needs **multi-provider support** (Claude, OpenAI, local, etc.)

**MCP provides:**
- Standard protocol → any MCP client works
- Tool discovery → agents don't need full docs
- Structured I/O → typed schemas enforce correct usage

---

## 2. Architecture

### 2.1 Current Architecture

```
Agent (Claude Code)
  ↓ (detect via env var)
Python Core
  ├─ CLI
  ├─ Python API
  ├─ RPC (VS Code)
  └─ ...operates on .research/ storage
```

### 2.2 Architecture with MCP

```
┌─────────────────────────────────────┐
│   Research OS Core (Python)         │
│                                     │
│   Plans · Tasks · Tools             │
│   Questions · Experiments · Runs    │
│   Findings · Decisions              │
└───────┬─────────────────────────────┘
        │
  ┌─────┴──────┬──────────┬───────────┬──────────┐
  │            │          │           │          │
  ▼            ▼          ▼           ▼          ▼
CLI         Python     VS Code     MCP       Direct
            API        (RPC)      Server     Function
                                    │
                                    ▼
                    ┌───────────────────────────┐
                    │   MCP Clients             │
                    ├───────────────────────────┤
                    │ - Claude Code             │
                    │ - Cursor                  │
                    │ - Claude Desktop          │
                    │ - OpenAI agents (future)  │
                    │ - Any MCP-compatible tool │
                    └───────────────────────────┘
```

**Key point:** MCP server is **one more interface** to the same core. CLI, Python API, VS Code UI continue to work unchanged.

---

## 3. Implementation

### 3.1 MCP Python SDK

Anthropic provides official Python SDK:

```bash
pip install mcp
```

**Size:** ~50KB
**Dependencies:** pydantic, httpx (optional), anyio
**License:** MIT

**API:**
```python
from mcp.server import Server
from mcp.server.stdio import stdio_server

server = Server("research-os")

@server.list_tools()
async def list_tools():
    return [...]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    return [...]

# Run
async def main():
    async with stdio_server() as streams:
        await server.run(streams[0], streams[1])
```

### 3.2 Research OS MCP Server Implementation

Create `packages/core/research/mcp_server.py`:

```python
"""MCP server for Research OS tools."""
import asyncio
import json
from typing import Any, Dict, List

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from . import (
    create_plan,
    create_task,
    get_next_ready_task,
    create_experiment,
    exec_run,
    submit_run,
    register_artifact,
    log_metric,
    create_finding,
    create_decision,
    create_checkpoint,
)
from .context import task_context, plan_current
from .store import Project
from .tools import TOOLS  # Tool registry from Phase 2


class ResearchMCPServer:
    """MCP server exposing Research OS tools."""

    def __init__(self, project_root: str = None):
        self.server = Server("research-os")
        self.project_root = project_root
        self._setup_handlers()

    def _setup_handlers(self):
        """Register MCP handlers."""

        @self.server.list_tools()
        async def list_tools() -> List[Tool]:
            """List available Research OS tools."""
            return [
                Tool(
                    name=name,
                    description=spec["description"],
                    inputSchema={
                        "type": "object",
                        "properties": self._params_to_schema(spec["params"]),
                        "required": [
                            k for k, v in spec["params"].items()
                            if not k.endswith("?")
                        ],
                    },
                )
                for name, spec in TOOLS.items()
            ]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict) -> List[TextContent]:
            """Execute a Research OS tool."""
            try:
                result = await self._execute_tool(name, arguments)
                return [
                    TextContent(
                        type="text",
                        text=json.dumps(result, indent=2, ensure_ascii=False)
                    )
                ]
            except Exception as e:
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({
                            "error": str(e),
                            "tool": name,
                            "arguments": arguments
                        }, indent=2)
                    )
                ]

        @self.server.list_resources()
        async def list_resources():
            """List available resources (context, plans, tasks, etc.)."""
            return [
                {
                    "uri": "plan://current",
                    "name": "Current Research State",
                    "description": "Current plan, tasks, and understanding",
                    "mimeType": "text/markdown"
                },
                {
                    "uri": "task://next",
                    "name": "Next Ready Task",
                    "description": "Next task that is ready to execute",
                    "mimeType": "application/json"
                },
            ]

        @self.server.read_resource()
        async def read_resource(uri: str) -> str:
            """Read a resource."""
            if uri == "plan://current":
                from .context import plan_current_md
                return plan_current_md(self._get_project())

            if uri == "task://next":
                task = get_next_ready_task(project=self._get_project())
                return json.dumps(task, indent=2) if task else "{}"

            if uri.startswith("task://"):
                task_id = uri.split("//")[1].split("/")[0]
                ctx = task_context(self._get_project(), task_id)
                return json.dumps(ctx, indent=2)

            raise ValueError(f"Unknown resource: {uri}")

    async def _execute_tool(self, name: str, arguments: dict) -> Dict[str, Any]:
        """Execute a tool by name."""
        project = self._get_project()

        # Map tool names to functions
        # This would be automated in real implementation via tool registry
        tool_map = {
            "get_task_context": lambda: task_context(project, arguments["task_id"]),
            "get_plan_current": lambda: plan_current(project),
            "get_next_ready_task": lambda: get_next_ready_task(
                project=project,
                plan_id=arguments.get("plan_id")
            ),

            "create_plan": lambda: create_plan(
                project=project,
                **arguments
            ),
            "create_task": lambda: create_task(
                project=project,
                **arguments
            ),
            "create_experiment": lambda: create_experiment(
                project=project,
                **arguments
            ),

            "run_experiment": lambda: exec_run(
                project=project,
                experiment=arguments["experiment_id"],
                command=arguments["command"],
                parameters=arguments.get("parameters"),
                label=arguments.get("label")
            ),

            "submit_slurm": lambda: submit_run(
                project=project,
                experiment=arguments["experiment_id"],
                command=arguments["command"],
                time=arguments.get("time", "01:00:00"),
                gpus=arguments.get("gpus", 1),
                partition=arguments.get("partition")
            ),

            "register_artifact": lambda: register_artifact(
                project=project,
                path=arguments["path"],
                run=arguments.get("run_id"),
                experiment=arguments.get("experiment_id"),
                description=arguments.get("description", "")
            ),

            "log_metric": lambda: log_metric(
                project=project,
                name=arguments["name"],
                value=arguments["value"],
                run=arguments.get("run_id"),
                experiment=arguments.get("experiment_id"),
                step=arguments.get("step"),
                unit=arguments.get("unit")
            ),

            "create_finding": lambda: create_finding(
                project=project,
                title=arguments["title"],
                statement=arguments.get("statement"),
                supports=arguments.get("supports", []),
                confidence=arguments.get("confidence", "medium"),
                limitations=arguments.get("limitations")
            ),

            "create_decision": lambda: create_decision(
                project=project,
                statement=arguments["statement"],
                reason=arguments.get("reason"),
                supporting_findings=arguments.get("findings", [])
            ),

            "create_checkpoint": lambda: create_checkpoint(
                project=project,
                **arguments
            ),

            "verify_tests": lambda: self._verify_tests(arguments["test_path"]),
            "compare_runs": lambda: self._compare_runs(project, arguments),
        }

        if name not in tool_map:
            raise ValueError(f"Unknown tool: {name}")

        # Execute in thread pool (Research OS is sync)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, tool_map[name])

    def _get_project(self) -> Project:
        """Get project instance."""
        return Project.find(self.project_root)

    def _params_to_schema(self, params: Dict[str, str]) -> Dict[str, Any]:
        """Convert simple param spec to JSON Schema."""
        schema = {}
        for name, typ in params.items():
            is_optional = name.endswith("?")
            clean_name = name.rstrip("?")

            # Map simple types to JSON Schema
            type_map = {
                "str": {"type": "string"},
                "int": {"type": "integer"},
                "bool": {"type": "boolean"},
                "list[str]": {"type": "array", "items": {"type": "string"}},
                "dict": {"type": "object"},
            }

            schema[clean_name] = type_map.get(typ, {"type": "string"})

        return schema

    def _verify_tests(self, test_path: str) -> Dict[str, Any]:
        """Run tests and return pass/fail."""
        import subprocess
        result = subprocess.run(
            ["pytest", test_path, "-v"],
            capture_output=True,
            text=True
        )
        return {
            "passed": result.returncode == 0,
            "exit_code": result.returncode,
            "summary": result.stdout.split("\n")[-2] if result.stdout else ""
        }

    def _compare_runs(self, project: Project, args: dict) -> Dict[str, Any]:
        """Compare metrics across runs."""
        from .views import compare_runs
        return compare_runs(
            project,
            run_ids=args["run_ids"],
            metrics=args.get("metrics", [])
        )

    async def run(self):
        """Run the MCP server on stdio."""
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options()
            )


async def main(project_root: str = None):
    """Entry point for MCP server."""
    server = ResearchMCPServer(project_root)
    await server.run()


if __name__ == "__main__":
    import sys
    root = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(main(root))
```

### 3.3 CLI Integration

Add to `packages/cli/research_cli/main.py`:

```python
def cmd_mcp(args):
    """Start MCP server."""
    import asyncio
    from research.mcp_server import main

    print("Starting Research OS MCP server on stdio...", file=sys.stderr)
    print("Server name: research-os", file=sys.stderr)
    print("Connect with any MCP-compatible client", file=sys.stderr)

    asyncio.run(main(args.root))

# In main parser:
mcp_parser = subparsers.add_parser(
    "mcp",
    help="Start MCP server for agent tool access"
)
mcp_parser.add_argument(
    "serve",
    nargs="?",
    default="serve",
    help="Start server on stdio"
)
mcp_parser.set_defaults(func=cmd_mcp)
```

Usage:
```bash
research mcp serve
```

### 3.4 MCP Client Configuration

**Claude Desktop config** (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "research-os": {
      "command": "research",
      "args": ["mcp", "serve"],
      "cwd": "/path/to/research/project"
    }
  }
}
```

**Claude Code config** (`.claude/mcp.json`):

```json
{
  "mcpServers": {
    "research-os": {
      "command": "research",
      "args": ["mcp", "serve"]
    }
  }
}
```

---

## 4. Tool Exposure via MCP

### 4.1 Example: Task Context Tool

**Tool definition:**
```json
{
  "name": "get_task_context",
  "description": "Get focused context for a specific research task, including goal, dependencies, inputs, outputs, acceptance criteria, and relevant findings",
  "inputSchema": {
    "type": "object",
    "properties": {
      "task_id": {
        "type": "string",
        "description": "Task ID (e.g., T-042)"
      }
    },
    "required": ["task_id"]
  }
}
```

**Agent usage:**
```
Agent: I need context for my current task

→ tools/call {
  "name": "get_task_context",
  "arguments": {"task_id": "T-042"}
}

← {
  "content": [{
    "type": "text",
    "text": "{
      \"task\": {
        \"id\": \"T-042\",
        \"title\": \"Implement baseline\",
        \"goal\": \"Working baseline model\",
        \"acceptance_criteria\": \"Tests pass, model loads\",
        ...
      },
      \"dependencies\": [
        {\"id\": \"T-001\", \"status\": \"done\", ...}
      ],
      \"skill\": \"implement-ml-model\",
      \"available_tools\": [...]
    }"
  }]
}

Agent: [Reads context, proceeds with implementation]
```

### 4.2 Example: Run Experiment Tool

**Tool definition:**
```json
{
  "name": "run_experiment",
  "description": "Execute an experiment run with full provenance tracking",
  "inputSchema": {
    "type": "object",
    "properties": {
      "experiment_id": {
        "type": "string",
        "description": "Experiment ID (e.g., EXP-012)"
      },
      "command": {
        "type": "string",
        "description": "Shell command to execute"
      },
      "parameters": {
        "type": "object",
        "description": "Run parameters (e.g., {\"alpha\": 0.1})"
      },
      "label": {
        "type": "string",
        "description": "Short label for this run"
      }
    },
    "required": ["experiment_id", "command"]
  }
}
```

**Agent usage:**
```
Agent: Running baseline sanity check

→ tools/call {
  "name": "run_experiment",
  "arguments": {
    "experiment_id": "EXP-012",
    "command": "python train.py --epochs 1 --subjects 1",
    "parameters": {"epochs": 1, "subjects": 1},
    "label": "sanity"
  }
}

← {
  "content": [{
    "type": "text",
    "text": "{
      \"run_id\": \"RUN-0045\",
      \"status\": \"completed\",
      \"exit_code\": 0,
      \"duration_s\": 42.3
    }"
  }]
}

Agent: Sanity run completed successfully (RUN-0045)
```

### 4.3 Example: Resource Access

**Resources** (read-only context):

```
plan://current          → Current plan state (Markdown)
task://T-042/context    → Full task context (JSON)
task://next             → Next ready task (JSON)
experiment://EXP-012    → Experiment detail (JSON)
finding://F-023         → Finding with evidence (JSON)
```

**Agent usage:**
```
Agent: What's the current research state?

→ resources/read {
  "uri": "plan://current"
}

← {
  "contents": [{
    "uri": "plan://current",
    "mimeType": "text/markdown",
    "text": "# Current Research State\n\n## Goal\n..."
  }]
}

Agent: [Reads current state, understands context]
```

---

## 5. Benefits

### 5.1 Agent Portability

**Before MCP:**
```python
# Hardcoded detection
if os.environ.get("CLAUDECODE"):
    agent = "claude-code"
# Agent must know Research OS API
research.create_finding(...)
```

**After MCP:**
```
Any MCP client:
  → tools/list
  ← [available tools]

  → tools/call {"name": "create_finding", ...}
  ← [result]
```

No custom integration per agent. Any MCP client works.

### 5.2 Discovery > Documentation

**Before:**
Agents need AGENTS.md (1800 tokens) explaining every API.

**After:**
```
→ tools/list
← {
  "tools": [
    {
      "name": "get_task_context",
      "description": "Get context for task",
      "inputSchema": {...}
    },
    ...
  ]
}
```

Agent discovers capabilities dynamically. Documentation is in schemas.

### 5.3 Typed I/O

**Before:**
Agent guesses parameter names/types from prose.

**After:**
```json
"inputSchema": {
  "type": "object",
  "properties": {
    "task_id": {"type": "string"},
    "parameters": {"type": "object"}
  },
  "required": ["task_id"]
}
```

Schema enforcement prevents malformed calls.

---

## 6. Risks & Mitigation

### 6.1 Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| MCP SDK breaks | LOW | Pin version, MCP is stable |
| Performance overhead (async wrapper) | LOW | Research OS calls are fast (<100ms) |
| Debugging MCP protocol harder than direct calls | MEDIUM | Keep CLI/Python API for debugging |
| MCP evolves incompatibly | LOW | MCP is open standard, Anthropic committed |

### 6.2 Fallback Plan

If MCP proves problematic:
1. MCP server is in `mcp_server.py` — delete file
2. Remove `pip install mcp` dependency
3. CLI, Python API, VS Code UI continue working
4. Loss: agent portability (back to hardcoded detection)

**No core logic affected.** MCP is interface only.

---

## 7. Testing

### 7.1 MCP Server Tests

```python
# tests/python/test_mcp.py

import pytest
import json
from research.mcp_server import ResearchMCPServer


@pytest.mark.asyncio
async def test_mcp_list_tools(tmp_project):
    """Test MCP tool discovery."""
    server = ResearchMCPServer(tmp_project.root)

    # Mock stdio call
    tools = await server.server._handlers["list_tools"]()

    assert len(tools) > 0
    assert any(t.name == "get_task_context" for t in tools)
    assert any(t.name == "create_finding" for t in tools)


@pytest.mark.asyncio
async def test_mcp_call_tool(tmp_project):
    """Test MCP tool execution."""
    server = ResearchMCPServer(tmp_project.root)

    # Create plan/task
    import research
    plan = research.create_plan(title="Test", project=tmp_project)
    task = research.create_task(
        plan_id=plan["id"],
        title="Test task",
        project=tmp_project
    )

    # Call via MCP
    result = await server._execute_tool(
        "get_task_context",
        {"task_id": task["id"]}
    )

    assert result["task"]["id"] == task["id"]
    assert result["task"]["title"] == "Test task"


@pytest.mark.asyncio
async def test_mcp_resource_access(tmp_project):
    """Test MCP resource reading."""
    server = ResearchMCPServer(tmp_project.root)

    # Read current plan state
    content = await server.server._handlers["read_resource"]("plan://current")

    assert isinstance(content, str)
    assert "Goal" in content or "No active plan" in content
```

### 7.2 Integration Test with Mock Client

```python
# tests/python/test_mcp_integration.py

import pytest
import asyncio
import json
from research.mcp_server import main


@pytest.mark.asyncio
async def test_mcp_stdio_protocol(tmp_project):
    """Test full MCP stdio protocol."""
    # This would use a mock stdio transport
    # to verify request/response cycle

    # Mock client sends:
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list"
    }

    # Server responds:
    # response = {...}

    # Assert valid MCP protocol
    pass  # Full test requires MCP test harness
```

---

## 8. Documentation

### 8.1 User Documentation

**README.md section:**

```markdown
### Using Research OS with MCP-Compatible Agents

Research OS provides an MCP server for tool access:

```bash
# Start MCP server
research mcp serve
```

**Configure in Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "research-os": {
      "command": "research",
      "args": ["mcp", "serve"],
      "cwd": "/path/to/your/research/project"
    }
  }
}
```

**Configure in Claude Code** (`.claude/mcp.json`):

```json
{
  "mcpServers": {
    "research-os": {
      "command": "research",
      "args": ["mcp", "serve"]
    }
  }
}
```

Now any MCP-compatible agent can access Research OS tools:
- Get task context
- Create plans and tasks
- Run experiments
- Register artifacts
- Create findings
- And more...
```

### 8.2 Developer Documentation

Create `docs/MCP.md`:

```markdown
# MCP Server Implementation

## Architecture

Research OS MCP server (`packages/core/research/mcp_server.py`) exposes
Research OS tools via the Model Context Protocol.

## Adding a New Tool

1. Add to `packages/core/research/tools.py`:
```python
TOOLS["my_tool"] = {
    "description": "What it does",
    "params": {"arg1": "str", "arg2?": "int"},
    "returns": "dict",
    "side_effects": True/False
}
```

2. Add handler in `mcp_server.py`:
```python
"my_tool": lambda: my_function(project, **arguments)
```

3. Test:
```bash
pytest tests/python/test_mcp.py -k my_tool
```

## Debugging

Use MCP inspector:
```bash
npx @modelcontextprotocol/inspector research mcp serve
```
```

---

## 9. Timeline

### Week 1 (Phase 2):
- Day 1: Implement `mcp_server.py` skeleton
- Day 2: Add core tools (task context, run experiment, etc.)
- Day 3: Add resource access (plan://current, task://T-001/context)
- Day 4: Testing (unit + integration)
- Day 5: Documentation + Claude Desktop integration test

---

## 10. Success Criteria

MCP integration is successful when:

1. ✅ Claude Desktop can discover Research OS tools
2. ✅ Claude Desktop can execute tools successfully
3. ✅ Tools create valid database records
4. ✅ Resource access returns current research state
5. ✅ Error handling works (invalid tool, missing params)
6. ✅ All tests pass
7. ✅ Documentation complete
8. ✅ Works with both Claude Desktop and Claude Code

**If MCP fails:** Remove `mcp_server.py`, continue with CLI/API. No core functionality lost.

---

## 11. Alternative: Direct Python API

If MCP proves problematic, fallback to:

**Option A: Custom protocol (current approach)**
```python
# Hardcoded agent detection
if os.environ.get("CLAUDECODE"):
    ...
```

**Option B: Plugin system**
```python
# Agent registers itself
research.agents.register(
    name="my-agent",
    adapter=MyAgentAdapter()
)
```

**MCP is preferred because:**
- Open standard (not proprietary to Research OS)
- Multiple implementations (Python, TypeScript, Rust)
- Wide adoption (Claude, OpenAI compatibility layer, etc.)
- Tool discovery built-in
- No custom protocol maintenance

---

## Conclusion

✅ **Implement MCP server in Phase 2**

**Rationale:**
- Low risk (interface layer, can be removed)
- High value (agent portability, tool discovery)
- Lightweight implementation (~300 lines)
- Aligns with Research OS architecture (tools > prompts)
- Enables provider-neutral agent ecosystem

**If timeline is tight:** Defer to Phase 3, implement CLI/API first.

**If MCP fails:** Remove easily, no core impact.
