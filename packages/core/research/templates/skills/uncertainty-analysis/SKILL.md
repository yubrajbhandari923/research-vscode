---
name: uncertainty-analysis
description: "Quantify how much a result can be trusted: repeated runs and seeds, confidence intervals and effect sizes, sensitivity to parameters and preprocessing, robustness to noise and data splits, and error propagation. Use before comparing methods, before claiming an improvement, when a difference is small, or when a single run is being reported."
---

# Uncertainty analysis

A single number is an anecdote. Decide how you will quantify uncertainty *before* the comparison (`hypothesis-design`).

1. **Sources**: list what can vary: seeds/initialisation, data splits, sampling noise, measurement noise, hardware nondeterminism, hyperparameters. Vary each deliberately.
2. **Replicates**: run each configuration with enough independent seeds (at least 5; use a power estimate when the effect is small). Never pick the best seed. Log every run (`research run exec` per seed) so the spread is in the records.
3. **Summaries**: report mean ± standard error or a confidence interval (bootstrap when the distribution is unknown), the effect size, and the number of runs. A comparison needs the uncertainty of the *difference* (paired by seed/split when possible).
4. **Sensitivity**: change one parameter or preprocessing choice at a time over a plausible range; report which ones the conclusion depends on. A conclusion that flips under a reasonable perturbation is not a finding.
5. **Robustness**: add noise, shift the data, change the split, drop a subset. State the range over which the claim holds, and where it stops.
6. **Propagation**: when a metric is computed from other measurements, propagate errors (analytic first-order, or Monte Carlo) rather than quoting false precision. Keep significant figures honest.
7. **Multiple comparisons**: many methods, metrics or subsets inflate false positives. Correct (Holm/Bonferroni/FDR) or say the analysis is exploratory.
8. **Held-out evidence**: tune on validation, report once on the test set; never re-tune after looking.

## Record
Bind each number to its source (`research evidence bind F-… --run RUN-… --metric NAME --claim …`; derived values through a declared calculation) and put the uncertainty and its method in the finding's limitations. Say plainly when the data cannot distinguish the options.
