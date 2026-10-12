---
name: hpc-setup
description: Configure, diagnose and repair remote HPC execution (SSH + Slurm). Use when `research hpc` reports a problem, when a project needs remote execution for the first time, or when the user asks why remote submission is not working.
---

# Set up or repair remote HPC execution

Everything here is diagnosis and configuration. **Never `ssh` to the cluster to do research work by
hand** — the `research hpc` commands enforce the approved paths, resource limits and provenance, and a
run submitted outside them has no record.

## 1. Always start here

```bash
research hpc doctor
```

It checks, in order: the cluster profile, SSH connectivity, Slurm tools, Python, each configured path,
and whether this project is deployed. Every failing check prints a specific fix. Read them before
changing anything — the usable modes line at the end tells you what you *can* do right now:

| doctor says | what works |
|---|---|
| `ready: ssh-slurm, bundle` | everything |
| `ready: ssh-direct, bundle` | no Slurm, but commands can run over SSH |
| `bundle` only | `research hpc bundle` → the human runs it → `research hpc import` |

A `bundle`-only cluster is not broken. It is the fallback, and it needs no connection at all.

## 2. "No HPC cluster is configured"

Expected on a project that has not opted in. Ask the user for their cluster's facts — **never invent
them**, and never assume `$HOME` is an appropriate place for data:

- the SSH alias they already use (`ssh <alias>` must work before Research OS can)
- the approved writable workspace root
- the shared dataset directory
- the partition and account, if their site requires them

Then decide **where** it belongs:

```bash
research hpc config --example      # prints a complete profile to copy
```

- **One project uses this cluster** → `.research/config.yaml` under `hpc.clusters.<name>`.
- **Several projects share it** (the usual case) → `~/.config/research-os/config.yaml`, so each project
  only adds its own `hpc.clusters.<name>.project.slug`. Check whether one already exists:
  `research config show --sources | tail -2`.

Confirm the layering afterwards: `research config show --sources` labels every value `default`,
`machine` or `project`.

## 3. "sbatch=NO" but `ssh <host>` then `sbatch` works by hand

The classic one. `ssh host <cmd>` gets a **non-interactive** shell, which on many clusters skips the
profile scripts (and auto-loaded modules) that put Slurm on `PATH`. Research OS therefore runs remote
commands through `bash -lc`, which reproduces what the user sees interactively.

Diagnose it directly:

```bash
ssh <host> 'command -v sbatch; echo $PATH'            # non-interactive: may be a bare PATH
ssh <host> 'bash -lc "command -v sbatch; module list"'  # login shell: what the user gets
```

- Found only in the login shell → it should already work; make sure `login_shell` is not set to `false`
  for this cluster.
- Found in neither → look for a module (`ssh <host> 'bash -lc "module avail 2>&1 | grep -i slurm"'`)
  and add `module load <name>` to that cluster's `slurm.setup`.
- The site's profile is slow or does something unwanted non-interactively → set `login_shell: false` and
  put the needed `module load` lines in `slurm.setup` instead.

`slurm.setup` lines run **inside every job** on the compute node, which is where your code's own modules
(python, cuda, compilers) belong — a compute node does not inherit the login node's environment.

## 4. Other failures and what they mean

| Check | Likely cause and fix |
|---|---|
| `ssh connectivity` fails | Research OS uses `BatchMode=yes`, so it cannot answer a password or MFA prompt. The user needs a key or an agent. Verify by hand: `ssh <host>` |
| `python` below 3.8 | the bundled runner needs ≥3.8. Set `python:` for the cluster, or `module load` a newer one in `slurm.setup` |
| a path "does not exist yet" | `research hpc init` creates it, if the parent is writable. If the parent is not, the user must supply an approved location |
| `shared_data` read-only | fine. Datasets are read-mostly; only staging needs write access |
| `scancel` absent | everything works except `research hpc cancel` |

## 5. First deployment

```bash
research hpc init                      # creates this project's remote dirs; creates only, never deletes
research hpc inspect                   # what is actually on the cluster
research hpc datasets list             # the filesystem is the source of truth; there is no registry
```

## 6. Before the first real submission

```bash
research hpc submit EXP-001 --dry-run  # shows exactly what would happen; transfers nothing
research hpc submit EXP-001 --yes      # only after the user has seen the dry run
```

If the dry run reports a missing dataset, resolve that first (`research hpc datasets check <id>`) rather
than passing `--stage-datasets` reflexively: staging moves a lot of bytes and may cost quota.

**If a submission is reported uncertain, do not resubmit.** The job may already be queued. Run
`research hpc sync`, which reads the remote receipt and adopts the existing job.

## 7. Record what you changed

Configuration is project state. After setting a cluster up, say so where the next session will see it:

```bash
research config check                  # validate before you declare success
research note new "HPC setup" "Cluster <name>: <what you configured and why>"
```

Do not paste credentials, private keys or tokens into any Research OS file. Authentication lives in
`~/.ssh/config` and nowhere else.
