#!/usr/bin/env bash
# Release checklist: clean install of the built artifacts in a fresh directory + venv, full protocol,
# reopen from a moved copy, and a scan for leaked build-machine paths.
#   bash scripts/clean_install_test.sh            (after: npm run package)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
echo "== clean dir: $T"
"$PY" -m venv "$T/venv"
"$T/venv/bin/pip" install -q "$ROOT"/dist/research_panel-*.whl
export PATH="$T/venv/bin:$PATH" RESEARCH_AUTHOR_TYPE=human
unset CLAUDECODE RESEARCH_AGENT || true
command -v research
mkdir -p "$T/proj" && cd "$T/proj" && git init -q
echo 'import sys, research; a=float(sys.argv[1]); open(f"out_{a}.txt","w").write(str(a*a)); research.log_metric("loss", a*a)' > train.py
git add . && git -c user.name=t -c user.email=t@t commit -qm init
research init --goal "Clean install test" >/dev/null
research question create "Does it work?" >/dev/null
research experiment create "smoke" -q Q-001 --hypothesis "it works" --param a=[1,2] >/dev/null
research run exec EXP-001 --param a=1 -- python train.py 1 >/dev/null 2>&1
research run exec EXP-001 --param a=2 -- python train.py 2 >/dev/null 2>&1
research artifact add out_2.0.txt --run RUN-0002 -d "output" >/dev/null
research experiment synthesize EXP-001 --happened "2 runs" --interpretation "works" >/dev/null
research finding create "It works" --supports EXP-001 RUN-0002 A-0001 --status supported >/dev/null
research decision create "Ship it" --findings F-001 >/dev/null
research checkpoint --problem "none" --next "release" >/dev/null
echo "== reopen from a moved copy (index deleted → rebuilt from text files)"
cp -r "$T/proj" "$T/moved" && rm "$T/moved/.research/research.db" && cd "$T/moved"
J="$(research --json resume)"
"$PY" - "$J" <<'PY'
import json, sys
r = json.loads(sys.argv[1])
c = r["counts"]
assert c == {"questions": 1, "experiments": 1, "runs": 2, "findings": 1, "decisions": 1, "checkpoints": 1, "artifacts": 1}, c
assert r["latest_checkpoint"]["next_experiment"] == "release"
assert r["established_findings"][0]["title"] == "It works"
print("resume ok:", c)
PY
A="$(research --json artifact list)"
"$PY" -c "import json,sys; a=json.loads(sys.argv[1])[0]; assert a['path']=='out_2.0.txt' and a['exists'], a; print('artifact path relative + resolves:', a['path'])" "$A"
echo "== scan for leaked build-machine paths"
if grep -rIl -e "/home/claude" -e "/tmp/claude-0" "$T/moved/.research" "$T/venv/lib"/python*/site-packages/research "$T/venv/lib"/python*/site-packages/research_cli ; then
  echo "LEAK FOUND"; exit 1; fi
( cd "$T" && unzip -q -o "$ROOT"/dist/research-panel-*.vsix -d vsix && ! grep -rIl -e "/home/claude" -e "/tmp/claude-0" vsix ) && echo "no leaked paths in project, wheel or VSIX"
echo "ALL CLEAN-INSTALL CHECKS PASSED"
