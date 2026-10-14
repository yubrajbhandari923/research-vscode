# 1.0.0 acceptance gates

These scenarios are requirements, not claims of passing tests. Use disposable
projects, isolated machine configuration and simulated providers unless explicitly
identified otherwise. Keep version 0.7.0 until the mandatory gates pass.

1. Create a scientific objective, plan, checked task, experiment and local run;
   verify implementation, synthesize observations, independently review a finding,
   record a decision and resume. Engineering checks never confer scientific support.
2. Configure two projects with distinct strategies, profiles and hooks. CLI,
   RPC (including the VS Code request), MCP and orchestrated dispatch must expose
   identical effective task obligations and resolve the same default allocation.
   Explicit overrides must show their source and reason.
3. Declare constraints, permitted files, outputs and frozen tests. All context and
   resume paths preserve these fields. Contradictory legacy fields remain visible
   and block dispatch/completion until reconciled. Required unapproved hooks block
   every completion path, including create-as-done and status updates.
4. Race real processes against task launch and ledger mutation. Exactly one
   process executes a task; no attempt or pin update is lost. A crashed launcher
   cannot permit a surviving worker to race a replacement.
5. Kill writers between canonical, audit, ledger and index writes and after the
   index commit. Reopening repeatedly recovers one coherent outcome. Inject a
   recovery failure; preserve recovery evidence and retry safely.
6. Show project-wide blockers with multiple active plans, regardless of selected
   focus. Distinguish routine next work from human approval or scientific judgment.
7. Preparation, terminal submission and observed execution are separate states.
   No verifier means unverified, skipped required checks mean blocked, and a failed
   verification produces a nonzero CLI exit and structured negative RPC/MCP result.
8. Render measurements neutrally without declared direction. Display numerical
   coverage separately from independent scientific review, including no bindings.
9. Validate shipped command examples with the parser and representative real
   workflows. Generated instructions accurately identify MCP gaps and CLI fallback.
10. Run complete Python/TypeScript/client suites, real extension-host tests,
    packaging and isolated installation; record exact commands, results, artifact
    hashes and untested platforms. Simulated providers are not live model tests.

Coordinator continuation is conditional on ownership and policy gates. If deferred,
document the smallest safe session-scoped design and what remains manual.
