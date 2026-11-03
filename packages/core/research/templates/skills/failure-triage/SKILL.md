---
name: failure-triage
description: Diagnose failed experiments, broken tests, numerical instability, and agent execution errors without losing evidence or repeatedly burning compute. Use when a check fails, a run disagrees with expectations, or retries are being considered.
---

# Diagnose a failure systematically

1. Capture the task/experiment/run IDs, exact command, exit code, logs, input parameters, and machine/environment. Use `research experiment show EXP-...` and `research run logs RUN-... --tail 100` when applicable.
2. Classify the failure: specification ambiguity, implementation, test oracle, scientific assumption, environment/dependency, resource limit, numerical instability, or data problem.
3. Reproduce with the smallest deterministic input. Change **one factor at a time** and record each attempt; do not relaunch a costly job before inspecting its existing receipt or status.
4. Compare invariants and independent baselines; inspect units, shapes, indexing, precision, seeds, and boundary conditions before changing algorithmic assumptions.
5. Preserve the failed case as a regression test or registered negative result. Do not erase logs, rerun under the same ID, or present a newly passing run as the original outcome.
6. Where the failure implicates the scientific hypothesis or the acceptance oracle, escalate to the researcher or designated reviewer instead of silently repairing the metric or criterion.
7. Summarize root cause (confirmed vs suspected), fix, evidence, residual risk, and next bounded action.

A retry is justified by a new diagnosis, not simply because the previous attempt failed.
