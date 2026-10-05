import json
import os
import shutil
import sqlite3

import pytest

from research import context as C
from research import runs as R
from research import schema
from research import services as S
from research import views as V
from research.store import Project
from research.util import BudgetExceeded, NotFound, ResearchError, detect_author

from conftest import git


# ---------------------------------------------------------------------------- init / schema

def test_init_creates_structure_and_agents_md(proj, repo):
    for d in ("questions", "experiments", "findings", "decisions", "checkpoints", "notes", "skills", "context",
              "prompts", "templates", "cache"):
        assert (repo / ".research" / d).is_dir(), d
    assert (repo / ".research" / "research.db").exists()
    assert (repo / ".research" / "config.yaml").exists()
    assert (repo / ".research" / "skills" / "run-experiment" / "SKILL.md").exists()
    agents = (repo / "AGENTS.md").read_text()
    assert "research:begin" in agents and "research:end" in agents
    assert (repo / ".research" / "context" / "current.md").exists()


def test_agents_md_preserves_user_content(repo):
    (repo / "AGENTS.md").write_text("# Mine\n\nKeep me.\n")
    p = Project.init(str(repo))
    t = (repo / "AGENTS.md").read_text()
    assert t.startswith("# Mine\n\nKeep me.")
    assert t.count("research:begin") == 1
    p.write_agents_md()
    assert (repo / "AGENTS.md").read_text().count("research:begin") == 1
    p.close()


def test_migrations_idempotent_and_versioned(proj):
    assert schema.current_version(proj.conn) == schema.SCHEMA_VERSION
    assert schema.migrate(proj.conn) == schema.SCHEMA_VERSION
    proj.conn.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
    with pytest.raises(ResearchError):
        schema.migrate(proj.conn)


def test_sqlite_uses_rollback_journal(proj):
    mode = proj.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "delete"


def test_ids_normalize():
    assert schema.normalize_id("exp-1") == "EXP-001"
    assert schema.normalize_id("EXP12") == "EXP-012"
    assert schema.normalize_id("3", "Q") == "Q-003"
    assert schema.normalize_id("run_7") == "RUN-0007"
    with pytest.raises(ResearchError):
        schema.normalize_id("F-1", "EXP")


# ---------------------------------------------------------------------------- entities

def test_question_hierarchy_and_edit(proj):
    q1 = S.create_question(proj, "Top question")
    q2 = S.create_question(proj, "Sub question", parent=q1["id"])
    assert q2["parent_id"] == "Q-001"
    q2 = S.update_question(proj, q2["id"], status="answered", description="done")
    assert q2["status"] == "answered"
    with pytest.raises(ResearchError):
        S.update_question(proj, q2["id"], status="nope")
    with pytest.raises(NotFound):
        S.create_question(proj, "x", parent="Q-099")
    d = V.question_detail(proj, "Q-001")
    assert [c["id"] for c in d["children"]] == ["Q-002"]


def test_experiment_records_git_and_moves_question(proj, repo):
    q = S.create_question(proj, "Q")
    e = S.create_experiment(proj, "E", question=q["id"], hypothesis="h", parameters={"alpha": 0.1})
    assert e["git_branch"] == "main" and len(e["git_commit"]) == 40 and e["git_dirty"] is False
    assert proj.get("question", q["id"])["status"] == "investigating"
    (repo / "train.py").write_text("print('changed')\n")
    e2 = S.create_experiment(proj, "E2")
    assert e2["git_dirty"] is True


def test_variant_records_delta(proj):
    e = S.create_experiment(proj, "entropy", parameters={"entropy_weight": 1e-3, "lr": 0.1}, hypothesis="h",
                            success_criteria="s")
    v = S.create_variant(proj, e["id"], {"entropy_weight": 3e-3})
    assert v["parent_id"] == e["id"]
    assert v["parameters"] == {"entropy_weight": 3e-3, "lr": 0.1}
    assert v["param_delta"] == {"entropy_weight": {"from": 1e-3, "to": 3e-3}}
    assert v["hypothesis"] == "h" and v["success_criteria"] == "s"
    d = V.experiment_detail(proj, e["id"])
    assert d["children"][0]["id"] == v["id"]


def test_run_artifact_metric_chain(proj, repo):
    e = S.create_experiment(proj, "E", metrics=["err"])
    r = R.attach_run(proj, e["id"], command="python train.py --alpha 0.1", parameters={"alpha": 0.1},
                     exit_code=0, status="completed")
    (repo / "out").mkdir()
    (repo / "out" / "plot.png").write_bytes(b"\x89PNG\r\n")
    a = R.register_artifact(proj, "out/plot.png", run=r["id"], description="error curve")
    assert a["path"] == "out/plot.png" and a["experiment_id"] == e["id"] and a["type"] == "image"
    assert a["hash"] and a["size"] == 6
    R.log_metric(proj, "err", 0.25, run=r["id"])
    R.log_metric(proj, "err", 0.20, step=2, run=r["id"])
    d = V.experiment_detail(proj, e["id"])
    assert d["runs"][0]["metrics"]["err"]["value"] == 0.20
    assert d["runs"][0]["artifacts"][0]["id"] == a["id"]
    # re-registering same path updates, not duplicates
    a2 = R.register_artifact(proj, "out/plot.png", run=r["id"])
    assert a2["id"] == a["id"] and a2["description"] == "error curve"
    assert d["status"] == "needs_review"


def test_external_artifact_absolute_path(proj, tmp_path):
    ext = tmp_path / "scratch" / "big.npy"
    ext.parent.mkdir()
    ext.write_bytes(b"x" * 10)
    e = S.create_experiment(proj, "E")
    a = R.register_artifact(proj, str(ext), experiment=e["id"])
    assert a["external"] and os.path.isabs(a["path"]) and a["type"] == "array"


def test_finding_evidence_and_decision(proj):
    e = S.create_experiment(proj, "E")
    r = R.attach_run(proj, e["id"], command="x")
    f = S.create_finding(proj, "alpha=0.5 best", statement="…", supports=[e["id"], r["id"]], confidence="medium")
    assert f["links"]["supports"] == [e["id"], r["id"]]
    with pytest.raises(NotFound):
        S.create_finding(proj, "bad", supports=["RUN-0999"])
    fail = S.create_finding(proj, "Unconstrained optimization collapses", kind="failure", supports=[e["id"]])
    d = S.create_decision(proj, "Use alpha=0.5 as baseline", reason="F-001", supporting_findings=[f["id"]])
    fd = V.finding_detail(proj, f["id"])
    assert fd["decisions"][0]["id"] == d["id"]
    assert fd["experiments"][0]["id"] == e["id"]
    ed = V.experiment_detail(proj, e["id"])
    assert {x["id"] for x in ed["findings"]} == {f["id"], fail["id"]}
    f2 = S.create_finding(proj, "refined", supports=[e["id"]])
    S.supersede_finding(proj, f["id"], by=f2["id"])
    assert proj.get("finding", f["id"])["status"] == "superseded"
    d2 = S.create_decision(proj, "Use alpha=0.4", supersedes=d["id"])
    assert proj.get("decision", d["id"])["status"] == "superseded"
    assert proj.get("decision", d["id"])["superseded_by"] == d2["id"]


def test_authorship(proj, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    assert detect_author()["author_type"] == "agent"
    assert detect_author()["author_name"] == "claude-code"
    proj.author = detect_author()
    f = S.create_finding(proj, "agent claim")
    assert f["author_type"] == "agent" and f["author_name"] == "claude-code"
    monkeypatch.delenv("CLAUDECODE")
    assert detect_author()["author_type"] == "human"


# ---------------------------------------------------------------------------- budget

def _agent(p):
    p.author = {"author_type": "agent", "author_name": "test-agent", "author_model": None}


def test_run_budget_enforced_for_agents(proj):
    proj.config["agent_policy"]["max_runs_without_synthesis"] = 3
    e = S.create_experiment(proj, "E")
    _agent(proj)
    for _ in range(3):
        R.attach_run(proj, e["id"], command="x")
    st = S.experiment_state(proj, e["id"])
    assert st["unsynthesized"] == 3 and st["over_run_budget"] and st["needs_synthesis"]
    with pytest.raises(BudgetExceeded):
        R.attach_run(proj, e["id"], command="x")
    # override is allowed and logged
    R.attach_run(proj, e["id"], command="x", override="human approved")
    assert proj.q1("SELECT COUNT(*) AS n FROM events WHERE action='override'")["n"] == 1
    S.synthesize(proj, e["id"], what_happened="ran 4")
    st = S.experiment_state(proj, e["id"])
    assert st["unsynthesized"] == 0 and not st["over_run_budget"]
    R.attach_run(proj, e["id"], command="x")


def test_budget_warns_humans(proj):
    proj.config["agent_policy"]["max_runs_without_synthesis"] = 1
    e = S.create_experiment(proj, "E")
    R.attach_run(proj, e["id"], command="x")
    r = R.attach_run(proj, e["id"], command="x")
    assert r["warnings"]


def test_failed_run_budget(proj):
    proj.config["agent_policy"]["max_failed_runs_without_review"] = 2
    e = S.create_experiment(proj, "E")
    _agent(proj)
    R.attach_run(proj, e["id"], command="x", status="failed", exit_code=1)
    R.attach_run(proj, e["id"], command="x", status="failed", exit_code=1)
    with pytest.raises(BudgetExceeded):
        R.attach_run(proj, e["id"], command="x")


def test_synthesis_required_before_new_experiment(proj):
    e = S.create_experiment(proj, "E")
    R.attach_run(proj, e["id"], command="x")
    _agent(proj)
    with pytest.raises(BudgetExceeded):
        S.create_experiment(proj, "E2")
    S.synthesize(proj, e["id"], interpretation="ok")
    S.create_experiment(proj, "E2")


# ---------------------------------------------------------------------------- execution

def test_local_exec_foreground_captures_logs_metrics_artifacts(proj, repo):
    e = S.create_experiment(proj, "E")
    code = ("import research; research.log_metric('err', 0.5); "
            "open('res.csv','w').write('a,b\\n1,2\\n'); research.register_artifact('res.csv', description='tbl'); "
            "print('out'); import sys; print('warn', file=sys.stderr)")
    r = R.exec_run(proj, e["id"], ["python3", "-c", code], tee=False)
    assert r["status"] == "completed" and r["exit_code"] == 0
    assert open(proj.abspath(r["stdout_path"])).read().strip() == "out"
    assert "warn" in open(proj.abspath(r["stderr_path"])).read()
    d = V.run_detail(proj, r["id"])
    assert d["metrics"]["err"]["value"] == 0.5
    assert d["artifacts"][0]["path"] == "res.csv"
    rj = json.load(open(os.path.join(proj.abspath(r["run_dir"]), "run.json")))
    assert rj["status"] == "completed" and rj["experiment_id"] == e["id"]


def test_local_exec_failure(proj):
    e = S.create_experiment(proj, "E")
    r = R.exec_run(proj, e["id"], "exit 3", tee=False)
    assert r["status"] == "failed" and r["exit_code"] == 3


def test_local_detached_and_reconcile(proj):
    import time
    e = S.create_experiment(proj, "E")
    r = R.exec_run(proj, e["id"], "sleep 0.3; echo done", detach=True)
    assert r["status"] == "running"
    for _ in range(50):
        time.sleep(0.1)
        r = R.reconcile_run(proj, r["id"])
        if r["status"] != "running":
            break
    assert r["status"] == "completed"
    assert proj.get("experiment", e["id"])["status"] == "needs_review"


def test_dirty_run_saves_diff(proj, repo):
    (repo / "train.py").write_text("print('changed')\n")
    e = S.create_experiment(proj, "E")
    r = R.attach_run(proj, e["id"], command="x")
    assert r["git_dirty"] is True
    assert os.path.exists(os.path.join(proj.abspath(r["run_dir"]), "git.diff"))


# ---------------------------------------------------------------------------- mirrors / rebuild / portability

def test_hand_edit_of_mirror_is_imported(proj, repo):
    q = S.create_question(proj, "Original")
    path = repo / ".research" / "questions" / f"{q['id']}.md"
    text = path.read_text().replace("title: Original", "title: Edited by hand").replace(
        "## Description\n", "## Description\n\nWritten in my editor.\n")
    path.write_text(text)
    os.utime(path, (1, 1))  # force mtime change detection
    proj.sync()
    q2 = proj.get("question", q["id"])
    assert q2["title"] == "Edited by hand"
    assert q2["description"] == "Written in my editor."
    assert proj.q1("SELECT action FROM events WHERE entity_id=? ORDER BY id DESC LIMIT 1", (q["id"],))["action"] == "edited"


def test_malformed_mirror_does_not_corrupt(proj, repo):
    q = S.create_question(proj, "Keep me")
    path = repo / ".research" / "questions" / f"{q['id']}.md"
    path.write_text("---\ntitle: [unclosed\n---\n")
    proj.sync()
    assert proj.get("question", q["id"])["title"] == "Keep me"


def test_new_file_dropped_in_is_imported(proj, repo):
    (repo / ".research" / "findings" / "my-idea.md").write_text(
        "---\ntitle: Hand-written finding\nstatus: supported\n---\n\n## Statement\n\nIt works.\n")
    proj.sync()
    fs = proj.list("finding")
    assert fs[0]["title"] == "Hand-written finding" and fs[0]["statement"] == "It works."
    assert fs[0]["status"] == "supported" and fs[0]["author_type"] == "human"


def test_deleted_mirror_is_restored(proj, repo):
    q = S.create_question(proj, "Q")
    os.remove(repo / ".research" / "questions" / f"{q['id']}.md")
    proj.sync()
    assert (repo / ".research" / "questions" / f"{q['id']}.md").exists()


def _populate(p, repo):
    q = S.create_question(p, "Does alpha matter?")
    e = S.create_experiment(p, "sweep", question=q["id"], parameters={"alpha": [0.1, 0.5]})
    r = R.exec_run(p, e["id"], ["python3", "-c", "import research; research.log_metric('err', 0.3)"], tee=False)
    (repo / "plot.png").write_bytes(b"png")
    a = R.register_artifact(p, "plot.png", run=r["id"])
    S.synthesize(p, e["id"], what_happened="ok", next_experiment="try 0.3")
    f = S.create_finding(p, "0.5 best", supports=[e["id"], r["id"], a["id"]], status="supported")
    S.create_decision(p, "Use 0.5", supporting_findings=[f["id"]])
    S.set_baseline(p, e["id"])
    C.create_checkpoint(p)
    return q, e, r, a, f


def test_rebuild_from_text_files(proj, repo):
    q, e, r, a, f = _populate(proj, repo)
    before = C.resume_context(proj)
    proj.close()
    os.remove(repo / ".research" / "research.db")
    p2 = Project(str(repo))
    after = C.resume_context(p2)
    assert after["counts"] == before["counts"]
    assert after["baseline"]["id"] == e["id"]
    assert V.run_detail(p2, r["id"])["metrics"]["err"]["value"] == 0.3
    assert V.finding_detail(p2, f["id"])["links"]["supports"] == [e["id"], r["id"], a["id"]]
    assert len(V.experiment_detail(p2, e["id"])["syntheses"]) == 1
    # counters continue after rebuild
    assert S.create_question(p2, "next")["id"] == "Q-002"
    p2.close()


def test_project_is_relocatable(proj, repo, tmp_path):
    _populate(proj, repo)
    proj.close()
    moved = tmp_path / "elsewhere" / "proj2"
    shutil.copytree(repo, moved)
    shutil.rmtree(repo)
    p = Project(str(moved))
    a = R.list_artifacts(p)[0]
    assert a["path"] == "plot.png" and a["exists"] and a["abspath"] == str(moved / "plot.png")
    text = open(moved / ".research" / "research.db", "rb").read()
    assert str(repo).encode() not in text  # no absolute paths to the old location in the index
    for root, _d, fns in os.walk(moved / ".research"):
        for fn in fns:
            if fn.endswith((".md", ".json", ".jsonl", ".yaml")):
                content = open(os.path.join(root, fn), encoding="utf-8").read()
                assert str(repo) not in content, fn
    p.close()


# ---------------------------------------------------------------------------- checkpoint / resume

def test_checkpoint_draft_and_resume(proj, repo):
    q, e, r, a, f = _populate(proj, repo)
    fail = S.create_finding(proj, "Big lr diverges", kind="failure", supports=[r["id"]])
    S.create_question(proj, "Blocked thing", status="blocked")
    d = C.checkpoint_draft(proj)
    assert f["id"] in d["finding_ids"] and fail["id"] in d["failure_ids"]
    assert d["baseline_experiment_id"] == e["id"]
    assert "Blocked thing" in d["current_problem"]
    cp = C.create_checkpoint(proj, current_problem="Calibration test failing", next_experiment="Projection loss ablation")
    assert cp["git_branch"] == "main" and cp["current_problem"] == "Calibration test failing"
    res = C.resume_context(proj)
    assert res["latest_checkpoint"]["id"] == cp["id"]
    assert res["established_findings"][0]["id"] == f["id"]
    assert res["failed_directions"][0]["id"] == fail["id"]
    assert res["blockers"][0]["text"] == "Calibration test failing"
    assert any(s["path"] == "plot.png" for s in res["suggested_files"])
    md = C.render_current_md(proj)
    assert "Understand alpha" in md and f["id"] in md and "Failed directions" in md


def test_notes(proj):
    q = S.create_question(proj, "Q")
    n = S.create_note(proj, "My thoughts on alpha", "blah", links=[q["id"]])
    assert n["id"] == "note:my-thoughts-on-alpha" and n["links"] == [q["id"]]
    n = S.pin_note(proj, n["id"])
    assert n["pinned"]
    assert S.list_notes(proj)[0]["pinned"]
    assert V.question_detail(proj, q["id"])["linked_notes"][0]["id"] == n["id"]


def test_skills(proj):
    names = [s["name"] for s in S.list_skills(proj)]
    assert "run-experiment" in names
    s = S.create_skill(proj, "Evaluate material map", "Use when evaluating maps")
    assert s["dir"] == "evaluate-material-map"
    d = S.duplicate_skill(proj, "run-experiment", "run-experiment-slurm")
    assert d["name"] == "run-experiment-slurm"
