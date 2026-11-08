"""Behaviour baseline for the `research` CLI: ~60 invocations in a temp project, stdout/stderr/exit code with ids/timestamps normalised.

    python tests/python/cli_golden.py --record   # rewrite fixtures/cli_golden.jsonl from the current code
    pytest tests/python/test_cli_golden.py       # replays and compares
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
GOLDEN = os.path.join(HERE, "fixtures", "cli_golden.jsonl")

# (cwd, argv). cwd "p" = the project, "e" = an empty dir (not a project)
STEPS = [
    ("e", ["status"]),
    ("e", ["status", "--json"]),
    ("e", ["version", "--json"]),
    ("e", ["init", "--template"]),
    ("p", ["init", ".", "--name", "Golden", "--goal", "Check the CLI", "--json"]),
    ("p", ["status"]),
    ("p", ["status", "--json"]),
    ("p", ["question", "create", "Does X beat Y?", "--json"]),
    ("p", ["question", "list"]),
    ("p", ["q", "list", "--json"]),
    ("p", ["experiment", "create", "Baseline", "--question", "Q-001", "--hypothesis", "X beats Y", "--json"]),
    ("p", ["experiment", "list"]),
    ("p", ["experiment", "show", "EXP-001"]),
    ("p", ["experiment", "show", "EXP-001", "--json"]),
    ("p", ["run", "exec", "EXP-001", "--param", "lr=0.1", "--label", "g1", "--", sys.executable, "-c", "print('hello')"]),
    ("p", ["run", "list", "--json"]),
    ("p", ["run", "show", "RUN-001"]),
    ("p", ["run", "logs", "RUN-001"]),
    ("p", ["metric", "add", "acc", "0.9", "--run", "RUN-001", "--json"]),
    ("p", ["artifact", "add", "out.txt", "--run", "RUN-001", "--json"]),
    ("p", ["artifact", "list"]),
    ("p", ["finding", "create", "X wins", "--kind", "result", "--supports", "RUN-001", "--json"]),
    ("p", ["finding", "list"]),
    ("p", ["finding", "list", "--json"]),
    ("p", ["decision", "create", "Use X", "--json"]),
    ("p", ["decision", "list"]),
    ("p", ["checkpoint", "create", "--title", "cp one", "--json"]),
    ("p", ["checkpoint", "list"]),
    ("p", ["note", "new", "scratch", "--json"]),
    ("p", ["note", "list"]),
    ("p", ["skills", "list"]),
    ("p", ["plan", "create", "Plan A", "--json"]),
    ("p", ["plan", "list"]),
    ("p", ["task", "create", "PLAN-001", "Do thing", "--json"]),
    ("p", ["task", "list", "--json"]),
    ("p", ["task", "show", "T-001"]),
    ("p", ["plan", "yaml", "template"]),
    ("p", ["plan", "yaml", "export", "PLAN-001"]),
    ("p", ["search", "baseline"]),
    ("p", ["search", "baseline", "--json"]),
    ("p", ["show", "EXP-001"]),
    ("p", ["show", "F-001", "--json"]),
    ("p", ["mark", "Q-001", "answered", "--json"]),
    ("p", ["sync", "--json"]),
    ("p", ["resume"]),
    ("p", ["resume", "--json"]),
    ("p", ["current"]),
    ("p", ["context"]),
    ("p", ["context", "--print"]),
    ("p", ["config", "show", "--json"]),
    ("p", ["config", "check"]),
    ("p", ["config", "explain", "agent_policy.max_runs_without_synthesis"]),
    ("p", ["log", "--limit", "5", "--json"]),
    ("p", ["agents"]),
    ("p", ["agents", "--json"]),
    ("p", ["provider", "list"]),
    ("p", ["execution", "list", "--json"]),
    ("p", ["exec", "list"]),
    ("p", ["agents-md", "--preview"]),
    ("p", ["strategy", "list"]),
    ("p", ["usage", "--json"]),
    ("p", ["hook", "list"]),
    ("p", ["source", "list", "--json"]),
    ("p", ["evidence", "status"]),
    ("p", ["workspace", "info", "--json"]),
    ("p", ["sandbox", "list"]),
    ("p", ["promote", "list", "--json"]),
    ("p", ["intervention", "list", "--json"]),
    ("p", ["proposal", "list", "--json"]),
    ("p", ["approval", "status", "--json"]),
    ("p", ["supervisor", "status", "--json"]),
    ("p", ["telegram", "status", "--json"]),
    ("p", ["env", "check", "--json"]),
    ("p", ["dispatch", "implementer", "T-001", "--print"]),
    ("p", ["ledger", "status", "--json"]),
    ("p", ["setup", "agents", "--provider", "claude", "--json"]),
    ("p", ["report", "--full", "--stdout"]),
    ("p", ["doctor", "--json"]),
    ("p", ["rebuild", "--json"]),
    # error paths
    ("p", ["show", "EXP-999"]),
    ("p", ["show", "EXP-999", "--json"]),
    ("p", ["mark", "Q-001", "bogus"]),
    ("p", ["task", "show", "T-999", "--json"]),
    ("p", ["run", "exec", "EXP-999", "--", "true"]),
    ("p", ["experiment", "frobnicate"]),
    ("p", ["strategy", "show", "nope"]),
    ("p", ["plan", "yaml", "validate", "/nonexistent.yaml"]),
    ("p", ["--bogus-flag"]),
    ("p", ["checkpoint"]),
]

TS = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
HEX = re.compile(r"\b[0-9a-f]{12,64}\b")
DUR = re.compile(r"\b\d+(\.\d+)?\s?(ms|s)\b")


def norm(s: str, roots) -> str:
    for r in roots:
        s = s.replace(r, "<ROOT>")
        s = s.replace(os.path.realpath(r), "<ROOT>")
    s = re.sub(r"\"(pid|duration_s|elapsed|last_activity_age_days|[a-z_]*age_days|[a-z_]*_s)\": [-0-9.e]+", r'"\1": 0', s)
    s = re.sub(r"PATH=\S+", "PATH=<PATH>", s)
    s = re.sub(r"\"events\": \d+", '"events": N', s)
    s = re.sub(r"since then: \d+ changes", "since then: N changes", s)
    s = re.sub(r"\b\d{8}-\d{6}\b", "<STAMP>", s)
    s = re.sub(r"\"modified\": [0-9.]+|- \*\*pid:\*\* \d+", "<VOLATILE>", s)
    s = TS.sub("<TS>", s)
    s = DATE.sub("<DATE>", s)
    s = HEX.sub("<HEX>", s)
    s = s.replace(sys.executable, "<PYEXE>")
    s = re.sub(r"(?<![\w.])\d+(\.\d+)?s\b", "<DUR>", s)   # whole-second durations too: a loaded machine turns 0s into 1s
    s = re.sub(r"\((\d+) B\)", "(<N> B)", s)               # file sizes in run-directory listings vary by a byte or two between runs
    for r in {REPO_ROOT, os.path.realpath(REPO_ROOT)}:   # the checkout's real location, whatever it is called or wherever it lives (dev box, CI, a worktree)
        s = s.replace(r, "<REPO>")
    s = re.sub(r"[A-Za-z0-9_.-]*research-vscode[^\s\"']*", "<REPO>", s)
    host = __import__("socket").gethostname().split(".")[0]      # the domain suffix depends on the network the machine is on
    s = re.sub(re.escape(host) + r"(\.[A-Za-z0-9-]+)*", "<HOST>", s)
    return s


def run_all():
    out = []
    with tempfile.TemporaryDirectory() as td:
        td = os.path.realpath(td)
        proj, empty, home = (os.path.join(td, n) for n in ("proj", "empty", "home"))
        for d in (proj, empty, home):
            os.makedirs(d)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("RESEARCH", "CLAUDE", "CODEX"))}
        env["PATH"] = os.path.join(HERE, "fakes") + os.pathsep + env.get("PATH", "")   # the same agent CLIs everywhere: the fakes, never the machine's own
        env.update(HOME=home, NO_COLOR="1", XDG_CONFIG_HOME=os.path.join(home, ".config"), RESEARCH_HOME=os.path.join(home, "rh"),
                   GIT_CONFIG_GLOBAL=os.devnull, PYTHONDONTWRITEBYTECODE="1", COLUMNS="100", RESEARCH_AUTHOR="golden")
        subprocess.run(["git", "init", "-q"], cwd=proj, env=env)
        open(os.path.join(proj, "out.txt"), "w").write("x")
        for cwd, argv in STEPS:
            r = subprocess.run([sys.executable, "-m", "research_cli", *argv], cwd=proj if cwd == "p" else empty, env=env,
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
            out.append({"argv": [norm(x, [td]) for x in argv], "cwd": cwd, "rc": r.returncode,
                        "stdout": norm(r.stdout, [td]), "stderr": norm(r.stderr, [td])})
    return out


if __name__ == "__main__":
    if "--record" in sys.argv:
        rows = run_all()
        with open(GOLDEN, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, sort_keys=True) + "\n")
        print(f"recorded {len(rows)} invocations")
