---
name: reproduce-paper
description: Staged protocol for reproducing a paper's results without runaway implementation or sweeps.
---

# Reproduce a paper

## Phase 1 — Understand (no code execution)
- Read the paper; list the concrete claims/figures/tables to reproduce.
- Inspect the repo; identify datasets, dependencies, hyperparameters, and **unknowns**.
- Write the list into a question description (`research question create "Can we reproduce Fig. 3?" -d "…"`).

## Phase 2 — Plan
- One question per claim you intend to reproduce (Q: reproduce Fig. 3, Q: reproduce error scaling …).
- Register *proposed* experiments for each, with explicit success criteria (e.g. "within 5% of Table 2").
- Stop and show the plan to the human if the compute cost is significant.

## Phase 3 — Implement
- Write code and unit tests for components. Do **not** launch expensive runs.

## Phase 4 — Sanity run
- Smallest viable configuration, registered as a run with `--label sanity`.

## Phase 5 — Reproduction experiment
- Run only the planned runs. Respect the run budget.

## Phase 6 — Synthesis
- Synthesize; create findings comparing to the paper; document every deviation
  (data, preprocessing, hyperparameters, compute) in limitations.
- Stop. Do not launch unrelated follow-ups; propose them instead.
