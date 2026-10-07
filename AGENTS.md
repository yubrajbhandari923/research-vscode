# Agent instructions — research-vscode

<!-- research:begin — managed by `research init`; edit outside these markers freely -->
## Research OS

This repo uses Research OS for research memory and task coordination.

### Quick start
1. Get current state: `research current`
2. Get your task: `research task context T-001`
3. Discover tools: `research --help`

### Core rules
- All runs must belong to an experiment
- Register artifacts and findings as you work
- After `max_runs_without_synthesis` runs, you must synthesize
- Never rewrite history; supersede findings/decisions instead
- Large outputs stay where produced; just register paths

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
research task start T-001          # mark running
# ... do the work ...
research task complete T-001 --result "summary of what happened"
```

For skills: `research skill show <name>`. For details: `research show <ID>`.
<!-- research:end -->
