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

Software remains 0.7.0 and schema remains 3 pending the
[1.0.0 review gates](RELEASE_1_0_0_REVIEW.md). No schema migration is required by this hardening pass.

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
