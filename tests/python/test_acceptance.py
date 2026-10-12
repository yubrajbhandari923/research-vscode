"""Final acceptance scenario: an existing project is upgraded, runs an experiment remotely, retrieves its
results and produces a deterministic coverage-verified history — losing and inventing nothing.

Runs the 17 steps of the specification end to end, offline, with no SSH credentials, no live Slurm and no LLM.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

import fakecluster as FC
from research import agentsmd as AM
from research import context as C
from research import fullreport as FR
from research import plans as P
from research import runs as R
from research import services as S
from research import upgrade as UP
from research import verification as V
from research.hpc import config as HC
from research.hpc import datasets as D
from research.hpc import remote as RM
from research.hpc import results as RES
from research.store import Project

USER_AGENTS_HEAD = """# Agent instructions — pravah

## Lab rules (mine, not Research OS's)

Always use float64. Never write to `data/raw/`. Report RMSE in the paper's units.

"""
USER_AGENTS_TAIL = """

## Reviewer notes

Numbers in the manuscript come from `results/final/`, never from a notebook.
"""

TRAIN = """
import json, os, sys
rd = os.environ["RESEARCH_RUN_DIR"]
alpha = float(sys.argv[1]) if len(sys.argv) > 1 else 0.1
with open(os.path.join(rd, "metrics.jsonl"), "a") as f:
    for step in range(4):
        f.write(json.dumps({"name": "rmse", "value": 0.4 - step * 0.05 - alpha / 10,
                            "step": step, "unit": "m/s"}) + "\\n")
    f.write(json.dumps({"name": "converged", "value": "yes"}) + "\\n")
open("prediction.npy", "wb").write(b"\\x00" * 64)
with open(os.path.join(rd, "artifacts.jsonl"), "a") as f:
    f.write(json.dumps({"path": os.path.abspath("prediction.npy"), "name": "prediction.npy",
                        "description": "model output"}) + "\\n")
print("alpha", alpha)
"""


def _counts(p):
    """Research records only — the audit log is append-only and is checked separately."""
    return {t: p.q(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]
            for t in ("questions", "experiments", "runs", "metrics", "artifacts", "syntheses",
                      "findings", "decisions", "checkpoints", "plans", "tasks", "reviews", "links")}


def _normalized(p):
    """Canonical research objects, independent of serialization, for before/after comparison."""
    out = {}
    for etype in ("question", "experiment", "finding", "decision", "checkpoint", "plan", "task"):
        for row in p.list(etype, order="id"):
            out[row["id"]] = {k: v for k, v in sorted(row.items()) if k != "updated_at"}
    for r in R.list_runs(p):
        out[r["id"]] = {k: v for k, v in sorted(r.items()) if k not in ("updated_at", "slurm")}
        out[r["id"]]["metrics"] = [(m["name"], m["value"], m["value_text"], m["step"])
                                   for m in R.metrics_for_run(p, r["id"])]
    for e in p.q("SELECT id FROM experiments ORDER BY id"):
        out[f"syn:{e['id']}"] = [{k: v for k, v in sorted(s.items())} for s in S.list_syntheses(p, e["id"])]
    return out


@pytest.fixture
def cluster(tmp_path):
    cl = FC.FakeCluster(str(tmp_path / "cluster"))
    restore = FC.install(cl)
    yield cl
    restore()


@pytest.fixture
def existing(proj):
    """An existing Research OS project at the scale the specification describes."""
    p = proj
    p.update_project(name="pravah", goal="Understand atmospheric transport error")
    with open(os.path.join(p.root, "train.py"), "w") as f:
        f.write(TRAIN)

    # 3 questions
    q1 = S.create_question(p, "Does α control transport error?", "α is the diffusion coefficient")
    q2 = S.create_question(p, "Is the pipeline reproducible across seeds?")
    q3 = S.create_question(p, "Which resolution is enough?", "Blocked on cluster quota", status="blocked")

    # 5 experiments, 20+ runs, 3 failed, 2 active/queued Slurm runs
    exps = []
    for i, (title, qid) in enumerate([("α sweep", q1["id"]), ("seed variation", q2["id"]),
                                      ("resolution study", q3["id"]), ("baseline", q1["id"]),
                                      ("ablation", q1["id"])]):
        e = S.create_experiment(
            p, title, question=qid, hypothesis=f"hypothesis for {title}",
            motivation="recorded motivation", method="recorded method",
            expected_outcome="monotone improvement", success_criteria="rmse < 0.3",
            stop_conditions="3 consecutive failures",
            parameters={"command": f"{sys.executable} train.py 0.1", "alpha": 0.1 * (i + 1)},
            metrics=["rmse"], planned_runs=30, override="fixture")
        exps.append(e["id"])
    S.set_baseline(p, exps[3])

    for i in range(20):
        r = R.attach_run(p, exps[i % 3], command=f"{sys.executable} train.py {0.1 * (i + 1):.1f}",
                         status="completed", parameters={"alpha": round(0.1 * (i + 1), 2)},
                         label=f"sweep {i}")
        for step in range(3):
            R.log_metric(p, "rmse", round(0.4 - step * 0.05 - i / 100, 4), step=step, run=r["id"],
                         unit="m/s")
    for i in range(3):
        R.attach_run(p, exps[i], command="python train.py --boom", status="failed", exit_code=1,
                     notes=f"failure {i}: ran out of memory at step 2")
    R.attach_run(p, exps[0], command="python train.py --long", status="queued", slurm_job_id="880001")
    R.attach_run(p, exps[1], command="python train.py --long", status="running", slurm_job_id="880002")

    # 4 syntheses (two of them on the same experiment)
    S.synthesize(p, exps[0], what_happened="swept α over 10 values",
                 interpretation="error falls with α up to 0.5", limitations="single seed")
    S.synthesize(p, exps[0], what_happened="extended the sweep",
                 interpretation="the trend reverses beyond α=0.7", unresolved="why the reversal",
                 next_experiment="vary the seed at α=0.7")
    S.synthesize(p, exps[1], what_happened="varied the seed", what_failed="two runs diverged")
    S.synthesize(p, exps[2], what_happened="tried 3 resolutions", what_failed="out of quota")

    # 6 findings across statuses, with an independent review and a supersession
    f1 = S.create_finding(p, "α≈0.5 minimises RMSE", statement="RMSE 0.21 m/s at α=0.5",
                          supports=[exps[0]], confidence="medium", questions=[q1["id"]])
    f2 = S.create_finding(p, "α has no effect", kind="failure", supports=[exps[0]],
                          limitations="superseded by a longer sweep")
    S.supersede_finding(p, f2["id"], by=f1["id"], reason="the longer sweep contradicts it")
    f3 = S.create_finding(p, "the pipeline is seed-stable", supports=[exps[1]], questions=[q2["id"]])
    p.author = {"author_type": "agent", "author_name": "reviewer-bot", "author_model": "m"}
    V.review_finding(p, f3["id"], "supported", notes="re-ran two seeds independently")
    p.author = {"author_type": "human", "author_name": None, "author_model": None}
    S.create_finding(p, "high resolution exceeds quota", kind="failure", supports=[exps[2]])
    S.create_finding(p, "the baseline is reproducible", supports=[exps[3]])
    S.create_finding(p, "ablation is inconclusive", supports=[exps[4]], confidence="low")

    # 3 decisions, one superseded
    d1 = S.create_decision(p, "Use α=0.3", reason="first estimate", supporting_findings=[f1["id"]])
    S.create_decision(p, "Use α=0.5", reason="the longer sweep", supporting_findings=[f1["id"]],
                      experiments=[exps[0]], supersedes=d1["id"])
    S.create_decision(p, "Defer the resolution study", reason="quota")

    # 2 plans, 8 tasks
    pl1 = P.create_plan(p, "Reproduce table 1", objective="match the published numbers",
                        success_criteria="within 1%", question=q1["id"])
    pl2 = P.create_plan(p, "Seed robustness", objective="quantify seed variance", question=q2["id"])
    t1 = P.create_task(p, pl1["id"], "implement the loader", goal="read the ERA5 subset",
                       checks=["cmd:true"], acceptance_criteria="unit tests pass")
    t2 = P.create_task(p, pl1["id"], "run the sweep", depends_on=[t1["id"]],
                       related_experiment_id=exps[0])
    P.create_task(p, pl1["id"], "write it up", depends_on=[t2["id"]], task_type="synthesis")
    P.create_task(p, pl1["id"], "check the units", task_type="verification")
    P.create_task(p, pl2["id"], "pick the seeds", goal="10 seeds")
    P.create_task(p, pl2["id"], "run them", related_experiment_id=exps[1])
    P.create_task(p, pl2["id"], "analyse the variance", task_type="analysis")
    P.create_task(p, pl2["id"], "blocked on quota", task_type="experiment")
    P.start_task(p, t1["id"])
    P.add_task_note(p, t1["id"], "found a units bug in the loader; fixed")
    P.complete_task(p, t1["id"], result="loader implemented in io.py")
    P.block_task(p, "T-008", "waiting for the quota increase")

    # 2 checkpoints and hand-edited notes
    C.create_checkpoint(p, understanding="α matters up to 0.5", current_problem="the reversal",
                        next_experiment="seed sweep at α=0.7")
    S.create_note(p, "lab notebook", "# Day 1\n\nTried α | 0.1 ünïcode.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
    S.create_note(p, "meeting 2026-02-03", "Agreed: α=0.5 goes in the paper.")
    C.create_checkpoint(p, understanding="seed-stable at the baseline", current_problem="quota")

    # project-specific configuration and a key this version does not know
    p.config["project"]["description"] = "atmospheric transport study"
    p.config["agent_policy"]["max_runs_without_synthesis"] = 8
    p.config["pravah"] = {"grid": "0.25deg", "reference_run": "RUN-0003"}
    p.save_config()
    p.config = p.load_config()

    # user-authored AGENTS.md around the managed block
    path = os.path.join(p.root, "AGENTS.md")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    block = text[text.index(AM.BEGIN):text.index(AM.END) + len(AM.END)]
    with open(path, "w", encoding="utf-8") as f:
        f.write(USER_AGENTS_HEAD + block + USER_AGENTS_TAIL)

    # a hand edit to canonical Markdown
    mirror = p.mirror_path("question", q1["id"])
    with open(mirror, encoding="utf-8") as f:
        md = f.read()
    with open(mirror, "w", encoding="utf-8") as f:
        f.write(md.replace("## Description\n", "## Description\n\nEdited by hand. α is the *diffusion* term.\n"))
    p.sync(reconcile_runs=False)
    return p


def test_acceptance_scenario(existing, cluster, tmp_path):
    p = existing
    root = p.root

    # ---- 1. load the existing project -------------------------------------------------------
    before_counts = _counts(p)
    before_objects = _normalized(p)
    assert before_counts["questions"] == 3
    assert before_counts["experiments"] == 5
    assert before_counts["runs"] == 25
    assert before_counts["syntheses"] == 4
    assert before_counts["findings"] == 6
    assert before_counts["decisions"] == 3
    assert before_counts["plans"] == 2
    assert before_counts["tasks"] == 8
    assert before_counts["checkpoints"] == 2
    assert len([r for r in R.list_runs(p) if r["status"] == "failed"]) == 3
    assert len([r for r in R.list_runs(p) if r["status"] in ("queued", "running")]) == 2

    # ---- 2. produce and verify the full deterministic export ---------------------------------
    snap1, text1 = FR.export(p)
    chk1 = FR.coverage(snap1, text1)
    assert chk1["ok"], chk1["exporter_problems"]
    assert FR.export(p)[1] == text1                       # byte-identical on a repeat
    assert snap1["counts"]["run"] == 25
    assert snap1["counts"]["synthesis"] == 4
    assert snap1["counts"]["note"] == 2
    assert "Edited by hand. α is the *diffusion* term." in text1
    assert "Tried α | 0.1 ünïcode" in text1

    # ---- 3. preview and apply the upgrade ----------------------------------------------------
    user_bytes_before = AM.outside_bytes(p)
    agents_before = open(os.path.join(root, "AGENTS.md"), "rb").read()
    p.close()
    shutil.rmtree(os.path.join(root, ".research", "reports"), ignore_errors=True)
    tree_before = _tree(root)
    preview = UP.plan(root)
    assert _tree(root) == tree_before                     # a dry run changes nothing at all
    assert preview["steps"] and not preview["downgrade_refused"]
    applied = UP.apply(root)
    assert applied["ok"], applied["failed"]

    # ---- 4. prove the canonical research state was preserved ---------------------------------
    p = Project(root, sync=False)
    assert _counts(p) == before_counts
    assert _normalized(p) == before_objects
    assert p.config["pravah"] == {"grid": "0.25deg", "reference_run": "RUN-0003"}
    assert p.config["agent_policy"]["max_runs_without_synthesis"] == 8
    assert p.config["project"]["description"] == "atmospheric transport study"

    # ---- 5 & 6. the managed block updates; the user's bytes do not ---------------------------
    state = AM.state(p)
    assert state["problem"] is None
    AM.apply(p)
    assert AM.outside_bytes(p) == user_bytes_before
    now = open(os.path.join(root, "AGENTS.md"), encoding="utf-8").read()
    assert now.startswith(USER_AGENTS_HEAD) and now.endswith(USER_AGENTS_TAIL)
    assert "Always use float64" in now and "Never write to `data/raw/`" in now
    if agents_before != open(os.path.join(root, "AGENTS.md"), "rb").read():
        assert AM.BEGIN in now and AM.END in now          # only the managed region moved

    # ---- 7. build an HPC bundle --------------------------------------------------------------
    p.config["hpc"] = FC.cluster_config(cluster, slug="pravah")
    p.save_config()
    p.config = p.load_config()
    _mk_dataset(cluster, "ERA5")
    S.update_experiment(p, "EXP-001", parameters={
        "command": f"{sys.executable} train.py 0.5", "alpha": 0.5, "datasets": ["ERA5"]})
    from research.hpc import bundle as B
    bun = B.build(p, "EXP-001", resources={"time": "00:10:00", "cpus": 1})
    run_id = bun["runs"][0]
    assert bun["manifest"]["experiment"]["hypothesis"]
    assert bun["manifest"]["datasets"][0]["id"] == "ERA5"
    assert bun["manifest"]["submission_token"] == bun["token"]

    # ---- 8. execute the bundle against the fake Slurm environment ----------------------------
    carried = tmp_path / "carried"
    carried.mkdir()
    subprocess.run(["tar", "-xzf", os.path.join(root, bun["path"]), "-C", str(carried)], check=True)
    bdir = str(carried / os.listdir(carried)[0])
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PATH"] = cluster.bin + os.pathsep + env.get("PATH", "")
    env["FAKE_SLURM_STATE"] = cluster.state
    assert subprocess.run(["bash", "scripts/preflight.sh"], cwd=bdir, env=env,
                          capture_output=True, text=True).returncode == 0
    sub = subprocess.run(["bash", "scripts/submit.sh"], cwd=bdir, env=env, capture_output=True, text=True)
    assert sub.returncode == 0, sub.stdout + sub.stderr
    assert "slurm job" in sub.stdout                      # it really went through fake sbatch

    # ---- 9. generate a results archive -------------------------------------------------------
    col = subprocess.run(["bash", "scripts/collect.sh"], cwd=bdir, env=env, capture_output=True, text=True)
    assert col.returncode == 0, col.stdout + col.stderr
    archive = str(carried / f"results-{run_id}.tar.gz")
    assert os.path.exists(archive)

    # ---- 10 & 11. import it, and verify identity, metrics, artifacts, provenance --------------
    imported = RES.import_archive(p, archive)
    assert imported["run"] == run_id
    assert imported["experiment"] == "EXP-001"
    assert imported["status"] == "completed"
    assert imported["metrics_added"] == 5
    assert not imported["warnings"], imported["warnings"]
    assert imported["integrity"]["checked"] >= 4 and not imported["integrity"]["mismatched"]
    run = R.get_run(p, run_id)
    assert run["experiment_id"] == "EXP-001" and run["exit_code"] == 0
    assert run["git_commit"] and ((run["slurm"] or {}).get("hpc") or {})["token"] == bun["token"]
    mnames = {m["name"] for m in R.metrics_for_run(p, run_id)}
    assert mnames == {"rmse", "converged"}
    assert any(a["name"] == "prediction.npy" for a in R.list_artifacts(p, run=run_id))

    # ---- 12. reconcile the completed run -----------------------------------------------------
    assert R.get_run(p, run_id)["status"] == "completed"
    assert p.get("experiment", "EXP-001")["status"] in ("needs_review", "running", "completed")

    # ---- 13. the analysis task is exposed, without dispatching an agent ----------------------
    # The new run is recorded as unsynthesized straight away. EXP-001 is not yet *ready* for
    # interpretation because the fixture left another run queued on it — exactly the right answer,
    # and the reason `_pending_analysis` reports readiness rather than guessing.
    state = S.experiment_state(p, "EXP-001")
    assert run_id in state["unsynthesized_ids"]
    assert state["active"] == 1 and not state["needs_synthesis"]
    R.update_run(p, "RUN-0024", status="cancelled")          # the stale queued run is resolved
    pending = RM._pending_analysis(p)
    assert any(x["experiment"] == "EXP-001" for x in pending)
    assert all(x["do"].startswith("research experiment synthesize") for x in pending)
    assert "EXP-001" in {e["id"] for e in S.experiments_needing_synthesis(p)}
    # nothing was dispatched: the task is merely exposed
    assert all("agent" not in x["do"] for x in pending)

    # ---- 14 & 15. export again; old and new records are both represented ---------------------
    snap2, text2 = FR.export(p)
    chk2 = FR.coverage(snap2, text2)
    assert chk2["ok"], chk2["exporter_problems"]
    assert FR.export(p)[1] == text2
    assert snap2["counts"]["run"] == before_counts["runs"] + 1
    assert f"<!-- research:record run {run_id} -->" in text2
    for old_id in ("Q-001", "EXP-005", "RUN-0001", "F-002", "D-001", "CP-001", "PLAN-002", "T-008"):
        assert old_id in text2, old_id
    assert "EXP-001/S1" in text2 and "EXP-001/S2" in text2     # both syntheses still there
    assert "α has no effect" in text2                          # the superseded finding survives
    assert "Use α=0.3" in text2                                # and the superseded decision

    # ---- 16. upgrades and imports again: idempotent -------------------------------------------
    counts_now = _counts(p)
    p.close()
    # The HPC block was written in step 7 without its default sub-keys, so one more merge is due.
    # What must hold is convergence: after it, another upgrade finds nothing left to do.
    UP.apply(root)
    third = UP.apply(root)
    assert third["up_to_date"], third["steps"]
    p = Project(root, sync=False)
    assert p.config["hpc"]["clusters"]["fake"]["ssh_host"] == cluster.host   # and nothing was lost
    again = RES.import_archive(p, archive)
    assert not again["changed"] and again["metrics_added"] == 0
    assert _counts(p) == counts_now
    # Everything the export says about the research itself is unchanged; the upgrade's own audit
    # entries are the only difference, and they belong in the event history by design.
    snap_r, text_r = FR.export(p)
    assert FR.coverage(snap_r, text_r)["ok"]
    cut = "## 12. Audit / Event History"
    assert text_r.split(cut)[0].split("## 2.", 1)[1] == text2.split(cut)[0].split("## 2.", 1)[1]

    # ---- 17. rebuild the database; everything remains accessible -------------------------------
    p.rebuild_index(backup=False)
    assert _counts(p) == counts_now
    assert _normalized(p)["RUN-0001"] == before_objects["RUN-0001"]
    snap3, text3 = FR.export(p)
    assert FR.coverage(snap3, text3)["ok"]
    assert text3 == text_r                               # byte-identical across a full rebuild
    p.close()


def _tree(root):
    out = {}
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in sorted(dirs) if x not in ("bin", "cache", "__pycache__", ".git")]
        for fn in sorted(files):
            if fn.startswith("research.db"):
                continue
            path = os.path.join(d, fn)
            with open(path, "rb") as f:
                out[os.path.relpath(path, root)] = f.read()
    return out


def _mk_dataset(cluster, ident):
    root = os.path.join(cluster.shared, ident)
    fdir = os.path.join(root, "files")
    os.makedirs(fdir, exist_ok=True)
    for f in ("winds.nc", "temps.nc"):
        with open(os.path.join(fdir, f), "w") as fh:
            fh.write("data\n")
    man = D.manifest_template(ident, status="complete", version="2025",
                              source="https://example.invalid/era5",
                              expected_files=["temps.nc", "winds.nc"])
    with open(os.path.join(root, "manifest.json"), "w") as fh:
        json.dump(man, fh, indent=2, sort_keys=True)
    return root


def test_two_projects_share_a_dataset_and_keep_separate_results(existing, cluster, tmp_path):
    """The addendum's acceptance scenario: project A stages nothing, project B reuses the same bytes."""
    p = existing
    p.config["hpc"] = FC.cluster_config(cluster, slug="pravah")
    p.save_config()
    p.config = p.load_config()
    _mk_dataset(cluster, "ERA5")

    a = D.fetch(p, HC.get(p), "ERA5")
    assert a["action"] == "reuse" and a["verification"]["strength"] == "inventory"
    RM.init_workspace(p)
    S.update_experiment(p, "EXP-001", parameters={
        "command": f"{sys.executable} train.py 0.5", "datasets": ["ERA5"]})
    ra = RM.submit(p, "EXP-001", approve=True)

    other = tmp_path / "medical-imaging"
    other.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(other), check=True)
    with open(other / "seg.py", "w") as f:
        f.write(TRAIN)
    p2 = Project.init(str(other), name="medical-imaging")
    try:
        p2.config["hpc"] = FC.cluster_config(cluster, slug="medical-imaging")
        p2.save_config()
        p2.config = p2.load_config()
        b = D.fetch(p2, HC.get(p2), "ERA5")
        assert b["action"] == "reuse"
        assert b["path"] == a["path"]                      # the same directory, not a second copy
        assert sorted(os.listdir(cluster.shared)) == ["ERA5"]
        S.create_experiment(p2, "segment", parameters={
            "command": f"{sys.executable} seg.py 0.2", "datasets": ["ERA5"]})
        RM.init_workspace(p2)
        rb = RM.submit(p2, "EXP-001", approve=True)
        assert rb["slurm_job_id"] != ra["slurm_job_id"]

        # separate code and results, one shared dataset
        projects = sorted(os.listdir(os.path.join(cluster.workspace, "projects")))
        assert projects == ["medical-imaging", "pravah"]

        # a later session, with no memory, recovers both from the filesystem
        RM.sync(p)
        RM.sync(p2)
        assert R.get_run(p, ra["run"])["status"] == "completed"
        assert R.get_run(p2, rb["run"])["status"] == "completed"
        assert [m["name"] for m in R.metrics_for_run(p2, rb["run"])]

        # and no registry was ever created
        for walk_root in (p.root, p2.root, cluster.root):
            for d, _dirs, files in os.walk(walk_root):
                for f in files:
                    assert f not in ("datasets.json", "projects.json"), os.path.join(d, f)
    finally:
        p2.close()
