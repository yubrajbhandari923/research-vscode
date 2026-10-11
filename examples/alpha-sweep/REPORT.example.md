# Research report: alpha-sweep demo

_Generated 2026-10-11 19:07 · git `master` @ `19fe2146c8`_

**Goal:** Understand how the step size alpha trades convergence speed against stability, and pick a baseline alpha.

## Where things stand

- **Current understanding:** - Error vs alpha is U-shaped on the small side (F-001).
- alpha=0.1 is a failed direction (F-002).
- Baseline: EXP-001 with alpha=0.5 (D-001).
- **Open problem:** Stability edge above alpha=1.0 unknown.
- **Next step:** Run EXP-002 (alpha 1.2, 1.5) — expect divergence above ~1.11.
- **Baseline:** EXP-001 — alpha sweep

## What we learned

### F-001 · alpha≈0.5–1.0 gives the best error/stability tradeoff

> In the 60-step test, alpha=1.0 reaches the lowest final error and alpha=0.5 is within the same order of magnitude with less oscillation. alpha=0.1 is an order of magnitude worse.

_supported, medium confidence · by human_

**Evidence:** EXP-001 alpha sweep; RUN-0002 alpha=0.5; RUN-0003 alpha=1.0; A-0007 plot.png

**Limitations:** single seed, 60 steps, one condition number

![A-0007 · final error (log) vs alpha; green = best](../../outputs/plot.png)

### F-004 · alpha=1.0 reaches ~1000x lower final error than alpha=0.5

> After 60 steps final_error is 1.24e-3 at alpha=0.5 and 1.09e-6 at alpha=1.0 (ratio about 1140). Single seed.

_supported, medium confidence · by claude/analyst · reviewed by claude/verifier: supported_

**Evidence:** EXP-001 alpha sweep; RUN-0002 alpha=0.5; RUN-0003 alpha=1.0

**Limitations:** single seed, one spectrum, design data only

| Value | Source (run · metric / calculation) | Artifact | Partition | Verification | Notes |
|---|---|---|---|---|---|
| 0.001238 | RUN-0002 · final_error | — | design | VERIFIED | — |
| 1.086e-06 | RUN-0003 · final_error | — | design | VERIFIED | — |
| 1140 | ratio(RUN-0002:final_error, RUN-0003:final_error) | — | design | VERIFIED | — |

_Numbers are re-checked against the recorded evidence when this report is built. Verified numbers say nothing about whether the interpretation is right; see the review status above._

## Failed directions and contradicted claims

- **F-002** Very small step sizes (alpha=0.1) do not converge in budget [supported]

## Decisions in force

- **D-001** (2026-10-11) Use alpha=0.5 as the baseline for future experiments — based on F-001

## Questions and experiments

### Q-001 · How does parameter alpha affect error? [investigating]

Damped fixed-point iteration on an ill-conditioned quadratic. We want the alpha that minimizes final error within 60 steps without oscillation.

#### EXP-001 · alpha sweep [completed, baseline]

**Hypothesis:** Error is U-shaped in alpha: too small converges slowly, too large oscillates. A mid value (~0.5–1.0) is best.

**Success criteria:** One alpha has lowest final error AND oscillation < 0.5

| run | status | alpha | final_error | oscillation | runtime |
|---|---|---|---|---|---|
| RUN-0001 | completed | 0.1 | 0.2114 | 0 | 0.299 |
| RUN-0002 | completed | 0.5 | 0.001238 | 0 | 0.299 |
| RUN-0003 | completed | 1 | 1.086e-06 | 0.5 | 0.302 |

- **What happened:** Three runs (RUN-0001..0003) at alpha 0.1/0.5/1.0. All completed.
- **Interpretation:** Consistent with the U-shape hypothesis on the small side. At alpha=1.0 half of all coordinate updates already overshoot — we are close to the stability edge.
- **What failed:** alpha=0.1 is far too slow: error barely drops within 60 steps.
- **Next:** EXP-002: probe alpha ∈ {1.2, 1.5} to locate the stability edge.

![A-0007 · final error (log) vs alpha; green = best](../../outputs/plot.png)

#### EXP-002 · Locate the stability edge [proposed]

**Hypothesis:** Error is U-shaped in alpha: too small converges slowly, too large oscillates. A mid value (~0.5–1.0) is best.

**Success criteria:** One alpha has lowest final error AND oscillation < 0.5

_No runs yet._

### Q-002 · Does the best alpha depend on the condition number? [open]

## Open questions

- **Q-001** How does parameter alpha affect error? [investigating]
- **Q-002** Does the best alpha depend on the condition number? [open]
