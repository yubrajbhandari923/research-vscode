"""`research report --full`: determinism, completeness, coverage verification, zero side effects."""
import json
import os
import subprocess
import sys

import pytest

from research import fullreport as FR
from research import plans as P
from research import runs as R
from research import services as S
from research import verification as V
from research.store import Project


# --------------------------------------------------------------------------- fixtures

def _hash_tree(root):
    """Hash every canonical file (the SQLite index and the per-machine bin/ are not canonical)."""
    out = {}
    for d, dirs, files in os.walk(os.path.join(root, ".research")):
        dirs[:] = [x for x in sorted(dirs) if x not in ("bin", "cache", "__pycache__")]
        for fn in sorted(files):
            if fn.startswith("research.db"):
                continue
            path = os.path.join(d, fn)
            with open(path, "rb") as f:
                out[os.path.relpath(path, root)] = len(f.read()), os.path.getmtime(path)
    return out


@pytest.fixture
def rich(proj):
    """A project with the awkward shapes: many runs, long metric histories, several syntheses,
    every finding status, failed/cancelled/active runs, unicode and Markdown in text, missing artifacts."""
    p = proj
    q1 = S.create_question(p, "Does α affect error?", "Unicode: ü ß 中文 · pipes | and *markdown*")
    q2 = S.create_question(p, "Is the pipeline reproducible?", parent=q1["id"])
    e1 = S.create_experiment(p, "α sweep | baseline", question=q1["id"],
                             hypothesis="Higher α lowers RMSE\n\nSecond paragraph with `code`.",
                             motivation="because", method="grid", expected_outcome="monotone",
                             success_criteria="rmse < 0.1", stop_conditions="3 failures",
                             parameters={"alpha": [0.1, 0.5], "seed": 1}, metrics=["rmse", "note"],
                             planned_runs=200, notes="multi\nline\nnotes")
    # 100 runs with long metric histories
    for i in range(100):
        r = R.attach_run(p, e1["id"], command=f"python t.py --i {i}", status="completed",
                         parameters={"alpha": 0.1 + i / 1000, "seed": 1}, label=f"run {i}")
        for step in range(5):
            R.log_metric(p, "rmse", 0.5 - step / 20 - i / 10000, step=step, run=r["id"])
        R.log_metric(p, "note", "converged ✓", run=r["id"])
    # failed / cancelled / queued / running / unknown runs, some with slurm job ids
    R.attach_run(p, e1["id"], command="python t.py --boom", status="failed", exit_code=2,
                 notes="OOM at step 3")
    R.attach_run(p, e1["id"], command="python t.py --cancel", status="cancelled")
    R.attach_run(p, e1["id"], command="python t.py --q", status="queued", slurm_job_id="991",
                 host="login2")
    R.attach_run(p, e1["id"], command="python t.py --r", status="running", slurm_job_id="992")
    R.attach_run(p, e1["id"], command="python t.py --u", status="unknown", slurm_job_id="993")
    # several syntheses of the same experiment
    S.synthesize(p, e1["id"], what_happened="first pass", interpretation="looks monotone",
                 limitations="only one seed")
    S.synthesize(p, e1["id"], what_happened="second pass", interpretation="not monotone after all",
                 unresolved="seed sensitivity", next_experiment="vary the seed")
    e2 = S.create_experiment(p, "seed variation", question=q2["id"], hypothesis="seeds matter",
                             parent=e1["id"], override="fixture")
    R.attach_run(p, e2["id"], command="python t.py --seed 2", status="completed")
    # artifacts: one that exists, one that does not
    real = os.path.join(p.root, "plot.png")
    open(real, "wb").write(b"\x89PNG fake")
    R.register_artifact(p, real, experiment=e1["id"], description="error vs α")
    R.register_artifact(p, os.path.join(p.root, "gone.npz"), experiment=e1["id"],
                        description="externally stored, not on this machine", allow_missing=True)
    # findings in every status, with supersession and an independent review
    f1 = S.create_finding(p, "α=0.1 is best | so far", statement="RMSE 0.12 at α=0.1",
                          supports=[e1["id"]], confidence="medium")
    f2 = S.create_finding(p, "α does not matter", kind="failure", supports=[e2["id"]],
                          limitations="single seed")
    S.supersede_finding(p, f2["id"], by=f1["id"], reason="better evidence")
    f3 = S.create_finding(p, "pipeline is reproducible", supports=[e2["id"]], questions=[q2["id"]])
    p.author = {"author_type": "agent", "author_name": "reviewer-bot", "author_model": "m"}
    V.review_finding(p, f3["id"], "supported", notes="checked independently")
    p.author = {"author_type": "human", "author_name": None, "author_model": None}
    f4 = S.create_finding(p, "dangling evidence", supports=[])
    # A broken reference as a user really creates one: by hand-editing the canonical Markdown.
    mirror = p.mirror_path("finding", f4["id"])
    with open(mirror, encoding="utf-8") as fh:
        md = fh.read()
    with open(mirror, "w", encoding="utf-8") as fh:
        fh.write(md.replace("supports: []", "supports:\n- RUN-9999"))
    p.sync(reconcile_runs=False)
    # decisions incl. a superseded one
    d1 = S.create_decision(p, "Use α=0.1", reason="best RMSE", supporting_findings=[f1["id"]],
                           experiments=[e1["id"]])
    S.create_decision(p, "Use α=0.3", reason="revised", supersedes=d1["id"])
    # plan + tasks with checks, deps, notes, verification history
    pl = P.create_plan(p, "Reproduce table 1", objective="match the paper",
                       success_criteria="within 1%", question=q2["id"])
    t1 = P.create_task(p, pl["id"], "implement baseline", goal="write model.py",
                       checks=["cmd:true"], acceptance_criteria="tests pass")
    P.create_task(p, pl["id"], "run the sweep", depends_on=[t1["id"]],
                  related_experiment_id=e1["id"])
    P.start_task(p, t1["id"])
    P.add_task_note(p, t1["id"], "started; found a bug in the loader")
    P.complete_task(p, t1["id"], result="done in model.py")
    # a hand-edited note and a checkpoint
    S.create_note(p, "lab notebook", "# Day 1\n\nTried α | 0.1 ünïcode. Table:\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
    from research import context as C
    C.create_checkpoint(p)
    return p


# --------------------------------------------------------------------------- determinism

def test_repeated_export_is_byte_identical(rich):
    _, a = FR.export(rich)
    _, b = FR.export(rich)
    assert a == b
    assert a.startswith("# Research Context Export\n")


def test_export_does_not_change_source_state(rich):
    before = _hash_tree(rich.root)
    events_before = rich.q("SELECT COUNT(*) AS n FROM events")[0]["n"]
    FR.export(rich)
    FR.export(rich, "EXP-001")
    assert _hash_tree(rich.root) == before
    assert rich.q("SELECT COUNT(*) AS n FROM events")[0]["n"] == events_before


def test_write_logs_no_event_and_only_touches_the_output(rich):
    events_before = rich.q("SELECT COUNT(*) AS n FROM events")[0]["n"]
    res = FR.write(rich, check=True)
    assert rich.q("SELECT COUNT(*) AS n FROM events")[0]["n"] == events_before
    assert res["path"] == ".research/reports/project-full.md"
    assert res["check"]["ok"], res["check"]["exporter_problems"]
    text = open(os.path.join(rich.root, res["path"]), encoding="utf-8").read()
    assert text == FR.export(rich)[1]


def test_no_wall_clock_time_unless_stamped(rich):
    _, plain = FR.export(rich)
    assert "generated at" not in plain
    _, stamped = FR.export(rich, stamp=True)
    assert "**generated at:**" in stamped
    assert "deterministic:** no" in stamped


def test_output_file_is_not_self_included(rich):
    FR.write(rich)
    _, again = FR.export(rich)
    assert "project-full.md" not in again


def test_paths_are_project_relative(rich):
    _, text = FR.export(rich)
    for line in text.splitlines():
        if line.startswith("- **run dir:**") or line.startswith("- **stdout path:**"):
            assert ".research/runs/" in line and not line.split("** ")[1].startswith("/")


def test_number_formatting_is_canonical():
    assert FR._num(1) == "1"
    assert FR._num(1.0) == "1.0"
    assert FR._num(0.1 + 0.2) == "0.30000000000000004"
    assert FR._num(True) == "true"
    assert FR._val(None) == "—" and FR._val([]) == "—" and FR._val({}) == "—"
    assert FR._val({"b": 2, "a": 1}) == "a=1, b=2"  # dicts always sorted
    assert FR._cell("a|b\nc") == "a\\|b<br>c"


# --------------------------------------------------------------------------- completeness

def test_every_record_category_is_covered(rich):
    snap, text = FR.export(rich)
    chk = FR.coverage(snap, text)
    assert chk["ok"], chk["exporter_problems"]
    for cat in FR.CATEGORIES:
        c = chk["categories"][cat]
        assert c["expected"] == c["emitted"], (cat, c)
    # 100 sweep runs + failed + cancelled + queued + running + unknown in EXP-001, + 1 in EXP-002
    assert chk["categories"]["run"]["expected"] == 106
    assert chk["categories"]["synthesis"]["expected"] == 2
    assert chk["categories"]["decision"]["expected"] == 2


def test_hundred_run_experiment_emits_all_runs(rich):
    snap, text = FR.export(rich, "EXP-001")
    runs = [r["id"] for e in snap["experiments"] for r in e["runs"]]
    assert len(runs) == 106   # 105 in EXP-001 + 1 in its variant EXP-002, which the scope includes
    for rid in runs:
        assert f"<!-- research:record run {rid} -->" in text
    # and the full metric history, not just final values
    assert text.count("<!-- research:record metric RUN-0001#") == 6  # 5 rmse steps + 1 textual metric


def test_all_syntheses_and_superseded_records_appear(rich):
    _, text = FR.export(rich)
    assert "first pass" in text and "second pass" in text
    assert "EXP-001/S1" in text and "EXP-001/S2" in text
    assert "Use α=0.1" in text and "Use α=0.3" in text          # superseded + active decision
    assert "α does not matter" in text                           # superseded finding
    assert "- **status:** superseded" in text


def test_textual_and_numeric_metrics_both_survive(rich):
    _, text = FR.export(rich)
    assert "converged ✓" in text


def test_missing_artifacts_and_dangling_refs_are_reported_not_inferred(rich):
    snap, text = FR.export(rich)
    miss = [m["path"] for m in snap["integrity"]["missing_artifacts"]]
    assert any(x.endswith("gone.npz") for x in miss)
    assert "NO (missing)" in text
    dang = snap["integrity"]["dangling_references"]
    assert any(x["to"] == "RUN-9999" for x in dang)
    assert "RUN-9999" in text


def test_nonterminal_runs_are_listed_as_incomplete(rich):
    snap, _ = FR.export(rich)
    states = {u["status"] for u in snap["integrity"]["incomplete_runs"]}
    assert states == {"queued", "running", "unknown"}


def test_reviews_and_task_verification_history_appear(rich):
    snap, text = FR.export(rich)
    assert snap["counts"]["review"] >= 2          # 1 finding review + >=1 task check record
    assert "checked independently" in text
    assert "started; found a bug in the loader" in text   # progress note trail


def test_preliminary_findings_are_not_presented_as_facts(rich):
    _, text = FR.export(rich)
    head = text.split("## 7. Findings and Reviews", 1)[1][:600]
    assert "**not** an established fact" in head
    assert "Independent review records: _none_" in text


def test_unknown_future_fields_are_preserved(proj):
    """A column a future schema adds must still reach the export, generically."""
    S.create_experiment(proj, "future", hypothesis="h")
    proj.conn.execute("ALTER TABLE experiments ADD COLUMN cosmic_ray_flux TEXT")
    proj.conn.execute("UPDATE experiments SET cosmic_ray_flux='42 Sv' WHERE id='EXP-001'")
    snap, text = FR.export(proj)
    assert snap["experiments"][0]["_unknown"]["cosmic_ray_flux"] == "42 Sv"
    assert "cosmic_ray_flux" in text and "42 Sv" in text


def test_notes_are_included_verbatim(rich):
    _, text = FR.export(rich)
    assert "Tried α | 0.1 ünïcode" in text
    assert "| a | b |" in text          # the note's own Markdown table, inside a fenced block


# --------------------------------------------------------------------------- coverage detects failures

def test_dropped_record_fails_coverage(rich):
    snap, text = FR.export(rich)
    broken = text.replace("<!-- research:record run RUN-0042 -->\n", "")
    chk = FR.coverage(snap, broken)
    assert not chk["ok"]
    assert any(x["kind"] == "missing" and x["id"] == "RUN-0042" for x in chk["exporter_problems"])


def test_duplicated_record_fails_coverage(rich):
    snap, text = FR.export(rich)
    broken = text.replace("<!-- research:record finding F-001 -->",
                          "<!-- research:record finding F-001 -->\n<!-- research:record finding F-001 -->")
    chk = FR.coverage(snap, broken)
    assert not chk["ok"]
    assert any(x["kind"] == "duplicated" and x["id"] == "F-001" for x in chk["exporter_problems"])


def test_unexpected_record_fails_coverage(rich):
    snap, text = FR.export(rich)
    chk = FR.coverage(snap, text + "\n<!-- research:record finding F-999 -->\n")
    assert not chk["ok"]
    assert any(x["kind"] == "unexpected" for x in chk["exporter_problems"])


def test_malformed_structure_fails_coverage(rich):
    snap, text = FR.export(rich)
    chk = FR.coverage(snap, text.replace("## 6. Experiments", "## Experiments"))
    assert not chk["ok"]
    assert any(x["kind"] == "malformed" for x in chk["exporter_problems"])


def test_source_incompleteness_is_not_an_exporter_failure(rich):
    snap, text = FR.export(rich)
    chk = FR.coverage(snap, text)
    assert chk["ok"]                                        # exporter is fine
    assert chk["source_incompleteness"]["missing_artifacts"] >= 1   # but the data has gaps
    assert chk["source_incompleteness"]["dangling_references"] >= 1


# --------------------------------------------------------------------------- rebuild equivalence

def test_rebuild_from_canonical_files_gives_the_same_export(rich):
    _, before = FR.export(rich)
    rich.rebuild_index(backup=False)
    _, after = FR.export(rich)
    assert after == before


def test_reopening_the_project_gives_the_same_export(rich):
    _, before = FR.export(rich)
    root = rich.root
    rich.close()
    with Project(root, sync=False) as p2:
        _, after = FR.export(p2)
    assert after == before


# --------------------------------------------------------------------------- scopes

@pytest.mark.parametrize("scope", [None, "Q-001", "EXP-001", "PLAN-001"])
def test_each_scope_is_deterministic_and_covered(rich, scope):
    snap, a = FR.export(rich, scope)
    _, b = FR.export(rich, scope)
    assert a == b
    assert FR.coverage(snap, a)["ok"]


def test_experiment_scope_excludes_other_experiments(rich):
    snap, text = FR.export(rich, "EXP-002")
    assert [e["id"] for e in snap["experiments"]] == ["EXP-002"]
    assert "<!-- research:record experiment EXP-001 -->" not in text


def test_unsupported_scope_is_refused(rich):
    from research.util import ResearchError
    with pytest.raises(ResearchError):
        FR.export(rich, "F-001")


def test_full_export_is_not_truncated_by_the_context_budget(rich):
    rich.config["context"]["max_chars"] = 100
    rich.save_config()
    _, text = FR.export(rich)
    assert len(text) > 100_000
    assert "… more runs" not in text and "truncated" not in text.lower().split("inherently")[0]


# --------------------------------------------------------------------------- no agents / network / LLM

def test_no_subprocess_or_network_during_rendering(rich, monkeypatch):
    """Rendering must not shell out or open a socket. (`snapshot` reads git metadata; `render` must not.)"""
    snap = FR.snapshot(rich)
    import socket
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("rendering ran a subprocess"))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("rendering spawned a process"))
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("rendering opened a socket"))
    FR.render(snap)
    FR.coverage(snap, FR.render(snap))


def test_module_does_not_import_any_agent_or_http_machinery():
    src = open(FR.__file__, encoding="utf-8").read()
    for forbidden in ("urllib", "requests", "http.client", "openai", "anthropic",
                      "from . import agents", "orchestrate", "dispatch"):
        assert forbidden not in src, forbidden


# --------------------------------------------------------------------------- CLI

def _cli(cwd, *args):
    return subprocess.run([sys.executable, "-m", "research_cli", *args], cwd=cwd,
                          capture_output=True, text=True)


def test_cli_full_check_json_and_exit_codes(rich):
    root = rich.root
    rich.close()
    r = _cli(root, "report", "--full", "--check", "--json")
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert d["check"]["ok"] and d["counts"]["run"] == 106
    assert d["path"] == ".research/reports/project-full.md"
    # --stdout prints the document itself
    r2 = _cli(root, "report", "--full", "--stdout")
    assert r2.returncode == 0 and r2.stdout.startswith("# Research Context Export")
    # a scoped export lands next to it under its own name
    r3 = _cli(root, "report", "--full", "EXP-001", "--json")
    assert json.loads(r3.stdout)["path"] == ".research/reports/EXP-001-full.md"


def test_cli_full_does_not_disturb_the_compact_report(rich):
    root = rich.root
    rich.close()
    assert _cli(root, "report", "--format", "md").returncode == 0
    compact = os.path.join(root, ".research", "reports", "project.md")
    assert os.path.exists(compact)
    before = open(compact, encoding="utf-8").read()
    assert _cli(root, "report", "--full").returncode == 0
    assert open(compact, encoding="utf-8").read() == before


def test_coverage_failure_is_what_makes_check_exit_nonzero(rich):
    """`--check` returns 1 exactly when coverage fails; this pins the condition the CLI tests."""
    snap, text = FR.export(rich)
    chk = FR.coverage(snap, text.replace("<!-- research:record decision D-001 -->\n", ""))
    assert not chk["ok"]
