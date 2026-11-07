# Phase cleanup R: Hermes Agent, opencode adapter, Discord companion (research, 2026-11-07)

Dev-only. Do not commit to main. Every claim has a URL; "UNVERIFIED" means I could not confirm it from an official page in this pass.

Repo context read: `packages/core/research/providers.py` (adapters `claude`, `codex`, `agy`, `custom`; `CAPABILITIES`, `build_argv`, `resume_argv`, `session_id_of`, `native_resume_supported` probes `--help`), `notify.py` (Telegram: stdlib `urllib` long poll, `handle()` with /status /blocked /task /logs /debug /options /choose /say /pause /resume /interventions; instructions needing protected changes are told to approve with the PIN via CLI/VS Code, never in chat), SPEC.md lines 149, 1598, 2448 (Telegram opt-in, creds in `~/.config/research-os/telegram.json` 0600, single authorized chat).

---

## 1. Hermes Agent

**What it is.** "Hermes agent" = `NousResearch/hermes-agent`, an MIT-licensed open-source agent from Nous Research ("The agent that grows with you"), with a TUI, a desktop app and a messaging gateway. It is not the Hermes LLM family, though it can use those models (Nous Portal lists 300+ models). Source: https://github.com/NousResearch/hermes-agent , docs https://hermes-agent.nousresearch.com/docs/

### Feature table

| Feature | How Hermes does it | Worth copying into ResearchOS? |
|---|---|---|
| Single gateway process, many platforms | One background process (`hermes gateway setup/start`) serves 20+ platforms (Telegram, Discord, Slack, WhatsApp, Signal, SMS, Email, Matrix, Teams...) through per-platform adapters feeding a per-chat session store. https://hermes-agent.nousresearch.com/docs/user-guide/messaging | Maybe. Extract a small `ChatAdapter` interface (send, edit, buttons, files) from `notify.py` so Telegram and Discord share `handle()`. Do not chase 20 platforms. |
| Slash commands in chat | `/new /reset /model /sessions /resume /bg /btw /usage /voice /whoami /stop /retry /undo /compress /insights /status /sethome`. Admins run all; regular users only admin-enabled ones plus `/help` `/whoami`. https://hermes-agent.nousresearch.com/docs/user-guide/messaging , https://github.com/NousResearch/hermes-agent | Yes: `/whoami`, `/usage` (token/cost per run), `/stop` (hard stop of a task), and a role split (viewer vs operator) so a second person can watch but not steer. |
| Busy-input modes | While agent works, a message either redirects (`interrupt`), queues as next turn (`queue`) or steers after the next tool call (`steer`) via `display.busy_input_mode`; `/queue list/rm/edit/move/clear`; queue lives on the gateway and survives restarts. https://hermes-agent.nousresearch.com/docs/user-guide/cli , https://hermes-agent.nousresearch.com/docs/user-guide/messaging | Yes. ResearchOS `/say` already steers; adding explicit queue-for-next-attempt vs apply-now with a visible queue is cheap and fits supervised runs. |
| Cron / scheduled tasks | Natural-language jobs: `in 30m`, `every 2h`, `weekdays at 9am`, 5-field cron, ISO. Targets: `origin`, `local`, `telegram`, `discord:#channel`, `all`. Manage by `/cron add/list/edit/pause/resume/run/remove` or `hermes cron ...`. `[SILENT]` in output suppresses delivery; failed runs always deliver; pre-check script can return `{"wakeAgent": false}` to skip the model; repeat counts; `context_from` chains jobs; per-job `--workdir` loads AGENTS.md/CLAUDE.md. https://hermes-agent.nousresearch.com/docs/user-guide/features/cron | Yes for a narrow slice: scheduled digests ("every day 08:00 summary of overnight runs") and watchdog checks that only notify on change (the `wakeAgent:false` / silent-tick idea). Skip agent-created cron (Hermes itself blocks it by default via `cron.allow_agent_scheduling`). |
| Memory | Two bounded files in `~/.hermes/memories/`: MEMORY.md (2,200 chars) and USER.md (1,375 chars), frozen snapshot into the system prompt at session start, edited by a `memory` tool (add/patch/remove); injection/exfiltration scanning; optional `write_approval` stages writes for `/memory approve`. Separate FTS5 session search (SQLite, no LLM call). https://hermes-agent.nousresearch.com/docs/user-guide/features/memory | Maybe. ResearchOS already has project knowledge (findings, decisions). The transferable ideas are: hard size caps and staged-write approval for anything an agent writes into shared instructions. FTS over past run logs: yes if not already present (S/M). |
| Skills | `SKILL.md` + frontmatter, progressive loading, `~/.hermes/skills/`, project `.hermes/skills` or `.agents/skills` only after `hermes skills trust`; agent can create/patch skills (`skill_manage`, `/learn`); `write_approval` staging; `hermes curator lock`; hub with trust levels (builtin/official/trusted/community) and a scanner that blocks dangerous verdicts. https://hermes-agent.nousresearch.com/docs/user-guide/features/skills | Mostly no. Autonomous skill creation conflicts with "never edit a user's scientific plan" and governance. Copy only: trust-before-load for repo-local skills (repo already has skills/packs; check it gates untrusted ones) and the lock flag. |
| TUI | `hermes` classic CLI and `hermes --tui` (modal overlays, mouse, non-blocking input). Keys: Enter send, Alt/Shift+Enter newline, Ctrl+G edit in `$EDITOR`, Ctrl+S stash draft, Esc Esc transcript browser/fork, Ctrl+T live work monitor, Ctrl+R work dock, Ctrl+B voice. Status bar: model, context bar, est. cost, compressions, background tasks, duration, session title, YOLO warning. `/bg` runs a prompt in an isolated session and reports in a panel; `/rewind`. https://hermes-agent.nousresearch.com/docs/user-guide/cli | Maybe, for the ResearchOS TUI: always-visible status bar with cost, running agents and a YOLO/unattended-mode warning; a live work monitor pane. Draft stash, voice and transcript fork: no. |
| Approval flow | `approvals.mode`: `smart` (aux LLM scores risk; auto-approve low, auto-deny dangerous), `manual`, `off`/`--yolo`. Responses: once, session, always (saved to `command_allowlist`), deny. 300 s timeout = deny (fail-closed). `approvals.deny` globs checked before YOLO; hardline blocklist (root wipe, fork bomb) even in YOLO; container backends skip checks because container is the boundary. In chat: `/approve`, `/deny`. https://hermes-agent.nousresearch.com/docs/user-guide/security | Partly. Copy: timeout-means-deny, deny rules that beat any auto mode, a hardline blocklist. Do NOT copy "approve always" or LLM-scored auto-approval: ResearchOS governance uses a PIN, which must stay out of chat. |
| Session handling | SQLite store; resume by id/title/latest (`hermes -c`, `--resume`); `/title`, `/sessions` picker; gateway chats persist and do NOT reset on inactivity (only `/new`); Discord threads inherit parent channel model/prompt overrides; per-channel `channel_overrides`. https://hermes-agent.nousresearch.com/docs/user-guide/messaging , cli page above | Yes: map one Discord thread (or Telegram topic) to one run/task so the conversation is the task log; titles for runs. |
| Home channel + notifications | `/sethome` marks a chat as the destination for restart notices, cron output and circuit-breaker operator notices. Failure alerts with the same error alert once then remind on a cooldown; after 3 consecutive failures the alert adds a review suggestion. https://hermes-agent.nousresearch.com/docs/user-guide/messaging , cron page above | Yes: alert dedupe plus cooldown and an escalation text after N failures is exactly what unattended supervision needs. |
| Voice | Voice-memo transcription on messaging platforms; `/voice`; Ctrl+B recording in CLI; TTS via Tool Gateway. Details beyond this are UNVERIFIED. https://github.com/NousResearch/hermes-agent | No for core. Maybe later: voice note to `/say` transcription is niche and needs a speech dependency. |
| File handling | README documents no dedicated file feature beyond context files and AGENTS.md; the platform table marks image/file/voice support per platform (Telegram, Discord, Slack: all three). Uploads/downloads details UNVERIFIED. https://hermes-agent.nousresearch.com/docs/user-guide/messaging | Yes (outbound only): send a result file (summary.md, plot PNG, diff) to the chat. Inbound files to agents: no (injection and governance surface). |
| Security: allowlists | Deny by default. Order: per-platform allow-all, pairing-approved, per-platform `*_ALLOWED_USERS`, global `GATEWAY_ALLOWED_USERS`, `GATEWAY_ALLOW_ALL_USERS`, then deny. https://hermes-agent.nousresearch.com/docs/user-guide/security | Yes: ResearchOS already restricts to one chat; extend to per-platform user-id allowlist, deny by default, no allow-all switch. |
| Security: DM pairing | Stranger DMs the bot, gets an 8-char code; owner runs `hermes pairing approve <platform> <code>` locally. Expires in 1 h; one code per user per 10 min; max 3 pending per platform; 5 bad attempts = 1 h lockout; files 0600, codes never logged; `unauthorized_dm_behavior` = pair/ignore/decline. https://hermes-agent.nousresearch.com/docs/user-guide/security | Yes. It replaces today's "discover chat id" step (`notify.discover_chat`) with a safer owner-confirmed flow, and works for Discord user ids. |
| Security: secrets and isolation | Child processes lose env vars named KEY/TOKEN/SECRET/PASSWORD; MCP subprocesses get a minimal env; error text is scrubbed of token patterns; write tools block `~/.ssh`, `.env`; Docker backend drops capabilities, `no-new-privileges`, pids limit; docs admit the terminal tool can still reach denied paths. Skills never ask for secrets in chat. https://hermes-agent.nousresearch.com/docs/user-guide/security , skills page | Yes: scrub env of `*TOKEN*` etc. when spawning agents (check supervisor), redact token patterns in anything sent to chat. |
| Subagents, 40+ tools, scripted tool calls, terminal backends (local, Docker, SSH, Modal, Daytona, Vercel) | https://github.com/NousResearch/hermes-agent | No: ResearchOS delegates execution to claude/codex/agy; remote backends are a different product. |
| Portal, Tool Gateway, trajectory generation, Honcho user modeling | https://github.com/NousResearch/hermes-agent | No. |
| Import from other agents | Imports settings/memories/skills from OpenClaw, Claude Code, Codex CLI, Cursor (`--migrate-secrets` allowlisted keys). https://hermes-agent.nousresearch.com/docs/user-guide/import-from-other-agents | No (ResearchOS already reads CLAUDE.md/AGENTS.md). |

Not verified in this pass: Hermes Telegram/Discord platform-specific pages (buttons for approvals, thread behavior), TUI guide, voice mode doc, Checkpoints & Rollback. Only the README and the pages above were read.

---

## 2. opencode adapter

**Identity and version churn (important).** The repo is now `anomalyco/opencode` (earlier `sst/opencode`; site opencode.ai). OpenCode 2.0 is out; the team closed v1 issues as "not planned" on 2026-10-11 and asks users to `opencode upgrade --major` (https://github.com/anomalyco/opencode/issues/44901 ; https://anoma.ly/notes/opencode-2-0). Docs pages for v1 (`/docs/...`) and v2 (`/v2/docs/...`) coexist and differ (see "v1 vs v2" below). An adapter must probe the installed version rather than assume a shape (same stance as `native_resume_supported`). Latest tags seen: v1.19.4, v2.0.30 (https://github.com/anomalyco/opencode/blob/v2.0.30/packages/cli/src/run/noninteractive.ts).

### Headless usage

- `opencode run [message..]` is the non-interactive entry; the docs say it is for scripts and CI (https://opencode.ai/docs/cli/ , https://opencode.ai:2096/v2/docs/cli/commands).
- Flags (https://opencode.ai/en/docs/cli , run.ts https://github.com/anomalyco/opencode/blob/v1.19.4/packages/opencode/src/cli/cmd/run.ts): `-c/--continue`, `-s/--session ID`, `--fork`, `-m/--model provider/model`, `--agent`, `--command` (slash command; message becomes its args), `-f/--file` (repeatable), `--format default|json`, `--title`, `--share`, `--attach URL`, `--port`, `--variant` (provider-specific reasoning effort), `--thinking`, `--dir`, `--auto` (alias `--yolo`, `--dangerously-skip-permissions` in source) = auto-approve permissions not explicitly denied; v2 also has `--archived` (keep scripted sessions out of lists).
- Prompt passing: positional args joined with spaces; stdin is also read when piped (run.ts `resolveRunInput(rawMessage, piped)`; the issue repro pipes the prompt on stdin: https://github.com/anomalyco/opencode/issues/26855). Empty prompt and no `--command` = error "You must provide a message or a command", exit 1 (run.ts v1.19.4). For long prompts pass via stdin, not argv.
- `opencode serve [--port 4096] [--hostname 127.0.0.1] [--cors origin]`: headless HTTP server with OpenAPI 3.1; protect with `OPENCODE_SERVER_PASSWORD` (basic auth, user `opencode` or `OPENCODE_SERVER_USERNAME`). Endpoints include `POST /session/:id/prompt_async` (204, no wait), `/session/:id/abort`, `/session/:id/fork`, `/session/:id/permissions/:permissionID` (respond to a permission request with `{response, remember?}`), `/config`, `/provider`, `/command` (https://opencode.ai/docs/server?format=md , https://dev.opencode.ai/docs/server). `opencode run --attach http://localhost:4096` reuses a server. Newer builds also run one shared background server per user by default; `--standalone` forces a private one (https://opencode.ai/docs/cli/ fetched page).
- **Permission behavior in `run`:** without `--auto`, any `ask` is auto-rejected with a message "permission requested: ...; auto-rejecting" (run.ts). So headless runs never hang on a prompt, but the agent silently loses the action; v2 feeds the model "This non-interactive run cannot ask the user for permission, so the request was rejected" (noninteractive.ts v2.0.30).

### JSON events (`--format json`)

Newline-delimited JSON; each line is `{type, timestamp, sessionID, ...}` where `type` is one of at least `step_start`, `text` (emitted when a text part ends), `tool_use` (tool part completed/errored; inferred from source, exact set UNVERIFIED), `step_finish`, plus an error event (run.ts: https://github.com/anomalyco/opencode/blob/v1.19.4/packages/opencode/src/cli/cmd/run.ts). `step_finish.part` carries `reason`, `tokens {total,input,output,reasoning,cache{write,read}}` and `cost` (issue #26855 shows the exact object). The session id for resume is the `sessionID` on every line (`ses_...`).

Known defects to code around: (a) final `step_finish` can be missing from stdout in some 1.14.x runs (https://github.com/anomalyco/opencode/issues/26855); (b) in 1.x `run --format json` can emit terminal `step_finish reason:"stop"` and never exit, so wrap with a timeout and treat a seen `stop` event as completion (https://github.com/anomalyco/opencode/issues/44901, closed not-planned, so assume it is unfixed on v1). Fallback for usage: `opencode stats --json` or `--cost` (v2 docs, https://opencode.ai:2096/v2/docs/cli/commands).

### Exit codes

Documented behavior is thin. Source shows: `process.exitCode = 1` when the run completes with an error; `process.exit(1)` for missing message or session not found; the run ends when `session.status` goes idle. No exit-code table exists in the docs (UNVERIFIED beyond that). Adapter should use exit code plus presence of an error event plus the last `step_finish.reason`.

### Version detection

Not documented on the pages read. `opencode --version` is the yargs convention (UNVERIFIED); `opencode upgrade [version] --method ...` exists (v2 docs). Adapter: `opencode --version`, fall back to `opencode upgrade --help` text; treat `>= 2` as v2 shape.

### Model and provider configuration

- `-m provider/model` per run; default in config key `"model": "anthropic/claude-sonnet-5-5"` (https://opencode.ai/docs/config). Providers: `/connect` in TUI stores credentials; config `provider.<id>.options.apiKey` can reference env via `{env:ANTHROPIC_API_KEY}`; `{file:...}` substitution exists; `disabled_providers` option (config page).
- Effort: `--variant` (provider-specific reasoning effort) on `run`; `--thinking` only toggles display.

### Config locations and precedence (https://opencode.ai/docs/config)

Remote `.well-known/opencode` defaults, then global `~/.config/opencode/opencode.json(c)`, then `OPENCODE_CONFIG` file, then project `opencode.json`, then `.opencode/` dirs, then inline `OPENCODE_CONFIG_CONTENT`, then managed files (`/Library/Application Support/opencode/` on macOS), then MDM. Subdirs under `.opencode` or `~/.config/opencode`: `agents/ commands/ modes/ plugins/ skills/ tools/ themes/` (singular accepted). `OPENCODE_CONFIG_DIR` sets a custom directory. `opencode debug paths [config|data|db|...]` prints real paths (https://opencode.ai/docs/cli/ fetched page).

**Adapter implication:** a supervised worktree run can inject everything without touching user files: `OPENCODE_CONFIG_CONTENT` (inline JSON for model, mcp, permission) and `OPENCODE_PERMISSION`. Mind that a project `opencode.json` in the worktree is overridden by inline content.

### MCP registration

In config: `"mcp": {"name": {"type":"local","command":["cmd","arg"],"environment":{...},"enabled":true}}` or `{"type":"remote","url":"https://..."}`; OAuth tokens stored in `~/.local/share/opencode/mcp-auth.json`; tools namespaced `<server>_<tool>`, toggled with `tools` globs (https://opencode.ai/docs/mcp-servers/). There is no `claude mcp add`-style command found in this pass (UNVERIFIED); write JSON. `providers._server_cmd` already has the command to put in `command`.

### Permissions model

v1: `"permission": {"edit":"ask","bash":"ask"}` with allow/ask/deny and per-command globs for bash (config example `"bash": {"*":"ask","rm -rf *":"deny"}`) (https://opencode.ai/docs/config , MDM sample). v2: ordered rule array `"permissions": [{"action":"shell","resource":"*","effect":"ask"}, ...]`, no-match = ask, any deny wins within one operation, `external_directory` rule guards paths outside the project, MCP tools are `<server>_<tool>` (https://opencode.ai/v2/docs/permissions). Shipped agents (`build`, `plan`) append their own policies. Env `OPENCODE_PERMISSION` inlines JSON.

### Instruction files, skills, agents, commands, plugins

- Instructions: `AGENTS.md` (project, walk-up), `~/.config/opencode/AGENTS.md`; falls back to `CLAUDE.md` / `~/.claude/CLAUDE.md` only if no AGENTS.md; extra files via `"instructions": [globs or URLs]` (5 s fetch timeout); disable Claude compat with `OPENCODE_DISABLE_CLAUDE_CODE`, `OPENCODE_DISABLE_CLAUDE_CODE_PROMPT`, `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS` (https://opencode.ai/docs/rules/). `providers.instruction_files/sync_instructions` that maintain AGENTS.md already satisfy it.
- Skills: `SKILL.md` in `.opencode/skills/<name>/`, `~/.config/opencode/skills`, and also `.claude/skills`, `~/.claude/skills`, `.agents/skills`; name regex `^[a-z0-9]+(-[a-z0-9]+)*$`, description 1-1024 chars; permission key `skill` (https://opencode.ai/docs/skills). Existing ResearchOS skills dropped in `.claude/skills` or `.agents/skills` are picked up with no extra work.
- Agents: `agent` key in config or markdown with frontmatter in `.opencode/agents/`; `opencode agent create`, `agent list`. Commands: `.opencode/commands/`, run via `--command`.
- Plugins/hooks: JS/TS in `.opencode/plugins/` or npm via `"plugin": [...]` (v2: `"plugins"`); events include `permission.asked`, `permission.replied`, `session.created`, `message.updated`, `file.edited`, `tool.execute.before` style interception (https://opencode.ai/docs/plugins/ , https://v2.opencode.ai/build/plugins). Plugins run in-process in Node/Bun, so they are not a good place for ResearchOS governance hooks; use the server's SSE events or JSON stream instead.

### Environment variables seen

`OPENCODE_CONFIG`, `OPENCODE_CONFIG_DIR`, `OPENCODE_CONFIG_CONTENT`, `OPENCODE_PERMISSION`, `OPENCODE_DISABLE_DEFAULT_PLUGINS`, `OPENCODE_DISABLE_CLAUDE_CODE*`, `OPENCODE_SERVER_PASSWORD`, `OPENCODE_SERVER_USERNAME`, `OPENCODE_DB` (sources above). Full list UNVERIFIED (the CLI page has an env table that was only partly excerpted).

### Sandboxing

No OS-level sandbox found in docs; control is by the permission rules and `external_directory`. For parity with codex `-s workspace-write`, rely on worktree isolation plus deny rules, or run inside a container. UNVERIFIED that no sandbox option exists in v2.

### Mapping to claude / codex (what the adapter needs)

| Need | claude (current adapter) | codex (current adapter) | opencode |
|---|---|---|---|
| Headless prompt | `claude -p --output-format json PROMPT` | `codex exec --json PROMPT` | `opencode run --format json PROMPT` (or stdin) |
| Workdir | subprocess cwd | `-C DIR` | subprocess cwd, or `--dir DIR` |
| Extra writable dirs | `--add-dir` | `--add-dir` | none found; use `external_directory` allow rule via `OPENCODE_CONFIG_CONTENT` |
| Model | `--model` | `-m` | `-m provider/model` (provider prefix required) |
| Effort | not supported (`effort: False`) | `-c model_reasoning_effort="x"` | `--variant x` |
| Permission posture | `--allowedTools ... --permission-mode acceptEdits` | `-s workspace-write` | inline `permission` config allowing edit and the research CLI/MCP, ask/deny the rest; `ask` auto-rejects headless; `--auto` only if explicitly opted in |
| Structured output | one JSON object (`session_id`, usage) | JSONL (`thread.started` thread_id) | JSONL events; `sessionID` on every line; usage in `step_finish` |
| Session id | `session_id` | `thread_id` | `sessionID` (`ses_...`) |
| Resume | `claude -p --resume ID` | `codex exec resume --json ... ID PROMPT` | `opencode run --session ID PROMPT` (or `-c`); `--fork` to branch |
| Usage/cost | in JSON result | `usage: False` | sum `step_finish.tokens`/`cost` across lines; fallback `opencode stats --json` |
| MCP | `.mcp.json` | `codex mcp add` | `mcp` block in `opencode.json` (`type: local`, `command` array) |
| Instructions | CLAUDE.md | AGENTS.md | AGENTS.md (CLAUDE.md fallback) |
| Version | `claude --version` | `codex --version` | `opencode --version` (UNVERIFIED) |

Suggested `CAPABILITIES["opencode"]`: `{"interactive": True, "headless": True, "model": True, "effort": True, "structured_output": "jsonl", "usage": True, "resume": True, "mcp": "opencode.json", "instructions": "AGENTS.md"}` and add a hard timeout watchdog (defect b). Effort: M, mostly verification against a real install, which I did not do.

**v1 vs v2 differences that bite the adapter:** `permission` (object, v1) vs `permissions` (ordered array, v2); `plugin` vs `plugins`; v2 CLI source moved to `packages/cli/src/run/noninteractive.ts`; docs say "OpenCode 1 does not use metadata..." in skills page, confirming both are live (https://opencode.ai/docs/skills). Generate config to match the detected major version, or set both keys (UNVERIFIED that unknown keys are tolerated).

---

## 3. Discord companion bot

### API facts (all https://docs.discord.com/developers/...)

- **Gateway:** connect to `wss://gateway.discord.gg/?v=10&encoding=json`; HELLO gives `heartbeat_interval`; first heartbeat after interval times random jitter; missing ACK = zombie, reconnect with a non-1000/1001 close; IDENTIFY with token + intents; READY gives `session_id` and `resume_gateway_url`; RESUME with `seq`. 120 outbound events per 60 s per connection; payloads over 4096 bytes close with 4002; 1000 IDENTIFYs per 24 h; sharding mandatory at 2500 guilds. https://docs.discord.com/developers/events/gateway
- **Intents:** privileged ones are GUILD_PRESENCES, GUILD_MEMBERS, MESSAGE_CONTENT; must be toggled in the Developer Portal; verification approval needed only at 100+ guilds. Without MESSAGE_CONTENT the `content` field is empty except for the bot's own messages, **DMs**, messages that mention the bot, and context-menu targets. 4014 = unapproved privileged intent. https://docs.discord.com/developers/events/gateway
  - Consequence: a slash-command + button design needs **no** privileged intent at all (interactions arrive over the Gateway regardless). Free-text steering works in DMs and with @mention without MESSAGE_CONTENT. Recommend: do not request MESSAGE_CONTENT.
- **Slash commands vs interactions:** apps can receive interactions over the Gateway or over HTTP to an Interactions Endpoint URL; HTTP needs signature verification and a public URL. https://docs.discord.com/developers/platform/interactions . Limits: 100 global CHAT_INPUT commands; 200 command creates per day per guild; guild commands update instantly while global ones propagate (read-repair). https://discord.com/developers/docs/interactions/application-commands
- **Response timing:** initial response within 3 s or the token is invalidated; token then valid 15 minutes for followups/edits; interaction endpoints are not bound to the global rate limit. https://docs.discord.com/developers/interactions/receiving-and-responding . So: always `DEFERRED` (type 5 or 6) then edit.
- **Ephemeral:** message flag `1<<6` makes a reply visible only to the invoker; settable on interaction responses. https://docs.discord.com/developers/resources/message
- **Components:** buttons live in action rows (5 children per row, 5 rows per message in classic components); button click produces an interaction with `custom_id`; responding can open a **modal** (type 9) for text input. `IS_COMPONENTS_V2` (1<<15) enables the new layout system but cannot be reverted on a message and disables `content`/`embeds`. https://docs.discord.com/developers/components/using-message-components , https://discordpy.readthedocs.io/en/v2.3.2/interactions/api.html . Use classic components with embeds for simplicity.
- **Limits:** `content` 2000 chars; up to 10 embeds, 6000 chars total, embed description 4096, title 256. https://docs.discord.com/developers/resources/channel , https://docs.discord.com/developers/resources/message . Interaction payloads include `attachment_size_limit` in bytes (default per-app limit not confirmed here: UNVERIFIED, commonly cited as 10 MiB for bots).
- **Threads:** `POST /channels/{id}/threads` starts a thread without a message; also from a message; threads inherit parent channel permissions; threads do not count against channel limits; sending into a thread auto-adds the sender; archived threads cannot receive messages until unarchived. https://docs.discord.com/developers/resources/channel
- **Rate limits:** per-route buckets plus global 50 req/s; parse `X-RateLimit-*` and `Retry-After`; do not hard-code; invalid-request ban at 10,000 per 10 min (401/403/429 count). https://docs.discord.com/developers/topics/rate-limits . Message edits for a status board should be debounced (e.g. at most once per few seconds per message).
- **DM vs guild:** in DMs `user` is sent, in guilds `member`; DMs deliver message content without the privileged intent. https://docs.discord.com/developers/interactions/receiving-and-responding , gateway page. A single-owner setup can use DMs only (no guild needed), but threads need a guild channel; for per-run threads create a private server with the owner and the bot.

### Library trade-off

| Option | Python 3.8 | Core stays stdlib | Verdict |
|---|---|---|---|
| discord.py 2.7.1 (3 Mar 2026) | `Requires-Python >=3.8` per the 3 Nov 2026 comparison; installs on 3.15 too. https://space-node.net/blog/pycord-python-3-15-install-error-fix-2026 , https://discordpy.readthedocs.io/en/latest/intro.html | Yes if shipped as an optional extra (`research-os[discord]`) imported lazily inside the Discord module only | Recommended |
| py-cord 2.8.1 | needs >=3.10, <3.15 (blocks Python 3.15) | same | No |
| hikari | 3.10-3.14 | same | No |
| Raw HTTP + gateway websocket | works if we write the websocket | breaks "stdlib-only": Python has no stdlib websocket client; we would hand-write RFC 6455 framing, TLS, ping/pong, zlib-stream, heartbeat/resume, rate-limit buckets (about 500+ lines, security-sensitive) | No |
| HTTP interactions only | n/a | stdlib `http.server` is enough, plus Ed25519 signature verification, which is NOT in the stdlib (needs `cryptography`/PyNaCl) | No: needs a public HTTPS URL (laptop/HPC behind NAT/firewall), and cannot push unsolicited alerts without a REST call anyway |

**Recommendation: discord.py as an optional extra, gateway mode, outbound-only (works behind NAT and on HPC login nodes).** Reasoning: it is the only maintained library that keeps `>=3.8`, it handles heartbeat/resume/rate limits/interaction deferral, and the Gateway needs no inbound port. Its aiohttp dependency: current aiohttp 3.14.x requires Python >=3.10 (https://pypi.org/project/aiohttp/), so on Python 3.8/3.9 pip resolves an older aiohttp; the exact resolved version and any CVE exposure on 3.8 were not checked (UNVERIFIED). Packaging: `[project.optional-dependencies] discord = ["discord.py>=2.4,<3"]`, import inside a function, and print a clear "pip install research-os[discord]" hint when missing (same pattern as other opt-ins). Run it in its own thread with its own event loop, or inside the supervisor daemon as a sidecar process, so `notify.py`'s sync design is untouched. Keep REST-only sending (webhook/bot token via `urllib`, like `_api` today) as a no-dependency fallback for alerts and files; only interactive features need the gateway.

### Security rules

1. Config: `discord.json` mode 0600 next to `telegram.json`; token never returned by RPC (same as Telegram).
2. Allow-lists, deny by default: `allowed_user_ids` (required), optional `allowed_guild_ids`, `allowed_channel_ids`; check on every interaction and message before doing anything; ignore other bots; on a miss reply ephemeral "not authorized" at most (or nothing). Check `interaction.user.id` (DM) / `member.user.id` (guild), never display names.
3. Pairing instead of pasting ids: DM the bot, get an 8-char code, approve locally with `research discord pair <code>`; 1 h expiry, 3 pending, lockout after 5 failures (Hermes numbers, https://hermes-agent.nousresearch.com/docs/user-guide/security).
4. **Never accept or display a PIN in Discord**, including via modal (modals are visible to Discord, logged client-side and are not a secret channel). A governance-gated action in chat shows a button "Open approval" that only prints the local command (`research intervention approve <id>`), exactly like Telegram does today. Redact PIN-like patterns in outbound text.
5. Approval-by-button rules: buttons may only approve actions classified as non-protected (the same `approved` state `intervene.request` already auto-approves); protected changes stay PIN-only. Encode `custom_id` as `action:request_id:nonce`, validate nonce and that the request is still `proposed`, make the click idempotent, disable the buttons after use (edit message), and re-check the allow-list on the click user (not the message author). A button posted in a shared channel is clickable by anyone who can see it, so authorization must come from the allow-list, not from channel membership. Timeout of a pending approval = deny (Hermes: 300 s).
6. Never post secrets, env, or raw agent logs without redaction; send diffs/logs as attachments; set `allowed_mentions` to none to avoid `@everyone` abuse from log content.
7. Disable guild-join by others: keep the bot "Public bot" off in the portal; one private server.

### Design sketch (what the companion should do)

- One private channel `#researchos` for the digest and a **thread per run/task** (thread name = task id + title); status message is edited in place, final result posted as an embed (task, verdict, changed files, tokens/cost, link/command) with attachments for `summary.md`/diff.
- Slash commands (guild-scoped for instant registration): `/status`, `/blocked`, `/task <id>`, `/logs <id>`, `/say <id> <text>`, `/pause`, `/resume`, `/stop <id>`, `/usage`, `/debug`, `/options`; thread messages in a task thread (via @mention or DM, no privileged intent) become `/say` for that task.
- Alert buttons: Retry with other agent, Show logs (ephemeral), Pause plan, Open approval (prints command).
- Same `handle()` as Telegram via a small adapter interface so both platforms share behavior and tests (the existing tests use a local fake Telegram server; reuse that pattern with a fake Discord REST server).

---

## 4. Steal list (prioritized)

| # | Idea | Source | Why for a research supervisor | Effort |
|---|---|---|---|---|
| 1 | Pairing + deny-by-default per-platform user allowlist (replace chat-id discovery) | Hermes security page | Safe onboarding on shared bots; prerequisite for Discord | S |
| 2 | Alert dedupe, cooldown and "3 failures in a row, consider X" escalation text | Hermes cron page | Unattended runs generate repeated identical alerts; phone fatigue | S |
| 3 | Chat adapter interface shared by Telegram and Discord (send, edit, buttons, file, redaction) | Hermes gateway design | One `handle()`, two front-ends, testable with fakes | M |
| 4 | Discord bot (discord.py extra, outbound gateway, slash commands, buttons, thread per run, ephemeral logs) | section 3 | Richer phone UX than Telegram text | M/L |
| 5 | Approval ergonomics: timeout-is-deny, deny rules that beat any auto mode, hardline blocklist; buttons for non-protected approvals only, PIN stays local | Hermes security | Keeps governance intact while cutting round trips | M |
| 6 | Scheduled digests and silent-unless-changed watchdogs (`every day 08:00 summary`, pre-check that skips the model) | Hermes cron | Morning "what happened overnight" summary with zero token cost when nothing changed | M |
| 7 | `/usage` and `/stop` plus a status bar (cost, running agents, unattended-mode warning) in chat and TUI | Hermes CLI/messaging | Visible spend and a hard kill from the phone | S |
| 8 | Busy-input modes: apply now vs queue for next attempt, visible and editable queue | Hermes `busy_input_mode`, `/queue` | Steering without clobbering a running task | M |
| 9 | Send result files (summary, plots, diffs) as chat attachments, redacted | Hermes platform file support | Results readable on a phone without opening the laptop | S |
| 10 | Thread/topic-per-run session mapping with titles; per-channel overrides (model, provider) | Hermes Discord threads and `channel_overrides` | Conversation is the task log; search and resume by title | M |
| 11 | Env scrubbing of `*TOKEN*/*KEY*/*SECRET*` for spawned agents and token-pattern redaction on outbound text | Hermes secrets handling | Chat and agent logs are leak vectors | S |
| 12 | opencode provider adapter (JSON events, `--session`, `--variant`, inline config), with version probe and watchdog timeout | section 2 | Cheap, multi-provider model access (any `provider/model`) via one adapter | M |

Declined on purpose: autonomous skill creation and agent-written memory, "approve always" lists, LLM-scored auto-approval, voice, inbound file handling, 20-platform fan-out, remote terminal backends.

---

## Summary

"Hermes agent" is Nous Research's `hermes-agent`: a TUI plus a multi-platform gateway with cron, bounded memory, skills and a deny-by-default security model (pairing codes, allowlists, timeout-deny approvals). Most worth copying: pairing and allowlists, alert dedupe and escalation, a shared chat adapter, scheduled silent-unless-changed digests, `/usage` and `/stop`, and env scrubbing and redaction. Skip autonomous skills, "approve always" and voice.

opencode (now `anomalyco/opencode`, v2 current, v1 end-of-life) maps cleanly: `opencode run --format json -m provider/model --variant X --session ID`, JSONL events with `sessionID` and per-step tokens and cost, config injected via `OPENCODE_CONFIG_CONTENT`, MCP as a `mcp` block. Probe the version (v1/v2 permission schemas differ) and add a timeout watchdog because `run` can hang after the final event.

Discord: use discord.py (still `>=3.8`) as an optional extra over the outbound gateway; slash commands and buttons need no privileged intent; defer within 3 s; thread per run; allowlist by user id; never take a PIN in Discord.

Not done: no live install of opencode or Hermes, so flags and exit codes are from docs and source only.
