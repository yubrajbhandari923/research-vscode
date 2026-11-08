# Configuration

**Configuration is YAML. The Control Center is an editor for those files, not a separate source of truth.** Close VS Code, edit the
files, reopen: the UI, CLI, MCP server and agents all see exactly what you wrote. There is no second configuration database and no
UI-only settings store.

## Layers and precedence

| Layer | File | Holds |
|---|---|---|
| default | built into the software | every default value |
| machine | `~/.config/research-os/config.yaml` (`$XDG_CONFIG_HOME/research-os/…`) | settings that belong to this computer: cluster hosts and paths, preferred agent CLI |
| project | `.research/config.yaml` | settings that belong to the project and travel with the repository |

Later layers override earlier ones: **default → machine → project**, key by key. Sandbox profile assignment can additionally be set
per experiment or task (`sandbox.assign.<experiment|task>.<id>`). Machine-specific paths belong in the machine file so the project file
stays portable after cloning. The machine-local ledger link (`<git dir>/research/ledger.json`) is runtime state, not configuration, and is
never tracked. Jobs, locks and run state are likewise kept out of configuration.

## Inspecting it

```bash
research config show --effective        # every effective value
research config show --sources          # which layer each value comes from
research config options                 # every setting the UI offers, with help
research config explain KEY             # one setting: value, default, origin, file, key, why, effect
research config check                   # diagnose: unknown keys, wrong types, duplicate keys, commented-out edits
```

In the Control Center, Settings shows for every row the effective value, its origin layer, the exact file and key, the default, and a
`?` explanation; **Open configuration file** opens the YAML in the editor.

## Changing it

- **By hand:** edit the YAML. Unknown keys are kept and reported by `research config check`; nothing is silently discarded or rewritten.
- **From the CLI:** `research config set KEY VALUE [--scope machine] [--dry-run]`, `research config reset KEY`.
- **From the UI:** change a value, **Review changes…** shows the exact YAML diff, **Save**.

All writers edit the file line by line: comments, custom keys and formatting are preserved, writes are atomic, and the UI refuses to
save if the file changed on disk since it was loaded ("changed on disk"; reload and retry). A file that does not parse is reported with the
parser's message and never overwritten.

Policy settings (run budgets, review requirements, sandbox policy) can only be changed by a human; agent sessions are refused.
Every change is recorded in the audit log, and `.research/context/policy.md` is regenerated so agents read the rules in force.

## Settings that have no form

Clusters (`hpc.clusters.*`), agent profiles and strategies (`agents.profiles.*`, `orchestration.strategies.*`), hooks (`harness.hooks.*`) and
sandbox profiles (`sandbox.profiles.*`) are configured in YAML and diagnosed by `research config check`; the Control Center shows their
effect (for example, the sandbox profile table on Work) but does not yet edit them with forms.

## Supervisor, promotion and Telegram settings

`auto_promotion.*`, `supervisor_assistant.*`, `intervention.*` and `notifications.*` are sparse: a project's `config.yaml`
holds only what you changed, merged over the defaults at load, so upgrading needs no edits. They appear under Settings and in
`research config`. The Telegram token and chat id are machine-level (`~/.config/research-os/telegram.json`, mode 0600), never in the
project. See [SUPERVISOR.md](SUPERVISOR.md) and the [reference](spec/10-configuration.md#reference-generated).

## Layout, providers and strategies

```yaml
layout:                      # written by Initialize Project; paths are relative to the folder holding main/ and ledger/
  main_dir: main
  ledger_dir: ledger
  experiments_dir: experiments   # new experiment worktrees are created here (null = <name>-experiments beside the checkout)
agents:
  default_profile: claude        # a provider (claude, codex, agy, opencode) or any profile you define
  profiles:                      # yours are merged over the ones generated for each installed provider
    codex-checked: {provider: codex, command: codex, model: gpt-5, effort: medium}
orchestration:
  active_strategy: balanced_first   # balanced_first | deep_first | fast_max | balanced_heavy | your own (extends: ...)
```

Providers generate `<provider>-fast`, `-balanced` and `-deep` profiles, and a strategy maps those three levels to work; see [PROVIDERS.md](PROVIDERS.md). All of
these appear in Settings (git category and agents) and round-trip with the file.

## Reference

Every registered setting (key, type, default, who may change it, meaning) is listed in the generated
[settings reference](spec/10-configuration.md#reference-generated); the same list is `research config options`. The specification of layers, the registry
and environment variables is [spec/10-configuration.md](spec/10-configuration.md).
