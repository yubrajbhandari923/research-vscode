#!/usr/bin/env bash
# Builds the demo research project by walking the full protocol with the `research` CLI.
#   bash run_demo.sh [target-dir]       (default: ./demo-project)
# Requires the `research` command (pip install -e <repo>, or a VS Code terminal with the extension installed).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${1:-$HERE/demo-project}"
rm -rf "$DEST" && mkdir -p "$DEST" && cp "$HERE/sim.py" "$HERE/plot.py" "$DEST/"
cd "$DEST"
git init -q && git add sim.py plot.py && git -c user.name=demo -c user.email=demo@example.com commit -qm "demo code"
export RESEARCH_AUTHOR_TYPE=human; unset CLAUDECODE RESEARCH_AGENT || true

research init --name "alpha-sweep demo" --goal "Understand how the step size alpha trades convergence speed against stability, and pick a baseline alpha."
research question create "How does parameter alpha affect error?" -d "Damped fixed-point iteration on an ill-conditioned quadratic. We want the alpha that minimizes final error within 60 steps without oscillation."
research experiment create "alpha sweep" -q Q-001 \
  --hypothesis "Error is U-shaped in alpha: too small converges slowly, too large oscillates. A mid value (~0.5–1.0) is best." \
  --motivation "Need a principled default before the larger ablations." \
  --method "Run sim.py for alpha ∈ {0.1, 0.5, 1.0}, 60 steps, fixed seed. Compare final error and oscillation." \
  --expected "alpha=0.5 or 1.0 lowest error; 0.1 clearly worse." \
  --success "One alpha has lowest final error AND oscillation < 0.5" \
  --stop "Stop after 3 runs; do not refine the grid before synthesis." \
  --param 'alpha=[0.1,0.5,1.0]' --param steps=60 --param seed=0 \
  --metric final_error --metric oscillation --metric runtime --expect-artifact plot.png --planned-runs 3

for A in 0.1 0.5 1.0; do
  research run exec EXP-001 --param alpha=$A --label "alpha=$A" -- python3 sim.py --alpha $A --out outputs/alpha_$A
done
python3 plot.py outputs/plot.png
research artifact add outputs/plot.png --experiment EXP-001 -d "final error (log) vs alpha; green = best"

research experiment synthesize EXP-001 \
  --happened "Three runs (RUN-0001..0003) at alpha 0.1/0.5/1.0. All completed." \
  --worked "alpha=1.0 reached the lowest final error (~1e-6); alpha=0.5 reaches ~1e-3 with zero overshoot (oscillation 0 vs 0.5)." \
  --failed "alpha=0.1 is far too slow: error barely drops within 60 steps." \
  --interpretation "Consistent with the U-shape hypothesis on the small side. At alpha=1.0 half of all coordinate updates already overshoot — we are close to the stability edge." \
  --limitations "Single seed; 60 steps only; one condition number." \
  --unresolved "Where does oscillation/divergence start above 1.0?" \
  --next "EXP-002: probe alpha ∈ {1.2, 1.5} to locate the stability edge." --complete

research finding create "alpha≈0.5–1.0 gives the best error/stability tradeoff" \
  -s "In the 60-step test, alpha=1.0 reaches the lowest final error and alpha=0.5 is within the same order of magnitude with less oscillation. alpha=0.1 is an order of magnitude worse." \
  --supports EXP-001 RUN-0002 RUN-0003 A-0007 --confidence medium --status supported --questions Q-001 \
  --limitations "single seed, 60 steps, one condition number"
research finding create "Very small step sizes (alpha=0.1) do not converge in budget" --kind failure \
  -s "alpha=0.1 leaves error ~10x higher after 60 steps. Do not use as a default unless the step budget grows." \
  --supports RUN-0001 --confidence high --status supported
research decision create "Use alpha=0.5 as the baseline for future experiments" \
  --reason "Near-best error with the lowest oscillation; safer than 1.0 until the stability edge is known (F-001)." \
  --findings F-001 --experiments EXP-001
research experiment baseline EXP-001
research experiment variant EXP-001 --param 'alpha=[1.2,1.5]' --title "Locate the stability edge" \
  --motivation "F-001 leaves the large-alpha side unexplored; oscillation should appear above 1.0."
research question create "Does the best alpha depend on the condition number?" --status open
research note new "Why alpha matters here" --body "Personal note: the spectrum is in [0.2, 1.8], so theory says divergence at alpha > 2/1.8 ≈ 1.11. EXP-002 should confirm." --link EXP-002 Q-001 --pin
research checkpoint --title "Baseline chosen: alpha=0.5" \
  --understanding "- Error vs alpha is U-shaped on the small side (F-001).
- alpha=0.1 is a failed direction (F-002).
- Baseline: EXP-001 with alpha=0.5 (D-001)." \
  --problem "Stability edge above alpha=1.0 unknown." \
  --next "Run EXP-002 (alpha 1.2, 1.5) — expect divergence above ~1.11."
echo
research resume
