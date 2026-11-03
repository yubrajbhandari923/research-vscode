# Upgrading an existing research project

A copy-pasteable sequence for each existing repository. Nothing here deletes or rewrites research records,
and every step shows you what it would do before it does it.

Run it from the project root (the directory containing `.research/`).

## 0. Install the new Research OS

```bash
# VS Code extension (the Python core is bundled inside it)
code --install-extension dist/research-panel-X.Y.Z.vsix

# and/or the CLI, if you use it outside VS Code (HPC login nodes, sbatch scripts, notebooks)
pip install -e /path/to/research-vscode
```

## 1. Snapshot first (optional but cheap)

`.research/` is text. If the project is in git, commit it; if not, copy it.

```bash
git add .research AGENTS.md && git commit -m "Before Research OS X.Y.Z upgrade" || cp -a .research .research.before-X.Y.Z
```

## 2. See what the upgrade would change

```bash
.research/bin/research upgrade --status
.research/bin/research upgrade --dry-run
```

`--dry-run` changes nothing at all — not even the SQLite index. Read the "Required" list; everything under
"Preserved" is left alone.

## 3. Review the managed AGENTS.md block separately

```bash
.research/bin/research agents-md --diff
```

Only the text between the two `research:begin` / `research:end` marker comments can change. Your own
sections are preserved byte for byte. If it reports ambiguous markers, fix them by hand first — it will
refuse rather than guess.

## 4. Apply

```bash
.research/bin/research upgrade --apply
```

Safe to re-run. Edited metadata files are backed up to `.research/cache/upgrade-<timestamp>/`.

## 4b. Optional: model strategies, hooks and usage (0.6 / 0.7)

Nothing here is required; every new setting defaults to off. Existing projects keep the legacy tier routing until you choose a strategy.

```bash
.research/bin/research strategy list            # built-in strategies
.research/bin/research strategy use sonnet_first --dry-run
.research/bin/research strategy use sonnet_first
.research/bin/research setup agents --apply     # regenerates .claude/agents/research-<level>.md; managed legacy files are moved aside, yours are untouched
.research/bin/research agents-md --diff         # the managed AGENTS.md block gained strategy/contract/hook pointers
```

Hooks stay inert until you run `research hook approve`. Running agent sessions keep the configuration they started with, so stop them
before switching if you want a clean cut-over.

## 4c. Optional: Scientist Guide v2 (1.1)

`upgrade --apply` only adds the `scientific-briefing` skill; it never creates the Quarto guide. To adopt it:

```bash
.research/bin/research guide quarto init --dry-run   # lists files; existing ones are left untouched
.research/bin/research guide quarto init             # your docs/SCIENTIST_GUIDE.md stays and appears as an appendix
.research/bin/research guide coverage                # what the new guide does not cover yet
```

Nothing in `.research/` records changes. Details: `docs/GUIDE.md`.

## 5. Prove nothing was lost

```bash
.research/bin/research status
.research/bin/research report --full --check
```

`--check` must print `coverage verified`. It also lists any **source-data incompleteness** (artifact files
that have moved, experiments never synthesized) — that is information about your data, not an upgrade
failure.

Then confirm the history is still rebuildable from the text files:

```bash
.research/bin/research rebuild
.research/bin/research report --full --check      # same verdict, and the same bytes
```

## 6. Optional: turn on HPC for this project

Only if you want remote execution. Skip it entirely and everything else keeps working.

```bash
.research/bin/research hpc config --example       # prints a profile to copy into .research/config.yaml
```

Edit `.research/config.yaml` and fill in **your cluster's approved paths** — Research OS will not guess a
storage location, and `$HOME` is never assumed to be right for data:

```yaml
hpc:
  default_cluster: duke
  clusters:
    duke:
      transport: ssh
      ssh_host: plp-drcc-login2        # an alias that already works: `ssh plp-drcc-login2`
      paths:
        workspace_root: /CHANGE/ME     # approved, writable
        shared_data: /CHANGE/ME        # shared datasets (may be read-only)
      slurm: { partition: null, account: null, time: "04:00:00", cpus: 4, mem: 16G, gpus: 1 }
      project: { slug: this-project }
```

Then:

```bash
.research/bin/research hpc doctor      # SSH, sbatch/squeue/sacct, python, storage permissions
.research/bin/research hpc init        # create this project's remote directories (creates only)
.research/bin/research hpc datasets list
```

`doctor` tells you which execution modes are usable. If SSH is unavailable, portable bundles
(`research hpc bundle EXP-001`) still work with no connection at all.

## If something looks wrong

Nothing is destroyed, so you can always go back:

```bash
git checkout .research AGENTS.md          # or: rm -rf .research && mv .research.before-X.Y.Z .research
```

An older Research OS refuses to open a project migrated to a newer schema rather than damaging it, so
downgrading the tool without restoring `.research/` is safe but will simply decline to run.

## Independent stabilization changes (unreleased)

Software is 1.0.0 and the schema remains 3. No schema migration is required. New transient files appear under
`.research/` (`.write.lock`, `.transaction.json`, `.recovery-needed`, `locks/`); they are gitignored, and
`orchestration/executions/` holds execution records.

Rebuild now refuses malformed canonical records and duplicate IDs instead of omitting them. It
backs up a readable live database, rolls back failed imports, and does not rewrite canonical
mirrors. Complete malformed JSONL records are errors; incomplete trailing records are preserved
and block appends until repaired. Back up files before repairing them. Resolve front-matter IDs
and references for branch collisions; changing filenames alone is insufficient.

Enabled required hooks without current approval now block their gate. Review and explicitly
approve the command or deliberately disable the hook. Generic task completion updates now apply
the same checks as `task done`. Re-freezing changed tests is refused; use the correction workflow.
Manually attached completed runs without a zero exit code no longer pass `experiment check`.
These are intentional correctness changes; existing records are not rewritten to manufacture a pass.

For an unreadable SQLite index, stop clients and back up the project before moving the index aside.
Do not delete canonical records, logs or journals as an automatic repair step. See the README and
review for remaining crash-consistency and concurrency limitations.


## Enabling the shared ledger (optional)

Nothing about existing projects changes unless you opt in. `research ledger enable` is a dry run that lists every step and blocker (tracked
`.research/` files, an unfinished journal, an existing target). `--apply` creates branch `research/ledger` and its worktree, copies the canonical
records, rebuilds the index there and compares per-table counts with the original, links the repository, and renames the original `.research/` to
`.research.pre-ledger` (kept; nothing is deleted). Any failure before linking removes only what was created. To undo: `git worktree remove <ledger>`,
delete `<git common dir>/research/ledger.json`, rename `.research.pre-ledger` back to `.research`. Nothing is committed for you.


## 2.1 → 2.2: the Markdown Scientist Guide is gone

`docs/SCIENTIST_GUIDE.md` and `.research/guide.json` are no longer read, and `research guide refresh` / `done` no longer exist.
Nothing is deleted: keep the file, or paste what you want into the new book. Create the Quarto guide with `research guide init`
(it adds `docs/scientist-guide/` and `.github/workflows/publish-guide.yml` on main; commit them). A guide created by 2.1 keeps its
`_quarto.yml`: `research guide log` adds `results.qmd` itself, and `research guide chapter add ideas.qmd --owner human` and
`research guide chapter add references.qmd --owner human` list the other new chapters (add `bibliography: references.bib` if absent).
