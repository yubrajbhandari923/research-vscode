---
name: parameter-tuning
description: Bounded, validation-only parameter tuning with a frozen selection criterion and a one-shot held-out test. Use when automating a parameter search.
---

# Parameter tuning

Appropriate only with an explicit oracle (an objective Sonnet/Opus approved). Prefer established optimizers (scipy, optuna,
grid/random search with a seed) over guessing.

1. Declare before searching: `research orchestrate tuning declare EXP-… --objective "…" --range lr=1e-4:1e-1 --strategy
   "random, seed 0" --selection "min validation rmse; ties → simpler" --budget 40`. The plan is frozen.
2. Each evaluation is a registered run (`research run exec EXP-… -- …`); log it with
   `research orchestrate tuning trial EXP-… --config '{"lr":0.01}' --split validation --value 0.12 --run RUN-…`.
   Only design/validation splits are accepted. Budget is enforced. Keep scientifically relevant failed trials.
3. `research orchestrate tuning select EXP-… --config '{…}' --rationale "…"` freezes the choice.
4. Evaluate held-out test ONCE: `research orchestrate tuning test EXP-… --value … --run RUN-…`. A second call is refused.
5. Opus interprets validity. Many passes of a noisy validation threshold do not prove generalization.
