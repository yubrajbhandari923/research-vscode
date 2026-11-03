# One-time setup: the two environments Research OS needs you to name

Research OS does not guess your Python. Agents, task checks and cluster jobs all run *your* code, so they need an environment that has
your packages. You set up two things once, and the **Initialize Project** wizard asks for both (Execution step):

| | What | Where it is used | Setting |
|---|---|---|---|
| **1. This computer's Python** | a venv (or any interpreter) with your packages | agent sessions, task `checks`, `{python}`, `research env check` | `environment.python` |
| **2. The cluster's Python** (only if you use HPC) | an interpreter/venv that compute nodes can see, plus the lines that activate it | every Slurm job | `hpc.clusters.<name>.python`, `…slurm.setup` |

Everything is also plain YAML in `.research/config.yaml`; the wizard only fills it in for you.

## 1. This computer (uses uv)

[uv](https://docs.astral.sh/uv/) is the simplest way: `brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`.

**In the wizard:** Execution → *Python environment* → **Create one with uv**, list your packages (for example `numpy, matplotlib`). Research OS runs
`uv venv <project>/.venv` and `uv pip install …`, then sets `environment.python`. Nothing is installed globally.

**From the terminal:**

```bash
research env create -k numpy -k matplotlib      # creates <project>/.venv with uv and records it
research env check                              # shows the interpreter and whether each package imports
research env install -k scipy                   # add packages later
```

**Already have an environment?** Choose *Use an existing environment* and give the venv folder, or run
`research config set environment.python /path/to/venv` (an interpreter path works too).

What it changes: agents get a `python`/`python3` launcher for that environment first on `PATH`; `{python}` in a check expands to it
(`cmd:{python} -m pytest -q`); a plain `python3` in a check also resolves to it. If you set nothing, everything falls back to the `python3` on
your PATH, which is exactly what fails when that Python lacks NumPy.

## 2. The cluster (only if you run on Slurm)

Compute nodes are **not** the login node. A package that imports on the login node can be missing on the compute node (this happened in
testing). So the wizard asks for:

1. **SSH host**: an alias from `~/.ssh/config` that already works with keys or an agent (no passwords or MFA prompts).
2. **Remote workspace directory**: where Research OS keeps code snapshots and results. Must be on storage the compute nodes see.
3. **Python on the cluster** *(optional)*: path of an interpreter or venv visible from compute nodes, for example
   `/home/you/venvs/research/bin/python`. Create it once yourself, on the cluster:
   `uv venv ~/venvs/research && uv pip install --python ~/venvs/research/bin/python numpy` (or `python3 -m venv` + `pip`).
4. **Job environment** *(optional)*: shell lines run inside every job, one per row, for example `module load python/3.11` or
   `source ~/venvs/research/bin/activate` or `export PYTHONPATH=/home/you/pylib`.
5. **Packages the jobs need**: for example `numpy`.
6. **Slurm partition** *(optional)*.

Then press **Test connection**. It checks ssh, Slurm tools, the Python, and that your packages import with your setup lines, on the login
node. Tick **Also test on a compute node** to prove it where jobs really run: a one-minute, one-CPU `srun`. From the terminal:
`research hpc doctor --compute`. The same keys in YAML:

```yaml
hpc:
  default_cluster: mycluster
  clusters:
    mycluster:
      ssh_host: my-login-alias
      paths: {workspace_root: /scratch/you/research-os}
      python: /home/you/venvs/research/bin/python
      required_packages: [numpy]
      slurm:
        partition: defq
        setup:
          - source /home/you/venvs/research/bin/activate
```

`research hpc init --yes` creates the remote folders (the wizard does not touch the cluster beyond read-only checks).

## 3. Writing plans against these environments

* Use `{python}` or plain `python3` in `cmd:` checks; do not hard-code interpreter paths in plans.
* Agents run headless with permission to edit files and run `research …` commands only; to run your code they use
  `research run exec EXP-001 -- python3 script.py` (which uses the project Python).
* Code that will run on the cluster must write results where Research OS can collect them (see the HPC result contract in
  [SUPERVISOR.md](SUPERVISOR.md#8-hpc)); the implementer brief tells agents this automatically.
* A Codex agent runs in a sandbox without network, so it cannot call `research hpc submit` itself; use Claude for submission tasks or submit manually.

## 4. Checklist before your first plan run

- [ ] `research env check` is green (or you consciously rely on the system `python3`)
- [ ] `research hpc doctor --compute` is green (HPC only)
- [ ] `research provider list` shows your agent CLIs installed and signed in
- [ ] `research plan yaml validate your-plan.yaml`
- [ ] the VS Code Control Center → **Plans** opens without a warning banner
