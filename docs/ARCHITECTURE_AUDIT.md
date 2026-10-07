# Research OS Architecture Audit & Redesign Plan

**Date:** 2026-10-05
**Status:** Architecture review for transformation from research CRUD interface to research control plane for autonomous agents
**Target:** Enable agent-driven research workflows where humans steer at the question/hypothesis level while agents handle implementation, experimentation, and bookkeeping

---

## Executive Summary

The current Research Panel implementation provides a **solid foundation** for the Research OS vision:

✅ **Keep these strengths:**
- Filesystem-first durable state (Markdown/YAML/JSON + derived SQLite index)
- Strong provenance (git state, authorship tracking, audit log)
- Well-separated Python core with clean RPC boundary
- Agent discipline (run budgets, synthesis enforcement)
- Question→Experiment→Run→Finding→Decision chain
- SLURM/HPC support with safe concurrent access patterns

⚠️ **Critical gaps for agent-driven research:**
- **No task/plan abstraction** — experiments conflate scientific tests with operational work
- **Context too large** — agents must read entire history or navigate complex CRUD
- **No orchestration layer** — no way to decompose high-level objectives into executable units
- **No verification gate** — agent outputs immediately become truth
- **No agent adapter abstraction** — tightly coupled to specific agent detection
- **No progressive context disclosure** — AGENTS.md is 1800 tokens but still incomplete
- **UI optimized for manual CRUD** — not for understanding research state at a glance

**Recommendation:** Evolutionary redesign in 6 major phases (detailed below)

---

## 1. What to Keep (Current Architecture Strengths)

### 1.1 Storage & Persistence Layer ✅

**Current implementation is excellent:**

```
Canonical:  .research/
  questions/Q-001.md     (Markdown + YAML front-matter)
  experiments/EXP-001.md
  runs/RUN-0001/         (run.json, status.json, metrics.jsonl, stdout.log)
  findings/F-001.md
  decisions/D-001.md
  checkpoints/CP-001.md
  artifacts.jsonl        (append-only)
  events.jsonl           (audit log)

Derived:  research.db   (SQLite index, rebuildable)
```

**Why this is correct:**
- Git-friendly, human-readable, inspectable
- Survives tool changes (Markdown outlives any framework)
- Rebuild-from-source guarantees data integrity
- Already handles NFS/Lustre safety (no WAL, compute nodes write files only)

**Decision: PRESERVE ENTIRELY.** Do not redesign persistence.

### 1.2 Provenance & Authorship ✅

Current system correctly tracks:
- Author type (human/agent/system)
- Agent name + model
- Git branch, commit, dirty state
- Full `git.diff` for dirty runs
- Append-only `events.jsonl` audit log

**Decision: KEEP.** Extend to Plans/Tasks with same pattern.

### 1.3 Python Core Architecture ✅

```
Python core (stdlib only, Python ≥3.8)
  ↓
CLI (research_cli)   VS Code Extension (stdio JSON-RPC)
```

**Strengths:**
- Single source of business logic
- Works on HPC compute nodes (no pip install needed)
- VS Code Remote SSH works naturally
- Clean RPC boundary keeps extension as pure UI

**Decision: KEEP.** Plan/Task/Agent layers fit naturally into `packages/core/research/`.

### 1.4 Run Budget & Agent Discipline ✅

Current policy enforcement is exactly right:
- `max_runs_without_synthesis: 5` → exit code 3
- `max_failed_runs_without_review: 3` → synthesis required
- Enforced for agents, warnings for humans
- Override logged with reason

**Decision: KEEP.** Extend to task-level budgets.

### 1.5 SLURM Integration ✅

Current implementation handles:
- Job submission with resource specs
- Status reconciliation (squeue → sacct fallback)
- No SQLite on compute nodes (file-based state only)
- Proper state mapping (TIMEOUT/OOM → failed)

**Decision: KEEP.** Make SLURM a backend option for tasks.

---

## 2. Critical Architectural Gaps

### 2.1 Missing Task/Plan Layer ❌

**Problem:**
Current `Experiment` conflates:
- Scientific hypothesis testing ("does entropy regularization reduce mixing?")
- Operational work ("implement baseline", "debug NaNs", "generate plots")

Example failure mode:
```
High-level goal: "Reproduce Figure 3 from the paper"

Current system forces:
  - One giant experiment with 20 runs, OR
  - Manual decomposition into many experiments without dependency structure
```

**What's needed:**
```
Plan: "Reproduce Figure 3"
  ├─ Task: Understand paper methodology
  ├─ Task: Implement baseline architecture
  │   └─ Task: Write unit tests
  ├─ Task: Prepare dataset
  ├─ Task: Sanity run (1 epoch)
  ├─ Task: Full reproduction run
  │   → creates Experiment EXP-012
  │   → creates Run RUN-0045
  ├─ Task: Generate comparison plots
  │   → creates Artifact A-0234
  ├─ Task: Verify against paper tolerances
  └─ Task: Write synthesis
      → creates Finding F-089
```

**Requirements for Plan/Task:**
- Lightweight DAG (not a heavyweight workflow engine)
- Each task can create/update Questions, Experiments, Runs, Findings, Artifacts
- Tasks track: goal, inputs, outputs, acceptance criteria, verification
- Tasks support non-experimental work (understand, implement, debug, plot, write)
- Task status: `todo → ready → running → verify → done | blocked`

### 2.2 No Context Tiering ❌

**Current approach:**
`AGENTS.md` (1800 tokens) tells agents to read `.research/context/current.md`, which is:
- Regenerated after every change
- Contains: goal, checkpoint, baseline, all findings, all experiments, policy
- Grows unbounded with project history

**Problem:**
A 6-month project with 50 experiments, 200 runs, 40 findings → ~10k token context dump.

**What's needed:**

```
Tier 0 (Bootstrap): AGENTS.md                           ~500 tokens
  - "This repo uses Research OS"
  - "Call `research task context <ID>` to get your current work"
  - "Mandatory safety rules"
  - "Available tool categories"

Tier 1 (Current State): research plan current           ~1-2k tokens
  - Goal
  - Current plan + active task
  - Current understanding (top 5 findings)
  - Known failures (top 3)
  - Blocker
  - Next step

Tier 2 (Task Context): research task context T-042      ~1-3k tokens
  - Task goal + acceptance criteria
  - Parent plan
  - Dependencies (completed tasks)
  - Related experiments/findings
  - Relevant skill (loaded on demand)
  - Available tools for this task

Tier 3 (Retrieval): On-demand
  - research finding show F-023
  - research experiment show EXP-012
  - research search history "dataset preparation"

Tier 4 (Raw Logs): Last resort
  - run stdout/stderr
  - full event log
```

**Current implementation has context.py with `resume_context()` but no task-scoped context.**

### 2.3 No Orchestration Layer ❌

**Problem:**
No mechanism to:
- Accept high-level objective → decompose into plan
- Assign tasks to specialized agents (planner vs implementer vs verifier)
- Coordinate multi-step workflows
- Continue work across agent sessions

**What's needed:**

```python
# Conceptual API (not current implementation)
research.create_plan(
    goal="Reproduce main quantitative result from paper X",
    objective="Focus on Table 2, column 'F1-macro'"
)
# → Planner agent creates task DAG

research.task.next_ready()
# → Returns T-042 with focused context

research.task.execute(
    task_id="T-042",
    agent_profile="implementer",
    instructions="skill:implement-baseline"
)
# → Executor agent works, creates code/runs/artifacts

research.task.verify(task_id="T-042")
# → Verifier checks acceptance criteria

research.task.complete(
    task_id="T-042",
    artifacts=["src/baseline.py", "A-0234"],
    synthesis="Baseline matches paper within tolerance"
)
```

**Current system has no concept of orchestration.**

### 2.4 No Verification Mechanism ❌

**Problem:**
Agent writes:
```python
research.create_finding(
    "Entropy regularization reduces mixing by 40%",
    supports=[run],
    confidence="high"
)
```

This immediately becomes "established truth" with no gate.

**What's needed:**

```
Task lifecycle with verification gate:

Agent observes result
   ↓
Agent creates preliminary finding (status=preliminary)
   ↓
Finding links evidence (runs, artifacts, metrics)
   ↓
Verification task created (auto or explicit)
   ↓
Deterministic checks:
  - tests pass?
  - expected files exist?
  - metric in tolerance?
  - no NaNs in output?
   ↓
Model-based review (for important findings):
  - Does evidence actually support claim?
  - Is interpretation overreaching?
  - What confounders remain?
   ↓
Status → supported | contradicted | remains preliminary
```

**Current system has finding status (`preliminary | supported | contradicted`) but no verification workflow.**

### 2.5 No Agent Adapter Abstraction ❌

**Current implementation:**

```python
# store.py:18
def detect_author():
    if os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_ENTRYPOINT"):
        return {
            "author_type": "agent",
            "author_name": "claude-code",
            "author_model": None
        }
    # ...
```

**Problem:**
- Hard-coded agent detection
- No way to configure which model handles which role
- Cannot swap planner/implementer/verifier independently
- No unified interface for invoking agents

**What's needed:**

```yaml
# .research/config.yaml
agents:
  roles:
    planner:
      profile: strong_reasoner
    implementer:
      profile: cheap_coder
    verifier:
      profile: strong_reviewer
    analyst:
      profile: strong_reasoner

  profiles:
    strong_reasoner:
      adapter: anthropic
      model: claude-opus-4-5
    cheap_coder:
      adapter: anthropic
      model: claude-sonnet-4-5
    strong_reviewer:
      adapter: anthropic
      model: claude-opus-4-5

  adapters:
    anthropic:
      type: claude_code
      # Or API-based, or local, or manual
```

```python
# Conceptual interface
class AgentAdapter:
    def run(
        task: Task,
        context: Dict,
        instructions: str,
        available_tools: List[Tool]
    ) -> AgentResult:
        ...
```

**Current system has no adapter abstraction.**

### 2.6 UI Optimized for CRUD, Not Research Understanding ❌

**Current UI (VS Code extension):**

Main views:
- **Overview** — entities needing attention, stats, checkpoint card
- **Questions** — tree hierarchy
- **Experiments** — grouped by status, runs nested
- **Findings** — grouped by status
- **Decisions / Checkpoints / Notes / Artifacts** — lists

**Problem:**
When opening a project after 2 weeks:
1. Must click through multiple views
2. Must parse experiment tree to understand what's happening
3. No clear "what needs my attention" signal beyond badges
4. Focus is on entity management, not research state

**What's needed (new Research Home):**

```
═══════════════════════════════════════════════════════════
RESEARCH HOME

Current Focus
  Goal: Reproduce physically plausible material maps...
  Understanding: Entropy regularization reduces mixing (F-023)
  Blocker: Dataset preprocessing timeout on GPU cluster
  Next: Investigate preprocessing bottleneck OR scale to CPU

Active Plan: Paper Reproduction
  ✓ Understand methodology
  ✓ Implement baseline
  ● Debug dataset preprocessing         ← current
  ○ Sanity run (1 subject)
  ○ Full reproduction (45 subjects)
  ○ Verify against paper Table 2

Latest Insights
  + Entropy weight λ=1e-3 optimal (F-023, verified)
  + Baseline converges in 20 epochs (F-019)
  ✗ Ray-based preprocessing OOMs on V100 (failed dir)

Needs My Attention
  ⚠ Task T-015: preprocessing timeout exceeded 4hr limit
  ⚠ Verifier disagrees with implementer on baseline accuracy

Key Outputs
  [Thumbnail] Entropy vs mixing scatter (A-0234)
  [Thumbnail] Convergence curves (A-0198)
═══════════════════════════════════════════════════════════
```

Focus:
- **Understanding** (what we believe)
- **Progress** (what's happening)
- **Problems** (what needs me)
- **Outputs** (visual results)

Not:
- Entity counts
- Status dropdowns
- CRUD forms (move to secondary UI)

---

## 3. Framework Evaluation: MCP vs Custom

### 3.1 Model Context Protocol (MCP)

**What is MCP:**
- Open protocol for exposing tools to LLMs
- Client-agnostic (Claude Desktop, Claude Code, OpenAI, etc.)
- Server provides: tools, resources, prompts
- Transport: stdio, HTTP, or custom

**MCP pros for Research OS:**

✅ **Agent portability**
```python
# Research OS MCP server exposes:
tools:
  - research.plan.create
  - research.task.context
  - research.task.complete
  - research.experiment.synthesize
  - research.artifact.register
  - etc.

resources:
  - plan://current
  - task://T-042/context
  - experiment://EXP-012
  - artifact://A-0234
```

Any MCP-compatible agent (Claude Code, cursor, etc.) can use Research OS immediately.

✅ **Discovery protocol** — agents can query available tools rather than loading full docs

✅ **Mature SDK** — Python SDK available, handles transport/serialization

✅ **No vendor lock-in** — MCP is open, not Claude-specific

**MCP cons:**

❌ Not a full orchestration solution — MCP provides tools, not task coordination
❌ Still need Plan/Task layer regardless
❌ Still need AgentAdapter for multi-provider support
❌ Adds dependency (though lightweight)

**Architecture with MCP:**

```
┌─────────────────────────────────────┐
│      Research OS Core               │
│  (Python: Plans, Tasks, Tools)      │
└──────────┬──────────────────────────┘
           │
    ┌──────┴──────┬──────────────┬─────────────┐
    │             │              │             │
    ▼             ▼              ▼             ▼
  MCP         CLI API        VS Code        Python
 Server                       UI            API
    │
    ▼
Claude Code, Cursor, OpenAI agents, etc.
```

**Recommendation:**
✅ **Implement MCP server as one interface to Research OS tools**

Rationale:
- Materially improves agent portability
- Does not replace CLI/Python API/VS Code UI
- Lightweight Python SDK available
- Progressive: start with core tools, expand over time
- If MCP fails, remove the server without touching core logic

**MCP is a good interface layer, not the architecture.**

### 3.2 Other Framework Options

**LangGraph / LangChain:**
❌ Do not use.
- Heavy dependencies
- Vendor-specific patterns
- Overkill for Research OS control plane
- Would couple research memory to LangChain patterns

**PydanticAI:**
❌ Do not use for core orchestration
✅ May use for tool schemas (lightweight)

**OpenAI Agents SDK / Anthropic SDK:**
❌ Do not use directly in core
✅ Use in AgentAdapters as providers

**Decision:**
- **Custom orchestration in Python** (Plans, Tasks, Tools)
- **Pydantic for schemas** (already using YAML/JSON, no big change)
- **MCP as optional interface layer**
- **Provider SDKs hidden behind AgentAdapter interface**

---

## 4. Proposed Architecture

### 4.1 Conceptual Layers

```
┌────────────────────────────────────────────────────────┐
│                 HUMAN INTERFACE                        │
│  Research Home UI · High-level Objective Input         │
└──────────────────────┬─────────────────────────────────┘
                       │
┌──────────────────────▼─────────────────────────────────┐
│              ORCHESTRATION LAYER                       │
│                                                        │
│  Planner Agent                                         │
│    input: high-level objective                         │
│    output: Plan + Task DAG                             │
│                                                        │
│  Executor Agent(s)                                     │
│    input: Task + focused context                       │
│    output: code/runs/artifacts + synthesis             │
│                                                        │
│  Verifier Agent                                        │
│    input: Task results + evidence                      │
│    output: verification status + critique              │
│                                                        │
│  Analyst Agent                                         │
│    input: experimental results                         │
│    output: findings + decisions + next tasks           │
└──────────────────────┬─────────────────────────────────┘
                       │
┌──────────────────────▼─────────────────────────────────┐
│          AGENT ADAPTER LAYER (Provider-Neutral)        │
│                                                        │
│  AgentAdapter Interface:                               │
│    .run(task, context, instructions, tools) → result   │
│                                                        │
│  Implementations:                                      │
│    - ClaudeCodeAdapter                                 │
│    - AnthropicAPIAdapter                               │
│    - OpenAIAdapter                                     │
│    - ManualAdapter (human execution)                   │
└──────────────────────┬─────────────────────────────────┘
                       │
┌──────────────────────▼─────────────────────────────────┐
│              RESEARCH OS TOOLS                         │
│                                                        │
│  Deterministic Operations:                             │
│    - run_experiment                                    │
│    - submit_slurm_job                                  │
│    - register_artifact                                 │
│    - create_finding (with evidence)                    │
│    - git_checkpoint                                    │
│    - get_task_context                                  │
│    - verify_acceptance_criteria                        │
│    - generate_standard_plots                           │
│    - compare_runs                                      │
│                                                        │
│  Exposed via:                                          │
│    - Python API                                        │
│    - CLI                                               │
│    - RPC (for VS Code)                                 │
│    - MCP Server (for agents)                           │
└──────────────────────┬─────────────────────────────────┘
                       │
┌──────────────────────▼─────────────────────────────────┐
│          RESEARCH MEMORY (Existing Layer)              │
│                                                        │
│  Entities:                                             │
│    Questions · Experiments · Runs · Findings ·         │
│    Decisions · Checkpoints · Artifacts                 │
│                                                        │
│  NEW: Plans · Tasks                                    │
│                                                        │
│  Storage: .research/ (Markdown/YAML + SQLite index)    │
│  Provenance: Git state + authorship + audit log        │
└────────────────────────────────────────────────────────┘
```

### 4.2 Data Model Extensions

#### Plan Entity

```yaml
# .research/plans/PLAN-001.md
---
id: PLAN-001
title: Reproduce Paper X Main Results
status: active | completed | blocked | abandoned
objective: Reproduce Table 2 and Figure 4 from paper
context: |
  Paper: "Physically Plausible Material Decomposition"
  Focus: Quantitative accuracy on clinical CT dataset
root_question: Q-001
created_by: human | planner-agent
created_at: 2026-10-05T10:30:00+00:00
completed_at: null
---

# PLAN-001 · Reproduce Paper X

## Objective
Reproduce the main quantitative results (Table 2, F1-macro scores)
and qualitative comparison (Figure 4, material maps).

## Tasks
(See tasks/ directory)

## Success Criteria
- F1-macro matches paper ± 2pp
- Visual inspection of material maps matches Figure 4
- Full reproduction completed within 2 weeks

<!-- research:generated -->
## Status
- 5 of 12 tasks completed
- Current: T-007 (running)
- Blocked: none

## Tasks
...
```

**Schema:**
```python
plans(
  id PK,
  title text NOT NULL,
  objective text,
  status text,  # active, completed, blocked, abandoned
  root_question_id text,
  success_criteria text,
  context text,
  author_type text,
  author_name text,
  author_model text,
  created_at text,
  updated_at text,
  completed_at text
)
```

#### Task Entity

```yaml
# .research/tasks/T-042.md
---
id: T-042
plan: PLAN-001
title: Implement baseline architecture
goal: Working implementation of the paper's baseline model
task_type: implementation
status: done
assigned_role: implementer
depends_on: [T-005, T-011]  # "Understand architecture", "Prepare environment"
related_question: Q-001
related_experiment: EXP-012
inputs:
  - Paper section 3.2 (architecture)
  - Dataset spec from T-008
expected_outputs:
  - src/models/baseline.py
  - tests/test_baseline.py
  - Sanity run confirming model loads
acceptance_criteria:
  - Code passes unit tests
  - Model forward pass produces expected shapes
  - Sanity run (1 subject, 1 epoch) completes
verification:
  - Tests pass (deterministic)
  - Code review by verifier (model-based)
artifacts: [A-0234, A-0235]
created_by: planner-agent
completed_by: implementer-agent
created_at: 2026-10-05T11:00:00+00:00
started_at: 2026-10-05T14:20:00+00:00
completed_at: 2026-10-05T16:45:00+00:00
---

# T-042 · Implement baseline architecture

## Goal
Create a working implementation of the baseline model from the paper
(section 3.2), with unit tests and a passing sanity run.

## Inputs
- Paper: "Physically Plausible Material Decomposition", section 3.2
- Dataset specification: see T-008
- Environment: Python 3.10, PyTorch 2.0

## Expected Outputs
1. `src/models/baseline.py` — model implementation
2. `tests/test_baseline.py` — unit tests (shapes, forward pass)
3. Sanity run artifact confirming model loads and runs

## Acceptance Criteria
- [ ] Unit tests pass
- [ ] Model architecture matches paper (verified in code review)
- [ ] Forward pass produces expected output shapes
- [ ] Sanity run (1 subject, 1 epoch) completes without errors

## Verification
- Deterministic: `pytest tests/test_baseline.py`
- Model-based: Code review by verifier-agent

<!-- research:generated -->
## Status
✓ Completed

## Artifacts
- A-0234: src/models/baseline.py
- A-0235: tests/test_baseline.py
- A-0236: sanity run output

## Activity
...
```

**Schema:**
```python
tasks(
  id PK,
  plan_id text NOT NULL,
  title text NOT NULL,
  goal text,
  task_type text,  # research, implementation, experiment, analysis, verification, synthesis, debug
  status text,  # todo, ready, running, verify, done, blocked
  assigned_role text,  # planner, implementer, verifier, analyst, human
  depends_on json,  # ["T-005", "T-011"]
  inputs text,
  expected_outputs text,
  acceptance_criteria text,
  verification text,
  related_question_id text,
  related_experiment_id text,
  artifacts json,  # ["A-0234", "A-0235"]
  blockers text,
  notes text,
  author_type text,
  author_name text,
  author_model text,
  created_by text,  # human | planner-agent
  completed_by text,  # implementer-agent
  created_at text,
  updated_at text,
  started_at text,
  completed_at text
)
```

**Task Types:**
- `research` — understand something (read paper, explore code)
- `implementation` — write code
- `experiment` — run scientific test (creates Experiment)
- `analysis` — interpret results
- `verification` — check acceptance criteria
- `synthesis` — write up findings
- `debug` — fix failing test/run
- `data` — prepare dataset

**Task Status Machine:**
```
todo → ready (dependencies satisfied)
     → running (agent assigned)
     → verify (output produced, awaiting verification)
     → done (verification passed)
     → blocked (dependency failed or external blocker)
```

### 4.3 Tool Registry

**Core Research OS Tools (deterministic):**

```python
# packages/core/research/tools.py

TOOLS = {
    "get_task_context": {
        "description": "Get focused context for a specific task",
        "params": {"task_id": "str"},
        "returns": "dict",
        "side_effects": False
    },
    "run_experiment": {
        "description": "Execute experiment run with provenance",
        "params": {
            "experiment_id": "str",
            "command": "str",
            "parameters": "dict",
            "label": "str?"
        },
        "returns": "run_id",
        "side_effects": True,
        "requires_permission": False
    },
    "submit_slurm": {
        "description": "Submit job to SLURM cluster",
        "params": {
            "experiment_id": "str",
            "command": "str",
            "time": "str",
            "gpus": "int",
            "partition": "str?"
        },
        "returns": "run_id",
        "side_effects": True,
        "requires_permission": True
    },
    "register_artifact": {
        "description": "Register file/directory as research artifact",
        "params": {
            "path": "str",
            "run_id": "str?",
            "experiment_id": "str?",
            "description": "str"
        },
        "returns": "artifact_id",
        "side_effects": False
    },
    "create_finding": {
        "description": "Record reusable research finding with evidence",
        "params": {
            "title": "str",
            "statement": "str",
            "supports": "list[str]",  # experiment/run/artifact IDs
            "confidence": "str",
            "limitations": "str"
        },
        "returns": "finding_id",
        "side_effects": True,
        "requires_permission": False
    },
    "git_checkpoint": {
        "description": "Create git commit with research checkpoint",
        "params": {
            "message": "str",
            "push": "bool"
        },
        "returns": "commit_hash",
        "side_effects": True,
        "requires_permission": True
    },
    "verify_tests": {
        "description": "Run test suite and check pass/fail",
        "params": {"test_path": "str"},
        "returns": {"passed": "bool", "summary": "str"},
        "side_effects": False
    },
    "compare_runs": {
        "description": "Compare metrics between runs",
        "params": {
            "run_ids": "list[str]",
            "metrics": "list[str]"
        },
        "returns": "comparison_table",
        "side_effects": False
    },
    "generate_plots": {
        "description": "Generate standard plots from run metrics",
        "params": {
            "experiment_id": "str",
            "plot_type": "str"
        },
        "returns": "artifact_ids",
        "side_effects": True
    }
}
```

**Tool permissions:**
```yaml
# .research/config.yaml
tools:
  permissions:
    submit_slurm: require_approval  # ask human before submitting
    git_checkpoint: auto_allowed    # can run automatically
    verify_tests: auto_allowed
    run_experiment: auto_allowed
```

### 4.4 Context Generation System

**Tier 0: Bootstrap (AGENTS.md)**

```markdown
<!-- research:begin -->
## Research OS Protocol

This repository uses Research OS for research memory and task coordination.

**Start here:**
1. Get your current task: `research task current`
2. Get task context: `research task context <task-id>`
3. Available tools: `research tools list`

**Mandatory rules:**
- Do not run code without an active task
- Record all artifacts and metrics
- Never skip verification steps
- Never write to .research/ directly

**For help:** `research --help` or see .research/context/current.md
<!-- research:end -->
```

**Target: ~500 tokens** (current AGENTS.md is 1800 tokens)

**Tier 1: Current State**

```bash
research plan current
```

Output:
```markdown
# Current Research State

## Goal
Reproduce physically plausible material decomposition from Paper X

## Active Plan
PLAN-001: Reproduce main results (5 of 12 tasks done)

## Current Task
T-042: Implement baseline architecture (running, implementer-agent)
Dependencies: ✓ T-005 (Understand), ✓ T-011 (Environment)

## Understanding
- Entropy regularization reduces mixing (F-023, verified)
- Baseline architecture: 3-layer UNet variant (F-019)
- Dataset: 45 clinical CT subjects (D-005)

## Known Failures
- Ray-based preprocessing OOMs on V100 (failed direction)
- Sinogram-space loss unstable (abandoned)

## Blocker
None

## Next Step
Complete T-042, then sanity run (T-043)
```

**Tier 2: Task Context**

```bash
research task context T-042
```

Output:
```markdown
# Task T-042: Implement baseline architecture

## Goal
Working implementation of paper baseline model with tests

## Plan
PLAN-001: Reproduce main results

## Dependencies (satisfied)
✓ T-005: Understand architecture (completed)
✓ T-011: Setup Python environment (completed)

## Inputs
- Paper section 3.2 (architecture details)
- Dataset spec: see T-008 output
- Environment: Python 3.10, PyTorch 2.0

## Expected Outputs
1. src/models/baseline.py
2. tests/test_baseline.py
3. Sanity run confirming model loads

## Acceptance Criteria
- Unit tests pass
- Model architecture matches paper
- Forward pass produces correct shapes
- Sanity run (1 subject, 1 epoch) completes

## Verification
- Deterministic: pytest tests/test_baseline.py
- Model-based: code review by verifier

## Available Skills
- implement-ml-model
- write-unit-tests

## Available Tools
(Core tools: run_experiment, register_artifact, verify_tests, etc.)

## Related Findings
- F-019: Baseline uses 3-layer UNet (related to architecture)

## Policy
- Max 3 failed test runs before review
- Register all code artifacts
- Verification required before marking done
```

**Implementation:**

```python
# packages/core/research/context.py

def task_context(project: Project, task_id: str) -> Dict[str, Any]:
    """Generate focused context for a specific task."""
    task = project.get_task(task_id)
    plan = project.get_plan(task["plan_id"])

    # Load only relevant pieces
    deps = [project.get_task(tid) for tid in task["depends_on"]]
    related_exp = project.get_experiment(task["related_experiment_id"]) if task["related_experiment_id"] else None
    related_findings = project.query(
        "SELECT * FROM findings WHERE id IN (SELECT dst_id FROM links WHERE src_id = ? AND relation = 'related')",
        (task_id,)
    )

    # Load skill if specified
    skill = load_skill(task.get("skill")) if task.get("skill") else None

    return {
        "task": task,
        "plan": plan,
        "dependencies": deps,
        "inputs": task["inputs"],
        "expected_outputs": task["expected_outputs"],
        "acceptance_criteria": task["acceptance_criteria"],
        "verification": task["verification"],
        "related_experiment": related_exp,
        "related_findings": related_findings[:5],  # top 5
        "skill": skill,
        "tools": get_available_tools(),
        "policy": project.config["agent_policy"]
    }
```

---

## 5. Implementation Plan

### Phase 0: Foundation (Week 1)

**Goal:** Prepare codebase for Plan/Task addition without breaking existing functionality

**Tasks:**
1. ✅ Create migration scaffold for schema v2
2. ✅ Add Plan and Task table definitions to `schema.py`
3. ✅ Add Plan and Task mirror parsing to `mirrors.py`
4. ✅ Extend sync logic to handle new entity types
5. ✅ Add basic CRUD operations in `services.py`
6. ✅ Write unit tests for Plan/Task storage

**Deliverables:**
- `packages/core/research/schema.py` — Plan and Task schemas
- `packages/core/research/plans.py` — Plan/Task CRUD
- `tests/python/test_plans_tasks.py` — storage tests
- Schema migration from v1 to v2

**Success Criteria:**
- All existing tests pass
- Can create/read Plan and Task entities
- Plans and Tasks survive rebuild

**Risks:** None (additive change, no breaking changes)

---

### Phase 1: Task Context Generation (Week 2)

**Goal:** Implement tiered context system

**Tasks:**
1. Refactor `context.py` to support context tiers
2. Implement `plan_current()` — current state summary
3. Implement `task_context(task_id)` — focused task context
4. Add CLI commands: `research plan current`, `research task context T-042`
5. Update AGENTS.md template to bootstrap approach (500 tokens)
6. Add RPC methods for plan/task context

**Deliverables:**
- `packages/core/research/context.py` — tiered context functions
- `packages/cli/research_cli/main.py` — plan/task subcommands
- New AGENTS.md template (500 tokens vs current 1800)
- RPC: `plan_current`, `task_context` methods

**Success Criteria:**
- `research plan current` returns <2k tokens
- `research task context T-001` returns focused context
- Context does not include full history

**Risks:** Medium — must ensure context is sufficient for agents

---

### Phase 2: Tool Registry & MCP Server (Week 3)

**Goal:** Create discoverable tool layer with MCP interface

**Tasks:**
1. Create `packages/core/research/tools.py` — tool registry
2. Implement core tools (get_task_context, run_experiment, register_artifact, etc.)
3. Add tool permission system to config.yaml
4. Implement MCP server using Anthropic MCP Python SDK
5. Expose Research OS tools via MCP
6. Add MCP server to CLI: `research mcp serve`
7. Test with Claude Code

**Deliverables:**
- `packages/core/research/tools.py` — tool definitions + registry
- `packages/core/research/mcp_server.py` — MCP server implementation
- MCP tool schemas for all core tools
- CLI: `research mcp serve`

**Success Criteria:**
- Claude Code can discover Research OS tools via MCP
- MCP tools create valid database records
- Tool permissions enforced

**Risks:** Low — MCP is optional interface, does not affect core

**MCP Example:**

```python
# packages/core/research/mcp_server.py
from mcp.server import Server
from mcp.server.stdio import stdio_server
from .tools import TOOLS

server = Server("research-os")

@server.list_tools()
async def list_tools():
    return [
        {
            "name": name,
            "description": spec["description"],
            "inputSchema": {
                "type": "object",
                "properties": spec["params"],
                "required": [k for k, v in spec["params"].items() if not k.endswith("?")]
            }
        }
        for name, spec in TOOLS.items()
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    # Delegate to research core
    ...
```

---

### Phase 3: Agent Adapter Layer (Week 4)

**Goal:** Provider-neutral agent invocation

**Tasks:**
1. Define AgentAdapter interface
2. Implement ClaudeCodeAdapter (wraps detected Claude Code)
3. Implement ManualAdapter (human execution)
4. Add agent config to config.yaml (roles, profiles, adapters)
5. Create agent invocation API: `research.agents.invoke(role, task, context)`
6. Update authorship tracking to use adapter profiles

**Deliverables:**
- `packages/core/research/agents/__init__.py` — AgentAdapter interface
- `packages/core/research/agents/claude_code.py` — ClaudeCodeAdapter
- `packages/core/research/agents/manual.py` — ManualAdapter
- Config schema for agents (roles/profiles/adapters)
- RPC: `invoke_agent` method

**Success Criteria:**
- Can configure planner vs implementer roles
- Can invoke agent via adapter
- Agent authorship correctly recorded

**Risks:** Medium — must get interface right for future providers

**Adapter Interface:**

```python
# packages/core/research/agents/__init__.py
from typing import Protocol, Dict, Any, List

class AgentAdapter(Protocol):
    """Interface for invoking agents."""

    def run(
        self,
        task: Dict[str, Any],
        context: Dict[str, Any],
        instructions: str,
        available_tools: List[str]
    ) -> Dict[str, Any]:
        """
        Execute task using this agent.

        Returns:
            {
                "status": "completed" | "failed" | "blocked",
                "output": "...",
                "artifacts": ["A-001", ...],
                "synthesis": "...",
                "next_tasks": [...]
            }
        """
        ...
```

---

### Phase 4: Verification System (Week 5)

**Goal:** Add verification gate between agent output and established truth

**Tasks:**
1. Add verification status to findings (preliminary → verified/rejected)
2. Create verification task type
3. Implement deterministic verifiers (tests_pass, file_exists, metric_in_range)
4. Create verification workflow: finding → verification task → status update
5. Add verification policy to config
6. Implement verifier role in agent system

**Deliverables:**
- `packages/core/research/verification.py` — verifier implementations
- Updated Finding schema with verification provenance
- Verification task lifecycle
- Config: verification policies

**Success Criteria:**
- Agent-created findings marked preliminary by default
- Deterministic verifiers can auto-verify claims
- Verifier agent can review findings

**Risks:** Low — extends existing finding status system

---

### Phase 5: Orchestration & Plan/Task Execution (Week 6-7)

**Goal:** End-to-end plan execution workflow

**Tasks:**
1. Implement plan creation from high-level objective
2. Implement task DAG evaluation (ready when deps satisfied)
3. Create task executor: `research task execute T-042`
4. Implement task completion with verification gate
5. Add CLI workflow commands
6. Create planner agent integration (uses plan/task tools)

**Deliverables:**
- `packages/core/research/orchestration.py` — plan/task execution
- CLI: `research plan create`, `research task next`, `research task execute`
- Planner integration (creates task DAG from objective)
- Full workflow: objective → plan → tasks → execution → verification → synthesis

**Success Criteria:**
- Can create plan from high-level objective
- Task DAG correctly determines ready tasks
- Agent can execute task with focused context
- Task completion triggers verification

**Risks:** High — most complex integration

---

### Phase 6: Research Home UI Redesign (Week 8-9)

**Goal:** Redesign UI for research understanding, not CRUD

**Tasks:**
1. Design Research Home webview mockup
2. Implement new Resume view (Current Focus, Active Plan, Insights, Attention)
3. Add plan/task tree view
4. Update Overview to lead with Research Home
5. Move CRUD forms to secondary "Edit" section
6. Add visual task DAG (optional: simple list may be better)
7. Implement "Needs Attention" aggregation

**Deliverables:**
- `packages/vscode-extension/src/render/research_home.ts`
- Updated Overview webview
- Plan/task tree view
- Restrained visual design (no dashboard clutter)

**Success Criteria:**
- Opening project after 2 weeks: understand state in <2 min
- Can see current plan progress at a glance
- What needs attention is obvious
- CRUD still available but not primary

**Risks:** Medium — UI changes are subjective

---

### Phase 7: Git Workflow Integration (Week 10)

**Goal:** Safe automated git checkpointing

**Tasks:**
1. Implement `research git checkpoint` tool
2. Add git policy to config (checkpoint after synthesis, etc.)
3. Add secret detection (refuse commits with .env, credentials.json)
4. Add large file detection (warn on >100MB)
5. Create checkpoint→commit→push workflow
6. Add git checkpoint to task completion flow

**Deliverables:**
- `packages/core/research/git_workflow.py`
- Config: git policies
- Tool: `git_checkpoint`
- Integration: automatic checkpoints

**Success Criteria:**
- Agent can safely checkpoint research state
- Secrets are not committed
- Large files are warned
- Git state preserved in research memory

**Risks:** Low — builds on existing git info capture

---

## 6. Testing Strategy

### 6.1 Black-Box Agent Test

**Goal:** Verify agents can work without understanding Research OS internals

**Test:**
```python
def test_black_box_agent():
    """
    Create a fake agent with access to:
    - AGENTS.md (bootstrap)
    - research task context T-001
    - Tool schemas (via MCP or direct)

    Agent should NOT have access to:
    - README.md
    - SPEC.md
    - Research Panel source code
    - Database schema

    Task: Implement a simple baseline model

    Success criteria:
    - Agent can get task context
    - Agent can create artifacts
    - Agent can complete task
    - Agent does not need to read SPEC.md
    """
    # Create isolated environment
    # Provide only AGENTS.md + task context + tools
    # Run mock agent
    # Verify completion without spec access
```

### 6.2 Context Budget Tests

**Goal:** Ensure context stays within bounds

```python
def test_context_tiers():
    """Verify context sizes."""
    # Create project with 50 experiments, 200 runs, 40 findings

    agents_md = read_file("AGENTS.md")
    assert token_count(agents_md) < 600  # Bootstrap tier

    plan_current = research.plan.current()
    assert token_count(plan_current) < 2500  # Current state tier

    task_ctx = research.task.context("T-042")
    assert token_count(task_ctx) < 3500  # Task tier
    assert "full history" not in task_ctx  # Should not dump everything
```

### 6.3 Verification Tests

**Goal:** Ensure verification gate works

```python
def test_verification_gate():
    """Findings require verification before becoming established."""
    # Agent creates finding
    finding = research.create_finding(
        title="Baseline accuracy is 95%",
        supports=["RUN-001"],
        confidence="high",
        author_type="agent"
    )
    assert finding["status"] == "preliminary"

    # Deterministic verification
    result = research.verify_finding(
        finding["id"],
        checks=["tests_pass", "metric_in_tolerance"]
    )
    assert result["verified"] == True

    # Status updated
    finding = research.get_finding(finding["id"])
    assert finding["status"] == "supported"
```

### 6.4 Agent Portability Tests

**Goal:** Ensure provider swapping works

```python
def test_agent_adapter_swap():
    """Swap agent providers without changing plans."""
    # Create plan with planner=claude-opus
    plan = research.create_plan(
        objective="Reproduce paper X",
        planner_profile="strong_reasoner"
    )

    # Swap to different provider
    update_config({
        "agents.profiles.strong_reasoner.adapter": "openai",
        "agents.profiles.strong_reasoner.model": "gpt-4"
    })

    # Continue plan with new provider
    task = research.task.next_ready()
    result = research.task.execute(task["id"])

    # Verify authorship reflects new provider
    assert result["completed_by"] == "openai:gpt-4"

    # Plan continues without migration
    assert plan["status"] == "active"
```

---

## 7. Migration & Backwards Compatibility

### 7.1 Schema Migration (v1 → v2)

```python
# packages/core/research/schema.py

MIGRATIONS = {
    1: [
        # Existing tables
        ...
    ],
    2: [
        # Add Plan and Task tables
        """CREATE TABLE plans (
            id text PRIMARY KEY,
            title text NOT NULL,
            objective text,
            status text,
            root_question_id text,
            success_criteria text,
            context text,
            author_type text,
            author_name text,
            author_model text,
            created_at text,
            updated_at text,
            completed_at text
        )""",
        """CREATE TABLE tasks (
            id text PRIMARY KEY,
            plan_id text NOT NULL,
            title text NOT NULL,
            goal text,
            task_type text,
            status text,
            assigned_role text,
            depends_on text,  -- JSON
            inputs text,
            expected_outputs text,
            acceptance_criteria text,
            verification text,
            related_question_id text,
            related_experiment_id text,
            artifacts text,  -- JSON
            blockers text,
            notes text,
            author_type text,
            author_name text,
            author_model text,
            created_by text,
            completed_by text,
            created_at text,
            updated_at text,
            started_at text,
            completed_at text
        )""",
        "CREATE INDEX idx_tasks_plan ON tasks(plan_id)",
        "CREATE INDEX idx_tasks_status ON tasks(status)",

        # Add verification fields to findings
        "ALTER TABLE findings ADD COLUMN verified_by text",
        "ALTER TABLE findings ADD COLUMN verified_at text",
        "ALTER TABLE findings ADD COLUMN verification_method text"
    ]
}
```

### 7.2 Existing Projects

**Projects without Plans:**
- Continue to work normally
- Experiments/Runs/Findings remain valid
- Plans are optional
- Can create plan retroactively to organize existing experiments

**No forced migration** — old projects open without changes

### 7.3 Tool Versioning

Tools declare version:
```python
TOOLS = {
    "run_experiment": {
        "version": "1.0",
        "description": "...",
        ...
    }
}
```

If tool schema changes:
- Increment version
- Support both old and new schemas during transition
- Deprecation warnings

---

## 8. Documentation Updates

### 8.1 New Files

1. **`docs/PLANS_TASKS.md`** — Plan/Task system guide
2. **`docs/AGENTS.md`** — Agent adapter + orchestration guide
3. **`docs/TOOLS.md`** — Tool registry + MCP server
4. **`docs/VERIFICATION.md`** — Verification workflows

### 8.2 Updated Files

1. **`README.md`** — Add Research OS vision, update workflow examples
2. **`docs/SPEC.md`** — Add Plan/Task schemas, tool registry, MCP protocol
3. **`docs/DESIGN.md`** — Add orchestration layer, context tiers

### 8.3 Example Projects

Create `examples/paper-reproduction/` demonstrating:
- High-level objective → planner creates plan
- Task DAG execution
- Implementer agent → code artifacts
- Experiment runs → results
- Verifier agent → finding verification
- Synthesis → next research question

---

## 9. Key Design Decisions

### 9.1 What We ARE Building

✅ **Research control plane** for autonomous agents
✅ **Durable research memory** (filesystem-first)
✅ **Plan/Task layer** for operational work decomposition
✅ **Provider-neutral agent adapters**
✅ **Verification gates** for agent outputs
✅ **Tiered context system** (bootstrap → current → task → retrieval)
✅ **Tool registry** with MCP exposure
✅ **Research Home UI** optimized for understanding

### 9.2 What We Are NOT Building

❌ **Not an autonomous-agent platform** (no unattended AutoGPT-style system)
❌ **Not a general workflow engine** (not Airflow/Prefect/Dagster)
❌ **Not an experiment tracker** (not W&B/MLflow clone)
❌ **Not a project management tool** (not Jira/Linear)
❌ **Not a chatbot** (high-level input ≠ conversational UI)
❌ **Not vendor-locked** (no hard dependency on Claude/OpenAI/etc.)

### 9.3 Invariants to Maintain

1. **Filesystem is source of truth** — SQLite is always rebuildable
2. **Agents are clients, not developers** — they use tools, not internals
3. **Context must be bounded** — no full-history dumps
4. **Provenance is mandatory** — git + authorship always recorded
5. **Manual control remains** — humans can always override/edit
6. **Test coverage for critical paths** — especially context, verification, orchestration

---

## 10. Risk Assessment

### 10.1 Technical Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Context generation inadequate for agents | HIGH | Black-box agent testing, iterate on context |
| MCP integration breaks existing workflows | MEDIUM | MCP is additive, can be disabled |
| Plan/Task DAG gets too complex | MEDIUM | Start simple (linear tasks), evolve if needed |
| Agent adapter interface too narrow | MEDIUM | Design for extensibility, start with 2+ implementations |
| Verification gate adds too much friction | LOW | Make deterministic verifiers fast, model-based optional |
| UI redesign disrupts existing users | LOW | Keep CRUD available, new UI is additive |

### 10.2 Schedule Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Phase 5 (orchestration) takes longer than 2 weeks | HIGH | Can ship phases 1-4 independently |
| Agent adapter interface requires iteration | MEDIUM | Start with Claude Code only, add others later |
| MCP server debugging delays | LOW | MCP is optional, can defer |

### 10.3 Adoption Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Users prefer old UI | MEDIUM | Keep both UIs, make Research Home opt-in initially |
| Plans feel like overhead for small projects | MEDIUM | Plans are optional, experiments still work standalone |
| Tool permission system too restrictive | LOW | Default to permissive, tighten per project |

---

## 11. Success Metrics

### 11.1 Technical Metrics

- Context size: AGENTS.md <600 tokens, task context <3.5k tokens
- Black-box agent test passes (agent completes task without reading SPEC.md)
- Agent adapter swap test passes (change provider without migrating state)
- All existing tests pass after each phase
- Schema migration completes in <5s for 1000-experiment project

### 11.2 Workflow Metrics

**Before (current):**
- High-level task: "Reproduce paper Figure 3"
- Steps: 15+ manual experiment/run creations, navigate 8+ UI screens

**After (target):**
- High-level input: "Reproduce Figure 3 focusing on quantitative accuracy"
- Planner creates 8-task plan
- Implementer executes tasks autonomously
- Verifier checks outputs
- Human reviews synthesis in Research Home (2 min to understand state)

### 11.3 User Experience Metrics

- Time to understand project state after 2-week absence: <2 minutes (currently ~10 min)
- Number of clicks to find "what needs my attention": 1 (currently 3-5)
- Agent session setup: 1 command (`research task current`) vs currently reading 1800-token AGENTS.md + manual context search

---

## 12. Next Steps

### Immediate (Week 1)

1. **Approve architecture direction** (this document)
2. **Begin Phase 0** (Plan/Task schema)
3. **Set up testing infrastructure** for black-box agent tests

### Short-term (Month 1)

1. Complete Phases 0-3 (foundation, context, tools, MCP)
2. Demonstrate MCP server with Claude Code
3. Show task context generation working

### Medium-term (Months 2-3)

1. Complete Phases 4-5 (verification, orchestration)
2. Demonstrate end-to-end workflow: objective → plan → execution → findings
3. Begin UI redesign

### Long-term (Months 3+)

1. Complete Phase 6 (Research Home UI)
2. Complete Phase 7 (git workflow)
3. Dogfood on real research projects
4. Iterate based on agent feedback
5. Add additional agent adapters (OpenAI, local models, etc.)

---

## Appendix A: Current vs Target Workflow Comparison

### Current Workflow (Manual CRUD)

```
Human:
  1. Read README.md, SPEC.md to understand system
  2. Create question: research question create "..."
  3. Create experiment: research experiment create "..." (10+ flags)
  4. Manually run code
  5. Manually register run: research run attach ...
  6. Manually register artifacts
  7. Manually synthesize
  8. Manually create finding
  9. Repeat for next experiment
```

**Pain points:**
- Every step is manual
- Agent must understand CRUD semantics
- No task decomposition
- Context is full history or nothing

### Target Workflow (Agent-Driven)

```
Human:
  1. High-level objective: "Reproduce Table 2 from Paper X"

System (Planner Agent):
  2. Reads paper → creates Plan with 12 tasks

System (Executor Agent):
  3. For each ready task:
     - Gets focused context (task context)
     - Uses tools to implement/run/analyze
     - Records artifacts/metrics automatically
     - Creates preliminary findings

System (Verifier Agent):
  4. Reviews each task output
     - Checks acceptance criteria
     - Validates claims against evidence
     - Marks findings as verified/rejected

Human:
  5. Opens Research Home
     - Sees: "10 of 12 tasks done, 2 findings verified"
     - Reviews synthesis
     - Approves or redirects
```

**Improvements:**
- Human operates at question/objective level
- Agents handle implementation/bookkeeping
- Context is scoped to current task
- Verification gate prevents false conclusions
- Research Home shows state at a glance

---

## Appendix B: File Structure After Implementation

```
.research/
├── config.yaml                  # + agents, tools sections
├── questions/Q-001.md
├── experiments/EXP-001.md
├── plans/PLAN-001.md            # NEW
├── tasks/T-042.md               # NEW
├── findings/F-001.md            # + verification fields
├── decisions/D-001.md
├── checkpoints/CP-001.md
├── runs/RUN-0001/
├── notes/
├── skills/
│   ├── reproduce-paper/
│   ├── implement-ml-model/      # NEW
│   └── verify-results/          # NEW
├── context/
│   ├── current.md               # Simplified
│   └── bootstrap.md             # NEW (minimal)
├── prompts/
├── templates/
├── policies/                    # NEW
│   ├── git.yaml
│   ├── verification.yaml
│   └── tools.yaml
└── research.db                  # + plans, tasks tables
```

```
packages/core/research/
├── __init__.py
├── schema.py                    # + Plan, Task schemas
├── store.py
├── mirrors.py                   # + Plan, Task parsing
├── services.py
├── runs.py
├── context.py                   # + tiered context
├── views.py
├── rpc.py
├── runner.py
├── gitinfo.py
├── util.py
├── yamlio.py
├── plans.py                     # NEW (Plan/Task CRUD)
├── tools.py                     # NEW (tool registry)
├── verification.py              # NEW (verifiers)
├── orchestration.py             # NEW (task execution)
├── git_workflow.py              # NEW (checkpoint/push)
├── agents/                      # NEW
│   ├── __init__.py             # AgentAdapter interface
│   ├── claude_code.py          # ClaudeCodeAdapter
│   ├── manual.py               # ManualAdapter
│   └── openai.py               # (future)
├── mcp_server.py               # NEW (MCP interface)
├── backends/
│   ├── base.py
│   ├── local.py
│   └── slurm.py
└── templates/
    ├── AGENTS.block.md         # Updated (500 tokens)
    ├── skills/
    │   ├── reproduce-paper/
    │   ├── implement-ml-model/ # NEW
    │   └── verify-results/     # NEW
    └── prompts/
```

---

## Conclusion

The current Research Panel provides an **excellent foundation** for the Research OS vision. The core architecture (filesystem-first storage, Python core, RPC boundary, agent discipline) is sound and should be preserved.

The transformation to a **research control plane** requires:

1. **Plan/Task layer** — operational work decomposition
2. **Tiered context** — bootstrap → current → task → retrieval
3. **Tool registry + MCP** — discoverable, provider-neutral tools
4. **Agent adapters** — swap providers without migrating research state
5. **Verification gates** — prevent agent hallucinations from becoming established truth
6. **Research Home UI** — optimize for understanding, not CRUD
7. **Git workflow** — safe automated checkpointing

This can be implemented **evolutionarily** over 10 weeks in 7 phases, with each phase delivering incremental value.

**The key invariant:** Agents must never need to read SPEC.md. They should work from task context + tools alone.

**End state:** Human steers research at question/hypothesis level. Agents execute, document, and verify. Research memory remains durable and provider-independent.
