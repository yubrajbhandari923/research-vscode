# Verification record — independent 1.0.0 stabilization

Baseline `main@b715c63`; working-tree changes are described in
[the engineering review](RELEASE_1_0_0_REVIEW.md). Software version remains 0.7.0, schema 3.
All commands below were executed from the repository root unless stated otherwise. Redirection
only saved output; failures were inspected, not suppressed. No coverage percentage is claimed:
coverage tooling was not installed, and test counts are not a coverage measurement.

## Executed tests and builds

| Exact command | Result |
|---|---|
| `npm test` | Failed before collection: system Python 3.14.7 had no pytest. Zero tests executed |
| `PATH="$PWD/.venv/bin:$PATH" npm test` (baseline) | 432 Python passed, 0 failed, 0 skipped; TypeScript passed. Python duration 228.64 s |
| `.venv/bin/python -m pytest tests/python/test_release_safety.py -q` (first pass) | 33 passed, 0 failed |
| `node --test packages/vscode-extension/test/client.test.cjs` | 3 passed, 0 failed, 0 skipped |
| `npm run typecheck` | Passed |
| `PATH="$PWD/.venv/bin:$PATH" npm test` (first expanded pass) | 465 Python passed, 0 failed, 0 skipped; TypeScript and 3 client tests passed. Python duration 223.14 s |
| `.venv/bin/python -m pytest tests/python/test_release_safety.py tests/python/test_agent_features.py tests/python/test_orchestrate.py tests/python/test_strategy.py tests/python/test_plans_tasks.py -q` | 125 passed, 0 failed, 0 skipped; 35.62 s |
| `PATH="$PWD/.venv/bin:$PATH" npm test` (second expanded pass) | 484 Python passed, 0 failed, 0 skipped; TypeScript and 3 client tests passed. Python duration 219.37 s |
| `.venv/bin/python -m pytest tests/python/test_release_safety.py -q` (all new regressions) | 53 passed, 0 failed, 0 skipped; 21.50 s |
| `.venv/bin/python -m pytest --collect-only -q` | 485 collected (432 existing + 53 new) |
| `.venv/bin/python -m pytest tests/python/test_release_safety.py tests/python/test_core.py tests/python/test_slurm_rpc_cli.py -q` | 93 passed, 0 failed, 0 skipped; 48.76 s, after final removal of redundant run JSON writes |
| `PATH="$PWD/.venv/bin:$PATH" npm test` (release verification) | 485 Python passed, 0 failed, 0 skipped; TypeScript and 3 client tests passed. Python duration 235.50 s |
| `npm run build` | Passed; esbuild development bundle and source map |
| `UV_CACHE_DIR=/tmp/research-uv-cache uv lock --offline` | uv panicked inside sandbox while accessing macOS system configuration |
| `uv lock --offline` (approved outside sandbox) | Passed; repaired lock version and removed runtime setuptools packages |
| `PATH="$PWD/.venv/bin:$PATH" npm run package` | First attempt produced VSIX but Python build hit a sandbox cache permission error. Approved reruns passed: VSIX, wheel and sdist generated; final artifacts rebuilt after source changes |
| `PATH="$PWD/.venv/bin:$PATH" bash scripts/clean_install_test.sh` | Passed against rebuilt artifacts; offline fresh-venv installation, two foreground runs, metric/artifact/finding/decision/checkpoint records, copied project with index removed, semantic count assertions, artifact path resolution and path scan |
| `PATH="$PWD/.venv/bin:$PATH" PYTHONPATH="$PWD/packages/core:$PWD/packages/cli" RESEARCH_CONFIG=/tmp/research-empty-config.yaml bash examples/alpha-sweep/run_demo.sh /tmp/research-release-demo-20261012` | Passed; isolated example for extension testing |
| `RESEARCH_TEST_WORKSPACE=/tmp/research-release-demo-20261012 RESEARCH_TEST_VSCODE_EXECUTABLE='/Applications/Visual Studio Code.app/Contents/MacOS/Electron' npm run test:ext` | Installed VSIX in real VS Code: 73/73 passed on three runs, including the final executable source. A subsequent documentation-only repack corrected the README MCP example; no executable source changed |
| `git diff --check` | Passed after removing an extra trailing blank line |

The full suite includes slow acceptance and portable-bundle tests; no `-m 'not slow'` filter was
used. Intermediate full runs preceded later targeted fixes. The last full run and final targeted
run are distinguished rather than presenting every intermediate count as the final source count.

Test outputs were captured under `/tmp/research-baseline-venv.log`, `/tmp/research-full.log`,
`/tmp/research-final-tests.log`, `/tmp/research-release-tests.log`, `/tmp/research-focused.log`,
`/tmp/research-latest-regressions.log`, `/tmp/research-run-final.log`,
`/tmp/research-clean-install-final.log`, `/tmp/research-extension-final.log` and
`/tmp/research-package-final.log`. These are session-local logs, not committed fixtures.

## What the tests actually exercise

- **Storage:** actual temporary projects, SQLite, Markdown/JSONL, hand edits, rebuild and relocation;
  injected replacement/export failure; exact-byte preservation; corrupted complete lines and
  incomplete tails; thread concurrency using independent connections and large JSONL appends.
  These are not crash-kill tests of every multi-file operation.
- **CLI/RPC/MCP:** existing CLI integration and actual MCP subprocess test
  (`test_research_os.py::test_mcp_server_as_subprocess`) initialize and invoke a tool over stdio.
  New malformed-message tests feed the protocol serving loops and verify a later request works.
- **Execution:** actual local foreground/detached runners in existing tests; actual shell/check/hook
  subprocesses, timeout and POSIX descendant cleanup; local portable HPC bundle execution.
- **Strategies/contracts:** configured routing, override precedence, frozen hashes, attempts,
  verification, hook approval and profile preparation. Fake agent output exercises usage parsing.
- **Scientific records:** existing numerical evidence, review, guide, synthesis, checkpoint and
  budget tests. No test result establishes scientific validity of a finding.
- **Extension:** Node tests simulate child processes to isolate restart, UTF-8 and stdin failures.
  Separately, the real VS Code harness installs the actual VSIX into a fresh profile and tests
  a copied/rebuilt example: views, forms/commands, RPC mutations, CLI shim and file watcher.
  It is not equivalent to Playwright visual/accessibility testing.
- **Packaging:** wheel installation is offline with no dependencies and with PYTHONPATH/PYTHONHOME/
  project-root and machine-config overrides cleared. A separate ZIP inspection compared all
  **67 packaged Python files** in each VSIX/wheel with checkout source; all matched.

## Local artifacts

Generated in `dist/`, ignored by Git. These are stabilization artifacts, **not approved 1.0.0 releases**.

| Artifact | SHA-256 |
|---|---|
| `research-panel-0.7.0.vsix` | `5856663382c0d22604b85f31bc21107d5a10c8286a606116fbf426ed47fdce0b` |
| `research_panel-0.7.0-py3-none-any.whl` | `933e6a2cee3da6b6a02d6de0b95b0aecb446fa59507126293ef1236593238404` |
| `research_panel-0.7.0.tar.gz` | `50f4752e24880744daf2047df0ea276f4e2f6616314b90afd7b9177307e11c1e` |

Build instructions are in the README. They reproduce functional artifacts, not necessarily byte-for-byte
identical archives: build timestamps and backend versions are not pinned for reproducible bytes.
Nothing was published, tagged or pushed.

## Environmental and verification gaps

- No live Claude/Codex/Gemini model session or provider billing/usage response was exercised.
  Profiles and model names must be checked in the user's actual provider environment.
- No live SSH/HPC job, site-specific SLURM accounting, NFS/Lustre durability or costly dataset
  download was attempted. Cluster behavior uses fake transport/executables and local bundles.
- code-server was absent and Playwright was not installed. Browser UI, visual regressions,
  accessibility and theme behavior were not independently validated by that harness.
- Python tests ran on 3.10.18/macOS arm64. Python 3.8, other supported versions, Linux and Windows
  were not run locally; CI declares 3.8/3.12 but was not triggered here.
- No full process-kill crash matrix, all-interface concurrent mutation stress suite, or comprehensive
  large-history performance/coverage analysis was completed. A 100-question local probe is recorded
  in the engineering review and must not be represented as a scalability certification.

These limitations and the unresolved P1 engineering findings prevent a READY verdict.

**42 README shell examples** were additionally parsed with the actual `research_cli.main.build_parser`
(after the CLI's checkpoint shorthand and `--` preprocessing). This caught and removed an invalid
`mcp status` example: MCP status exists in core/RPC and the setup UI, not as a public CLI action.
Parser acceptance checks command syntax only; the demo and clean-install checks provide actual
workflow execution evidence.
