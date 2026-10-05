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
5. If the result changes how the project proceeds, record a decision linked to the finding.
6. Mark the experiment: `research experiment complete EXP-00X` or `research experiment fail EXP-00X`.
7. `research context` to refresh `.research/context/current.md`.
