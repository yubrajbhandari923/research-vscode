---
name: scientific-visualization
description: "Make rigorous, publication-quality figures from recorded data: choose the right plot, label axes and units, show uncertainty, avoid misleading scales and colour, make them accessible and reproducible from a script that reads recorded values. Use when producing any figure for a finding, report, guide chapter or paper, or reviewing one."
---

# Scientific figures

A figure is evidence. It must be regenerable from recorded data by a script, not from a screenshot.

## Build
1. **Question first**: one claim per figure. Write the sentence the reader should leave with, then pick the plot (comparison → dot/bar with intervals; trend → line; distribution → histogram/violin/ECDF; relationship → scatter with fit and band; field/image → shared colour scale and a scale bar; convergence → log axes with the expected slope).
2. **Data in, figure out**: a script under the experiment's worktree reads recorded metrics/artifacts (`research run list`, `metrics.jsonl`, `research evidence value RUN-… --metric NAME`) and writes the file. Register the output (`research artifact add figures/f.png --run RUN-…`) and, for the Guide, `research guide figure add … --script … --input …`.
3. **Honest encoding**: axes start where the data justifies (bars from zero); log scales labelled as such; the same limits across panels that are compared; no dual axes unless unavoidable; no 3-D for 2-D data.
4. **Uncertainty**: error bars or bands with their meaning stated (SD, SEM, 95% CI, n); show individual points when n is small.
5. **Labels**: every axis has a quantity and a unit; panel letters; legends name the *thing*, not the colour; fonts readable at final size (≥ 8 pt).
6. **Colour and access**: colour-blind safe palettes (viridis/cividis for sequential, a diverging map centred on a meaningful value); never red/green alone; vary marker or line style too; vector output (SVG/PDF) for line art, enough dpi for rasters.
7. **Caption**: what is plotted, over what data (n, split, seeds), axes and units, the uncertainty, and the take-away, without Research OS identifiers in text readers see.
8. **Check**: regenerate from scratch; compare the plotted numbers with the records; view it in greyscale and at final size.

## Never
Smoothing or clipping that hides data without saying so · cherry-picked runs or seeds · a conceptual illustration presented as a measurement (register it as `conceptual`) · figures whose script is lost.
The `scientific-visualization-kdense` skill has detailed matplotlib recipes and journal styles.
