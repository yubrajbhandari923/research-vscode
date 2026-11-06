# Starting fresh with Research OS 2

Research OS 2 uses **project format 2**. It is a deliberate breaking release: there is no migration or upgrade command for
v1 projects, and v2 never converts, moves or deletes an existing `.research/` directory on its own.

## What happens if you open a v1 project

The extension and CLI recognise a v1 project (a `.research/config.yaml` with no `.research/format.json`) and refuse to use it.
You see a "not a Research OS 2 project" screen (or the same message from the CLI). **Nothing is changed**, so you can still read
your old records with Research OS 1.x.

## Starting a v2 project in the same folder

```bash
mv .research .research-v1-archive      # keeps every old record, untouched
research init --name "My project" --goal "What you are trying to find out"
```

In VS Code, run **Research: Open Control Center** in the (now empty) folder; the setup wizard does the same in two questions.

`.research-v1-archive/` is plain text and SQLite; it stays in your repository (or add it to `.gitignore`) as a historical record.
v2 does not read it. To carry knowledge forward, re-register what still matters:

| Carry over | How |
|---|---|
| Open questions and hypotheses | `research question create …`, `research experiment create …` |
| Conclusions you still stand behind | `research finding create …` with the evidence you can re-link |
| Decisions | `research decision create …` |

Old runs, metrics and artifacts are not imported; run directories and files you registered remain wherever they are on disk.

## What is the same

The record types and workflow (question → experiment → runs → synthesis → findings → decisions), the `research` command line, the
MCP server and the Scientist Guide work as before. Software version (2.6.0 at the time of writing), project format (2) and storage schema (3) are three
separate numbers; only the format decides whether a project can be opened.

## Recovery still works

For a valid v2 project, `research doctor` reports problems, `research rebuild` reconstructs the index from canonical files, and the
crash-recovery journal runs on open. See the README's Recovery section.
