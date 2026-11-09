# Chat: alerts and steering from your phone

Research OS can message you when a run needs you and let you answer from Telegram or Discord. Everything is **opt-in** and off by default.
Both platforms share one router, one allow-list, one outbox and the same buttons.

```
supervisor (per project)  --writes alerts-->  .research/supervisor/outbox.jsonl
                                                        |
chat gateway (ONE process per machine)  --tails every enabled project's outbox, delivers, long-polls Telegram, runs your commands
```

* **Supervisors never talk to chat.** They append to an outbox; the gateway delivers (at-least-once, a restart never re-sends).
* **One gateway per machine** owns the bot connection, so two projects can share one bot without fighting over updates.
* The gateway starts by itself (`research chat setup`, `research supervisor start`, every supervisor pass while chat is on, `research self-update`).
  It exits ten minutes after no project enables chat. `research chat gateway start|stop|restart|status|run`; log at `~/.config/research-os/gateway/gateway.log`.
* On an HPC login node it works with outbound connections only; check your site's rules about long-running processes.

## Set up Telegram

1. In Telegram, talk to **@BotFather**, create a bot, copy the token.
2. `research chat setup` (hidden token prompt). Then open your bot and send it any message: that private chat becomes the first paired user
   (operator) and `notifications.telegram.enabled` is switched on for the project. Or use Control Center → Plans → *Chat (Telegram)*.
3. `research chat status` shows the token/chat/gateway state and the paired people, with the command that fixes whatever is missing;
   `research chat doctor` checks every step (add `--live` to ask Telegram/Discord whether the saved token still works) and prints one fix per problem; `research chat test` sends a message; `research chat remove` deletes the credentials.

The old `research telegram setup|status|test|remove|poll` still works (hidden aliases). An existing Telegram setup needs no edits: the same
`telegram.json` and `notifications.telegram.enabled` keep working; on first gateway start a private chat id becomes an operator entry.

## Set up Discord

Discord needs the optional `discord.py` package (Python 3.10 or newer; Telegram works on every supported Python). Without it `research chat status`
and `research chat setup discord` print the install line for how you installed Research OS:
`uv tool install --force 'research-panel[discord]'`, `pipx inject research-panel discord.py`, or `pip install 'research-panel[discord]'`.
The bot connects **outbound** to Discord; nothing listens on a public URL.

1. In the [developer portal](https://discord.com/developers/applications): **New Application**, open the **Bot** tab, **Reset Token** and copy it.
   Turn **Public Bot** off and leave every *Privileged Gateway Intent* off (the bot never reads message content: only mentions, DMs and slash commands).
2. `research chat setup discord`: paste the token (hidden; checked with `GET /users/@me`). It prints an **invite URL** (scopes `bot` + `applications.commands`;
   permissions: send messages, embed links, attach files, create public threads, send messages in threads, read message history). Open it, pick your server, Authorize.
3. The setup lists the servers the bot is in (`GET /users/@me/guilds`) and asks which if there are several (`--guild ID` to say it up front).
   Optional `--channel ID` makes that channel this project's alert channel and the home channel.
4. Send the bot a **direct message**. It answers with a pairing code; type it at the setup prompt (or later: `research chat pair CODE`).
   Run `/sethome` in the channel that should receive digests and machine alerts.
   Discord answers HTTP 429 now and then (setup makes two requests to Discord). Research OS reads `Retry-After` and retries by itself when the wait is
   10 seconds or less (up to four attempts, saying so as it waits). A longer wait, or a limit that does not clear, stops with the time Discord asked for;
   nothing is saved at that point, so run the same command again afterwards.
5. `research chat status`, `research chat doctor`, `research chat test discord` (posts to the home channel), `research chat remove discord` (deletes the token, disables Discord for this project).

What you get:

* **Slash commands** (registered in your server, typed options, autocomplete for project, task and plan ids): `/status /blocked /task /logs /report /finding /usage /proposals /interventions /digest /decisions /options /whoami /help /project /say /choose /debug /pause /resume /stop /continue /approve-start /sethome`.
  Replies are private to you (ephemeral). A project channel (`--channel`) implies the project, so `/status` there needs no argument.
* **Alerts as embeds**: color by severity (green ok, yellow warn, red error, blue info), fields (task, plus whatever the alert carries), project and time in the footer.
  With `notifications.discord.thread_per_task` (default on) alerts about a task go to one **thread** named after it; the channel gets a one-line pointer for errors and approvals.
* **Buttons and menus**: one button per option of a decision request (a **select menu** when there are more than five), Reject, *Approve on my computer*, and an **Instruct** button that opens a text box and sends your words as `/say` for that task.
* **Free text**: mention the bot in a task thread (or DM it) and your message is an instruction for that task (`/say`). A reply to a decision card is the answer. Mentions and DMs need no privileged intent.
* **Approving a protected proposal** (only when you enrolled an authenticator and enabled chat approval, role `approver`): *Approve (authenticator code)* opens a modal that only you see.
  Type the current 6-digit code there. It never appears in the channel and is sent only to the approval check; a wrong or reused code leaves the button usable. **The PIN is never asked for, in any form.**
* Long text is split (4,096 characters per embed, 2,000 per follow-up message); reports, logs and plots arrive as attachments up to 8 MiB (larger ones are named with their path on your machine).
  Sends to one channel are paced at about one per second, a rate-limit answer is waited out and retried, and a send that still fails stays in the outbox for the next pass.
* **Who may use it**: only paired Discord user ids (roles `viewer`, `operator`, `approver` come from the pairing allow-list, not from Discord roles), only in the configured server, and (if `allowed_channels` is set in `discord.json`) only in those channels plus project channels and DMs.
  A stranger who DMs the bot gets a pairing code; a stranger in a server gets one private "not authorized" and no code.

Not in this version: a pinned status board per channel, and creating project channels for you (create the channel and pass `--channel ID`).

### Manual smoke test (run once with a real bot; the automated tests use a fake Discord)

1. `uv pip install discord.py` (or the extra), `research chat setup discord --channel <id>`, DM the bot, pair at the prompt. `research chat status` shows the token saved, a server linked, you as operator, gateway running.
2. In the server type `/` and see the commands with options. Run `/whoami` (answer in a few seconds, visible only to you) and `/status`.
3. `research chat test discord` posts in the channel.
4. Start a plan with a task that fails: an error embed appears in a new thread `T-n`, with a pointer in the channel; a second alert for the same task reuses the thread.
5. Press **Instruct**, type a sentence: the supervisor acknowledges it. Mention the bot in the thread with another sentence: same.
6. Create a decision request (`research decide` shows it; or let a halt create one). Tap an option: the card edits to "answered by you", the buttons disappear, and tapping again says it is already answered. With more than five options the card shows a menu.
7. If chat approval is set up: raise an approval card, press *Approve (authenticator code)*, enter a wrong code (error only you see, button still works), then a right one (card shows approved by you, code nowhere in the channel). A card for a frozen-test edit shows only *Approve on my computer*.
8. `/report` attaches the report; `/logs T-n` attaches a long log; restart the gateway (`research chat gateway restart`): no alert is re-sent.
9. From a second Discord account: DM the bot (pairing code appears, nothing else), mention it in the server (no reply), run a slash command (private "not authorized").
10. `research chat remove discord`; the bot goes silent and `discord.json` is gone.

## Pairing and roles

The allow-list is by **platform user id** (never display name), checked on every message, command and button click.

| Role | May |
|---|---|
| `viewer` | read: `/status /blocked /task /logs /report /finding /usage /proposals /interventions /digest /options /whoami /help /project` |
| `operator` | plus steering: `/say /choose /debug /pause /resume /stop /continue /approve-start /sethome`, reject buttons |
| `approver` | plus answering an authenticator-code prompt, if the owner enabled chat approval (see GOVERNANCE.md) |

* Someone who messages the bot gets an 8-character code (alphabet without 0/O/1/I; valid 1 hour; at most 3 pending; one per person per 10 minutes).
  You approve it **at your computer**: `research chat pair CODE [--role viewer|operator|approver]`. The first paired user is an operator, later ones viewers unless you say.
  Five wrong codes lock pairing for an hour. `research chat unpair telegram USER_ID` removes someone.
* In a **group**, only paired user ids are obeyed (the chat id alone is not enough). A group set as the home chat receives alerts; members
  type `/pair` in the group to get a code. Unpaired members get no reply. Other bots are ignored.
* Pairing happens in the terminal (or the extension) on the machine that runs Research OS, never in chat; agents cannot run `chat setup` or `chat pair`.

## What chat can do

| Class | Examples | In chat |
|---|---|---|
| Read | status, blocked, task, logs, report, finding, usage, proposals, digest | viewer and up |
| Steer (reversible) | `/say`, `/choose N`, `/debug`, `/pause`, `/resume [plan]`, `/stop T-12` (current attempt), `/continue T-12 PROFILE`, `/approve-start T-12` | operator |
| Refuse (safe direction) | Reject on a proposal or intervention card | operator |
| PIN-protected | approve a proposal or protected intervention, create experiments, change policy, approve promotions, authorize auto-promotion | **never** from chat; the card's *Approve on my computer* button tells you the exact command |
| Bypasses verification or history | `supervisor accept`, void attempts, stop a plan with agents, restart | **never** from chat |

`/say [T-12] text` (or plain text) is an instruction to the supervisor: unprotected requests apply immediately; protected ones become a proposal that waits for your PIN.
Long logs and reports travel as attachments (at most four plots referenced by the report, 20 MB each); nothing inline over 1,500 characters.
Several projects on one bot: `/project use NAME` chooses the project for that chat; a command only ever touches the selected project.

## Alerts

Kinds (`notifications.alerts.on`, default all): `halt`, `task_failed`, `approval_needed`, `promotion_ready`, `plan_finished`, `run_failed`, `budget`,
`supervisor_down`, `decision`, `provider_limit`. The same alert is suppressed for `notifications.alerts.cooldown_minutes` (30), then repeated once as
"still: ..."; after `escalate_after` (3) failures in a row for one task the message suggests a next step; "moving again" is sent once. More than five alerts
for one project in a minute fold into one summary. Approval card buttons disappear after `approval_expiry_hours` (24); the proposal stays pending (waiting never approves or denies).

Buttons carry only `a:<token>`: the token maps on this machine to the project, operation and parameters; it is single use, expires after 24 hours, and is checked against the clicker's role.
Decision requests render as one message with a button per option; a free-text reply to that message is the answer.

The gateway also raises `supervisor_down` when a plan is running but the supervisor's heartbeat is older than three polls, and sends the optional morning digest
(`notifications.digest.at: "08:00"`, local time; silent when nothing changed unless `notifications.digest.silent_if_unchanged: false`). `/sethome` chooses the chat that receives digests.

## Settings

| Key | Default | |
|---|---|---|
| `notifications.telegram.enabled` | false | switch for this project |
| `notifications.discord.enabled` | false | switch for this project (needs the discord extra) |
| `notifications.discord.thread_per_task` | true | one thread per task for its alerts |
| `notifications.alerts.on` | all kinds | which kinds reach chat |
| `notifications.alerts.cooldown_minutes` | 30 | dedupe window |
| `notifications.alerts.escalate_after` | 3 | failures in a row before a suggestion |
| `notifications.alerts.approval_expiry_hours` | 24 | buttons removed after |
| `notifications.digest.at` | off | `"HH:MM"` local |
| `notifications.digest.silent_if_unchanged` | true | |
| `notifications.halt_grace_seconds`, `notifications.auto_debug` | 120, false | halt notice delay, automatic diagnosis |

Machine files, all mode 0600 and never in a project: `telegram.json` (token, home chat), `discord.json` (token, application and server id, home channel, project channels, task threads, optional `allowed_channels`), `gateway/{pairing,actions,cursors,chats,refs,pending}.json`, `gateway/daemon.json`
(heartbeat), `gateway/gateway.log`, `projects.json`.

## Threat model

* **Credentials**: the bot token is stored with mode 0600 from creation, never in project files, git, prompts, events or RPC output (status returns booleans). All outbound text and file names pass the redaction filter and anything shaped like a bot token is replaced.
* **Who can talk to the bot**: only paired user ids. Anyone can message a public bot, so strangers get at most one pairing code or one polite refusal per hour, and nothing in groups.
* **What a hijacked chat account gets**: its role. A `viewer` can read project state and logs (which can contain your data; do not pair people you would not show them to). An `operator` can pause, resume, stop an attempt, hand a task to another agent, and instruct the supervisor, but cannot approve anything protected: the PIN never enters chat and no chat operation exists for protected actions. Telegram and Discord store every message on their servers.
* **Replays and floods**: the Telegram offset is saved before each update is handled, so a failed reply never replays a command; each bot has one offset in the gateway's cursor file; storms fold.
* **Chat approval**: off unless you enrolled an authenticator and enabled it (see GOVERNANCE.md). Then approving needs your chat account (role `approver`) **and** your authenticator device; losing the phone means revoking at the computer. A short list of actions (frozen tests, scientific plan text, governance settings, PIN and allow-list changes) is computer-only forever.
* **Not covered**: Telegram and Discord can read what you send (including the authenticator code you type in a modal); use chat for status and steering, not for secrets.

## Troubleshooting

* Nothing arrives: `research chat status` (token, chat, gateway running?), `research chat gateway restart`, `tail ~/.config/research-os/gateway/gateway.log`.
* "Several projects use this bot": `/project use NAME`.
* A person gets "not authorized": pair them (`research chat pair CODE`) or check their role with `/whoami`.
* The gateway runs on the machine where the project lives. Projects on another machine need their own gateway and their own bot.
