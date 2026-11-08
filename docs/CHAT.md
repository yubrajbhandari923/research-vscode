# Chat: alerts and steering from your phone

Research OS can message you when a run needs you and let you answer from Telegram. Everything is **opt-in** and off by default. Discord is
planned on the same core and is not available yet.

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
3. `research chat status` shows the token/chat/gateway state and the paired people; `research chat test` sends a message; `research chat remove` deletes the credentials.

The old `research telegram setup|status|test|remove|poll` still works (hidden aliases). An existing Telegram setup needs no edits: the same
`telegram.json` and `notifications.telegram.enabled` keep working; on first gateway start a private chat id becomes an operator entry.

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
| `notifications.alerts.on` | all kinds | which kinds reach chat |
| `notifications.alerts.cooldown_minutes` | 30 | dedupe window |
| `notifications.alerts.escalate_after` | 3 | failures in a row before a suggestion |
| `notifications.alerts.approval_expiry_hours` | 24 | buttons removed after |
| `notifications.digest.at` | off | `"HH:MM"` local |
| `notifications.digest.silent_if_unchanged` | true | |
| `notifications.halt_grace_seconds`, `notifications.auto_debug` | 120, false | halt notice delay, automatic diagnosis |

Machine files, all mode 0600 and never in a project: `telegram.json` (token, home chat), `gateway/{pairing,actions,cursors,chats,refs,pending}.json`, `gateway/daemon.json`
(heartbeat), `gateway/gateway.log`, `projects.json`.

## Threat model

* **Credentials**: the bot token is stored with mode 0600 from creation, never in project files, git, prompts, events or RPC output (status returns booleans). All outbound text and file names pass the redaction filter and anything shaped like a bot token is replaced.
* **Who can talk to the bot**: only paired user ids. Anyone can message a public bot, so strangers get at most one pairing code or one polite refusal per hour, and nothing in groups.
* **What a hijacked chat account gets**: its role. A `viewer` can read project state and logs (which can contain your data; do not pair people you would not show them to). An `operator` can pause, resume, stop an attempt, hand a task to another agent, and instruct the supervisor, but cannot approve anything protected: the PIN never enters chat and no chat operation exists for protected actions. Telegram stores every message on its servers.
* **Replays and floods**: the Telegram offset is saved before each update is handled, so a failed reply never replays a command; each bot has one offset in the gateway's cursor file; storms fold.
* **Chat approval**: off unless you enrolled an authenticator and enabled it (see GOVERNANCE.md). Then approving needs your chat account (role `approver`) **and** your authenticator device; losing the phone means revoking at the computer. A short list of actions (frozen tests, scientific plan text, governance settings, PIN and allow-list changes) is computer-only forever.
* **Not covered**: Telegram can read what you send; use chat for status and steering, not for secrets.

## Troubleshooting

* Nothing arrives: `research chat status` (token, chat, gateway running?), `research chat gateway restart`, `tail ~/.config/research-os/gateway/gateway.log`.
* "Several projects use this bot": `/project use NAME`.
* A person gets "not authorized": pair them (`research chat pair CODE`) or check their role with `/whoami`.
* The gateway runs on the machine where the project lives. Projects on another machine need their own gateway and their own bot.
