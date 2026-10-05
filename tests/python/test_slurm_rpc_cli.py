import io
import json
import os
import stat
import subprocess
import sys

import pytest

from research import runs as R
from research import services as S
from research.backends.slurm import build_script, map_state

from conftest import ROOT


def _fake_slurm(tmp_path, monkeypatch, sacct_state="COMPLETED", squeue_state=""):
    b = tmp_path / "fakebin"
    b.mkdir()
    log = tmp_path / "slurm.log"

    def w(name, body):
        f = b / name
        f.write_text("#!/bin/bash\n" + body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)

    w("sbatch", f'echo "sbatch $@" >> {log}\necho "4242;cluster"\n')
    w("squeue", f'echo "squeue $@" >> {log}\n' + (f'echo "{squeue_state}|node01|2026-01-01T00:00:00"\n' if squeue_state else ""))
    w("sacct", f'echo "sacct $@" >> {log}\necho "4242|{sacct_state}|0:0|2026-01-01T00:00:00|2026-01-01T00:05:00|node01"\n'
               f'echo "4242.batch|{sacct_state}|0:0|||node01"\n')
    w("scancel", f'echo "scancel $@" >> {log}\n')
    monkeypatch.setenv("PATH", str(b) + os.pathsep + os.environ["PATH"])
    return log


def test_map_state():
    assert map_state("COMPLETED") == "completed"
    assert map_state("CANCELLED by 123") == "cancelled"
    assert map_state("TIMEOUT") == "failed"
    assert map_state("PENDING") == "queued"
    assert map_state("WHATEVER") == "unknown"


def test_build_script_contains_resources():
    s = build_script("/p/.research/runs/RUN-0001", "python train.py --a 1", "/p", "RUN-0001",
                     {"partition": "gpu", "time": "02:00:00", "gpus": 1, "mem": "16G", "cpus": 4,
                      "setup": ["module load python"], "extra_args": ["--constraint=a100"]})
    for needle in ("#SBATCH --partition=gpu", "#SBATCH --time=02:00:00", "#SBATCH --gres=gpu:1",
                   "#SBATCH --mem=16G", "#SBATCH --cpus-per-task=4", "#SBATCH --constraint=a100",
                   "module load python", "-m research.runner", "'python train.py --a 1'"):
        assert needle in s, needle


def test_slurm_submit_status_complete(proj, tmp_path, monkeypatch):
    log = _fake_slurm(tmp_path, monkeypatch)
    e = S.create_experiment(proj, "E")
    r = R.submit_run(proj, e["id"], "echo from-job", partition="gpu", time="00:10:00")
    assert r["slurm_job_id"] == "4242" and r["status"] == "queued" and r["backend"] == "slurm"
    assert r["slurm"]["requested"]["partition"] == "gpu"
    script = os.path.join(proj.abspath(r["run_dir"]), "job.sbatch")
    assert "#SBATCH --partition=gpu" in open(script).read()
    # simulate the job running on a compute node
    env = {**os.environ, "SLURM_JOB_ID": "4242", "SLURM_JOB_NODELIST": "node01"}
    subprocess.run(["bash", script], env=env, check=True)
    r = R.reconcile_run(proj, r["id"], force=True)
    assert r["status"] == "completed" and r["exit_code"] == 0
    assert open(proj.abspath(r["stdout_path"])).read().strip() == "from-job"
    assert "sbatch --parsable" in log.read_text()


def test_slurm_job_killed_before_runner_finished(proj, tmp_path, monkeypatch):
    _fake_slurm(tmp_path, monkeypatch, sacct_state="TIMEOUT")
    e = S.create_experiment(proj, "E")
    r = R.submit_run(proj, e["id"], "sleep 100")
    r = R.reconcile_run(proj, r["id"], force=True)
    assert r["status"] == "failed"
    assert (r.get("slurm") or {}).get("state") == "TIMEOUT"


def test_slurm_running_from_squeue(proj, tmp_path, monkeypatch):
    _fake_slurm(tmp_path, monkeypatch, squeue_state="RUNNING")
    e = S.create_experiment(proj, "E")
    r = R.submit_run(proj, e["id"], "sleep 100")
    r = R.reconcile_run(proj, r["id"], force=True)
    assert r["status"] == "running"
    R.cancel_run(proj, r["id"])
    assert R.get_run(proj, r["id"])["status"] == "cancelled"


def test_slurm_missing_gives_clear_error(proj, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    e = S.create_experiment(proj, "E")
    with pytest.raises(Exception) as ei:
        R.submit_run(proj, e["id"], "echo x")
    assert "sbatch" in str(ei.value)
    assert R.list_runs(proj)[0]["status"] == "failed"


# ---------------------------------------------------------------------------- RPC

def test_rpc_roundtrip(proj, repo):
    from research.rpc import serve
    reqs = [
        {"id": 1, "method": "ping"},
        {"id": 2, "method": "create", "params": {"type": "question", "title": "RPC question"}},
        {"id": 3, "method": "create", "params": {"type": "experiment", "title": "RPC exp", "question": "Q-001",
                                                   "parameters": {"alpha": 0.1}}},
        {"id": 4, "method": "run_attach", "params": {"experiment": "EXP-001", "command": "echo", "exit_code": 0}},
        {"id": 5, "method": "tree"},
        {"id": 6, "method": "show", "params": {"id": "EXP-001"}},
        {"id": 7, "method": "resume"},
        {"id": 8, "method": "show", "params": {"id": "EXP-999"}},
        {"id": 9, "method": "nope"},
        {"id": 10, "method": "create", "params": {"type": "variant", "source": "EXP-001", "params": {"alpha": 0.5}}},
        {"id": 11, "method": "checkpoint_draft"},
        {"id": 12, "method": "index"},
    ]
    inp = io.StringIO("\n".join(json.dumps(r) for r in reqs) + "\n")
    out = io.StringIO()
    serve(str(repo), inp, out)
    lines = [json.loads(l) for l in out.getvalue().splitlines()]
    assert lines[0]["event"] == "ready"
    by = {l["id"]: l for l in lines[1:]}
    assert by[1]["result"]["initialized"] is True
    assert by[2]["result"]["id"] == "Q-001" and by[2]["result"]["author_type"] == "human"
    assert by[4]["result"]["status"] == "completed"
    tree = by[5]["result"]
    assert tree["experiments"][0]["runs"][0]["id"] == "RUN-0001"
    assert tree["needs_synthesis"] == ["EXP-001"]
    assert by[6]["result"]["runs"][0]["id"] == "RUN-0001"
    assert by[7]["result"]["project"]["goal"] == "Understand alpha"
    assert by[8]["error"]["code"] == 4
    assert by[9]["error"]["code"] == -32601
    assert by[10]["result"]["param_delta"] == {"alpha": {"from": 0.1, "to": 0.5}}
    assert "finding_ids" in by[11]["result"]
    assert any(i["id"] == "RUN-0001" for i in by[12]["result"]["items"])


def test_rpc_uninitialized(tmp_path):
    from research.rpc import serve
    inp = io.StringIO(json.dumps({"id": 1, "method": "resume"}) + "\n" + json.dumps({"id": 2, "method": "init", "params": {"name": "x"}}) + "\n")
    out = io.StringIO()
    serve(str(tmp_path), inp, out)
    lines = [json.loads(l) for l in out.getvalue().splitlines()]
    assert lines[0]["initialized"] is False
    assert lines[1]["error"]["code"] == 2
    assert lines[2]["result"]["name"] == "x"
    assert (tmp_path / ".research" / "config.yaml").exists()


# ---------------------------------------------------------------------------- CLI (subprocess, as agents use it)

def cli(repo, *args, env=None, check=True):
    e = {**os.environ, **(env or {})}
    p = subprocess.run([sys.executable, "-m", "research_cli", *args], cwd=repo, capture_output=True, text=True, env=e)
    if check and p.returncode != 0:
        raise AssertionError(f"research {' '.join(args)} failed ({p.returncode}):\n{p.stdout}\n{p.stderr}")
    return p


def test_cli_full_flow(repo):
    cli(repo, "init", "--goal", "G")
    cli(repo, "question", "create", "Does alpha affect error?")
    cli(repo, "experiment", "create", "alpha sweep", "-q", "Q-001", "--param", "alpha=[0.1,0.5,1.0]",
        "--metric", "err", "--planned-runs", "3")
    for a in ("0.1", "0.5", "1.0"):
        cli(repo, "run", "exec", "EXP-001", "--param", f"alpha={a}", "--",
            sys.executable, "-c", f"import research; research.log_metric('err', abs({a}-0.5)+0.1)")
    out = json.loads(cli(repo, "--json", "experiment", "show", "EXP-001").stdout)
    assert [r["metrics"]["err"]["value"] for r in out["runs"]] == pytest.approx([0.5, 0.1, 0.6])
    cli(repo, "experiment", "synthesize", "EXP-001", "--happened", "3 runs", "--interpretation", "0.5 best")
    cli(repo, "finding", "create", "alpha=0.5 best", "--supports", "EXP-001", "RUN-0002", "--confidence", "medium")
    cli(repo, "decision", "create", "Use alpha=0.5 as baseline", "--findings", "F-001")
    cli(repo, "experiment", "baseline", "EXP-001")
    cli(repo, "checkpoint")
    res = json.loads(cli(repo, "resume", "--json").stdout)
    assert res["latest_checkpoint"]["id"] == "CP-001" and res["baseline"]["id"] == "EXP-001"
    assert "alpha=0.5 best" in (repo / ".research" / "context" / "current.md").read_text()
    human = cli(repo, "resume").stdout
    assert "alpha=0.5 best" in human


def test_cli_budget_exit_code_for_agents(repo):
    cli(repo, "init")
    cli(repo, "experiment", "create", "E")
    env = {"RESEARCH_AGENT": "codex"}
    cfg = repo / ".research" / "config.yaml"
    cfg.write_text(cfg.read_text().replace("max_runs_without_synthesis: 5", "max_runs_without_synthesis: 2"))
    cli(repo, "run", "exec", "EXP-001", "--", "true", env=env)
    cli(repo, "run", "exec", "EXP-001", "--", "true", env=env)
    p = cli(repo, "run", "exec", "EXP-001", "--", "true", env=env, check=False)
    assert p.returncode == 3 and "synthes" in p.stderr.lower()
    p = cli(repo, "experiment", "create", "E2", env=env, check=False)
    assert p.returncode == 3
    p = cli(repo, "--json", "run", "list")
    runs = json.loads(p.stdout)
    assert len(runs) == 2 and runs[0]["author_name"] == "codex"


def test_cli_not_initialized(tmp_path):
    p = subprocess.run([sys.executable, "-m", "research_cli", "status"], cwd=tmp_path, capture_output=True, text=True)
    assert p.returncode == 2 and "research init" in p.stderr
