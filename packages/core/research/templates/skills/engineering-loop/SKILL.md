---
name: engineering-loop
description: Specification → tests → implementation loop across Opus/Sonnet/Haiku tiers with bounded retries. Use for any engineering task; skip tiers that the task does not need.
---

# Engineering loop

Assign by difficulty and verifiability, not label: `research orchestrate route T-…` shows the tier. Not every task needs all three.

With an allocation strategy active (`research strategy show`), read "Opus/Sonnet/Haiku" below as the strategy's deep/coordinator/fast levels:
the coordinator sets the task contract (`research orchestrate contract`), the worker checks itself with
`research orchestrate verify T-… --tier LEVEL --record` (frozen tests, file boundary, approved hooks, task checks), and `--tier` takes level names.

1. **Opus** gives a bounded task: scientific intent, assumptions, acceptance criteria. Unclear science → stays with Opus.
2. **Sonnet** reads the interface and writes the contract (inputs, outputs, units, invariants, edge cases, errors).
3. **Sonnet** writes an independent test suite that checks behavior, invariants and numerical properties, not a copy of
   the implementation. Run it; confirm it fails for the right reason.
4. `research orchestrate freeze T-… tests/test_x.py` freezes the oracle. Completion is blocked if it changes.
5. Haiku-suitable (fully specified, well tested)? `research orchestrate dispatch T-… --tier haiku` (refused without frozen
   tests). Otherwise Sonnet implements. Delegate children with `research orchestrate delegate T-parent "title" --tier haiku`.
6. **Haiku**: load `ponytail` first; smallest complete implementation; run tests; log each attempt:
   `research orchestrate attempt T-… --tier haiku --outcome fail --category incorrect_implementation`. Max 3 attempts,
   then escalate as the command says. Never edit frozen tests.
7. **Sonnet** reviews (`ponytail-review`), runs integration/regression checks, then `research task done T-…` (checks gate it).
8. Wrong test? Sonnet only: `research orchestrate correct-test T-… path --reason "…" --tier sonnet` (logged).
9. Ambiguous science or a questionable oracle → escalate to Opus; do not cycle between agents.

Failure categories: incorrect_implementation, ambiguous_specification, insufficient_test_oracle, environment,
scientific_assumption, resource_limit. Passing unit tests is not scientific validity: computational experiments are
registered experiments and runs (`research run exec`), with the usual run budgets and exit code 3.
Only Opus updates the Scientist Guide.
