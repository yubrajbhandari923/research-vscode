---
name: uncertainty-analysis
description: Quantify uncertainty, robustness, and sensitivity in computational research. Use when drawing claims from noisy data, sparse observations, stochastic simulations, imperfect models, or uncertain input conditions.
---

# Analyze uncertainty before claiming reliability

1. Separate sources: sampling noise, measurement error, random seeds, uncertain parameters, model discrepancy, numerical error, missing observations, and domain shift.
2. Identify the estimand, unit of analysis, primary metric, and target population or deployment setting. Avoid treating repeated correlated observations as independent samples.
3. Choose an appropriate uncertainty method (independent replications, bootstrap with correct grouping, analytic propagation, ensembles, or calibrated intervals), and state assumptions.
4. Compare the baseline and proposed method under the **same** scenarios and randomization when appropriate. Examine worst-case and distribution-shift conditions rather than only an average-case score.
5. Include a sensitivity experiment for influential nuisance variables. Preserve the original metric and success criterion; do not tune against the held-out final evaluation.
6. Link numerical claims to runs and metrics using Research OS evidence records where supported, then state which conclusions survive uncertainty and which do not.
7. Report confidence/credible interval meaning precisely, practical effect size, number of truly independent replications, and limitations.

A sensitivity curve is not automatically a probability interval. Synthetic-noise robustness is not evidence of real-world calibration without validation data.
