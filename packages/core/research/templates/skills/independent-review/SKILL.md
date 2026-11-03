---
name: independent-review
description: "Review another agent's or person's work as an independent verifier: check methodology, evidence, numbers, controls and conclusions against the records, and give a verdict (supported, needs work, contradicted) with reasons. Use when dispatched as verifier, when asked to review a finding, experiment, task result or guide chapter, or before accepting your own surprising result."
---

# Independent review

You are not the author. Do not edit what you review; record a verdict (`research finding review F-… --verdict supported|needs_work|contradicted --notes "…" [--confidence low|medium|high]`). The same agent that wrote a finding cannot review it.

## Checklist (read the records, not the summary)
1. **Claim vs evidence**: does the cited evidence (`research show F-…`, its runs, artifacts, bindings) actually show the claim? Re-check numbers: `research evidence verify F-…`, `research evidence value RUN-… --metric NAME`.
2. **Design**: was the hypothesis falsifiable and written before the runs? baseline fair (same data, budget, tuning effort)? controls adequate? leakage between tuning and test?
3. **Uncertainty**: enough seeds/splits for the effect size; intervals reported; a difference within noise treated as noise.
4. **Correctness**: independent oracles pass (`validation-oracles`); results survive a perturbation you choose; code is the recorded commit and clean.
5. **Interpretation**: alternative explanations considered; limits stated; scope not overgeneralised from one dataset or phantom.
6. **Provenance**: every number and figure traces to a recorded run/artifact; nothing copied from memory; sources verified (`literature-evidence`).
7. **Process**: policy gates respected (frozen tests unchanged, run budget, no unregistered runs).

## Verdict
- **supported**: the evidence supports the claim *as worded*; numerical bindings verified; your notes state the interpretation you checked (verified numbers do not make an interpretation correct).
- **needs_work**: fixable gaps; list each as a concrete action.
- **contradicted**: evidence contradicts it; cite the record.
Be specific and brief: finding, evidence, what would change your mind. Do not soften a defect to be agreeable, and do not invent concerns to appear thorough.
