---
name: clean-dataset
description: Treat data cleaning/conversion as an experiment with validation evidence rather than an untracked script run.
---

# Clean / convert a dataset

1. Question: "Can dataset X be converted into analysis-ready form?"
2. Experiment: evaluate the pipeline on a small sample (e.g. 20 files) first.
   Success criteria: explicit checks (schema, geometry, ranges, missing values).
3. Runs: sample run → full run. Never overwrite raw data; write to a new location.
4. Artifacts to register: cleaned dataset path, validation report, error/exclusion list, summary statistics.
5. Findings: what was excluded and why, what checks pass ("14 corrupted files excluded; all retained files pass geometry checks").
