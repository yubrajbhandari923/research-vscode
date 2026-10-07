# Agent instructions — research-vscode

<!-- research:begin — managed by `research init`; edit outside these markers freely -->
## Research OS

This repo uses Research OS for research memory and task coordination. Every tool below is also available as an
MCP tool if the `research` MCP server is configured (`research mcp install`).

### Running the CLI (no install needed)
Use `.research/bin/research` from the project root (e.g. `.research/bin/research current`). It is a project-local
copy of the CLI, kept up to date by the VS Code extension and by `research bin install`; it needs only Python 3.
`research` works too where it is on PATH (VS Code terminals, or after `pip install`). The examples below write
`research` for short. Don't `pip install` anything just to get the CLI. If `.research/bin/research` is missing, ask
the human to open the project in VS Code with the Research Panel extension, or to run `research bin install`.

### Quick start
1. Get current state: `research current`
2. Get your task: `research task next`, then `research task context T-001` (focused brief: goal, checks, deps, skills)
3. Look things up instead of reading history: `research search "…"`, `research show <ID>`
4. Discover tools: `research --help`

### Core rules
- All runs must belong to an experiment
- Claim before working (`research task start T-001`); leave progress notes as you go (`research task note T-001 "…"`)
- Register artifacts and findings as you work
- After `max_runs_without_synthesis` runs, you must synthesize
- Findings stay preliminary until an independent review; never review your own
- Never rewrite history; supersede findings/decisions instead
- Large outputs stay where produced; just register paths
- Exit code 3 means a policy blocked you: read the hint, don't work around it

### Recording work
```bash
# Run with provenance
research run exec EXP-001 -- python train.py

# Inside code
import research
research.log_metric("rmse", 0.12)
research.register_artifact("plot.png", description="loss curve")

# Findings
research finding create "..." --supports EXP-001 RUN-001

# Checkpoint when done
research checkpoint create
```

### Task workflow (if using plans)
```bash
research task start T-001          # claim + mark running
# ... do the work, `research task note T-001 "..."` along the way ...
research task complete T-001 --result "summary of what happened"   # runs the task's checks first
```

For skills: `research skill show <name>`. For details: `research show <ID>`.
<!-- research:end -->
