# Skills, hooks and MCP: what they are for

Research OS keeps the records. These three things shape what an **agent** does with them. All of them are set up by **Initialize Project** and managed in
**Agents → Skills, hooks and MCP** (and by the `research skills` / `research hook` commands); the files stay the source of truth.

| | What it is | Why it matters | New project |
|---|---|---|---|
| **Skills** | short written procedures, one `SKILL.md` each | an agent follows your project's way of working instead of improvising; a *required* skill is injected into its brief and hashed | the Core Research pack, plus any packs you chose, linked for Claude Code and Codex |
| **Hooks** | commands **you approve**, run by Research OS at fixed points | deterministic checks replace tokens an agent would spend re-checking; an unapproved hook is *skipped*, never *passed* | nine cheap starter hooks, approved by you in the wizard |
| **MCP server** | the records as tools an agent calls directly (`research_current`, `task_context`, `finding_review`, `guide_status`, `report`, …) | the agent reads the real state and writes records through the same rules as the CLI | registered for each enabled agent |

## Skills

### Where they come from

`research skills catalog` lists 58 skills in three kinds:

* **first-party** (19): written for Research OS, shipped in every project.
* **bundled** (31): curated copies of third-party skills (MIT or Apache-2.0 only), each with its upstream `LICENSE`, a `SOURCE.yaml` (repository, path, **pinned commit**, retrieval date, what was copied) and an entry in `THIRD_PARTY_NOTICES.md`. They work offline and are never modified (one rename is recorded).
* **external** (8): listed with their licence and a pinned commit, but **not shipped**. They are installed from their GitHub source only when you ask (`research skills install NAME`, needs network) because their licence does not allow bundling under ours (Trail of Bits skills are CC-BY-SA-4.0: ShareAlike) or none is published (Vercel's repository), or they are large (Anthropic's `skill-creator`).

What was evaluated and why:

| Source | Licence | Taken | Left out, and why |
|---|---|---|---|
| Ponytail | MIT | `ponytail`, `ponytail-review` (scientific-python) | audit/debt/gain/help: meta commands for its own tooling |
| Superpowers | MIT | `systematic-debugging`, `verification-before-completion`, `requesting-code-review`, `receiving-code-review` (core) | TDD, plans, worktrees, finishing-a-branch: overlap Research OS's `engineering-loop`, plans, worktrees and promotion |
| K-Dense scientific-agent-skills | MIT | experimental design, hypothesis generation, literature review, critical thinking, statistics, power, EDA, writing, peer review, citations, matplotlib, SymPy, SimPy, PyTorch Lightning, SHAP, scikit-learn, pydicom, Qiskit, Cirq, PennyLane, Dask (+ one plotting skill renamed `scientific-visualization-kdense`) | ~150 domain skills (biology, chemistry, …): outside the packs. Large ones ship `SKILL.md` only; their references stay upstream at the pinned commit |
| Trail of Bits skills | CC-BY-SA-4.0 | catalog entries only (`modern-python`, `property-based-testing`, `mutation-testing`, `dimensional-analysis`, `differential-review`) | ShareAlike would bind our files; offered as install-from-source |
| Anthropic skills | Apache-2.0 | `frontend-design`, `webapp-testing` (frontend) | `skill-creator` offered from source (large); document skills (docx/pdf/…) are not research tools |
| Vercel agent-skills | none published | catalog entries only (`web-design-guidelines`, `react-best-practices`) | no licence file: cannot be redistributed |
| Kalaris research-agent-skills | MIT | `reproducibility-statement` | the rest are adaptations of K-Dense skills (duplicates) |

### Packs

| Pack | For | Bundled third-party skills | Install from source (optional) |
|---|---|---|---|
| **Core Research** (`core-research`) · default | Research workflow, verification and good scientific method. Always on; the default for every new project. | the 19 first-party skills + `systematic-debugging`, `verification-before-completion`, `requesting-code-review`, `receiving-code-review`, `experimental-design`, `scientific-critical-thinking`, `hypothesis-generation` | — |
| **Scientific Python** (`scientific-python`) | Statistics, exploratory analysis, plotting, symbolic maths and code-quality skills for Python research code. | `ponytail`, `ponytail-review`, `statistical-analysis`, `statistical-power`, `exploratory-data-analysis`, `matplotlib`, `scientific-visualization-kdense`, `sympy` | `modern-python`, `property-based-testing`, `mutation-testing` |
| **Machine Learning** (`machine-learning`) | Training, evaluation, explanation and honest comparison of models. | `pytorch-lightning`, `scikit-learn`, `shap`, `statistical-power`, `exploratory-data-analysis` | — |
| **Physics / Numerical Simulation** (`physics-numerical`) | Symbolic derivation, discrete-event and numerical simulation, units and convergence checks. | `sympy`, `simpy`, `matplotlib` | `dimensional-analysis` |
| **Medical Imaging** (`medical-imaging`) | DICOM handling and imaging-specific data care. | `pydicom`, `exploratory-data-analysis`, `scientific-visualization-kdense` | — |
| **Quantum Computing** (`quantum-computing`) | Qiskit, Cirq and PennyLane circuits, simulation and variational methods. | `qiskit`, `cirq`, `pennylane`, `sympy` | — |
| **HPC** (`hpc`) | Remote execution on Slurm clusters and parallel data processing. | `dask` | — |
| **Frontend Development** (`frontend`) | Distinctive UI design and browser testing for research tools and dashboards. | `frontend-design`, `webapp-testing` | `web-design-guidelines`, `react-best-practices` |
| **Scientific Writing** (`scientific-writing`) | Papers, reviews, citations and reproducibility statements, for the Scientist Guide and beyond. | `scientific-writing`, `literature-review`, `citation-management`, `peer-review`, `reproducibility-statement`, `scientific-visualization-kdense` | — |

First-party skills (Core Research): `research-context`, `hypothesis-design`, `validation-oracles`, `failure-triage`, `reproducibility-audit`, `uncertainty-analysis`, `literature-evidence`, `scientific-visualization`, `independent-review`, `promote-validated-work`, and the existing `run-experiment`, `engineering-loop`, `parameter-tuning`, `clean-dataset`, `reproduce-paper`, `hpc-setup`, `scientific-briefing`, `scientific-handoff`, `synthesize-experiment`.

### Activation: a bigger toolbox is not a bigger prompt

A skill reaches an agent's brief only when it is **assigned**, its `applies_to` matches the task's **type** (`research`, `implementation`, `experiment`, `analysis`, `verification`, `synthesis`, `debug`, `data`) or **role** (`planner`, `implementer`, `verifier`, `analyst`), or it is **always on** (only `research-context`). Everything else is one line in the skill list. *Required* skills are injected with their hash. The defaults ship in the catalog and are copied into `.research/skills.yaml`, where you change them (`research skills set NAME --applies-to analysis verifier`, or Agents → Skills → *edit*).

### Configuration (files; the UI edits the same keys)

```yaml
# ~/.config/research-os/config.yaml  (machine: the default for every new project)   or   .research/config.yaml  (project: wins)
skills:
  packs: [core-research, scientific-python, machine-learning]   # which packs are on
  disabled: [shap]                                               # on disk, not suggested, not linked
harness:
  require_seed: false          # experiment-config fails instead of warning when a run records no seed
  invariants:                  # numeric-invariants: declared bounds per metric
    rmse: {min: 0}
```

CLI: `research skills catalog [--search words] [--pack ID] [--kind K]` · `skills packs [enable ID [--optional] | disable ID]` · `skills install NAME…` · `skills enable|disable NAME…` · `skills upgrade` (bring bundled/first-party skills to this release; **a skill you changed is never replaced**) · `skills set` · `skills link` · `skills add <folder|git url>` for anything else. Your own skills are never touched.

## Hooks

Commands **you** approve, run by Research OS before a task is dispatched, after an agent's change, before a run, or after runs finish. Approval is a hash of stage, command and timeout, so any edit revokes it, and agents cannot approve. Each has a timeout; every execution is appended to `.research/hooks/results.jsonl`. A hook is binding only when it is required; the catalog ones are advisory (a failure is reported, never blocks) and **do not count as the verifier** that a task needs to pass (`orchestrate verify` still wants real task checks or a required hook).

The checks are ordinary programs (`python -m research.checks NAME`): no network, no model, and they never run your code, except `tests`, which is why it is optional. A check that cannot run says `SKIPPED: why` instead of passing silently.

| Hook | Stage | Default | What it checks |
|---|---|---|---|
| `syntax-check` | post implementation | **default** | Python files compile after an agent's change (catches syntax errors before anything runs). |
| `whitespace-check` | post implementation | **default** | No conflict markers or trailing-whitespace errors in the change. |
| `git-integrity` | post implementation | **default** | The repository is healthy: no damaged objects, unresolved conflicts or half-finished merge/rebase. |
| `secrets` | post implementation | **default** | No private keys or well-known token formats in changed or new files (values are never printed). |
| `task-scope` | post implementation | **default** | A task changes only the files its contract allows. |
| `frozen-tests` | post implementation | **default** | A task's frozen acceptance tests are byte-identical to what was approved. |
| `experiment-config` | post experiment | **default** | Runs record the code revision, a clean tree and the command; a seed is flagged (an error with harness.require_seed). |
| `numeric-invariants` | post experiment | **default** | No NaN/Inf metric values; optional bounds from harness.invariants (e.g. rmse: {min: 0}). |
| `artifact-integrity` | post experiment | **default** | Registered artifacts exist and still match their recorded hash. |
| `lint` | post implementation | optional · if python | ruff (or pyflakes) on the changed Python files; skipped, and says so, when neither is installed. |
| `format` | post implementation | optional · if python | ruff format / black --check on the changed Python files. |
| `tests` | post implementation | optional · runs your code | Runs the project's pytest suite. It executes project code, so it is offered, never automatic. |
| `tsc` | post implementation | optional · runs your code | TypeScript type check with the project's own compiler (never downloads one). |

**Project-aware:** `research hook recommend` (and Agents → Hooks → *Recommended for this project*) detects the stack from marker files (Python, TypeScript, notebooks, scientific/ML/medical/quantum dependencies, Slurm) and shows what applies; `research hook install` adds every cheap hook that fits; `--approve` approves them (your decision). Add your own with `research hook add NAME --stage post_implementation --command "…" [--advisory]`.

## MCP

Registered per agent by the wizard (`main/.mcp.json`, `.agents/mcp_config.json`, or `codex mcp add`). Agents → each provider card shows *MCP registered*; **Register** and **Test** repair or prove it (the server is started from the directory the agent runs in and its tools are listed). Everything in MCP is also in the CLI; it spares the agent from guessing syntax. It covers the core loop (records, tasks, runs, findings and reviews, reports, the Scientist Guide, `hpc_doctor`, `hpc_sync` and `hpc_bundle`, and the supervised-agent reporting tools); strategies, orchestration, hooks, usage and HPC submission need the CLI. The full tool list, generated from the code, is in [the interfaces chapter](spec/09-interfaces.md#mcp-server).

## Limits

* Third-party skill text is not reviewed for scientific correctness by Research OS; it is attributed, pinned and read-only unless you edit it (then it is yours and updates leave it alone).
* The catalog pins commits at release time; `skills upgrade` brings *bundled* skills to the release's version, external ones are reinstalled with `skills install --force`.
* A *direct* Claude Code or Codex session bypasses dispatch: it only sees linked skills and `AGENTS.md` (`research skills audit`).
* Hooks do not sandbox anything; an approved hook runs with your permissions.
