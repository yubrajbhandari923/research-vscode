"""Deterministic checks the starter hooks run: `python -m research.checks NAME` (exit 0 pass, 1 fail, output explains).

They are ordinary programs over the working tree and the records: no network, no model, and they never execute the project's own code
except `tests`, which is therefore an optional hook a human must approve. A check that cannot run here (no linter installed, no
experiment yet) prints `SKIPPED: why` and exits 0: the hook result says what happened, but a skipped check is not evidence.

    git-integrity       object database is consistent, no unresolved merge conflict, no half-finished rebase/merge
    secrets             no private keys or well-known token formats in changed or new files (the secret is never printed)
    lint / format       ruff (or pyflakes) on the changed Python files
    tests / tsc         pytest, TypeScript compiler (optional: they run project code)
    task-scope          files changed by a task stay inside its contract (RESEARCH_TASK)
    frozen-tests        a task's frozen acceptance tests are byte-identical (RESEARCH_TASK)
    experiment-config   runs record commit, clean tree, command and (when required) a seed (RESEARCH_EXPERIMENT optional)
    numeric-invariants  no NaN/Inf metric values; optional bounds in harness.invariants
    artifact-integrity  registered artifacts exist and still match their recorded hash
"""
from __future__ import annotations

import fnmatch
import importlib.util
import math
import os
import re
import shutil
import subprocess
import sys
from typing import Callable, Dict, List, Optional, Tuple

Result = Tuple[bool, List[str]]
SKIP = "SKIPPED: "


def _git(root: str, *args: str, timeout: int = 60) -> Tuple[int, str]:
    try:
        r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)


def _changed(root: str, suffixes: Tuple[str, ...] = ()) -> List[str]:
    """Modified, staged and untracked files (not ignored), relative to the root, excluding .research."""
    rc, out = _git(root, "status", "--porcelain", "-uall", "--", ".", ":(exclude).research")
    files = sorted({ln[3:].split(" -> ")[-1].strip().strip('"') for ln in out.splitlines() if len(ln) > 3 and ln[:2] != " D" and ln[:2] != "D "}) if rc == 0 else []
    return [f for f in files if os.path.isfile(os.path.join(root, f)) and (not suffixes or f.endswith(suffixes))]


def _project(root: str):
    from .store import Project
    return Project(root, sync=False)


# ----------------------------------------------------------------------------- git

def git_integrity(root: str) -> Result:
    rc, _ = _git(root, "rev-parse", "--git-dir")
    if rc:
        return True, [SKIP + "not a git repository"]
    problems: List[str] = []
    rc, out = _git(root, "diff", "--name-only", "--diff-filter=U")
    if out.strip():
        problems.append("unresolved merge conflicts in: " + ", ".join(out.split()[:8]))
    rc, gd = _git(root, "rev-parse", "--git-dir")
    gdir = gd.strip() if os.path.isabs(gd.strip()) else os.path.join(root, gd.strip())
    for marker, what in (("MERGE_HEAD", "a merge"), ("rebase-merge", "a rebase"), ("rebase-apply", "a rebase/am"), ("CHERRY_PICK_HEAD", "a cherry-pick")):
        if os.path.exists(os.path.join(gdir, marker)):
            problems.append(f"{what} is in progress: finish or abort it before continuing")
    rc, out = _git(root, "fsck", "--connectivity-only", "--no-dangling", "--no-progress", timeout=120)
    if rc:
        problems.append("git fsck found a damaged object database: " + " ".join(out.split("\n")[:2]))
    return not problems, problems or ["git repository is consistent"]


# ----------------------------------------------------------------------------- secrets

_SECRETS = [
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("API key (sk- style)", re.compile(r"\bsk-(?:ant-|proj-|live-)?[A-Za-z0-9_-]{32,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("hard-coded credential", re.compile(r"""(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)\b["']?\s*[:=]\s*["'][^"'\s$<{][^"'\s]{11,}["']""")),
]
_ALLOW = "research:allow-secret"


def secrets(root: str) -> Result:
    hits: List[str] = []
    n = 0
    for f in _changed(root):
        path = os.path.join(root, f)
        try:
            if os.path.getsize(path) > 1_000_000:
                continue
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        if b"\0" in data[:4096]:
            continue
        n += 1
        for i, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            if _ALLOW in line:
                continue
            for label, rx in _SECRETS:
                if rx.search(line):
                    hits.append(f"{f}:{i}: looks like a {label} (value not shown; add `# {_ALLOW}` on that line if it is a placeholder)")
                    break
    return not hits, hits or [f"no secrets found in {n} changed file(s)"]


# ----------------------------------------------------------------------------- code quality

def _run(argv: List[str], root: str, timeout: int = 600) -> Tuple[int, str]:
    try:
        r = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except OSError as e:
        return 127, str(e)


def _module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def lint(root: str) -> Result:
    files = _changed(root, (".py",))
    if not files:
        return True, ["no changed Python files"]
    if shutil.which("ruff"):
        rc, out = _run(["ruff", "check", "--no-fix", "--output-format", "concise", *files], root)
        return rc == 0, [out[-1500:] or "ruff: clean"]
    if _module("pyflakes"):
        rc, out = _run([sys.executable, "-m", "pyflakes", *files], root)
        return rc == 0, [out[-1500:] or "pyflakes: clean"]
    return True, [SKIP + "neither ruff nor pyflakes is installed (`uv pip install ruff`)"]


def format_check(root: str) -> Result:
    files = _changed(root, (".py",))
    if not files:
        return True, ["no changed Python files"]
    if shutil.which("ruff"):
        rc, out = _run(["ruff", "format", "--check", *files], root)
        return rc == 0, [out[-1500:] or "ruff format: clean"]
    if shutil.which("black"):
        rc, out = _run(["black", "--check", "-q", *files], root)
        return rc == 0, [out[-1500:] or "black: clean"]
    return True, [SKIP + "no formatter (ruff or black) is installed"]


def tests(root: str) -> Result:
    if not (os.path.isdir(os.path.join(root, "tests")) or _changed(root, ("_test.py",)) or any(f.startswith("test_") for f in os.listdir(root))):
        return True, [SKIP + "no tests found"]
    if not _module("pytest"):
        return True, [SKIP + "pytest is not installed in this interpreter"]
    rc, out = _run([sys.executable, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"], root, 900)
    return rc in (0, 5), [out[-2500:]]   # 5: no tests collected


def tsc(root: str) -> Result:
    if not os.path.exists(os.path.join(root, "tsconfig.json")):
        return True, [SKIP + "no tsconfig.json"]
    local = os.path.join(root, "node_modules", ".bin", "tsc")
    if not os.path.exists(local):
        return True, [SKIP + "TypeScript is not installed (`npm install`); nothing is downloaded by a hook"]
    rc, out = _run([local, "--noEmit", "--pretty", "false"], root, 900)
    return rc == 0, [out[-2500:] or "tsc: no type errors"]


# ----------------------------------------------------------------------------- task discipline

def task_scope(root: str) -> Result:
    tid = os.environ.get("RESEARCH_TASK")
    if not tid:
        return True, [SKIP + "no task (set by dispatch and verification)"]
    from . import orchestrate as O
    p = _project(root)
    try:
        extra = O.scope_violations(p, tid)
        if not (O.ledger(p, tid).get("contract") or {}).get("allowed_files"):
            return True, [SKIP + f"{tid} has no contract with allowed_files"]
        return not extra, [f"{tid} changed files outside its contract: " + ", ".join(extra)] if extra else [f"{tid} stayed inside its contract"]
    finally:
        p.close()


def frozen_tests(root: str) -> Result:
    tid = os.environ.get("RESEARCH_TASK")
    if not tid:
        return True, [SKIP + "no task"]
    from . import orchestrate as O
    p = _project(root)
    try:
        if not O.ledger(p, tid).get("frozen"):
            return True, [SKIP + f"{tid} has no frozen tests"]
        bad = O.tampered(p, tid)
        return not bad, [f"frozen acceptance tests changed: {', '.join(bad)}"] if bad else ["frozen acceptance tests are intact"]
    finally:
        p.close()


# ----------------------------------------------------------------------------- experiments and records

def _runs(p, limit: int = 30):
    exp = os.environ.get("RESEARCH_EXPERIMENT")
    if exp:
        return p.q("SELECT * FROM runs WHERE experiment_id=? ORDER BY id DESC LIMIT ?", (exp, limit))
    return p.q("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))


_SEED = re.compile(r"seed|random_state|rng", re.I)


def experiment_config(root: str) -> Result:
    p = _project(root)
    try:
        runs = _runs(p)
        if not runs:
            return True, [SKIP + "no runs yet"]
        import json
        need_seed = bool((p.config.get("harness") or {}).get("require_seed"))
        bad: List[str] = []
        warn: List[str] = []
        for r in runs:
            if r["status"] in ("created", "cancelled"):
                continue
            who = r["id"]
            if not r["git_commit"]:
                bad.append(f"{who}: no code revision recorded")
            elif r["git_dirty"]:
                bad.append(f"{who}: ran from a dirty working tree (commit first, or the result cannot be reproduced)")
            if not (r["command"] or "").strip():
                bad.append(f"{who}: no command recorded")
            try:
                params = json.loads(r["parameters"] or "{}") or {}
            except ValueError:
                params = {}
            if not any(_SEED.search(str(k)) for k in params):
                (bad if need_seed else warn).append(f"{who}: no seed among the recorded parameters (`--param seed=…`; harness.require_seed makes this an error)")
        out = bad + warn[:3] + ([f"…{len(warn) - 3} more without a seed"] if len(warn) > 3 else [])
        return not bad, out or [f"{len(runs)} run(s) record commit, clean tree and command"]
    finally:
        p.close()


def numeric_invariants(root: str) -> Result:
    p = _project(root)
    try:
        exp = os.environ.get("RESEARCH_EXPERIMENT")
        rows = p.q("SELECT run_id, name, value, value_text FROM metrics" + (" WHERE experiment_id=?" if exp else "") + " ORDER BY id DESC LIMIT 5000", (exp,) if exp else ())
        if not rows:
            return True, [SKIP + "no metrics recorded"]
        bounds = (p.config.get("harness") or {}).get("invariants") or {}
        bad: List[str] = []
        for r in rows:
            v, t = r["value"], (r["value_text"] or "").strip().lower()
            if v is None and t in ("nan", "inf", "-inf", "infinity", "-infinity"):
                bad.append(f"{r['run_id']} {r['name']} = {t}")
                continue
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                bad.append(f"{r['run_id']} {r['name']} = {v}")
                continue
            b = bounds.get(r["name"]) if isinstance(bounds, dict) else None
            if v is not None and isinstance(b, dict):
                if b.get("min") is not None and v < float(b["min"]):
                    bad.append(f"{r['run_id']} {r['name']} = {v:g} is below its minimum {b['min']}")
                if b.get("max") is not None and v > float(b["max"]):
                    bad.append(f"{r['run_id']} {r['name']} = {v:g} is above its maximum {b['max']}")
        return not bad, bad[:12] or [f"{len(rows)} metric value(s) are finite and within the declared bounds"]
    finally:
        p.close()


def artifact_integrity(root: str) -> Result:
    p = _project(root)
    try:
        from . import evidence
        exp = os.environ.get("RESEARCH_EXPERIMENT")
        arts = p.q("SELECT id FROM artifacts" + (" WHERE experiment_id=?" if exp else "") + " ORDER BY id DESC LIMIT 500", (exp,) if exp else ())
        if not arts:
            return True, [SKIP + "no artifacts registered"]
        bad = []
        for a in arts:
            r = evidence.artifact_integrity(p, a["id"])
            if r["status"] in ("modified", "missing"):
                bad.append(f"{a['id']} {r['path']}: {r['status']} ({r['detail']})")
        return not bad, bad[:12] or [f"{len(arts)} artifact(s) exist and match their recorded hashes"]
    finally:
        p.close()


CHECKS: Dict[str, Callable[[str], Result]] = {
    "git-integrity": git_integrity, "secrets": secrets, "lint": lint, "format": format_check, "tests": tests, "tsc": tsc,
    "task-scope": task_scope, "frozen-tests": frozen_tests, "experiment-config": experiment_config,
    "numeric-invariants": numeric_invariants, "artifact-integrity": artifact_integrity,
}


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in CHECKS:
        print("usage: python -m research.checks " + "|".join(CHECKS), file=sys.stderr)
        return 2
    root = os.environ.get("RESEARCH_ROOT") or os.getcwd()
    try:
        ok, lines = CHECKS[argv[0]](root)
    except Exception as e:  # noqa: BLE001  a crashed check is a failed check, never a pass
        print(f"{argv[0]} could not run: {e}")
        return 1
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
