"""Headless permission denials (PRAVAH PLAN-004, T-009): a Claude agent refused `python scripts/smoke04_runner.py --self-test` stopped before submitting HPC, and the
supervisor called it missing metrics. Now: a permission failure of its own, the default policy stays restrictive, `research agent check` runs declared checks without
Bash, a conflict is reported before launch, and recovery keeps the failed attempt, never duplicates an HPC run and changes no budget or check."""
import json
import subprocess
import sys
import textwrap

import pytest

from research import planspec as PS, recovery as RC, runs as R, supervisor as SV
from research.hpc import remote as HR
from research.providers import claude as CL
from test_supervisor import MOCK, lproj, phases, run_until  # noqa: F401

SMOKE = "PYTHONPATH=src python scripts/smoke04_runner.py --self-test"
DENIAL = json.dumps({"type": "result", "is_error": False, "result": "I could not run the self-test.",
                     "permission_denials": [{"tool_name": "Bash", "tool_input": {"command": SMOKE}}]})
HPC_PY = ("import os; from research.store import Project; from research import runs; p=Project(os.environ['RESEARCH_ROOT']); "
          "e=p.get('task',os.environ['RESEARCH_TASK'])['related_experiment_id']; "
          "r=runs.create_run(p,e,command='job',backend='slurm',status='completed',exit_code=0,slurm_job_id='9001'); "
          "runs._update_run_row(p,r['id'],{'slurm':{'hpc':{'state':'collected','cluster':'c'}}}); "
          "open('result.txt','w').write('ok')")


def plan(profile="claude-fake", prompt_extra=""):
    return textwrap.dedent(f"""\
        schema_version: 1
        plans:
          - key: perm
            title: Perm
            execution: {{max_attempts_per_task: 3, max_hpc_resubmissions: 0}}
            experiments:
              - key: e
                title: E
                tasks:
                  - key: job
                    title: Job
                    agent: {{role: implementer, profile: {profile}}}
                    resources: {{backend: slurm}}
                    checks: ["file:result.txt"]
                    prompt: |
                      Run the self-test with `{SMOKE}` and only then submit.{prompt_extra}
                      MOCK_SH@1: printf '%s\\n' '{DENIAL}'
                      MOCK_SH@2: {sys.executable} -c "{HPC_PY}"
        """)


@pytest.fixture
def pj(lproj, tmp_path):
    script = tmp_path / "mock_agent.py"
    script.write_text(MOCK)
    lproj.config["agents"]["profiles"]["claude-fake"] = {"provider": "claude", "command": sys.executable, "args": [str(script)]}
    lproj.save_config()
    return lproj


def slurm_runs(p):
    return [r["id"] for r in p.q("SELECT id FROM runs WHERE backend='slurm'")]


def entry(p):
    st = PS.load_state(p, "perm")
    rs = SV.load_run(p, st["plan_id"])
    return rs, rs["tasks"][st["tasks"]["job"]], st["tasks"]["job"]


def test_a_denied_command_is_a_permission_failure_not_missing_evidence(pj):
    PS.apply(pj, plan())
    SV.plan_run(pj, "perm", start_daemon=False)
    assert run_until(pj, lambda: phases(pj, "perm")["job"] == "blocked", 60)
    rs, e, tid = entry(pj)
    a = e["attempts"][0]
    assert a["category"] == "permission_denied" and a["permission_denials"] == [SMOKE]
    assert e["recovery"]["cause"] == "permission_denied" and "allow_commands" in " ".join(e["recovery"]["needs_human"]) and "research agent check" in a["verification"]["diagnostics"][0]
    assert "metric" not in a["verification"]["diagnostics"][0] and "evidence_pending" not in e["reason"]
    assert len(e["attempts"]) == 1 and slurm_runs(pj) == []                      # no repair attempt, nothing submitted
    assert a["verification"]["skipped"] and not any(h["event"] == "completed" for h in rs["history"])


def test_recovery_keeps_the_failed_attempt_submits_one_hpc_run_and_changes_nothing_else(pj):
    PS.apply(pj, plan())
    SV.plan_run(pj, "perm", start_daemon=False)
    assert run_until(pj, lambda: phases(pj, "perm")["job"] == "blocked", 60)
    rs, e, tid = entry(pj)
    contract = pj.get("task", tid)["checks"]
    first = dict(e["attempts"][0])
    cfg_before = {k: pj.config["supervisor"].get(k) for k in ("max_hpc_resubmissions", "max_attempts_per_task", "max_total_attempts")}
    pj.config["agents"]["profiles"]["claude-fake"]["allow_commands"] = ["PYTHONPATH=src python scripts/smoke04_runner.py *"]       # the human's explicit decision
    pj.save_config()
    SV.resolve(pj, tid, "unblock", "allowed the self-test command")
    assert run_until(pj, lambda: SV.status(pj, "perm")["state"] == "completed", 90)
    rs, e, tid = entry(pj)
    assert [a["category"] if a["state"] == "failed" else a["state"] for a in e["attempts"]] == ["permission_denied", "verified"]
    assert e["attempts"][0]["permission_denials"] == first["permission_denials"] and e["attempts"][0]["execution"] == first["execution"]      # the original stays as it was
    assert len(slurm_runs(pj)) == 1                                              # exactly one HPC run in total
    assert pj.get("task", tid)["checks"] == contract and rs["budgets"]["attempts"] == 2          # checks unchanged; the retry counted against the normal budget
    assert {k: pj.config["supervisor"].get(k) for k in cfg_before} == cfg_before


def test_the_default_policy_is_restrictive_and_allow_commands_adds_only_what_is_named():
    base = CL.allowed_tools({})
    assert base == CL.DEFAULT_TOOLS and not any(t.startswith("Bash(python") or t == "Bash" or t.startswith("Bash(*") and "research" not in t for t in base)
    more = CL.allowed_tools({"allow_commands": ["python scripts/smoke04_runner.py *"]})
    assert more == base + ["Bash(python scripts/smoke04_runner.py *)"]
    argv = CL.build({"allow_commands": ["pytest -q"]}, None, None, "go", "/w", [], True)
    assert "Bash(pytest -q)" in argv[argv.index("--allowedTools") + 1] and "--dangerously-skip-permissions" not in argv
    assert CL.allowed_tools({"allowed_tools": ["Read"], "allow_commands": ["ls"]}) == ["Read", "Bash(ls)"]


def test_a_conflict_between_the_task_text_and_the_permissions_is_reported_before_launch(pj):
    PS.apply(pj, plan())
    spec = PS.load_state(pj, "perm")["spec_tasks"]["job"]
    assert SV._permission_conflicts(pj, "claude-fake", spec) == [SMOKE]
    assert SV._permission_conflicts(pj, "fake", spec) == []                       # a provider whose allow-list is not known: no opinion
    pj.config["agents"]["profiles"]["claude-fake"]["allow_commands"] = ["PYTHONPATH=src python scripts/smoke04_runner.py *"]
    assert SV._permission_conflicts(pj, "claude-fake", spec) == []
    pj.config["agents"]["profiles"]["claude-fake"].pop("allow_commands")
    SV.plan_run(pj, "perm", start_daemon=False)
    assert run_until(pj, lambda: any(r["permission_notes"] for r in SV.status(pj, "perm")["tasks"]), 30)
    row = SV.status(pj, "perm")["tasks"][0]
    assert row["permission_notes"] == [SMOKE]
    rs, e, tid = entry(pj)
    brief = open(pj.abspath(e["attempts"][0]["prompt_path"])).read()
    assert "Permissions (read first)" in brief and "research agent check" in brief and SMOKE in brief
    assert any(ev["action"] == "supervisor_permission_conflict" for ev in pj.q("SELECT action FROM events WHERE entity_id=?", (tid,)))


def test_agent_check_runs_declared_checks_read_only_without_bash(pj, repo):
    PS.apply(pj, plan())
    tid = PS.load_state(pj, "perm")["tasks"]["job"]
    status_before = pj.get("task", tid)["status"]

    def check():
        return subprocess.run([sys.executable, "-m", "research_cli", "agent", "check", tid, "--root", str(repo), "--json"], capture_output=True, text=True, timeout=120)
    r = check()
    d = json.loads(r.stdout)
    assert r.returncode == 1 and d["passed"] is False and d["results"][0]["check"].startswith("file") and "missing" in d["results"][0]["detail"]
    (repo / "result.txt").write_text("ok")
    r = check()
    assert r.returncode == 0 and json.loads(r.stdout)["passed"] is True
    assert pj.get("task", tid)["status"] == status_before and pj.q("SELECT id FROM reviews WHERE id LIKE ?", (tid + "/%",)) == []     # nothing recorded, nothing moved
