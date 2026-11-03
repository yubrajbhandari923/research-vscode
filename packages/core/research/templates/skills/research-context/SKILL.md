---
name: research-context
description: "Recover where the project stands before acting: goal, open questions, experiments and their state, decisions in force, the task you were given and what is claimed or blocked. Use at the start of every session, after a context reset, when taking over from another agent, or when you are unsure what has already been tried."
---

# Recover the research state first

Never act from memory or from the last chat. The records are the memory.

1. `research current` (regenerates `.research/context/current.md`): goal, focus, what needs attention, active experiments, decisions in force. Read all of it.
2. Your assignment: `research task context T-…` (MCP `task_context`) gives the task, its plan, acceptance checks, frozen tests, claimed-by and the skills that apply.
3. What was already tried: `research search "<keywords>"`, then `research show EXP-…` / `RUN-…` / `F-…` / `D-…` for the ones that matter. A failed or contradicted finding is information: read why.
4. What changed recently: `research log` (audit trail) and `git log --oneline -15` in the worktree you are in (`pwd`, `git branch --show-current`).
5. What you are allowed to touch: the task contract (`research orchestrate contract T-…`) and the experiment's worktree. Do not edit `main/` unless the task says so.
6. Write down the state in two or three lines before you start (goal, what is known, what is unknown). If two records disagree, say so rather than choosing silently.

## Stop and ask when
- the task conflicts with a decision in force (`D-…`), or with its own acceptance checks;
- another agent has the task claimed or the experiment has unreviewed runs past the budget;
- you cannot find the experiment the work belongs to: register one first, do not run unregistered.
