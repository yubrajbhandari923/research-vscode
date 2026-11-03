---
name: reproducibility-audit
description: Audit whether a completed computation or experiment can be recreated from code, inputs, parameters, environment, seeds, commands, and artifacts. Use before sharing results, publishing, handing off, or promoting experimental code.
---

# Audit computational reproducibility

1. Open the registered experiment and runs (`research experiment show EXP-...`); identify the exact claim being audited.
2. Confirm the source revision or dirty-tree state, command, parameters, random seeds, dependency versions, operating environment, hardware when relevant, and any nondeterministic settings.
3. Trace data inputs to stable identifiers, versions or checksums; distinguish raw, derived, and cached data. Record permissions and external dependencies without copying credentials or sensitive data.
4. Confirm that each reported table, metric, and figure is linked to the correct registered run and artifact. Missing files are not substitutes for a recorded artifact path.
5. Re-execute a tiny representative case if authorized and within budget; compare with a predeclared tolerance. Do not launch the full experiment without resource approval.
6. Report **reproducible**, **partially reproducible**, or **not established**, with explicit missing pieces and the smallest corrective action.
7. Use `research checkpoint` or project records to preserve a concise audit trail and hand off to an independent reviewer when scientific validity is in question.

Never assert reproducibility solely because the current agent can read the same code. Preserve failed seeds, environment incompatibilities, and deviations.
