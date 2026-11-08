"""Behaviour baseline for the JSON-RPC server (what the VS Code extension depends on): ~60 requests in ONE `python -m research.rpc`
session over a temp project, results/errors with ids, paths and timestamps normalised.

    python tests/python/gen_rpc_golden.py --record   # rewrite fixtures/rpc_golden.jsonl from the current code
    pytest tests/python/test_rpc_golden.py           # replays and compares (the `ready` event and the `changed` flag are not compared)
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from cli_golden import norm  # noqa: E402

GOLDEN = os.path.join(HERE, "fixtures", "rpc_golden.jsonl")
PLAN_TEXT = ("schema_version: 1\nplans:\n  - key: gold\n    title: Golden plan\n    objective: Check the wire\n    experiments:\n"
             "      - key: e1\n        title: First experiment\n        hypothesis: It works\n        tasks:\n          - key: a\n"
             "            title: First\n            prompt: say hi\n")

STEPS = [
    ("ping", {}),
    ("tree", {}),                                   # not initialized: error
    ("doctor", {}),
    ("hpc_discover", {}),
    ("init_environment", {}),
    ("init", {"name": "Golden", "goal": "Check the wire"}),
    ("ping", {}),
    ("create", {"type": "question", "title": "Does X beat Y?"}),
    ("create", {"type": "experiment", "title": "Baseline", "question": "Q-001", "hypothesis": "X beats Y"}),
    ("create", {"type": "finding", "title": "A result", "statement": "X wins", "supports": ["EXP-001"]}),
    ("create", {"type": "nonsense", "title": "x"}),
    ("update", {"type": "question", "id": "Q-001", "description": "refined"}),
    ("set_status", {"id": "EXP-001", "status": "running"}),
    ("tree", {}),
    ("index", {}),
    ("show", {"id": "EXP-001"}),
    ("show", {"id": "Q-001"}),
    ("config", {}),
    ("resume", {}),
    ("home", {}),
    ("events", {"limit": 5}),
    ("search", {"query": "Baseline"}),
    ("experiment_state", {"id": "EXP-001"}),
    ("check_run_budget", {"experiment": "EXP-001"}),
    ("checkpoint_draft", {}),
    ("run_refresh", {}),
    ("run_attach", {"experiment": "EXP-001", "command": "echo one", "label": "one"}),
    ("run_attach", {"experiment": "EXP-001", "command": "echo two", "label": "two"}),
    ("log_metric", {"name": "acc", "value": 0.5, "run": "RUN-001"}),
    ("show", {"id": "RUN-001"}),
    ("compare_runs", {"a": "RUN-001", "b": "RUN-002"}),
    ("run_cancel", {"id": "RUN-001"}),
    ("plan_yaml_apply", {"text": PLAN_TEXT}),
    ("task_context", {"id": "T-001"}),
    ("task_brief", {"id": "T-001"}),
    ("task_note", {"id": "T-001", "text": "hello"}),
    ("plan_yaml_template", {}),
    ("plan_yaml_validate", {"text": PLAN_TEXT}),
    ("plan_yaml_diff", {"text": PLAN_TEXT}),
    ("create", {"type": "task", "plan_id": "PLAN-001", "title": "Step two"}),
    ("managed_plans", {}),
    ("supervisor_status", {"plan": "gold"}),
    ("plan_yaml_export", {"plan": "gold"}),
    ("promotion_hint", {"plan": "gold"}),
    ("tree", {}),
    ("proposals", {}),
    ("governance", {}),
    ("approval_set_pin", {}),                      # bad params
    ("supervisor_daemon", {"action": "status"}),
    ("provider_status", {}),
    ("providers", {}),
    ("agents", {}),
    ("agent_setup", {}),
    ("strategy_use", {"name": "nope"}),
    ("tools", {}),
    ("settings", {"category": "agents"}),
    ("settings_preview", {"changes": {"context.max_chars": 12000}}),
    ("settings_apply", {"changes": {"context.max_chars": 12000}, "reason": "golden"}),
    ("config_show", {}),
    ("config_show", {"sources": True}),
    ("config_check", {}),
    ("workspace_info", {}),
    ("workspaces", {}),
    ("workspace_archive", {"id": "WS-404"}),
    ("sandbox_preview", {}),
    ("promotions", {}),
    ("interventions", {}),
    ("executions", {}),
    ("hpc_status", {}),
    ("reports", {}),
    ("guide_status", {}),
    ("guide_validate", {}),
    ("agents_md_state", {}),
    ("init_yaml_template", {}),
    ("control", {"page": "home"}),
    ("control", {"page": "nope"}),
    ("telegram_status", {}),
    ("debug_latest", {}),
    ("no_such_method", {}),
    ("show", {"id": "EXP-001", "bogus": 1}),         # bad params
    ("tree", {}),
]


def _norm(s, roots):
    s = re.sub(r"(?<=: )-?\d+(\.\d+)?e-\d+", "0", s)   # tiny float ages ("5e-06") change every run
    s = re.sub(r'"since_checkpoint": \{[^{}]*\}', '"since_checkpoint": {}', s)   # compares timestamps at second granularity: flaky
    return norm(s, roots)


def run_all():
    with tempfile.TemporaryDirectory() as td:
        td = os.path.realpath(td)
        proj, home = os.path.join(td, "proj"), os.path.join(td, "home")
        os.makedirs(proj), os.makedirs(home)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("RESEARCH", "CLAUDE", "CODEX"))}
        env["PATH"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fakes") + os.pathsep + env.get("PATH", "")   # the same agent CLIs everywhere: the fakes, never the machine's own
        env.update(HOME=home, NO_COLOR="1", XDG_CONFIG_HOME=os.path.join(home, ".config"), RESEARCH_HOME=os.path.join(home, "rh"),
                   GIT_CONFIG_GLOBAL=os.devnull, PYTHONDONTWRITEBYTECODE="1", RESEARCH_AUTHOR="golden",
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        subprocess.run(["git", "init", "-q"], cwd=proj, env=env)
        reqs = [{"id": i + 1, "method": m, "params": p} for i, (m, p) in enumerate(STEPS)]
        r = subprocess.run([sys.executable, "-m", "research.rpc", "--root", proj], cwd=proj, env=env, input="".join(json.dumps(x) + "\n" for x in reqs),
                           capture_output=True, text=True, timeout=300)
        lines = [json.loads(l) for l in r.stdout.splitlines() if l.strip()]
        ready, resp = lines[0], {l["id"]: l for l in lines[1:]}
        assert ready.get("event") == "ready", ready
        out = []
        for q in reqs:
            x = dict(resp[q["id"]])
            x.pop("changed", None)
            out.append({"method": q["method"], "params": q["params"], "resp": _norm(json.dumps(x, sort_keys=True), [td])})
        return out


if __name__ == "__main__":
    if "--record" in sys.argv:
        rows = run_all()
        with open(GOLDEN, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, sort_keys=True) + "\n")
        print(f"recorded {len(rows)} requests")
