# 11. Testing, build and release

## Tests

All Python tests run offline: no SSH, no real Slurm, no model, no network. Providers and clusters are replaced by fake binaries and a fake cluster
under `tests/python/fakes/`, and Telegram by a local fake server.

| Suite | Command | What it covers |
|---|---|---|
| Python, fast | `python -m pytest -q` | the default run: parallel (`-n auto`), skips tests marked `slow` or `daemon` |
| Python, full | `python -m pytest -q -m "not daemon"` then `python -m pytest -q -n 0 -m daemon` | what CI runs; daemon tests start a supervisor process and run serially |
| Reference docs | `python -m pytest -q tests/python/test_docs_reference.py tests/python/test_command_examples.py` | generated tables match the code, every relative link in `README.md` and `docs/**/*.md` resolves, every `research ...` example in `README.md`, the `docs/*.md` guides and the packaged templates parses with the real CLI parser |
| Contract tests | `test_ops_contract.py`, `test_config_parity.py`, `test_rpc_golden.py`, `test_cli_golden.py`, `test_py38_syntax.py` | op layer shape, frozen default configuration, RPC and CLI golden output, Python 3.8 syntax compatibility |
| TypeScript | `npm run typecheck` | the extension compiles under `strict` |
| Extension client and webview | `npm run test:client`, `npm run test:webview` | the RPC client and the React Control Center pages (jsdom interaction tests) |
| Real VS Code harness | `RESEARCH_TEST_WORKSPACE=<project> npm run test:ext` | downloads VS Code, installs the VSIX and runs checks in the extension host; needs network access and is not part of CI. Run it with a clean environment (`env -i HOME=$HOME PATH=... npm run test:ext`) when launched from a terminal inside VS Code, otherwise inherited `VSCODE_*` variables hijack the test instance |
| Clean install | `bash scripts/clean_install_test.sh` | fresh venv, wheel install, the whole record protocol, copy the project to a new path, delete the index, confirm the resume context is reconstructed exactly and no build-machine path remains in the project, the installed package or the VSIX |
| Real MCP client | `claude -p "..." --mcp-config .mcp.json --strict-mcp-config --allowedTools mcp__research__research_current` | manual check that an agent client connects to `research mcp` |

`python -m pytest -q --collect-only` gives the current test count; this document does not repeat it.

## Build and package

```bash
pip install -e ".[dev]"        # editable core and CLI, pytest
npm install                    # extension dependencies (root postinstall)
npm run build                  # esbuild -> packages/vscode-extension/dist/extension.js
npm test                       # pytest + typecheck + client and webview tests
npm run package                # scripts/package.sh -> dist/
```

`scripts/package.sh` builds the extension in production mode, runs `vsce package --no-dependencies` (`dist/research-panel-X.Y.Z.vsix`, a small
client with no Python inside), then builds the wheel and sdist (`python -m build`, falling back to `pip wheel` or `uv build`).

The optional extras are `tui` (Textual). The wheel has no required dependencies; PyYAML is vendored and used only when no system PyYAML is importable.

## Versioning

The version is kept in four places and must change together: `pyproject.toml`, the root `package.json`, `packages/vscode-extension/package.json` (and the
two version lines at the top of its `package-lock.json`), and `packages/core/research/__init__.py`. `researchCore` in the extension manifest stays at the oldest
compatible `major.minor` ([contract](../SPEC.md#version-compatibility-contract)). Schema changes add an entry to `schema.MIGRATIONS` and raise `SCHEMA_VERSION`;
existing entries are never edited. The project directory format (`store.PROJECT_FORMAT`) is separate from the index schema.

## Release checklist

1. `python -m pytest -q`, `npm run typecheck`, `npm run test:client`, `npm run test:webview`
2. `npm run package`
3. `bash scripts/clean_install_test.sh`
4. install the VSIX and the wheel locally and look at the result
5. tag, and attach the VSIX, wheel, sdist and `SHA256SUMS` to a GitHub release (`research self-update` verifies the wheel against `SHA256SUMS`)

## Repository layout

```
research-vscode/
├── README.md  CHANGELOG.md  LICENSE  pyproject.toml  package.json
├── docs/                      this specification (SPEC.md and spec/), user guides, screenshots
├── packages/
│   ├── core/research/         the core (see architecture); templates/ holds AGENTS.block.md, skills, packs, catalog, agent files
│   ├── cli/research_cli/      main.py, commands/*.py, output.py, tui.py
│   └── vscode-extension/      package.json, src/ (client, model, trees, panels, commands, render/, webview/), media/, scripts/, test/
├── examples/                  alpha-sweep, auto-promotion-demo, custom-provider
├── tests/python/              pytest suites, fakes/, fixtures/
└── scripts/                   package.sh, clean_install_test.sh
```
