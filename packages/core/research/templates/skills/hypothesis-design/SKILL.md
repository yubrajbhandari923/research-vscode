---
name: hypothesis-design
description: Turn an idea into a falsifiable hypothesis with a prediction, controls, baselines, a primary metric, success and failure criteria and the confounds that could fool you, before any run exists. Use when creating or revising an experiment, when a hypothesis is vague ("improves", "better"), or when results arrive and nobody wrote down what would count as failure.
---

# Design the hypothesis before the run

An experiment states, in advance, what would change your mind. Write it into the experiment record (`research experiment create "…" --hypothesis "…"`; edit with `research experiment update`).

## Write each of these
1. **Claim**: one sentence, one direction, one population/regime. "Method A reduces RMSE versus baseline B on set S by at least 10%" — not "A works better".
2. **Prediction and mechanism**: what you expect to see and why. If you cannot say why, the experiment is exploration: label it exploration and do not report it as a test.
3. **Baseline(s)**: the simplest competitor that could plausibly win (a no-op, the previous method, a trivial heuristic). Link it as the experiment baseline when it exists.
4. **Metric and unit**: one primary metric decided now; secondary metrics named now. Say how it is computed and on which split.
5. **Controls**: what is held fixed (data, seed policy, hardware, preprocessing, budget) and what is varied. Vary one thing, or justify a factorial design.
6. **Success and failure criteria**: numeric thresholds and the comparison rule (effect size and uncertainty, not "looks better"). State what result would refute the claim.
7. **Data discipline**: which data tunes, which validates, which is the held-out test used once (`research tuning declare` for bounded tuning).
8. **Confounds and threats**: leakage, seed luck, selection of the best run, metric gaming, different compute budgets. For each, how the design guards against it.
9. **Budget**: runs, seeds, wall time. Stop rules prevent post-hoc sweeps.

## Check before leaving
- Could a result exist that makes you say "refuted"? If not, tighten the claim.
- Are success criteria written *before* any run of this experiment? Edit afterwards only with a recorded reason.
- Pair with `validation-oracles` (independent checks) and `uncertainty-analysis` (how many seeds you need).
