# Governance, approvals, provider handoff and YAML setup (2.4)

Research OS is the supervisor. Claude Code, Codex and Antigravity are workers: they propose, you decide.

## 1. Who may create what

```yaml
governance:
  experiments:
    agent_creation: propose_only      # propose_only | human_only
  tasks:
    agent_creation: bounded_auto      # bounded_auto | propose_only | human_only
    allowed_auto_types: [bugfix, diagnostic, verification]
    max_auto_per_experiment: 3
  scientific_changes:
    require_human_approval: true
  approval:
    require_pin: true
    require_rationale: true
    record_rejections: true
    confirm_human_experiment_creation: false
  experiment_overrides: {}            # EXP-0001: {task_creation: human_only}  (can only be stricter)
```

* **Experiments.** An agent can never create one. With `propose_only` it submits a proposal (hypothesis, rationale, method, expected outputs, relation to existing work); with `human_only` it cannot even propose. `research proposal experiment …` or the MCP tool `experiment_propose`.
* **Tasks.** `human_only`: no agent task creation or proposals. `propose_only`: agents propose, a human approves. `bounded_auto`: an agent may add a task of an allowed type (default bugfix, diagnostic, verification) inside its own experiment, up to the limit; anything else becomes a proposal. Scientific changes always wait for you.
* **Precedence.** `governance` is the ceiling. The older `supervisor.auto_apply_operational` and `supervisor.max_auto_tasks` can only tighten it: a proposal auto-applies only if governance allows it *and* the supervisor switch is on *and* both caps hold (the smaller wins). Experiment overrides can only be stricter than the project policy.
* **Humans.** Creating an experiment yourself, interactively, needs no PIN. Set `confirm_human_experiment_creation: true` if you want one.

### Proposal states

`pending` → `approved` (applied exactly once, under the supervisor lock) · `rejected` (never becomes work) · `revision_requested` (the agent revises, which returns it to `pending` with revision + 1). Auto-applied operational proposals are `applied`. Approving checks the revision you reviewed and re-checks conflicts against the current plan. Every decision is in the audit trail (actor, time, action, reason; never the PIN).

### Where it is enforced

Not in the buttons and not only in the briefs. The checks are in the functions every path ends in: `services.create_experiment` and `plans.create_task` (CLI, MCP, RPC, plan YAML apply and the supervisor all call them), `supervisor.decide_proposal`, `registry.apply` (policy keys), `promotion.approve` and `promotion.integrate`. A proposal being applied runs inside an in-process grant that agents cannot reach. Agents are also told, in `.research/context/policy.md`, what they may not do and to submit a proposal instead.

## 2. The approval PIN

A lightweight guardrail against *accidental* autonomous changes. Protected actions: approving an agent's proposal, changing policy settings, approving or merging a promotion into `main`.

```bash
research approval set-pin     # create it, or change it (asks for the current PIN)
research approval status
research approval verify
```

In VS Code a dialog asks for the PIN when a protected action needs it (Settings → Approval PIN to set it). In a terminal it is read from `/dev/tty`; a non-interactive session (an agent, CI) has none and the action **fails closed**.

* Stored as a salted scrypt verifier in `approval.json` next to your machine config (`~/.config/research-os/`, mode 0600). Never in the project, Git, events, prompts or MCP output. No default PIN. 5 wrong tries lock it for 60 seconds.
* If no PIN is set, humans can still do protected actions (agents still cannot), and the Doctor says so. Forgot it? Delete `approval.json` and set a new one.
* `--as human` does not help an agent: a process that looks like an agent (its environment) is refused, and without the PIN there is nothing to type.

**Limits, plainly.** This is not authentication. An agent running under your OS user can read your files, edit `approval.json` or the Research OS code. Use job isolation and worktrees to limit what agents can touch, and review what they do.

## 3. Claude, Codex and Antigravity

Checked against the installed CLIs (claude 2.1.318, codex-cli 0.163.0, agy 1.5.0):

| | headless | native resume | structured output |
|---|---|---|---|
| Claude Code | `-p` | `--resume ID` | JSON (`session_id`) |
| Codex | `exec --json` | `exec resume ID` | JSONL (`thread_id`) |
| Antigravity | `-p --output-format json --mode accept-edits` | `--conversation ID` | JSON (`conversation_id`) |

Antigravity edits are accepted automatically in print mode (the same stance as Claude's `acceptEdits`); `--dangerously-skip-permissions` is never used.

### Continuing a task with another agent

Press **Continue with another agent** on a stopped or failed task (or `research supervisor continue T-004 --profile codex`). The same task keeps its identity, attempt history, acceptance checks and worktree; the next attempt is launched with the other provider and a portable context document (task, experiment, checks, previous attempts and diagnostics, progress notes, and `git status` of the worktree). Provider session ids never cross providers; the same provider resumes natively as before. It is refused while the previous process might still be running (unknown execution state), so a worktree never has two writers. If the plan is paused, resume it to start the attempt.

Automatic fallback is opt-in:

```yaml
agents:
  handoff:
    enabled: false
    fallback_order: [claude, codex, agy]
    on_provider_unavailable: pause     # try_next to continue with the next provider
    on_unknown_execution_state: pause  # always
```

It applies when a provider is unavailable or reports a limit, tries each provider at most once per task, counts against the normal attempt limits, and is never used when an attempt's state is unknown. The history is shown with the task and in the audit trail.

## 4. Set up a project from one YAML file

```bash
research init --template > research-os.yaml        # complete, documented defaults
research init --from research-os.yaml --dry-run    # validate and preview the tree; writes nothing
research init --from research-os.yaml --apply
research init --export                             # the current project's portable settings
```

The file covers project, layout, Git remote, agents and model allocation, supervisor limits, governance, skills/hooks, the Python environment, HPC (as a reference to a cluster in your machine config, or described), the Scientist Guide and the VS Code workspace. It uses the same initializer as the wizard (`project-init --spec`), so both give the same result. It never contains a PIN or credentials and refuses to touch a folder that already holds a project. In VS Code: *Initialize Project* → **Edit YAML** (validate with inline errors, preview the tree, load/save a file, initialize).
