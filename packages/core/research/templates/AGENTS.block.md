<!-- research:begin — managed by `research init`; edit outside these markers freely -->
## Research protocol (for coding agents)

This project keeps its research state in `.research/` — questions, experiments, runs,
findings, decisions and checkpoints. The `research` CLI (or `import research` in
Python) is the only supported way to change it. Everything you do must stay traceable:
**question → experiment → runs → artifacts/metrics → findings → decisions → checkpoint**.

### Start of every session
1. Read `.research/context/current.md` (regenerate with `research context`). Do not read the whole history.
2. If the task is non-trivial, `research resume` shows the latest checkpoint and open threads.
3. Identify yourself once per shell: `export RESEARCH_AGENT=<your-name> RESEARCH_AGENT_MODEL=<model>`.

### The loop (mandatory for substantial computational work)
1. **UNDERSTAND** — read the code/paper/data. Identify unknowns. No execution yet.
2. **PLAN** — find or create the question: `research question create "…"`.
3. **REGISTER** — before running anything substantial:
   ```
   research experiment create "title" --question Q-001 \
     --hypothesis "…" --motivation "why" --expected "…" --success "criteria" \
     --param alpha=0.1 --metric rmse --expect-artifact plot.png \
     --planned-runs 3 --stop "stop if … / after 3 runs"
   ```
4. **IMPLEMENT** — write code and unit tests. Do not launch sweeps.
5. **SANITY CHECK** — smallest viable run, registered like any other run.
6. **RUN** — every execution goes through the experiment:
   `research run exec EXP-001 --param alpha=0.1 -- python train.py --alpha 0.1`
   (SLURM: `research run submit EXP-001 --time 02:00:00 --gpus 1 -- python train.py`).
   Inside your code, `research.log_metric("rmse", 0.12)` attaches to the active run automatically.
7. **COLLECT EVIDENCE** — `research artifact add path/to/plot.png --run RUN-0003 -d "what it shows"`.
8. **SYNTHESIZE** — `research experiment synthesize EXP-001 --happened … --worked … --failed … --interpretation … --next …`
9. **STOP or PROPOSE** — record reusable results as findings, then stop and report.
   Propose the next experiment (status `proposed`); do not start unrelated follow-ups.

### Hard rules
- **No invisible experiments.** Runs belong to a registered experiment.
- **Budgets are enforced**: after `max_runs_without_synthesis` finished runs (see `.research/config.yaml`),
  `research run …` exits with code 3 until you synthesize. Same after too many failed runs.
  Never use `--override` unless the human explicitly told you to.
- **No unbounded sweeps.** Declare planned runs up front; ask before exceeding them.
- **Findings** (`research finding create`) are reusable conclusions with evidence (`--supports EXP-001 RUN-0003 A-0002`),
  a confidence and limitations. Failed directions are findings too (`--kind failure`). Not every observation is a finding.
- **Decisions** (`research decision create`) record deliberate choices, linked to findings.
- **Checkpoint** (`research checkpoint create`) at major stopping points and before ending a long session.
- **Never silently rewrite history.** Supersede findings/decisions instead of editing their meaning;
  every change is logged in `.research/events.jsonl`.
- **Project notes (`.research/notes/`) belong to the human.** Do not create or fill them.
- Large outputs stay where they are produced; register their paths, never copy them into `.research/`.

### Reference
`research --help`, `research <group> --help`. Every command accepts `--json` for machine-readable output.
Python: `import research; research.get_resume_context()`, `research.create_finding(...)`, …
Skills for common workflows: `.research/skills/*/SKILL.md` (list with `research skills list`).
<!-- research:end -->
