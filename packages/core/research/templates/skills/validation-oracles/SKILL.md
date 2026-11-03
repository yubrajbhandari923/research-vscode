---
name: validation-oracles
description: Design independent deterministic checks for scientific code, numerical simulators, reconstruction algorithms, and data transformations. Use before implementation or whenever a passing unit test may not establish physical or numerical correctness.
---

# Build independent validation oracles

1. Write the contract: inputs, outputs, shapes, units, coordinate conventions, domains, invariants, tolerances, and expected failure behavior.
2. Select checks that are **independent of the implementation**: analytic toy solutions, conservation laws, adjoint or symmetry identities, limiting cases, manufactured solutions, reference datasets, or trusted alternative algorithms.
3. Add unit and property-based tests plus a tiny integration test. Choose tolerances from numerical error analysis; do not widen them until a bug disappears.
4. Separate deterministic engineering checks from scientific-validation experiments. A test suite can verify code while the scientific model remains wrong.
5. For an orchestration task, define acceptance checks, independently review them, and use Research OS frozen-test mechanisms where appropriate. Never edit frozen oracles through an implementer role.
6. Execute the cheapest oracle first, inspect counterexamples, and report a table of pass/fail/not-run with the command, environment, and limitation.
7. If a check is expensive, propose it as a project-approved `post_implementation` or `pre_experiment` hook rather than making it a universal default.

Do not claim that an implementation and its own generated reference output are independent evidence.
