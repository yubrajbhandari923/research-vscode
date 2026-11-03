# Third-party notices

Research OS (MIT) ships curated copies of a few third-party agent skills so that projects work offline. Each copy lives in
`packages/core/research/templates/catalog/<skill>/` with the upstream `LICENSE` and a `SOURCE.yaml` (repository, path, pinned commit, retrieval date,
what was copied, whether anything was modified). Nothing is changed except where `SOURCE.yaml` says so. Skills whose licence does not allow
redistribution under ours (Trail of Bits: CC-BY-SA-4.0) or that publish no licence (Vercel) are **not** shipped: they are listed in the catalog and
installed from their source, at a pinned commit, only when the user asks.

## DietrichGebert/ponytail
- Licence: MIT · Copyright: DietrichGebert · Source: https://github.com/DietrichGebert/ponytail
- Pinned commit: `96608e078b2d5ea607c5c3b8b5a54d1c1fba6846`
- Skills: `ponytail`, `ponytail-review`

## KalarisLabs/research-agent-skills
- Licence: MIT · Copyright: Kalaris Labs · Source: https://github.com/KalarisLabs/research-agent-skills
- Pinned commit: `fa023d6d166562a8de118f59bb73647c0e444b8c`
- Skills: `reproducibility-statement`

## anthropics/skills
- Licence: Apache-2.0 · Copyright: Anthropic, PBC · Source: https://github.com/anthropics/skills
- Pinned commit: `ca45fc496a3436251f191354091d557da31c15b5`
- Skills: `frontend-design`, `webapp-testing`

## k-dense-ai/scientific-agent-skills
- Licence: MIT · Copyright: K-Dense Inc. · Source: https://github.com/k-dense-ai/scientific-agent-skills
- Pinned commit: `a25cf9ec00e2745d03f8704acf6f6919694ccd9a`
- Skills: `experimental-design`, `hypothesis-generation`, `literature-review`, `scientific-critical-thinking`, `statistical-analysis`, `statistical-power`, `exploratory-data-analysis`, `scientific-writing`, `peer-review`, `citation-management`, `matplotlib`, `sympy`, `simpy`, `pytorch-lightning`, `shap`, `scikit-learn`, `pydicom`, `qiskit`, `cirq`, `pennylane`, `dask`, `scientific-visualization-kdense`

## obra/superpowers
- Licence: MIT · Copyright: Jesse Vincent · Source: https://github.com/obra/superpowers
- Pinned commit: `2ce8aa15d05bd57213e6f746f953aafb7ded7a8b`
- Skills: `systematic-debugging`, `verification-before-completion`, `requesting-code-review`, `receiving-code-review`

The K-Dense skills carry a request to cite the project if a skill materially contributed to work; that note is preserved in each `SKILL.md`.
