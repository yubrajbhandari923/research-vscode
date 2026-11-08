---
name: synthesize-experiment
description: Turn finished runs into a synthesis, findings and (if warranted) decisions. Use when an experiment needs review or the run budget is reached.
---

# Synthesize an experiment

1. `research experiment show EXP-00X` — read runs, metrics and artifacts.
2. Compare results against the hypothesis and success criteria stated at registration.
3. Write the synthesis (be concrete, cite run IDs):
   ```bash
   research experiment synthesize EXP-00X \
     --happened "…" --worked "…" --failed "…" --interpretation "…" \
     --limitations "…" --unresolved "…" --next "…"
   ```
4. Promote only *reusable* conclusions to findings:
   ```bash
   research finding create "<short title>" --statement "<claim>" \
     --supports EXP-00X RUN-XXXX A-XXXX --confidence low|medium|high \
     --limitations "<scope>" [--kind failure]
   ```
   For every number a finding states, bind it to its source so it can be checked by software, not trusted:
   `research evidence bind F-… --run RUN-… --metric NAME` (reads the value), or `--derive relative_improvement --input RUN-A:m --input RUN-B:m --claim 37.5`.
   `research evidence verify F-…` must pass before review; a plausible explanation never fixes a mismatch (supersede with `--supersedes`).
5. If the result changes how the project proceeds, record a decision linked to the finding.
6. Mark the experiment: `research experiment complete EXP-00X` or `research experiment fail EXP-00X`.
7. Do not write into the Scientist Guide yourself. Important results (supported or starred findings) are logged there
   automatically, and the researcher decides when to ask for a short draft (`research guide draft`).
8. `research current --quiet` to refresh `.research/context/current.md`.
