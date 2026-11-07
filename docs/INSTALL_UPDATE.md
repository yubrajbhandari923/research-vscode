# Install and update from GitHub

Releases are on GitHub: the VSIX, a wheel, an sdist and `SHA256SUMS`.

- Install: `pip install <research_panel-X.Y.Z-py3-none-any.whl>` (download it from the release).
- Update: `research self-update` (`--check` only reports; `--version X.Y.Z` pins; `--from-file WHEEL` installs offline).
  It downloads the wheel, verifies it against `SHA256SUMS`, then installs with the Python that runs the CLI
  (pip, else `uv pip`; `uv tool` and pipx installs use their own `--force` install). On any failure the existing install is untouched and the exit code is non-zero.
- A major-version upgrade is breaking: `self-update` says so and needs `--major`.
- Optional terminal UI: `pip install 'research-panel[tui]'`, then `research tui`.

## Extension and core compatibility

The extension spawns `research rpc`. `research version --json` and the rpc `ready`/`ping` result report `version` and `protocol` (currently 1).
The extension declares the core it needs as `"3.0"`: the core must have the same major version and a minor version at least that
(`research.compat.core_satisfies(core_version, required)`). A mismatch means update the core (`research self-update`) or the extension.
