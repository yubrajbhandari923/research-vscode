# Skills, hooks and MCP: what they are for

Research OS keeps the records. These three things shape what an **agent** does with them. All three are set up for you by
**Initialize Project** and visible in **Agents → Skills, hooks and MCP**.

| | What it is | Why it matters | Loaded by default? |
|---|---|---|---|
| **Skills** | short written procedures, one `SKILL.md` each (`run-experiment`, `engineering-loop`, `scientific-briefing`, `hpc-setup`, …) | an agent follows your project's way of working instead of improvising; a *required* skill is injected into its brief and hashed | yes: shipped into `.research/skills/` and linked into `main/.claude/skills` and `main/.agents/skills` so Claude Code and Codex find them (machine-local links, git-ignored) |
| **Hooks** | commands **you approve**, run by Research OS at fixed points: before a task is dispatched, after an agent's change, before a run, after runs finish | deterministic checks (tests, lint, a physics oracle) replace tokens an agent would spend re-checking; an unapproved hook is *skipped*, never *passed* | two starter hooks, approved by the wizard: a Python syntax check and `git diff --check` after each change (they never block) |
| **MCP server** | the research records as tools an agent calls directly (`research_current`, `task_context`, `finding_review`, `guide_status`, `report`, …) | the agent reads the real state and writes records through the same rules as the CLI, so nothing depends on its memory | yes: registered for each enabled agent (`main/.mcp.json`, `.agents/mcp_config.json`, or `codex mcp add`) |

## Using them well

**Skills**
- `research skills list` shows them; `research skills add <folder|git url>` imports more (an imported `SKILL.md` is never modified).
- `research skills set NAME --level required` makes a skill binding: managed dispatches fail if it is missing, its text is injected and hashed.
  `--level protocol --check 'research …'` also requires those commands to exit 0 (`research skill verify NAME`).
- `--always` lists a skill in every agent's context; `--applies-to` suggests it for task types, roles or tags.
- A *direct* Claude Code or Codex session is not gated: it only sees linked skills and `AGENTS.md`. `research skills audit` lists exactly what it can discover; Agents → *Link skills now* repairs the links (for example after cloning on a new machine).

**Hooks**
- `research hook add NAME --stage post_implementation --command "pytest tests/oracle -q" [--advisory]`, then **approve** it (Agents page, or `research hook approve`). Approval is a hash of stage, command and timeout, so any edit revokes it, and agents cannot approve.
- A hook is binding unless you add `--advisory` (the starter hooks are advisory: they report, they never block). Every execution is appended to `.research/hooks/results.jsonl`.
- Good first hooks: your unit tests, a physical-consistency oracle, a formatter check. Keep them fast; each has a timeout.

**MCP**
- Agents → each provider shows *MCP registered*; **Register** and **Test** repair or prove it (it starts the server from the directory the agent runs in and lists its tools).
- Everything the MCP exposes is also in the CLI; MCP just spares the agent from guessing command syntax. It covers the core loop; orchestration, hooks and HPC need the CLI.

## What a new project gets

Skills linked, starter hooks approved, MCP registered, `AGENTS.md`/`CLAUDE.md` generated, and the Scientist Guide created (`docs/GUIDE.md`). Skip the skill linking or the starter hooks in the wizard spec with `tools: {link_skills: false, starter_hooks: false}`.
