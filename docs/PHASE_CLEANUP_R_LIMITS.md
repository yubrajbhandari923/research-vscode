# Headless usage/rate-limit signals for Claude Code, Codex CLI, opencode and agy

Research date: 2026-11-07. Dev-only file (matches `docs/PHASE_*`); do not commit to main.
Labels: REAL = text/JSON quoted from an issue, doc or source file (URL given). REALISTIC = built from verified field names but not captured from a live run. UNVERIFIED = could not confirm.
No live runs were made; everything below is from docs, source and issues.

---------------------------------------------------------------------------------------------------
## 0. Cross-tool summary

| | Claude Code `-p` | Codex `exec --json` | opencode `run --format json` | agy `-p` |
|---|---|---|---|---|
| Limit hit, exit code | 1 (non-zero) | 1 | 1 (`process.exitCode = 1`) but may retry for a long time first | 3 on agy 1.2.14; 0 on 1.2.2 (timeout path) |
| Where the text is | stdout JSON `result`; sometimes nowhere on stdout | stdout JSONL `error` / `turn.failed`; stderr has log lines | stdout JSON `{"type":"error"}` | stderr `AGY_ERROR: {...}` and stdout `result` event |
| Reset info | epoch seconds in `rate_limit_event.rate_limit_info.resetsAt`; clock time in message | local-time clock string "Try again at ..." | `retryAfterMs` (relative) or none | relative `Resets in 2h39m52s` |
| Resume | `claude -p --resume <session_id>` | `codex exec resume --json <thread_id> "<prompt>"` | `opencode run --session <id>` / `--continue` (UNVERIFIED flags) | `agy --conversation <id> -p ...` |
| Query quota | none official for headless (status line / headers) | app-server `account/rateLimits/read` (UNVERIFIED shape) | none | `/usage` (TUI only) |

---------------------------------------------------------------------------------------------------
## 1. Claude Code CLI

### 1.1 Kinds of limit and their exact text
Sources: https://code.claude.com/docs/en/errors (error index; page is 600 KB, only the first 100 KB was read, so the "Usage limits" section was NOT read: UNVERIFIED for its exact reset wording), https://dev.to/aobaworks/claude-code-rate-limit-reached-retry-setup-for-cron-jobs-1ioi

| Kind | Message (verbatim) |
|---|---|
| 5-hour session | `You've hit your session limit` ... `resets 3:45pm` (dev.to); with plan banner: `You've hit your monthly spend limit · raise it at https://claude.ai/settings/usage?from=cc_cli_limit_message · your session limit resets 9:20pm` (REAL, https://github.com/anthropics/claude-code/issues/104690, a mislabel bug, see 1.5) |
| weekly | `You've hit your weekly limit · resets 11am (UTC)` (REAL, https://github.com/anthropics/claude-code/issues/103820) |
| per-model | `You've hit your Opus limit`, `You've hit your Sonnet limit` (errors doc) |
| API 429 | `Request rejected (429)`, `Server is temporarily limiting requests` (errors doc) |
| overload 529 | `API Error: Repeated 529 Overloaded errors. The API is at capacity — this is usually temporary. Try again in a moment. If it persists, check https://status.claude.com.` (errors doc). A 529 does not count against quota. |
| credit | `Credit balance is too low` (errors doc) |
| API spend cap | 429 body `"error_code": "enforced_spend_limit_reached"`, "You will regain access on 2026-09-01 at 00:00 UTC.", no `retry-after` (https://platform.claude.com/docs/en/api/rate-limits) |
| older interactive texts | `5-hour limit reached - resets 3pm`; `Claude usage limit reached. Your limit will reset at 2pm (America/New_York)`; Desktop: `Usage limit reached · Auto-resuming at 12:50 PM` (https://github.com/anthropics/claude-code/issues/35744) |

### 1.2 What `claude -p` emits
Docs: "Claude Code exits with code 0 on success and a non-zero code when the run fails ... When a failure happens inside the run, such as missing authentication, Claude Code prints the failure as the result on stdout." (https://code.claude.com/docs/en/headless). SIGTERM gives 143 (same page).

REAL final `result` for a usage limit with `--output-format json` (https://github.com/anthropics/claude-code/issues/103807, v2.1.304, exit 1):
```json
{"type":"result","subtype":"success","terminal_reason":"api_error","is_error":true,"api_error_status":429,"api_error":"usage_limit_reached"}
```
Note `subtype` is `"success"` even though it failed: key on `is_error` / `terminal_reason` / `api_error`, never `subtype`.

`stream-json` (needs `--verbose`) carries a quota snapshot event BEFORE the failure (https://littlebearapps.com/help/untether/claude-stream-json-cheatsheet/ and issue 104690):
```json
{"type":"rate_limit_event","rate_limit_info":{"status":"allowed","resetsAt":1774447200,"rateLimitType":"five_hour","overageStatus":"rejected","overageDisabledReason":"org_level_disabled","isUsingOverage":false}}
```
- `status`: `allowed` | `allowed_warning` | `rejected`. `resetsAt`: epoch SECONDS (UTC instant, no timezone problem). `rateLimitType`: `five_hour`, `seven_day`, `seven_day_opus`, `seven_day_sonnet`, `seven_day_overage_included`, `overage` (cheatsheet). The same event also carries `utilization` (0.97 seen in issue 103820).
- Caveats from the cheatsheet: it is a snapshot, not a throttle; `rejected` + `isUsingOverage:true` means extra usage covers it (not a stop); a `rejected` with a past `resetsAt` is not a throttle. The cheatsheet's legacy fields (`retry_after_ms` etc.) were never seen from a real CLI.
- Docs-confirmed retry event (https://code.claude.com/docs/en/headless): `system/api_retry` with `attempt`, `max_retries`, `retry_delay_ms`, `error_status` (int|null), `error` (one of `rate_limit`, `overloaded`, `billing_error`, `authentication_failed`, `account_on_hold`, `server_error`, ...), `session_id`, `uuid`. This is the "backing off" signal; it is NOT a limit-reached signal.
- The official docs page for `SDKRateLimitEvent` (https://code.claude.com/docs/en/agent-sdk/typescript) was not reached in my fetch (page truncated): the field list above is from the cheatsheet plus issues, so schema-level confidence is medium.

Reset headers on the underlying 429 (REAL, issue 103820): `anthropic-ratelimit-unified-7d-status: rejected`, `anthropic-ratelimit-unified-reset: 1792494000` (epoch s), `retry-after: 56482` (seconds). Issue 104690 also names `anthropic-ratelimit-unified-status`, `anthropic-ratelimit-unified-representative-claim: five_hour`, `anthropic-ratelimit-unified-overage-status`, `anthropic-ratelimit-unified-overage-disabled-reason`. Documented API headers: `retry-after` is "the number of seconds"; `anthropic-ratelimit-*-reset` is "RFC 3339" (https://platform.claude.com/docs/en/api/rate-limits). Token bucket, so per-minute limits refill continuously.

### 1.3 Behaviour when the limit hits mid-run
- Transient 429/529 are retried internally up to 10 times with exponential backoff (default); `CLAUDE_CODE_MAX_RETRIES` (cap 15 since v2.1.186) (https://code.claude.com/docs/en/errors).
- `CLAUDE_CODE_RETRY_WATCHDOG=1`: retries 429 and 529 indefinitely for unattended runs; "A standard-speed 429 that reports a spend limit or exhausted credits fails immediately." Also `CLAUDE_CODE_RETRY_WATCHDOG_MAX_WAIT_MS` (v2.1.295+) and `CLAUDE_CODE_RETRY_WATCHDOG_USAGE_LIMIT_RECHECK_MS` (60000-10800000, v2.1.305+) (same page). Consequence for a supervisor: with the watchdog on, the process may SLEEP (no exit) for hours through a 5-hour limit, so "no output for a long time" is not a hang. The supervisor must decide whether to enable it; leaving it off gives a clean exit 1 plus reset time, which is easier to schedule.
- Watchdog bug: accounts without extra usage hit `overageDisabledReason: org_level_disabled_until`; the CLI then misreads a session limit as a spend cap, exits at once and prints "monthly spend limit" (REAL, issue 104690, v2.1.309). So the message TEXT can lie; trust `rateLimitType` + `resetsAt`.
- Unattended silent gating: a lead session in agent-team mode froze 5h01m with no transcript event and no banner (https://github.com/anthropics/claude-code/issues/67964, v2.1.175). Not a `-p` crash, but shows a stuck run can be a usage gate: detect via `no new events` + a recent `rate_limit_event` with `status: rejected`.

### 1.4 Known holes in what `-p` emits (all REAL, filed Oct-Nov 2026)
1. Weekly limit, exit 1 with nothing on stdout: no `rate_limit_event` rejected, no assistant error, no `result`; the message exists only in the transcript `~/.claude/projects/<slug>/<session>.jsonl` as an `assistant` entry with `isApiErrorMessage: true`. Last stdout line was a base64 fragment (https://github.com/anthropics/claude-code/issues/103820, v2.1.303-304, with `ANTHROPIC_LOG=debug`; may be specific to that setup, UNVERIFIED in the clean case).
2. A late `rate_limit_event` after `result` makes `--output-format json`/text exit 1 with `Error: No messages returned from query` and empty stdout (https://github.com/anthropics/claude-code/issues/103253, v2.1.302). `stream-json` is unaffected. This is a false failure (task finished), not a limit.
3. `rate_limit_event.overageStatus:"rejected"` with `result:""` and exit 0 in v2.1.83 (https://github.com/anthropics/claude-code/issues/38673): empty result, no error.
4. `subtype:"success"` with `is_error:true` (issue 103807, above).

### 1.5 Resume afterwards
`claude -p "<prompt>" --resume <session_id>` (or `--continue` for the latest in the cwd); session id is `session_id` in every JSON/stream event; sessions are found by id in any project on the machine (https://code.claude.com/docs/en/headless "Continue conversations"). External relays do exactly this after the reset: `claude --resume <session> -p "<continuation prompt>"` (issue 35744 comment) and the issue 104690 runner "reads the `rate_limit_event` with `"status":"rejected"` from stream-json output, sleeps until `resetsAt`, and resumes the session with `--resume`". An interrupted turn is not continued unless `CLAUDE_CODE_RESUME_INTERRUPTED_TURN=1`.

### 1.6 Querying quota
No official headless command found. Interactive `/usage` exists (UNVERIFIED for headless). The `rate_limit_event` snapshot (also at `allowed`, with `utilization`) is the only in-stream source; a comment in issue 35744 says one tool reads reset time from status-line data (UNVERIFIED which field). API-key users: response headers above.

---------------------------------------------------------------------------------------------------
## 2. OpenAI Codex CLI (`codex exec`)

### 2.1 Event stream
Docs (https://learn.chatgpt.com/docs/non-interactive-mode, redirect target of developers.openai.com/codex/noninteractive): with `--json` stdout is JSONL; "Event types include `thread.started`, `turn.started`, `turn.completed`, `turn.failed`, `item.*`, and `error`." Without `--json`, progress goes to stderr and only the final message to stdout. Exit codes are NOT documented there.

Source (https://github.com/openai/codex/blob/main/codex-rs/exec/src/event_processor_with_jsonl_output.rs): a server error becomes `ThreadEvent::Error(ThreadErrorEvent{message})`, message = `"{error.message} ({additional_details})"` when details exist; a failed turn becomes `turn.failed` with `{"error":{"message":...}}` (fallback `"turn failed"`). `thread.started` carries `thread_id`.
Exit code (https://github.com/openai/codex/blob/main/codex-rs/exec/src/lib.rs lines ~1415-1485): `error_seen` is set on a non-retrying `Error` notification for the primary turn, or a turn that ends `Failed`/`Interrupted`; then `std::process::exit(1)`. Other startup failures also `exit(1)`. So exit 1 = any failure; limit vs other must be read from the message.

### 2.2 Usage-limit message (source of truth)
`UsageLimitReachedError` Display, https://github.com/openai/codex/blob/main/codex-rs/protocol/src/error.rs (note the typographic apostrophe U+2019 in "You’ve"):
- Plus: `You’ve hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit https://chatgpt.com/settings/usage to purchase more credits{suffix}`
- Team/Business/Enterprise-CBP: `You’ve hit your usage limit. To get more access now, send a request to your admin{suffix}`
- Free/Go: `You’ve hit your usage limit. Upgrade to Plus to continue using Codex (https://chatgpt.com/explore/plus),{suffix}`
- Pro/ProLite/ProMax: `You’ve hit your usage limit. Visit https://chatgpt.com/settings/usage to purchase more credits{suffix}`
- Enterprise/Edu, unknown plan: `You’ve hit your usage limit.{suffix}`
- Named limit (e.g. per-model bucket): `You’ve hit your usage limit for {limit_name}. Switch to another model now,{suffix}`
- suffix: ` Try again at {formatted}.` / ` Try again later.`; after "or": ` or try again at {formatted}.`
- Format: `format_retry_timestamp` converts to the LOCAL timezone of the codex process; same local day: `"%-I:%M %p"` (e.g. `3:41 PM`); otherwise `"%b %-d{st|nd|rd|th}, %Y %-I:%M %p"` (e.g. `Feb 23rd, 2026 9:01 PM`). No timezone name in the string. Since the supervisor and codex share a host, parse it as host-local time.
- Real example (https://github.com/openai/codex/issues/12299, Plus, VS Code): `You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at Feb 23rd, 2026 9:01 PM.` (older build, ASCII apostrophe, different URL).
- When the reset time is unknown the text ends `Try again later.` (no timestamp): fall back to a probe/backoff schedule.
- Limit kinds: 5-hour primary window, weekly secondary window, credits, per-limit-name buckets, workspace limits (`rate_limit_reached_type`, wording not read: UNVERIFIED). Struct: `RateLimitSnapshot{limit_id, limit_name, primary, secondary, credits, ...}`, `RateLimitWindow{used_percent (0-100), window_minutes, resets_at (unix seconds)}` (https://github.com/openai/codex/blob/main/codex-rs/protocol/src/protocol.rs). Epoch seconds are therefore available to app-server clients; I did NOT verify they appear in `exec --json` output (UNVERIFIED; the message text is the only confirmed carrier).

### 2.3 Plain 429 (per-minute) path
Real stderr from `codex exec` (https://github.com/openai/codex/issues/9135, Pro, 0.80-based; note the account still showed 61% remaining):
```
ERROR codex_api::endpoint::responses: error=http 429 Too Many Requests: Some("{\n  \"error\": {\n    \"message\": \"You've exceeded the rate limit, please slow down and try again after 60.616292 seconds.\",\n    \"type\": \"invalid_request_error\",\n    \"param\": null,\n    \"code\": \"rate_limit_exceeded\"\n  }\n}")
ERROR: exceeded retry limit, last status: 429 Too Many Requests, request id: 9bd33f31fd269bb1-HNL
```
Here the reset is a RELATIVE float seconds inside the message ("try again after 60.616292 seconds"). Codex retries internally first (the same `error.rs` has `retry_delay`, honours server `retry_after`, "Expired advice stays present as zero"), and gives up with `exceeded retry limit`. In `--json` mode this should surface as an `error` / `turn.failed` event (REALISTIC, not captured). The issue's fifteen-minute failure lasted until the 5h window reset.

### 2.4 Resume
`codex exec resume --last "<prompt>"` or `codex exec resume <SESSION_ID> "<prompt>"` (docs above; clap source https://github.com/openai/codex/blob/main/codex-rs/exec/src/cli.rs: `Resume(ResumeArgs)` with `--last`). `thread_id` from the `thread.started` event is the id. The repo's adapter notes resume accepts neither `-s` nor `-C`.

### 2.5 Querying quota
TUI `/status`. In the app-server protocol there is an `account/rateLimits/read` method and an `AccountRateLimitsUpdatedNotification` (file names only confirmed in https://github.com/openai/codex/tree/main/codex-rs/app-server-protocol; field shapes UNVERIFIED beyond RateLimitSnapshot above). API-key (non-ChatGPT) auth uses OpenAI API headers: `x-ratelimit-reset-requests: 1s`, `x-ratelimit-reset-tokens: 6m0s` (relative Go-style durations), `Retry-After` may appear on 429 (https://developers.openai.com/api/docs/guides/rate-limits). Docs have no 429 body example; they list code `slow_down`.

---------------------------------------------------------------------------------------------------
## 3. opencode (`opencode run --format json`)

Repo is now github.com/anomalyco/opencode (branch `dev`; previously sst/opencode, which now 404s on raw paths). Source below is from `dev`, a rewritten (v2-style) core; older releases may differ (UNVERIFIED which release each user runs).

### 3.1 JSON event stream
`packages/cli/src/run/noninteractive.ts`: every JSON event is `{"type": <type>, "timestamp": <ms>, "sessionID": <id>, ...data}` newline-delimited on stdout (`emit()`). Types seen in source: `error`, `retry`, `tool`, `step-start`, `step-finish`, text/reasoning parts.
- Retry notice (REALISTIC from source; fields `messageID`, `attempt`, `nextRetryAt` (epoch ms), `error`): `{"type":"retry","timestamp":1790000000000,"sessionID":"ses_x","messageID":"msg_x","attempt":2,"nextRetryAt":1790000004000,"error":{"type":"...","message":"...","status":429,"retryAfterMs":4000}}`
- Final failure, `packages/cli/src/run/run.ts` `reportRunError`: sets `process.exitCode = 1` and prints `{"type":"error","timestamp":...,"sessionID":"...","error":{"type":"unknown","message":"..."}}`. When the session outcome carries a structured error it is passed through as `error: {type,message,status?,response?:{body},retryAfterMs?}` (https://github.com/anomalyco/opencode/blob/dev/packages/schema/src/session-error.ts). Else a fallback `Session ended without a recorded outcome` / `Session failed`.
- Default (non-JSON) format prints `retrying (attempt N): <message>` and `UI.error(message)` to stderr (source).

### 3.2 Error taxonomy (https://github.com/anomalyco/opencode/blob/dev/packages/ai/src/schema/errors.ts)
`RateLimit` (retryable; `retryAfterMs`, `rateLimit` details), `QuotaExceeded` (NOT retryable; optional `retryAfterMs`), `ProviderInternal` (retryable; 503/529), `Authentication`, `InvalidRequest`, `Timeout`, `Transport`, `UnknownProvider` (retryable). An `x-should-retry` response header overrides the classification. So a hard quota (weekly/monthly, e.g. Anthropic spend cap, OpenAI `insufficient_quota`) should fail immediately as `QuotaExceeded`, while per-minute 429 is retried. The mapping of each provider's 429 body to `QuotaExceeded` vs `RateLimit` was not read: UNVERIFIED (PR https://github.com/anomalyco/opencode/pull/24013 states the intent "non-transient rate limits (weekly/monthly quotas) should not be retried").

### 3.3 Retry behaviour (hang risk)
- Current `dev` (`packages/core/src/model-retry.ts`): 10 retries, exponential from 2s capped at 10s, jittered (about 84s total); provider `retry-after` honoured but bounded to 15 minutes (`RETRY_AFTER_MAX`); network-unreachable errors get a separate budget of up to 1 hour; at most 3 timeout retries. After exhaustion the error surfaces and the run exits 1.
- Earlier behaviour (REAL, https://github.com/anomalyco/opencode/issues/21960): retried 429/529/overloaded "indefinitely", backoff 2s,4s,8s,16s,30s,30s...; `retry-after-ms` honoured up to 2,147,483,647 ms (~24.8 days); status stayed `retry`, `session.error`/`session.idle` never emitted. Maintainer comment: capped to 10 attempts, `retry-after` honoured up to 15 min, "Fixed in #49441". Also reported: `opencode run --auto` hanging "with zero output" (comment in same issue). So an older opencode can produce NO terminal event; the supervisor needs its own inactivity timeout and should treat `retry` events with large `nextRetryAt` as a limit.
- HTTP 400 "rate limit exceeded" from some providers stops the session instead of retrying (https://github.com/anomalyco/opencode/issues/40241, PR #40239).
- Free-tier text: UI string `Free usage exceeded` (https://github.com/anomalyco/opencode/blob/dev/packages/ui/src/i18n/en.ts); CLI JSON form UNVERIFIED.

### 3.4 Underlying provider bodies and headers (opencode passes these through into `error.message` / `response.body`)
- Anthropic 429: `{"type":"error","error":{"type":"rate_limit_error","message":"..."}}`, headers `retry-after` (seconds), `anthropic-ratelimit-*-reset` (RFC 3339); 529: `overloaded_error`; spend cap: `error_code: enforced_spend_limit_reached`, no `retry-after`, reset in message as `2026-09-01 at 00:00 UTC` (https://platform.claude.com/docs/en/api/rate-limits). Self-set spend limit returns HTTP 400 `invalid_request_error` "You have reached your specified API usage limits" (same page): a 400, not a 429.
- OpenAI 429: `x-ratelimit-reset-requests: 1s`, `x-ratelimit-reset-tokens: 6m0s`, optional `Retry-After`; body code `rate_limit_exceeded` (REAL from issue 9135 above) or `insufficient_quota` (UNVERIFIED body text).
- Google (Gemini API / Antigravity backend): `RESOURCE_EXHAUSTED (code 429)` (see agy section).

### 3.5 Resume
`opencode run --session <id>` and `--continue` (UNVERIFIED: flags not fetched this session; recall from opencode CLI docs, verify with `opencode run --help`). `sessionID` is present on every JSON event.

### 3.6 Querying quota
None found for opencode itself (UNVERIFIED). Use provider APIs/headers.

---------------------------------------------------------------------------------------------------
## 4. agy (Google Antigravity CLI)

Official headless docs exist: https://antigravity.google/docs/cli/headless/ (found via search; fetched). The page documents NO quota behaviour. Findings below are mostly from the official issue tracker https://github.com/google-antigravity/antigravity-cli (maintainers' own repo), so they are REAL but community-measured.

### 4.1 Headless interface (official docs)
- `-p`/`--print`/`--prompt "<text>"`; `--output-format text|json|stream-json`; `--json-schema`; `--dangerously-skip-permissions`; `--print-timeout` (default `0s` = no limit in current docs; older 1.2.x had 5m, so a limit-hit can end as a timeout there).
- Exit: "A successful run exits `0`. A run that fails to produce a response exits non-zero". Status values in json/stream-json: `SUCCESS ERROR CANCELED INTERRUPTED INVALID WAITING RUNNING`. Non-authenticated, non-TTY run exits with an `authentication required` error.
- Resume: `--continue`/`-c` (most recent), `--conversation <id>` (id from `conversation_id` of a previous run); each is a new process.
- Third-party note: `agy -p` printed nothing when stdout is not a TTY (agy-headless-bridge on PyPI; page could not be fetched, UNVERIFIED and likely version-specific).

### 4.2 The limit signal
REAL (https://github.com/google-antigravity/antigravity-cli/issues/1018, measured 2026-09-14 on 1.2.2 and re-measured on 1.2.14, Windows):
- 1.2.2 (headless, `--output-format stream-json`): in-process retries ran until `--print-timeout`; exit code 0; stderr `[agy] print timeout after 23s with turn in progress; returning partial output`; final stdout event:
```json
{"event":"result","result":{"status":"ERROR","response":"","error":"API error (attempt 3): RESOURCE_EXHAUSTED (code 429): Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 2h39m52s.","duration_seconds":14.7485001,"num_turns":1,"usage":{"total_tokens":0}}}
```
- 1.2.14: ends in 6.8 s, exit code 3, stderr:
```
AGY_ERROR: {"short_error":"RESOURCE_EXHAUSTED (code 429): Individual quota reached. ... Resets in 1h1m35s.","status":"RESOURCE_EXHAUSTED","error_code":429,"code_kind":"http","retryable":true}
```
  Caveat in the same issue: `retryable` is `true` even though the reset is more than an hour away, so do not key on `retryable`.
- Reset format: RELATIVE `Resets in 2h39m52s` / `1h1m35s` (Go-style duration pieces; shorter forms like `45s` or `5m3s` are likely: UNVERIFIED). Relative to the moment of the error, no timezone.
- Other kinds (https://github.com/google-antigravity/antigravity-cli/issues/1278 and community, UNVERIFIED wording): `MODEL_CAPACITY_EXHAUSTED` is temporary capacity (retry/switch model, no reset); `Individual quota reached` is the real quota. Google's plan page: AI Pro refreshes every five hours until the weekly limit, other plans weekly (https://antigravity.google/docs/plans, found via search, not fetched, so UNVERIFIED). Issue 1278 (agy 1.4.2) reports quota exhaustion sometimes surfaces as a generic timeout rather than a quota error: the supervisor must also treat "timeout with 0 tokens" as suspect.
- A date-format issue exists for the IDE notification (`11/6/2026, 4:06:45 PM`, ambiguous), IDE only, not CLI (https://github.com/google-antigravity/antigravity-cli/issues/1378).
- Silent retries can drain quota and hang past `--print-timeout` on some backends (https://github.com/google-antigravity/antigravity-cli/issues/594, title only, UNVERIFIED).
- Headless `agy -p` starts with a cold prompt cache every process, so resuming costs more quota (issue 1411, user measurement `2.5-7.2x` per turn: UNVERIFIED claim).

### 4.3 Mid-run behaviour, resume, query
Mid-run: retries in-process (1.2.1 notes: 502/503/504, per-minute 429 retried with exponential backoff); newer builds stop right away for a daily/billing cap (1.2.13 note, quoted in issue 1018). Exit code 3 observed on 1.2.14 only; the code table is not documented (UNVERIFIED). Resume: `agy --conversation <id> --output-format json -p "<prompt>"` (repo adapter says verified on agy 1.5.0). Query: `/usage` (alias `/quota`) in the TUI shows remaining quota per model (community docs, https://github.com/google-antigravity/antigravity-cli/issues/46 is titled "Usage and Quota", not read); no headless equivalent found.

---------------------------------------------------------------------------------------------------
## 5. What the repo's current check misses

Code (packages/core/research/supervisor.py):
- `_PROVIDER_ERR = re.compile(r"hit your .*limit|rate.?limit|usage limit|overloaded|\b429\b|\b529\b|credit balance|quota", re.I)`
- `_provider_error(p, a)` reads the attempt's STDOUT only (`E.logs(...).get("out")`), takes the LAST non-empty line, `json.loads` it, and only reports when the dict has `is_error` truthy or `terminal_reason == "api_error"`; for `terminal_reason == "api_error"` it returns `msg[:200] or "api error"`. Any other last line hits `break`; a non-JSON last line raises ValueError which is swallowed, returning None.
- It is only called inside `_finish_attempt` when `ex["state"] == "failed"` (non-zero exit / failed launcher). No reset time is parsed anywhere; the reason string says only "resume the plan or press Retry when it is available again".
- Adapters (providers/claude.py, codex.py, agy.py, custom.py): claude runs `-p --output-format json` (single JSON object, so the last line works); codex runs `exec --json` (JSONL); agy runs `--output-format json -p`. There is NO opencode adapter, so opencode falls under `custom`.

Misses, by provider:

Claude
1. Reset time is never extracted: in `--output-format json` there is no `resetsAt` (that is in stream-json only), and the `result` text carries `resets 3:45pm` / `resets 11am (UTC)` / `(America/New_York)`, which is ignored.
2. Exit 1 with empty stdout (issue 103820, weekly limit; issue 103253 late `rate_limit_event`): last line absent, so the failure is booked as `agent_process_failure` and burns an attempt. Nothing is read from stderr or the transcript `.jsonl`.
3. `result` text with no `is_error` (issue 38673: exit 0, empty `result`, overage rejected) is neither a failure nor a limit.
4. Wrong label: the "monthly spend limit" text for a session limit (issue 104690); the regex matches both, so no mis-detection, but any logic keyed on "monthly" would be wrong.
5. `api_error` values (`usage_limit_reached`, `rate_limit`, `billing_error`) and `api_error_status: 429` are not inspected; only `terminal_reason`. `subtype: "success"` is fine (not used). `Server is temporarily limiting requests` does not match the regex (no `429`/`rate limit` words) and `Request rejected (429)` only matches via `\b429\b`; both would be missed if `is_error` were absent.

Codex
6. COMPLETELY missed on the JSON path: the last stdout line is `{"type":"turn.failed","error":{"message":"You’ve hit your usage limit. ..."}}`, which has no `is_error` or `terminal_reason`. The `error` event is `{"type":"error","message":...}`, also no keys checked. The regex would match the text, but it is never applied to these events.
7. stderr lines (`ERROR: exceeded retry limit, last status: 429 Too Many Requests`) are never read. Exit 1 maps to `agent_process_failure`, counting an attempt.
8. "Try again at ..." local-time clock string is never parsed.
9. The curly apostrophe: `hit your .*limit` does not need the apostrophe, so it matches, but any regex written as `You've` with ASCII `'` will not (use `You.ve`).

agy
10. COMPLETELY missed: stdout has `{"event":"result","result":{"status":"ERROR",...}}` (no `is_error`), and on 1.2.14 the message is on STDERR as `AGY_ERROR: {...}` with exit 3; stdout is read only.
11. 1.2.2-era behaviour exits 0 after `--print-timeout`, so `_provider_error` is not even called (it needs `state == "failed"`). The only signal is `status:"ERROR"` in stdout.
12. `Resets in 2h39m52s` relative duration is never parsed; `RESOURCE_EXHAUSTED` alone is not in the regex (`quota` in "Individual quota reached" does match; `MODEL_CAPACITY_EXHAUSTED` does not and has no reset).
13. Resume after the limit is possible (`--conversation <id>`) but the supervisor stores no `resume_at`.

opencode
14. No adapter; JSON `{"type":"error","error":{...,"retryAfterMs":...}}` has no `is_error`; the `retry` events with `nextRetryAt` are ignored; a long `retry-after` makes the run look hung until `attempt_timeout_minutes`.

General fixes suggested (no code written): scan stdout AND stderr tail with per-provider recognisers; apply the regex to `message`/`error`/`result` fields of whatever final event exists, not only to `is_error`; return a structured `{kind, reset_epoch|None, raw}`; relax the "only when failed" guard (agy exit 0, claude exit 0 with empty result and a prior `rejected` event); keep a fallback schedule when no reset time is given.

---------------------------------------------------------------------------------------------------
## 6. Detection and parse table

| Provider | Signal to detect | Where | Reset info format | Parse recipe | Confidence |
|---|---|---|---|---|---|
| Claude | `terminal_reason=="api_error"` and `api_error=="usage_limit_reached"` (or `is_error` and text `hit your (session\|weekly\|Opus\|Sonnet) limit`) | stdout final `result` JSON; exit 1 | clock time in text, may include `(TZ)`; no date | `re.search(r"resets (\d{1,2}(?::\d{2})?\s?[ap]m)(?: \(([^)]+)\))?", s, re.I)`; if no tz, host-local; roll to next day if past | high for detection, medium for reset time |
| Claude (stream-json) | `type=="rate_limit_event"` and `rate_limit_info.status=="rejected"` and not `isUsingOverage` | stdout line | `resetsAt` epoch seconds | `datetime.fromtimestamp(info["resetsAt"], tz=utc)`; kind = `rateLimitType` | high (docs-adjacent, many issues) |
| Claude | exit 1, no `result` line, no stdout | exit code, transcript | none | read newest `~/.claude/projects/*/<session>.jsonl`, find `isApiErrorMessage:true` entry, regex as row 1 | medium (single report, issue 103820) |
| Claude | `system/api_retry` `error in {rate_limit, overloaded}` | stdout (stream-json) | `retry_delay_ms` relative | `now + retry_delay_ms/1000` | high (docs), but transient only |
| Claude / any Anthropic API | HTTP 429, `retry-after`; spend cap 429 has `error_code: enforced_spend_limit_reached` | API response / `error.response.body` | seconds; RFC 3339; `00:00 UTC` first of month | `float(h["retry-after"])`; `datetime.fromisoformat(h["anthropic-ratelimit-requests-reset"])`; `re.search(r"regain access on (\d{4}-\d\d-\d\d) at (\d\d:\d\d) UTC", msg)` | high (docs) |
| Claude 529 | `Repeated 529 Overloaded errors` | stdout `result` | none | back off 1-5 min; not a quota | high |
| Codex | event `turn.failed` or `error` whose message matches `hit your usage limit` | stdout JSONL; exit 1 | `Try again at 3:41 PM.` or `Feb 23rd, 2026 9:01 PM`, host-local | `re.search(r"[Tt]ry again at (.+?)\.?$", msg)` then `strptime` after stripping `st\|nd\|rd\|th` from the day; `"later"` means unknown | high (source) |
| Codex | stderr `exceeded retry limit, last status: 429` / JSON `rate_limit_exceeded` | stderr or `error` event | relative seconds `try again after 60.616292 seconds` | `re.search(r"after ([\d.]+) seconds", s)` | medium (one issue) |
| opencode | stdout `{"type":"error"}` with `error.status==429` or message matching limit/quota; `error.retryAfterMs` | stdout JSON; exit 1 | `retryAfterMs` relative ms (or absent) | `now + e.get("retryAfterMs",0)/1000` | medium (source, not live) |
| opencode | `{"type":"retry"}` with big `nextRetryAt`, or no events for N min | stdout | `nextRetryAt` epoch ms | `nextRetryAt/1000` | medium |
| agy | stderr `AGY_ERROR: {...}` JSON with `status=="RESOURCE_EXHAUSTED"`, exit 3 | stderr; exit 3 | `Resets in 2h39m52s` | `re.search(r"Resets in (?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", s)`; `now + h*3600+m*60+s` | medium-high (issue 1018) |
| agy | stdout `{"event":"result","result":{"status":"ERROR","error":"... RESOURCE_EXHAUSTED ..."}}` (maybe exit 0) | stdout | same | same | medium-high |
| agy | `MODEL_CAPACITY_EXHAUSTED` | stderr/stdout | none | retry/switch model | low (community only) |

---------------------------------------------------------------------------------------------------
## 7. Test fixture strings

Each string is one line unless noted. "REAL" means quoted from the cited source.

### 7.1 Claude Code
1. REAL (issue 103807): `{"type":"result","subtype":"success","terminal_reason":"api_error","is_error":true,"api_error_status":429,"api_error":"usage_limit_reached"}`
2. REALISTIC (field names from issue 103807; `result` text from issue 103820): `{"type":"result","subtype":"success","is_error":true,"terminal_reason":"api_error","api_error_status":429,"api_error":"usage_limit_reached","result":"You've hit your weekly limit · resets 11am (UTC)","session_id":"abc"}`
3. REAL (issue 104690 text): `You've hit your monthly spend limit · raise it at https://claude.ai/settings/usage?from=cc_cli_limit_message · your session limit resets 9:20pm`
4. REAL (issue 103820 text): `You've hit your weekly limit · resets 11am (UTC)`
5. REAL (dev.to): `You've hit your session limit · resets 3:45pm`
6. REAL (issue 104690 field set): `{"type":"rate_limit_event","rate_limit_info":{"status":"rejected","rateLimitType":"five_hour","overageStatus":"rejected","overageDisabledReason":"org_level_disabled_until","isUsingOverage":false,"resetsAt":1790578200}}` (the `resetsAt` value here is borrowed from the cheatsheet example)
7. REAL (cheatsheet, truncated): `{"type":"rate_limit_event","rate_limit_info":{"status":"allowed","resetsAt":1790578200,"rateLimitType":"five_hour"}}` (must NOT trigger a pause)
8. REAL (issue 103820 line): `{"type":"rate_limit_event","rate_limit_info":{"status":"allowed_warning","rateLimitType":"seven_day","utilization":0.97,"resetsAt":1792494000}}` (warning only, no pause)
9. REAL (errors doc): `API Error: Repeated 529 Overloaded errors. The API is at capacity — this is usually temporary. Try again in a moment. If it persists, check https://status.claude.com.`
10. REAL (older banner, issue 35744 title of #5977): `Claude usage limit reached. Your limit will reset at 2pm (America/New_York)`
11. REAL (API body, platform docs): `{"type":"error","error":{"type":"rate_limit_error","message":"You have reached your API usage limits: your organization has crossed its monthly API usage threshold, set based on your organization's API tier. You will regain access on 2026-09-01 at 00:00 UTC.","details":{"error_code":"enforced_spend_limit_reached"}}}`
12. REAL (issue 103253, stderr; false failure, must NOT be treated as a limit): `Error: No messages returned from query`
13. REAL (issue 103820 headers): `retry-after: 56482` and `anthropic-ratelimit-unified-reset: 1792494000`
14. REAL (errors doc): `Credit balance is too low`

### 7.2 Codex
1. REAL (source template, Plus): `You’ve hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit https://chatgpt.com/settings/usage to purchase more credits or try again at 3:41 PM.`
2. REAL (issue 12299): `You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at Feb 23rd, 2026 9:01 PM.`
3. REALISTIC (event shape from `exec_events.rs`, text from source): `{"type":"turn.failed","error":{"message":"You’ve hit your usage limit. Visit https://chatgpt.com/settings/usage to purchase more credits Try again at Nov 8th, 2026 1:05 AM."}}`
4. REALISTIC: `{"type":"error","message":"You’ve hit your usage limit. To get more access now, send a request to your admin Try again later."}` (no timestamp, fallback)
5. REAL (source template, named limit): `You’ve hit your usage limit for GPT-5-Codex-Spark. Switch to another model now, Try again at 9:12 AM.` (limit name here is invented, template is real)
6. REAL (issue 9135 stderr): `ERROR: exceeded retry limit, last status: 429 Too Many Requests, request id: 9bd33f31fd269bb1-HNL`
7. REAL (issue 9135): `ERROR codex_api::endpoint::responses: error=http 429 Too Many Requests: Some("{\n  \"error\": {\n    \"message\": \"You've exceeded the rate limit, please slow down and try again after 60.616292 seconds.\",\n    \"type\": \"invalid_request_error\",\n    \"param\": null,\n    \"code\": \"rate_limit_exceeded\"\n  }\n}")`
8. REAL (docs, event names): `{"type":"thread.started","thread_id":"019bb64c-b94f-7c61-98a1-61db54ac2c20"}` (thread id is REALISTIC; the key is real; must yield session id, not a limit)
9. REALISTIC: `{"type":"turn.failed","error":{"message":"turn failed"}}` (source fallback; ambiguous, not a limit)
10. REALISTIC (day-suffix edge): `... Try again at Nov 11th, 2026 12:00 AM.` and `... Try again at Nov 22nd, 2026 11:59 PM.` (checks `th` for 11-13 and `nd`)

### 7.3 opencode
All REALISTIC unless noted; shapes from `reportRunError`, `noninteractive.ts` and `session-error.ts`.
1. `{"type":"error","timestamp":1790000000000,"sessionID":"ses_1","error":{"type":"unknown","message":"Rate limit exceeded"}}` (REAL shape from run.ts)
2. `{"type":"error","timestamp":1790000000000,"sessionID":"ses_1","error":{"type":"APIError","message":"rate_limit_error: This request would exceed the rate limit","status":429,"retryAfterMs":42000}}`
3. `{"type":"retry","timestamp":1790000000000,"sessionID":"ses_1","messageID":"msg_1","attempt":3,"nextRetryAt":1790000016000,"error":{"type":"APIError","message":"Overloaded","status":529}}`
4. `{"type":"error","timestamp":1790000000000,"sessionID":"ses_1","error":{"type":"QuotaExceeded","message":"You exceeded your current quota, please check your plan and billing details.","status":429}}` (OpenAI `insufficient_quota` style text, UNVERIFIED wording)
5. `{"type":"error","timestamp":1790000000000,"sessionID":"ses_1","error":{"type":"unknown","message":"Free usage exceeded"}}` (string from `ui.sessionTurn.error.freeUsageExceeded`; CLI use UNVERIFIED)
6. `{"type":"error","timestamp":1790000000000,"sessionID":"","error":{"type":"unknown","message":"Session ended without a recorded outcome"}}` (REAL fallback text, not necessarily a limit)
7. `retrying (attempt 2): rate limit reached, retry after 30s` (default-format stderr, text pattern REAL, wording invented)
8. `{"type":"retry","timestamp":1790000000000,"sessionID":"ses_1","messageID":"msg_1","attempt":1,"nextRetryAt":1790000900000,"error":{"type":"APIError","message":"429","status":429,"retryAfterMs":900000}}` (15-minute cap case)
9. `{"type":"error","timestamp":1790000000000,"sessionID":"ses_1","error":{"type":"unknown","message":"HTTP 400: rate limit exceeded"}}` (issue 40241 style, 400 not 429)
10. Provider bodies (REAL, Anthropic docs 529): `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}`

### 7.4 agy
1. REAL (issue 1018, 1.2.2 stdout): `{"event":"result","result":{"status":"ERROR","response":"","error":"API error (attempt 3): RESOURCE_EXHAUSTED (code 429): Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 2h39m52s.","duration_seconds":14.7485001,"num_turns":1,"usage":{"total_tokens":0}}}`
2. REAL (issue 1018, 1.2.14 stderr): `AGY_ERROR: {"short_error":"RESOURCE_EXHAUSTED (code 429): Individual quota reached. ... Resets in 1h1m35s.","status":"RESOURCE_EXHAUSTED","error_code":429,"code_kind":"http","retryable":true}`
3. REAL (issue 1018 stderr, exit 0 path): `[agy] print timeout after 23s with turn in progress; returning partial output`
4. REALISTIC (minutes and seconds only): `AGY_ERROR: {"short_error":"RESOURCE_EXHAUSTED (code 429): Individual quota reached. Resets in 4m12s.","status":"RESOURCE_EXHAUSTED","error_code":429,"code_kind":"http","retryable":true}`
5. REALISTIC (hours only): `... Individual quota reached. Resets in 3h.`
6. REALISTIC (capacity, no reset): `AGY_ERROR: {"short_error":"MODEL_CAPACITY_EXHAUSTED (code 503): The model is overloaded. Please try again later.","status":"UNAVAILABLE","error_code":503,"code_kind":"http","retryable":true}`
7. REAL phrase (docs): `authentication required` (non-interactive, unauthenticated; not a limit)
8. REALISTIC: `{"status":"ERROR","response":"","error":"RESOURCE_EXHAUSTED (code 429): Individual quota reached. Resets in 1h1m35s.","conversation_id":"c0ffee00-1111-2222-3333-444455556666"}` (json-mode shape is UNVERIFIED; `conversation_id` key is used by the repo adapter)
9. REAL (docs, status values): `{"event":"result","result":{"status":"CANCELED"}}` (must not be a limit)
10. REALISTIC (timeout masquerading as quota, issue 1278): `[agy] print timeout after 300s with turn in progress; returning partial output` with `"total_tokens":0` in the result, exit 0

---------------------------------------------------------------------------------------------------
## 8. Open items to verify with a live run
1. Claude: exact `-p --output-format json` stdout and exit code at a real 5-hour limit (no `resetsAt` in json mode?); the "Usage limits" section of https://code.claude.com/docs/en/errors.md (not read).
2. Codex: does `exec --json` expose `resets_at` epoch (token_count/rate-limit events) beyond the message text.
3. opencode: `run --session`/`--continue` flags; whether the released binary is the capped-retry build; what Anthropic/OpenAI limits look like in `error` JSON.
4. agy: JSON (non-stream) error shape; full exit-code table; whether `Resets in` ever drops units or adds days.
5. Timezone for Claude `resets 3:45pm` without a zone: probably the user's local zone (UNVERIFIED); prefer `resetsAt` from stream-json whenever available.
