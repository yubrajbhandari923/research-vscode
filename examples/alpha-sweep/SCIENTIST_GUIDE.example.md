# alpha-sweep demo — Scientist Guide

<!-- research:guide:begin — narrative maintained by the scientist-guide skill -->

We are studying a toy problem on purpose: a damped fixed-point iteration x ← x − α·Λx, where Λ is a diagonal "curvature"
matrix with eigenvalues spread over [0.2, 1.8] (Q-001). It is a stand-in for any iterative solver, and it is synthetic: nothing
here is an empirical measurement of a physical system. For this linear map each mode contracts by |1 − αλ| per step, so the
whole iteration converges only for α < 2/λ_max ≈ 1.11, and the slowest mode (λ = 0.2) sets how fast a *small* α can work.
Our question is therefore a trade: how large can α be before overshoot appears, and how much does that buy.

EXP-001 ran α ∈ {0.1, 0.5, 1.0} for 60 steps with one seed (RUN-0001..0003). The recorded results agree with the
mode-by-mode picture. α = 0.1 barely converges in 60 steps (F-002, a recorded failure direction). Between the other two, a
first draft of our write-up (F-003, now superseded) described α = 1.0 as "about 5x lower" error than α = 0.5; that was wrong.
The recorded final errors are 1.24e-3 at α = 0.5 (RUN-0002) and 1.09e-6 at α = 1.0 (RUN-0003), a ratio of about 1140, and
the evidence check (F-004/B1–B3) confirms those numbers against the run records. The fix is kept in the history: F-003 is
superseded by F-004, not edited. Why so large? A back-of-envelope analysis (not a recorded result): the slowest mode (λ = 0.2) contracts by 0.9 per step at
α = 0.5 and by 0.8 per step at α = 1.0, and after 60 steps 0.9⁶⁰ ≈ 1.8e-3 versus 0.8⁶⁰ ≈ 1.5e-6, the same order as the recorded
errors. A modest per-step difference compounds over 60 steps; α = 1.0 is still inside the stability bound for the stiffest mode
(|1 − 1.0·1.8| = 0.8).

What this does and does not establish. F-004 is *supported*: its numbers verify against the recorded metrics and a reviewer
checked the reasoning. It is still a single-seed result on one spectrum, evaluated on the design split only, so it says
nothing yet about how α should be chosen on other problems. The earlier baseline choice of α = 0.5 (D-001) was made for
stability margin, not accuracy, and EXP-001 does not contradict it. The open question is where oscillation starts above 1.0
(the planned EXP-002 at α ∈ {1.2, 1.5}); the theory predicts divergence beyond α ≈ 1.11, and we have not yet tested that.

_Evidence note: EXP-001/S1; RUN-0002, RUN-0003; F-001, F-002, F-003 (superseded), F-004; D-001. Quantities above were read with
`research evidence value` / `verify`, not copied from earlier prose. The theoretical 2/λ_max figure is analysis, not a recorded result._

<!-- research:guide:end -->
