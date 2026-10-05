---
name: run-experiment
description: Register, execute and track a computational experiment with bounded runs. Use before any substantial run, sweep, or SLURM job.
---

# Run an experiment

1. Check context: `research status` and `.research/context/current.md`.
2. Make sure a question exists (`research question list`), otherwise create one.
3. Register the experiment *before* running:
   ```bash
   research experiment create "<title>" --question Q-00X \
     --hypothesis "<falsifiable claim>" --motivation "<why now>" \
     --method "<what will be run>" --expected "<expected result>" \
     --success "<how we judge it>" --planned-runs <N> --stop "<stop condition>" \
     --param key=value --metric <name> --expect-artifact <file>
   ```
   For a small change of an existing experiment use `research experiment variant EXP-00X --param key=new`.
4. Sanity run first (smallest input): `research run exec EXP-00X --label sanity -- <cmd>`.
5. Real runs: `research run exec EXP-00X --param k=v -- <cmd>` (or `research run submit` for SLURM).
   Log metrics from code with `research.log_metric(name, value, step=None, unit=None)`.
6. Register important outputs: `research artifact add <path> --run RUN-XXXX -d "<what it shows>"`.
7. When runs finish or the budget stops you: follow the `synthesize-experiment` skill.
8. Stop. Report what you learned and propose (don't start) the next experiment.
