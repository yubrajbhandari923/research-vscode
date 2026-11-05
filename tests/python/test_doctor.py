"""Project Doctor: read-only, actionable, and quiet about capabilities the user never enabled."""
import json
import os
import subprocess
import sys

import pytest

from research import doctor as DR, runs, services, workspace as W
from research.store import Project
from research.util import ResearchError

from conftest import git
from test_format import v1_project


def by_id(r):
    return {c["id"]: c for c in r["checks"]}


def test_a_fresh_project_is_healthy_and_optional_features_are_info_not_problems(proj):
    r = DR.run(proj.root)
    c = by_id(r)
    assert r["healthy"] and r["summary"]["errors"] == 0 and r["summary"]["warnings"] == 0
    assert c["format"]["level"] == "ok" and c["records.rebuild"]["level"] == "ok"
    assert c["ledger"]["level"] == "info" and c["sandbox"]["level"] == "info" and c["mcp"]["level"] == "info"
    assert "hpc" not in c, "HPC is not enabled, so it is not mentioned"


def test_v1_project_stops_the_doctor_at_a_clear_error_and_changes_nothing(repo):
    v1_project(str(repo))
    before = open(repo / ".research" / "config.yaml").read()
    r = DR.run(str(repo))
    assert not r["healthy"] and [c["id"] for c in r["checks"]] == ["format"]
    assert ".research-v1-archive" in r["checks"][0]["fix"]["text"]
    assert open(repo / ".research" / "config.yaml").read() == before and not (repo / ".research" / "research.db").exists()


def test_a_malformed_record_is_an_error_and_the_original_file_is_not_touched(proj):
    q = services.create_question(proj, "fine")
    path = proj.mirror_path("question", q["id"])
    proj.close()
    open(path, "w").write("---\nid: [unterminated\n---\nbody\n")
    before = open(path).read()
    r = DR.run(proj.root)
    assert by_id(r)["records.rebuild"]["level"] in ("error", "warn")
    assert open(path).read() == before


def test_missing_artifacts_and_interrupted_runs_are_reported_with_next_steps(proj, tmp_path):
    from research import runs as R
    e = services.create_experiment(proj, "E", hypothesis="h")
    r1 = R.create_run(proj, e["id"], command="x", backend="local", status="running")
    proj.conn.execute("UPDATE runs SET pid=?, hostname=? WHERE id=?", (2_000_000_000, __import__("socket").gethostname(), r1["id"]))
    f = tmp_path / "out.txt"
    f.write_text("x")
    R.register_artifact(proj, str(f), run=r1["id"])
    f.unlink()
    c = by_id(DR.run(proj.root))
    assert c["runs.stale"]["level"] == "warn" and r1["id"] in c["runs.stale"]["detail"] and c["runs.stale"]["fix"]["command"] == "research sync"
    assert c["artifacts.missing"]["level"] == "warn" and "records are kept" in c["artifacts.missing"]["detail"]


def test_stale_generated_context_is_reported_and_fixed_only_on_request(proj):
    from research import registry
    proj.close()
    os.remove(proj.path("context", "policy.md")) if os.path.exists(proj.path("context", "policy.md")) else None
    c = by_id(DR.run(proj.root))
    assert c["context.stale"]["level"] == "warn" and c["context.stale"]["fix"]["safe"]
    assert not os.path.exists(proj.path("context", "policy.md")), "reporting never repairs"
    DR.apply_fix(proj.root, "context.stale")
    assert os.path.exists(proj.path("context", "policy.md")) and by_id(DR.run(proj.root))["context"]["level"] == "ok"
    with pytest.raises(ResearchError, match="no automatic fix"):
        DR.apply_fix(proj.root, "records.rebuild")


def test_workspace_problems_are_visible(repo, tmp_path):
    import shutil
    Project.init(str(repo), name="d").close()
    W.enable(str(repo), str(tmp_path / "ledger"))
    with Project(str(repo)) as p:
        e = services.create_experiment(p, "E", hypothesis="h")
        a = W.create(p, e["id"], name="a")
        b = W.create(p, e["id"], name="b")        # same experiment, a different implementation: gets its own directory
        assert a["path"] != b["path"]
    open(os.path.join(a["path"], "wip.py"), "w").write("x")
    shutil.rmtree(b["path"])
    c = by_id(DR.run(str(repo)))
    assert c["ledger"]["level"] == "ok"
    assert c[f"workspace.{a['id']}"]["level"] == "warn" and "uncommitted" in c[f"workspace.{a['id']}"]["title"]
    assert "no directory" in c[f"workspace.{b['id']}"]["title"]
    assert c["worktrees.prunable"]["level"] == "warn"


def test_a_sandbox_that_cannot_be_enforced_is_an_error_not_a_pass(proj, monkeypatch):
    proj.save_config({"sandbox.enabled": True, "sandbox.default_profile": "strict", "sandbox.profiles.strict": {"runtime": "apptainer", "image": "/x.sif"}})
    proj.close()
    monkeypatch.setenv("PATH", "/nonexistent")
    c = by_id(DR.run(proj.root))
    assert c["sandbox.strict"]["level"] == "error" and "refused" in c["sandbox.strict"]["title"]


def test_cli_exit_codes_and_json(repo):
    Project.init(str(repo), name="d").close()
    run = lambda *a: subprocess.run([sys.executable, "-m", "research_cli", "doctor", *a], cwd=repo, capture_output=True, text=True)
    r = run("--json")
    assert r.returncode == 0 and json.loads(r.stdout)["healthy"]
    v1 = repo.parent / "v1"
    v1.mkdir()
    v1_project(str(v1))
    r = subprocess.run([sys.executable, "-m", "research_cli", "doctor"], cwd=v1, capture_output=True, text=True)
    assert r.returncode == 1 and "Project format is not v2" in r.stdout


def test_fix_objects_are_normalised_and_the_cli_survives_command_only_fixes(proj, capsys):
    assert DR.normalize_fix({"command": "research hpc doctor"})["text"] == "Run `research hpc doctor`."
    assert DR.normalize_fix("do it") == {"text": "do it"}
    assert DR.normalize_fix({"text": "t", "safe": True}) == {"text": "t", "safe": True}
    assert DR.normalize_fix(None) is None and DR.normalize_fix({}) is None
    from research_cli import main as M
    real = DR.run
    DR.run = lambda root: {"root": root, "healthy": True, "summary": {"errors": 0, "warnings": 1, "info": 0, "ok": 0}, "checks": [
        {"id": "x", "area": "A", "level": "warn", "title": "t", "detail": "", "fix": {"command": "research sync"}}]}
    try:
        a = type("A", (), {"root": proj.root, "fix": None, "json": False, "all": True})()
        assert M.cmd_doctor(a) == 0
    finally:
        DR.run = real
    assert "research sync" in capsys.readouterr().out
