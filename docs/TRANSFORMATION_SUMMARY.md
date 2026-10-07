# Research OS Transformation — Executive Summary

**Date:** 2026-10-05
**Prepared by:** Claude Sonnet 4.5
**Status:** Architecture approved, ready for implementation

---

## Vision

Transform Research Panel from a **research CRUD interface** into a **research control plane** where:

- **You** operate at the level of questions, hypotheses, and high-level research objectives
- **Agents** handle implementation, experimentation, debugging, plotting, and bookkeeping
- **The system** maintains durable, provider-independent research memory

---

## Current State Assessment

### ✅ Strengths to Preserve

Your existing Research Panel implementation is **excellent** in these areas:

1. **Storage architecture** — Filesystem-first (Markdown/YAML), SQLite as derived index
2. **Provenance** — Git state, authorship tracking, audit log
3. **Python core** — Clean separation, works on HPC, no heavy dependencies
4. **Agent discipline** — Run budgets, synthesis enforcement
5. **SLURM integration** — Safe concurrent access, no SQLite on compute nodes

**Decision: Keep these entirely.** Do not redesign persistence or core architecture.

### ❌ Critical Gaps

1. **No task/plan layer** — Experiments conflate scientific tests with operational work
2. **Context too large** — Agents must read full history or navigate complex CRUD
3. **No orchestration** — Cannot decompose high-level objectives into executable units
4. **No verification gate** — Agent outputs immediately become established truth
5. **No agent adapter** — Hard-coded provider detection, cannot swap models
6. **UI optimized for CRUD** — Not for understanding research state at a glance

---

## Proposed Solution

### 6 Major Additions (Evolutionary, Not Revolutionary)

#### 1. Plan/Task Layer
```
Plan: "Reproduce Paper X Figure 3"
  ├─ Task: Understand methodology
  ├─ Task: Implement baseline
  ├─ Task: Sanity run
  ├─ Task: Full reproduction → creates Experiment + Runs
  ├─ Task: Generate plots → creates Artifacts
  ├─ Task: Verify against paper → creates Finding
  └─ Task: Write synthesis
```

**Why:** Separates operational work from scientific experiments.

#### 2. Tiered Context System

```
Tier 0 (Bootstrap):  AGENTS.md                    ~500 tokens
Tier 1 (Current):    research plan current         ~1-2k tokens
Tier 2 (Task):       research task context T-042   ~1-3k tokens
Tier 3 (Retrieval):  research finding show F-023   on-demand
Tier 4 (Raw logs):   run stdout/stderr             last resort
```

**Why:** Agents work with bounded context, not full history dumps.

#### 3. Tool Registry + MCP Server

```
Research OS Tools (deterministic):
  - get_task_context
  - run_experiment
  - register_artifact
  - create_finding
  - verify_tests
  - compare_runs
  - git_checkpoint

Exposed via:
  - Python API (existing)
  - CLI (existing)
  - RPC for VS Code (existing)
  - MCP Server (NEW) → any MCP-compatible agent
```

**Why:** Tools > prompts. Agent-portable, discoverable, typed.

#### 4. Agent Adapter Layer

```yaml
# .research/config.yaml
agents:
  roles:
    planner: strong_reasoner
    implementer: cheap_coder
    verifier: strong_reviewer

  profiles:
    strong_reasoner:
      adapter: anthropic
      model: claude-opus-4-5

  adapters:
    anthropic: claude_code | api
    openai: api
    manual: human
```

**Why:** Swap models without migrating research state. ROLE ≠ MODEL ≠ PROVIDER.

#### 5. Verification System

```
Agent observes result
   ↓
Creates preliminary finding
   ↓
Links evidence (runs, artifacts, metrics)
   ↓
Verification task (deterministic + optional model-based)
   ↓
Status: supported | contradicted | remains preliminary
```

**Why:** Prevent agent hallucinations from becoming established truth.

#### 6. Research Home UI

```
Current Focus
  Goal: ...
  Understanding: Top 5 findings
  Blocker: ...
  Next: ...

Active Plan
  ✓ Task 1 done
  ✓ Task 2 done
  ● Task 3 running
  ○ Task 4 ready

Latest Insights
  + Finding (verified)
  ✗ Failed direction

Needs My Attention
  ⚠ Task timeout
  ⚠ Verifier disagrees

Key Outputs
  [Plots, figures, tables]
```

**Why:** Understand project state in 2 minutes, not 10.

---

## Implementation Plan

### 10-Week Phased Rollout

| Phase | Duration | Goal | Risk |
|-------|----------|------|------|
| **0. Foundation** | Week 1 | Add Plan/Task entities to storage | LOW (additive) |
| **1. Context** | Week 2 | Tiered context generation | MEDIUM |
| **2. Tools + MCP** | Week 3 | Tool registry, MCP server | LOW (optional) |
| **3. Adapters** | Week 4 | Agent adapter layer | MEDIUM |
| **4. Verification** | Week 5 | Verification workflow | LOW |
| **5. Orchestration** | Week 6-7 | End-to-end plan execution | HIGH |
| **6. Research Home UI** | Week 8-9 | UI redesign | MEDIUM |
| **7. Git Workflow** | Week 10 | Safe auto-checkpointing | LOW |

**Each phase delivers independently.** Can ship 0-4 without 5-7.

### Phase 0 Detail (Week 1) — Ready to Implement

**Files to create:**
- `packages/core/research/plans.py` — Plan/Task CRUD
- `tests/python/test_plans_tasks.py` — Storage tests

**Files to modify:**
- `schema.py` — Add Plan/Task tables, migration v1→v2
- `mirrors.py` — Add Plan/Task rendering
- `__init__.py`, `rpc.py`, `main.py` — Export/expose Plan/Task

**Success criteria:**
- All existing tests pass
- Can create/list Plan and Task
- Plan/Task survive rebuild
- Hand-edited mirrors import correctly

**Detailed spec:** See `docs/PHASE_0_SPEC.md`

---

## Key Decisions

### What We ARE Building

✅ Research control plane for autonomous agents
✅ Durable research memory (filesystem-first)
✅ Plan/Task operational decomposition
✅ Provider-neutral agent adapters
✅ Verification gates
✅ Tiered context (bootstrap → current → task → retrieval)
✅ MCP tool exposure
✅ Research Home UI (understanding > CRUD)

### What We Are NOT Building

❌ Autonomous-agent platform (no unattended AutoGPT)
❌ Workflow engine (not Airflow/Prefect)
❌ Experiment tracker (not W&B/MLflow clone)
❌ Project management (not Jira/Linear)
❌ Chatbot UI
❌ Vendor lock-in

---

## Invariants

These must remain true:

1. **Filesystem is source of truth** — SQLite always rebuildable
2. **Agents are clients, not developers** — Use tools, not internals
3. **Context must be bounded** — No full-history dumps
4. **Provenance is mandatory** — Git + authorship always recorded
5. **Manual control remains** — Humans can override/edit
6. **Backwards compatible** — Existing projects work without migration

---

## Success Metrics

### Before (Current State)

**High-level task:** "Reproduce paper Figure 3"
**Steps:** 15+ manual experiment/run creations, navigate 8+ UI screens
**Time to understand project after 2 weeks:** ~10 minutes

### After (Target State)

**High-level input:** "Reproduce Figure 3, focus on quantitative accuracy"
**Planner:** Creates 8-task plan automatically
**Executor:** Runs tasks autonomously
**Verifier:** Checks outputs
**You:** Review synthesis in Research Home (2 minutes to understand state)

### Black-Box Agent Test

**Goal:** Agent completes research task with access to:
- `AGENTS.md` (500 tokens)
- `research task context T-042` (focused context)
- Tool schemas

**Without access to:**
- `README.md`, `SPEC.md`, source code
- Database schema
- Full research history

**Success:** Task completes, artifacts/findings recorded correctly.

---

## Risks & Mitigation

| Risk | Severity | Mitigation |
|------|----------|------------|
| Context generation inadequate | HIGH | Black-box agent testing, iterate |
| MCP integration breaks workflows | MEDIUM | MCP is optional, can disable |
| Plan/Task DAG too complex | MEDIUM | Start simple, linear tasks |
| Agent adapter interface too narrow | MEDIUM | Design for extensibility, 2+ implementations |
| UI redesign disrupts users | LOW | Keep CRUD available, new UI additive |

---

## Deliverables

### Documentation Created

1. ✅ **`docs/ARCHITECTURE_AUDIT.md`** — Full architecture review (12,000 words)
2. ✅ **`docs/PHASE_0_SPEC.md`** — Detailed Phase 0 implementation (5,000 words)
3. ✅ **`docs/MCP_INTEGRATION.md`** — MCP feasibility analysis + examples (3,500 words)
4. ✅ **`docs/TRANSFORMATION_SUMMARY.md`** — This executive summary

### Ready for Implementation

- Schema changes defined
- Migration v1→v2 specified
- CRUD operations designed
- CLI commands specified
- RPC methods listed
- Tests outlined
- File checklist complete

---

## Next Steps

### Immediate (This Week)

1. **Review and approve** this architecture direction
2. **Decide:** Proceed with Phase 0 implementation?
3. **Assign:** Who implements? (You, contractor, Claude Code iteration)

### Week 1 (Phase 0)

1. Implement Plan/Task schema + CRUD
2. Add CLI commands: `research plan create/list/show`, `research task create/list/show/next`
3. Write tests
4. Verify: All existing tests pass, new tests pass
5. Manual testing: Create plan, create tasks with dependencies, verify task DAG

### Week 2+ (Phase 1 onwards)

Follow phased rollout plan. Each phase can be reviewed/adjusted based on Phase 0 learnings.

---

## Open Questions for You

1. **Timeline:** Is 10-week phased rollout acceptable? Or prioritize differently?
2. **MCP:** Implement in Phase 2 (recommended) or defer/skip?
3. **UI:** Redesign in parallel with backend work, or after Phases 0-5 complete?
4. **Scope:** Implement all 7 phases, or stop at minimal viable (Phases 0-4)?
5. **Dogfooding:** Use Research OS to track Research OS development? (meta!)

---

## Recommendation

✅ **Proceed with Phase 0 implementation**

**Rationale:**
- Low risk (additive, no breaking changes)
- High value (enables all future phases)
- Clear specification (ready to code)
- Testable (existing tests + new tests)
- ~1 week effort

**After Phase 0:** Reassess. If Plan/Task foundation is solid, continue to Phase 1 (context). If issues surface, iterate.

**Long-term vision:** Your Research OS becomes the standard protocol for durable, agent-portable research memory. Anyone can:
- Clone your repo
- See research history in human-readable files
- Continue work with any agent provider
- Understand state in minutes
- Trust verified findings

---

## Questions?

**For implementation details:**
- See `docs/ARCHITECTURE_AUDIT.md` (comprehensive)
- See `docs/PHASE_0_SPEC.md` (code-level detail)
- See `docs/MCP_INTEGRATION.md` (MCP specifics)

**For discussion:**
- Architecture decisions
- Timeline adjustments
- Scope changes
- Implementation approach

Ready to build the future of computational research memory.
