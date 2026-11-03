---
name: failure-triage
description: "Diagnose a failed or suspicious run, test or experiment systematically: classify the failure, reproduce it, isolate the cause with evidence, fix the cause once, and record what was learned. Use when a run fails, a metric is NaN or implausible, a test flakes, results contradict the hypothesis, or a task check fails twice."
---

# Failure triage

Do not retry hoping. Classify first, then reproduce, then isolate.

## 1. Classify (decides everything else)
| Class | Signs | First move |
|---|---|---|
| Environment | ModuleNotFound, CUDA/driver, path, permissions, out-of-memory, queue/time limit | `research doctor`, the exact interpreter and versions, `research run logs RUN-…` |
| Code defect | exception, wrong shape/dtype, off-by-one, test failure | minimal reproduction, a failing test |
| Numerical | NaN/Inf, divergence, instability, tolerance misses | smaller inputs, double precision, check conditioning and learning rate |
| Data | missing/changed files, leakage, distribution shift, label error | hash and inspect the inputs (`research evidence artifact`) |
| Design | works as coded but contradicts the hypothesis | do not "fix"; write it up as a result (`research finding create … --kind failure`) |
| Flaky | passes sometimes | fix seeds, remove time/ordering dependence, then rerun N times |

## 2. Reproduce
Get the failure in the smallest, fastest form. Record the exact command, parameters, commit and seed (they are in `research show RUN-…`). If you cannot reproduce it, say so and collect more evidence instead of guessing.

## 3. Isolate
Bisect: `git bisect` over commits, halve the input, remove one component, swap in the oracle or baseline. Form one hypothesis, make one change, observe. Print intermediate values at the first place they go wrong, not at the end. Use `systematic-debugging` for the full loop.

## 4. Fix once, at the cause
Add a regression test that fails before the fix. Re-run the original failing run through `research run exec` so the record shows it now passes (never edit a failed run's record).

## 5. Record
A failed attempt that taught something is a result: `research finding create "…" --kind failure --statement "what failed, why, how to avoid it"`. Update the experiment synthesis. Escalate (`research task` blocker) after two failed attempts at the same class instead of looping.
