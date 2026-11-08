# Governance, approvals, provider handoff and YAML setup

Research OS is the supervisor. The agent CLIs (Claude Code, Codex, Antigravity, OpenCode) are workers: they propose, you decide.

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

In VS Code a dialog asks for the PIN when a protected action needs it (Settings → Approval PIN to set it). In a terminal it is read from `/dev/tty` with echo off.

* **One rule when no PIN is set.** A protected action **fails closed**: in a real terminal the first one asks you to create a PIN; anywhere else (VS Code, RPC, CI, an agent shell) it is refused with "No approval PIN is set on this machine" and the hint to run `research approval set-pin` or use Settings → Approval PIN. Agents are always refused.
* **Explicit opt-out.** If you really want no PIN, set `governance.approval.require_pin: false`. Protected actions then still need a human (never an agent) and a rationale.
* Stored as a salted scrypt verifier in `approval.json` next to your machine config (`~/.config/research-os/`, mode 0600). Never in the project, Git, events, prompts or MCP output. No default PIN. 5 wrong tries lock it for 60 seconds. A PIN that could not be read (no terminal) is refused without counting as a wrong attempt. Forgot it? Delete `approval.json` and set a new one.
* `--as human` does not help an agent: a process that looks like an agent (`CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`, `CODEX_SANDBOX*`, `RESEARCH_AGENT`) is refused and prompts are disabled by design. Run the command in your own terminal.
* Chat: `telegram.json` and the gateway's state files are written 0600; the update offset is saved before each message is handled, so a failed reply never replays a command; only paired user ids are obeyed (a group's chat id alone is not enough); protected actions have no chat operation. See [CHAT.md](CHAT.md).
* Hand-edited mirrors: if an agent writes a new experiment file, it is imported as `proposed` with a proposal (`import_rejected` event) instead of becoming an experiment; an agent edit that sets a finding to `supported` is reverted with an event.

**Limits, plainly.** This is not authentication. An agent running under your OS user can read your files, edit `approval.json` or the Research OS code. Use job isolation and worktrees to limit what agents can touch, and review what they do.

### Approving from chat (authenticator code, opt-in)

The PIN never goes through chat. A second, separate credential does: an authenticator app (TOTP, RFC 6238, 6 digits, 30 s).

```bash
research approval chat-enroll   # PIN + interactive terminal; shows an otpauth:// URI and the base32 secret once (--qr if `qrcode` is installed), asks for one valid code
research approval chat-status   # enrolled? disabled?
research approval chat-revoke   # PIN required; lost the phone? do this at the computer
```

Chat approval is **off** until `governance.approval.chat.enabled: true` **and** an authenticator is enrolled. Then a chat user with the `approver` role (above `operator`) can approve a pending proposal by sending the current code. Rules, all in code (`approval.confirm_chat`):

* The approval is bound to one proposal id and the content hash on the card; any change (a new revision) invalidates the card.
* Codes from the previous, current and next 30 s step are accepted; each accepted step is stored, so a code works **once** (replay is refused).
* Wrong codes use the same failure counter and 60 s lock as the PIN; the 5th wrong code disables chat approval until you log in with your PIN at the computer (`research approval verify`).
* Chat may approve new experiment proposals and proposals that only add tasks (and, through the decision flow, options flagged protected). It can **never** approve edits to frozen tests, a scientific plan or hypothesis, governance or approval settings, the PIN/authenticator, or the chat allow-list. That list (`approval.CHAT_FORBIDDEN`) is a constant in code, not config. `governance.approval.chat_allow` can only narrow what chat may approve.
* The secret sits beside the PIN hash (machine config dir, mode 0600), never in a project, event, log or message, and is refused in agent environments. The ledger records the approval as method `totp-chat` with platform, user id and role.

**Threat model, plainly: chat approval is as strong as your chat account AND your authenticator device.** Someone with only your chat account cannot approve; someone with your phone and the chat account can, for the actions above. If you lose the phone, revoke at the computer.

## 3. Agent providers

Provider adapters, their capabilities (headless, resume, structured output, usage) and the exact command lines are in [PROVIDERS.md](PROVIDERS.md). In headless mode no one can answer a permission prompt, so the adapters accept edits (`acceptEdits` for Claude Code, `--mode accept-edits` for Antigravity); `--dangerously-skip-permissions` is never used.

### Continuing a task with another agent

Press **Continue with another agent** on a stopped or failed task (or `research supervisor continue T-004 --profile codex`). The same task keeps its identity, attempt history, acceptance checks and worktree; the next attempt is launched with the other provider and a portable context document (task, experiment, checks, previous attempts and diagnostics, progress notes, and `git status` of the worktree). Provider session ids never cross providers; the same provider resumes natively as before. It is refused while the previous process might still be running (unknown execution state), so a worktree never has two writers. If the plan is paused, resume it to start the attempt.

Automatic fallback is opt-in:

```yaml
agents:
  handoff:
    enabled: false
    fallback_order: [claude, codex, agy]     # the default lists every known provider
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

## 5. Unattended code promotion

`research plan autopromote PLAN authorize` is a protected action: human only, approval PIN, rationale. Agents are refused, the YAML flag
`execution.auto_promotion` alone does nothing, and any change to the verification commands or reviewer suspends the authorization.
The supervisor's integration is recorded as `system/supervisor` naming the authorizing human; it is code-only, fast-forward-only, never
approves scientific claims, and the manual `research promote approve|integrate` gates are unchanged. See SUPERVISOR.md section 14.
