---
name: validation-oracles
description: "Build independent checks that a computation is right before trusting its numbers: analytic or limiting cases, conservation laws and invariants, dimensional and unit analysis, convergence under refinement, symmetry, a second implementation, and known benchmark values. Use when implementing a numerical method, simulation, solver, reconstruction or metric, or when a result is surprisingly good."
---

# Independent oracles

A program that agrees with itself proves nothing. An oracle is a check whose correctness does not depend on the code under test. Add oracles as tests or as approved hooks (`research hook add NAME --stage post_implementation --command "pytest tests/oracle -q"`), and freeze them before implementing when a task requires frozen tests.

## Pick from this list (at least three, from different families)
- **Exact or limiting case**: a closed form, a trivial input (zeros, identity, a point source), a limit where the answer is known.
- **Invariants and conservation**: mass, energy, probability sums to 1, positivity, monotonicity, symmetry, idempotence, norms bounded, round trip `decode(encode(x)) ≈ x`.
- **Dimensional / unit analysis**: every equation balances; inputs carry units; scaling a variable scales the output as theory says.
- **Convergence**: halve the step, mesh or tolerance and confirm the error falls at the *expected order*; a wrong order is a bug even if values look fine.
- **Second path**: an independent, slower or simpler implementation (brute force, symbolic with sympy, a library routine) on small cases.
- **Metamorphic relations**: transformations with known effect (rotate input → rotate output; scale data → scale metric; permute samples → same result).
- **Known benchmarks**: published values, standard test problems, reference datasets, with the tolerance they justify.
- **Adversarial inputs**: NaN, empty, extreme magnitudes, ill-conditioned cases must fail loudly or behave as specified.

## Rules
- Tolerances come from the method (truncation error, floating point, noise), not from making the test pass. Write down where each came from.
- An oracle must be able to fail: break the code on purpose once and watch it fail.
- Never tune the oracle to the implementation. If it disagrees, find out which one is wrong.
- Record what passed and what it does *not* cover (`research evidence bind` for numbers used in findings).
