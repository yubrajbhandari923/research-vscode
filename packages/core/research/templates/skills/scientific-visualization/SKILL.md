---
name: scientific-visualization
description: Create or review quantitative research plots and tables that accurately communicate measured results. Use when preparing figures, comparing experimental runs, inspecting simulations, or drafting a scientific report.
---

# Make evidence-bearing figures

1. Resolve the registered experiment, runs, metrics, and artifacts. Read the data source rather than extracting numbers from an old screenshot.
2. Decide which scientific comparison the figure must answer. Show baseline and controls where they matter, and use the same scale and normalization for comparable data.
3. Label axes, units, sample sizes, and meaningful uncertainty. Describe preprocessing, masking, normalization, interpolation, and any excluded observations.
4. Respect physical geometry and coordinate frames for image/field data. Use perceptually appropriate color scales; avoid misleading truncated axes or cherry-picked examples.
5. When showing distributions, distinguish individual samples from uncertainty around a summary. Do not equate many pixels/voxels with independent patients or runs.
6. Register the generated figure as an artifact of its source run or analysis, preserving plotting code and inputs whenever practical.
7. Write a caption stating what is plotted, how it was computed, and what it cannot establish. Use the existing `scientific-briefing` workflow for Guide drafts.

Never invent uncertainty bars or present illustrative/synthetic values as observations.
