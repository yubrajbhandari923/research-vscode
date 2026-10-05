#!/usr/bin/env bash
# Builds all distributables into dist/: the VSIX (with bundled Python core), a wheel and an sdist.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p dist
rm -f dist/*.vsix dist/*.whl dist/*.tar.gz
echo "==> VS Code extension"
( cd packages/vscode-extension && [ -d node_modules ] || npm install --no-audit --no-fund )
( cd packages/vscode-extension && npm run package )
echo "==> Python package"
PY="${PYTHON:-python3}"
if "$PY" -c "import build" 2>/dev/null; then
  "$PY" -m build --outdir dist .
else
  "$PY" -m pip wheel . --no-deps --no-build-isolation -w dist -q || "$PY" -m pip wheel . --no-deps -w dist -q
  echo "   (install 'build' for an sdist: $PY -m pip install build)"
fi
rm -rf build packages/core/*.egg-info packages/cli/*.egg-info *.egg-info
echo "==> dist/"
ls -lh dist
