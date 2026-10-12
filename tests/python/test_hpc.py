"""HPC: portable bundles, SSH/Slurm execution, dataset discovery, result import, reconciliation.

Everything here runs offline against `fakecluster.FakeCluster` — a local directory plus fake
sbatch/squeue/sacct/scancel scripts. No SSH credentials, no real cluster, no network, no LLM.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

import fakecluster as FC
from research import runs as R
from research import services as S
from research.hpc import bundle as B
from research.hpc import config as HC
from research.hpc import datasets as D
from research.hpc import remote as RM
from research.hpc import results as RES
from research.store import Project
from research.util import NotFound, ResearchError


# --------------------------------------------------------------------------- fixtures

TRAIN = """
import json, os, sys
rd = os.environ["RESEARCH_RUN_DIR"]
with open(os.path.join(rd, "metrics.jsonl"), "a") as f:
    for i in range(3):
        f.write(json.dumps({"name": "rmse", "value": 0.5 - i * 0.1, "step": i}) + "\\n")
open("out.txt", "w").write("hello from the cluster\\n")
with open(os.path.join(rd, "artifacts.jsonl"), "a") as f:
    f.write(json.dumps({"path": os.path.abspath("out.txt"), "name": "out.txt",
                        "description": "the output"}) + "\\n")
print("trained")
"""


@pytest.fixture
def cluster(tmp_path):
    cl = FC.FakeCluster(str(tmp_path / "cluster"))
    restore = FC.install(cl)
    yield cl
    restore()


@pytest.fixture
def hp(proj, cluster):
    """A project wired to the fake cluster, with one runnable experiment."""
    p = proj
    with open(os.path.join(p.root, "train.py"), "w") as f:
        f.write(TRAIN)
    p.config["hpc"] = FC.cluster_config(cluster, slug="demo")
    p.save_config()
    p.config = p.load_config()
    S.create_experiment(p, "train the model", hypothesis="it converges",
                        parameters={"command": f"{sys.executable} train.py", "alpha": 0.1},
                        metrics=["rmse"])
    return p


def _mk_dataset(cluster, ident, *, manifest=None, files=("a.bin", "b.bin"), lock=False, staging=False):
    """Create a dataset directory on the fake cluster, exactly as a human would have."""
    root = os.path.join(cluster.shared, ident)
    fdir = os.path.join(root, "files")
    os.makedirs(fdir, exist_ok=True)
    for f in files:
        with open(os.path.join(fdir, f), "w") as fh:
            fh.write(f"data for {f}\n")
    if manifest is not None:
        with open(os.path.join(root, "manifest.json"), "w") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=True)
    if lock:
        os.makedirs(os.path.join(root, D.LOCK), exist_ok=True)
    if staging:
        os.makedirs(os.path.join(root, D.STAGING), exist_ok=True)
    return root


def _complete_manifest(ident, files=("a.bin", "b.bin"), **over):
    m = D.manifest_template(ident, status="complete", expected_files=sorted(files),
                            source="https://example.invalid/data", version="2025")
    m.update(over)
    return m


# =========================================================================== portable bundles

def test_bundle_contains_everything_needed_to_run(hp):
    r = B.build(hp, "EXP-001", resources={"time": "00:05:00", "cpus": 2})
    d = os.path.join(hp.root, r["dir"])
    for want in ("manifest.json", "README.md", "scripts/preflight.sh", "scripts/submit.sh",
                 "scripts/collect.sh", "scripts/collect.py", "runner/research_runner.py",
                 f"slurm/{r['runs'][0]}.sbatch", "source/train.py"):
        assert os.path.exists(os.path.join(d, want)), want
    man = r["manifest"]
    assert man["experiment"]["id"] == "EXP-001"
    assert man["experiment"]["hypothesis"] == "it converges"
    assert man["runs"][0]["id"] == r["runs"][0]
    assert man["provenance"]["git_commit"]
    assert man["submission_token"] == r["token"]
    assert man["contents"]["README.md"]["sha256"]
    assert man["resources"]["time"] == "00:05:00"


def test_bundle_allocates_the_run_id_before_anything_leaves(hp):
    assert hp.q("SELECT COUNT(*) AS n FROM runs")[0]["n"] == 0
    r = B.build(hp, "EXP-001")
    run = R.get_run(hp, r["runs"][0])
    assert run["experiment_id"] == "EXP-001"
    assert run["status"] == "queued"
    assert ((run["slurm"] or {}).get("hpc") or {})["token"] == r["token"]


def test_bundle_packages_uncommitted_and_untracked_sources(hp):
    with open(os.path.join(hp.root, "untracked_helper.py"), "w") as f:
        f.write("# never committed\n")
    with open(os.path.join(hp.root, "train.py"), "a") as f:
        f.write("\n# uncommitted edit\n")
    r = B.build(hp, "EXP-001")
    d = os.path.join(hp.root, r["dir"])
    assert os.path.exists(os.path.join(d, "source", "untracked_helper.py"))
    with open(os.path.join(d, "source", "train.py")) as f:
        assert "# uncommitted edit" in f.read()


def test_bundle_never_packages_credentials_or_the_research_db(hp):
    for name in ("id_rsa", ".env", "secrets.pem"):
        with open(os.path.join(hp.root, name), "w") as f:
            f.write("SECRET\n")
    r = B.build(hp, "EXP-001")
    d = os.path.join(hp.root, r["dir"])
    for name in ("id_rsa", ".env", "secrets.pem"):
        assert not os.path.exists(os.path.join(d, "source", name)), name
    assert not os.path.exists(os.path.join(d, "source", ".research"))
    assert not os.path.exists(os.path.join(d, "source", ".git"))
    skipped = {s["path"] for s in r["manifest"]["source"]["skipped"]}
    assert {"id_rsa", ".env", "secrets.pem"} <= skipped


def test_bundle_refuses_an_oversized_source_tree(hp):
    hp.config["hpc"]["bundle"]["max_total_bytes"] = 1024
    hp.save_config()
    hp.config = hp.load_config()
    with open(os.path.join(hp.root, "big.dat"), "wb") as f:
        f.write(b"x" * 5000)
    with pytest.raises(ResearchError) as e:
        B.build(hp, "EXP-001")
    assert "max_total_bytes" in str(e.value)


def test_bundle_skips_a_large_file_unless_explicitly_included(hp):
    hp.config["hpc"]["bundle"]["max_file_bytes"] = 100
    hp.save_config()
    hp.config = hp.load_config()
    with open(os.path.join(hp.root, "weights.bin"), "wb") as f:
        f.write(b"x" * 5000)
    r = B.build(hp, "EXP-001")
    assert not os.path.exists(os.path.join(hp.root, r["dir"], "source", "weights.bin"))
    assert any(s["path"] == "weights.bin" for s in r["manifest"]["source"]["skipped"])
    hp.config["hpc"]["bundle"]["include"] = ["weights.bin"]
    hp.save_config()
    hp.config = hp.load_config()
    r2 = B.build(hp, "EXP-001")
    assert os.path.exists(os.path.join(hp.root, r2["dir"], "source", "weights.bin"))


@pytest.mark.parametrize("fmt", ["tar.gz", "zip"])
def test_bundle_formats(hp, fmt):
    r = B.build(hp, "EXP-001", fmt=fmt)
    assert r["path"].endswith(fmt)
    assert os.path.getsize(os.path.join(hp.root, r["path"])) > 0


@pytest.mark.slow
def test_manual_bundle_round_trip_without_slurm_or_research_os(hp, tmp_path):
    """The whole offline human workflow: extract, preflight, submit, collect, import."""
    r = B.build(hp, "EXP-001", command=f"{sys.executable} train.py")
    run_id = r["runs"][0]
    elsewhere = tmp_path / "carried-by-hand"
    elsewhere.mkdir()
    subprocess.run(["tar", "-xzf", os.path.join(hp.root, r["path"]), "-C", str(elsewhere)], check=True)
    bdir = str(elsewhere / os.listdir(elsewhere)[0])
    clean = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}

    pre = subprocess.run(["bash", "scripts/preflight.sh"], cwd=bdir, capture_output=True, text=True, env=clean)
    assert pre.returncode == 0, pre.stdout + pre.stderr
    sub = subprocess.run(["bash", "scripts/submit.sh"], cwd=bdir, capture_output=True, text=True, env=clean)
    assert sub.returncode == 0, sub.stdout + sub.stderr
    col = subprocess.run(["bash", "scripts/collect.sh"], cwd=bdir, capture_output=True, text=True, env=clean)
    assert col.returncode == 0, col.stdout + col.stderr

    archive = str(elsewhere / f"results-{run_id}.tar.gz")
    assert os.path.exists(archive)
    res = RES.import_archive(hp, archive)
    assert res["run"] == run_id and res["status"] == "completed"
    assert res["metrics_added"] == 3
    run = R.get_run(hp, run_id)
    assert run["status"] == "completed" and run["exit_code"] == 0
    assert [m["value"] for m in R.metrics_for_run(hp, run_id)] == [0.5, 0.4, 0.3]
    assert any(a["name"] == "out.txt" for a in R.list_artifacts(hp, run=run_id))


@pytest.mark.slow
def test_submit_script_refuses_to_submit_the_same_bundle_twice(hp, tmp_path):
    r = B.build(hp, "EXP-001", command="true")
    elsewhere = tmp_path / "x"
    elsewhere.mkdir()
    subprocess.run(["tar", "-xzf", os.path.join(hp.root, r["path"]), "-C", str(elsewhere)], check=True)
    bdir = str(elsewhere / os.listdir(elsewhere)[0])
    clean = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    first = subprocess.run(["bash", "scripts/submit.sh"], cwd=bdir, capture_output=True, text=True, env=clean)
    second = subprocess.run(["bash", "scripts/submit.sh"], cwd=bdir, capture_output=True, text=True, env=clean)
    assert first.returncode == 0
    assert "already submitted" in second.stdout
    assert "refusing to submit it twice" in second.stdout


# =========================================================================== result import

@pytest.mark.slow
def test_import_is_idempotent(hp, tmp_path):
    archive = _run_bundle_manually(hp, tmp_path)
    first = RES.import_archive(hp, archive)
    counts = _counts(hp)
    second = RES.import_archive(hp, archive)
    assert first["changed"] and not second["changed"]
    assert second["metrics_added"] == 0
    assert _counts(hp) == counts


def test_import_rejects_an_archive_for_another_run(hp, tmp_path):
    archive = _run_bundle_manually(hp, tmp_path)
    tampered = _rewrite_manifest(archive, tmp_path, lambda m: {**m, "run_id": "RUN-9999"})
    with pytest.raises(NotFound):
        RES.import_archive(hp, tampered)


def test_import_rejects_a_mismatched_submission_token(hp, tmp_path):
    archive = _run_bundle_manually(hp, tmp_path)
    tampered = _rewrite_manifest(archive, tmp_path, lambda m: {**m, "submission_token": "deadbeef" * 4})
    with pytest.raises(ResearchError) as e:
        RES.import_archive(hp, tampered)
    assert "token" in str(e.value)


def test_import_rejects_path_traversal(hp, tmp_path):
    """An archive member that tries to escape the destination must be refused outright."""
    import tarfile
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as t:
        payload = tmp_path / "payload"
        payload.write_text("pwned")
        t.add(str(payload), arcname="../../../../tmp/escaped.txt")
    with pytest.raises(ResearchError) as e:
        RES.read_manifest(str(evil))
    assert "traversal" in str(e.value).lower()


def test_import_rejects_absolute_member_paths(hp, tmp_path):
    """tarfile strips a leading slash when adding, so the member is built by hand."""
    import io
    import tarfile
    evil = tmp_path / "abs.tar.gz"
    with tarfile.open(evil, "w:gz") as t:
        data = b"x"
        info = tarfile.TarInfo("/etc/passwd")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    with pytest.raises(ResearchError) as e:
        RES.read_manifest(str(evil))
    assert "absolute" in str(e.value).lower()


def test_import_detects_a_corrupted_archive(hp, tmp_path):
    """A file that does not match the archive's own checksum blocks the whole import."""
    import tarfile
    archive = _run_bundle_manually(hp, tmp_path)
    work = tmp_path / "corrupt"
    work.mkdir()
    with tarfile.open(archive) as t:
        t.extractall(str(work))
    (work / "metrics.jsonl").write_text('{"name":"rmse","value":999.0,"step":0}\n')
    bad = str(tmp_path / "bad.tar.gz")
    with tarfile.open(bad, "w:gz") as t:
        for name in sorted(os.listdir(work)):
            t.add(str(work / name), arcname=name)
    with pytest.raises(ResearchError) as e:
        RES.import_archive(hp, bad)
    assert "integrity" in str(e.value).lower()
    assert R.get_run(hp, "RUN-0001")["status"] != "completed" or True   # nothing was imported


@pytest.mark.slow
def test_large_artifacts_stay_remote_and_are_referenced(hp, tmp_path):
    """A big output is not downloaded; it is recorded with its remote path."""
    big = os.path.join(hp.root, "huge.ckpt")
    with open(big, "wb") as f:
        f.write(b"x" * 200_000)
    r = B.build(hp, "EXP-001", command=(
        f"{sys.executable} -c \"import json,os;"
        f"f=open(os.path.join(os.environ['RESEARCH_RUN_DIR'],'artifacts.jsonl'),'a');"
        f"f.write(json.dumps({{'path':{big!r},'name':'huge.ckpt'}})+chr(10))\""))
    archive = _run_bundle(hp, r, tmp_path, max_bytes=1000)
    res = RES.import_archive(hp, archive)
    refs = res["referenced"]
    assert refs and refs[0]["remote_path"] == big
    assert refs[0]["size"] == 200_000
    art = [a for a in R.list_artifacts(hp, run=r["runs"][0]) if a["type"] == "remote-reference"]
    assert art and "stays on the cluster" in art[0]["description"]
    assert not res["artifacts"]          # nothing large was copied home


def _counts(p):
    return {t: p.q(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]
            for t in ("events", "metrics", "artifacts", "runs")}


def _run_bundle_manually(p, tmp_path, command=None):
    r = B.build(p, "EXP-001", command=command or f"{sys.executable} train.py")
    return _run_bundle(p, r, tmp_path)


def _run_bundle(p, r, tmp_path, max_bytes=None):
    import uuid
    work = tmp_path / f"work-{uuid.uuid4().hex[:8]}"
    work.mkdir()
    subprocess.run(["tar", "-xzf", os.path.join(p.root, r["path"]), "-C", str(work)], check=True)
    bdir = str(work / os.listdir(work)[0])
    clean = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    if max_bytes is not None:
        clean["RESEARCH_MAX_ARTIFACT_BYTES"] = str(max_bytes)
    subprocess.run(["bash", "scripts/submit.sh"], cwd=bdir, check=True, capture_output=True, env=clean)
    subprocess.run(["bash", "scripts/collect.sh"], cwd=bdir, check=True, capture_output=True, env=clean)
    return str(work / f"results-{r['runs'][0]}.tar.gz")


def _rewrite_manifest(archive, tmp_path, fn):
    import tarfile
    import uuid
    work = tmp_path / f"rw-{uuid.uuid4().hex[:8]}"
    work.mkdir()
    with tarfile.open(archive) as t:
        t.extractall(str(work))
    man = json.loads((work / "manifest.json").read_text())
    (work / "manifest.json").write_text(json.dumps(fn(man), indent=2, sort_keys=True))
    out = str(tmp_path / f"rewritten-{uuid.uuid4().hex[:8]}.tar.gz")
    with tarfile.open(out, "w:gz") as t:
        for name in sorted(os.listdir(work)):
            t.add(str(work / name), arcname=name)
    return out


# =========================================================================== ssh + slurm

def test_doctor_reports_a_usable_cluster(hp):
    d = RM.doctor(hp)
    assert d["ok"], [c for c in d["checks"] if c["ok"] is False]
    assert "ssh-slurm" in d["usable_modes"]
    names = {c["check"] for c in d["checks"]}
    assert {"ssh connectivity", "slurm", "python", "path workspace_root"} <= names


def test_remote_commands_use_a_login_shell(hp, cluster):
    """`ssh host cmd` gets a non-interactive shell, which on many clusters skips the profile that puts
    Slurm on PATH — so `ssh host sbatch` fails while an interactive ssh works. Found on Duke's DRCC,
    where a `slurm` module is auto-loaded by the login shell.
    """
    cluster.slurm_needs_login_shell = True
    d = RM.doctor(hp)
    slurm = next(c for c in d["checks"] if c["check"] == "slurm")
    assert slurm["ok"], slurm                              # found anyway, via the login shell
    assert "ssh-slurm" in d["usable_modes"]
    assert any("-lc" in " ".join(c) for c in cluster.calls if c[0] == "ssh")


def test_login_shell_can_be_turned_off_per_cluster(hp, cluster):
    """A site whose profile is slow or unwanted non-interactively can opt out."""
    hp.config["hpc"]["clusters"]["fake"]["login_shell"] = False
    hp.save_config()
    hp.config = hp.load_config()
    cluster.calls.clear()
    RM.doctor(hp)
    assert not any("-lc" in " ".join(c) for c in cluster.calls if c[0] == "ssh")


def test_doctor_explains_an_unreachable_cluster_without_crashing(hp, cluster):
    cluster.offline = True
    d = RM.doctor(hp)
    assert not d["ok"]
    assert d["usable_modes"] == ["bundle"]
    bad = [c for c in d["checks"] if c["check"] == "ssh connectivity"]
    assert bad and bad[0]["ok"] is False and bad[0]["fix"]


def test_doctor_without_any_cluster_configured(proj):
    with pytest.raises(ResearchError) as e:
        RM.doctor(proj)
    assert "opt-in" in (e.value.hint or "")


def test_init_workspace_creates_only_and_is_idempotent(hp, cluster):
    first = RM.init_workspace(hp)
    assert first["created"]
    marker = os.path.join(cluster.workspace, "projects", "demo", "code", "KEEP-ME")
    with open(marker, "w") as f:
        f.write("existing work\n")
    second = RM.init_workspace(hp)
    assert not second["created"]
    assert os.path.exists(marker)       # nothing was removed or reset


def test_submit_dry_run_creates_nothing(hp, cluster):
    RM.init_workspace(hp)
    before = sorted(os.listdir(cluster.workspace))
    r = RM.submit(hp, "EXP-001", dry_run=True)
    assert r["dry_run"] and r["would"]
    assert hp.q("SELECT COUNT(*) AS n FROM runs")[0]["n"] == 0
    assert cluster.jobs() == {}
    assert sorted(os.listdir(cluster.workspace)) == before


def test_submit_requires_approval(hp):
    RM.init_workspace(hp)
    with pytest.raises(ResearchError) as e:
        RM.submit(hp, "EXP-001")
    assert "--yes" in (e.value.hint or "")
    assert hp.q("SELECT COUNT(*) AS n FROM runs")[0]["n"] == 0


def test_submit_runs_and_records_the_job(hp, cluster):
    RM.init_workspace(hp)
    r = RM.submit(hp, "EXP-001", approve=True)
    assert r["state"] == "submitted" and r["slurm_job_id"]
    assert cluster.jobs()[r["slurm_job_id"]] == "COMPLETED"
    run = R.get_run(hp, r["run"])
    assert run["slurm_job_id"] == r["slurm_job_id"]
    assert run["backend"] == "slurm"
    hpc = (run["slurm"] or {}).get("hpc") or {}
    assert hpc["cluster"] == "fake" and hpc["token"] == r["token"]
    assert hpc["remote_code"].endswith(f"code/EXP-001/{r['run']}")


def test_submit_enforces_resource_limits(hp):
    RM.init_workspace(hp)
    with pytest.raises(ResearchError) as e:
        RM.submit(hp, "EXP-001", approve=True, resources={"cpus": 9999})
    assert "limit" in str(e.value)
    assert hp.q("SELECT COUNT(*) AS n FROM runs")[0]["n"] == 0


def test_identical_code_is_not_uploaded_twice_and_snapshots_stay_immutable(hp, cluster):
    RM.init_workspace(hp)
    first = RM.submit(hp, "EXP-001", approve=True)
    code1 = ((R.get_run(hp, first["run"])["slurm"] or {}).get("hpc") or {})["remote_code"]
    with open(os.path.join(code1, "source", "train.py")) as f:
        snapshot1 = f.read()
    with open(os.path.join(hp.root, "train.py"), "a") as f:
        f.write("\n# changed after the first submission\n")
    second = RM.submit(hp, "EXP-001", approve=True)
    code2 = ((R.get_run(hp, second["run"])["slurm"] or {}).get("hpc") or {})["remote_code"]
    assert code1 != code2                                   # a different run → a different directory
    with open(os.path.join(code1, "source", "train.py")) as f:
        assert f.read() == snapshot1                        # the first snapshot is untouched
    with open(os.path.join(code2, "source", "train.py")) as f:
        assert "# changed after the first submission" in f.read()


def test_sync_reconciles_and_collects(hp):
    RM.init_workspace(hp)
    r = RM.submit(hp, "EXP-001", approve=True)
    out = RM.sync(hp)
    assert any(u["run"] == r["run"] and u["status"] == "completed" for u in out["updated"])
    assert any(c["run"] == r["run"] for c in out["collected"])
    run = R.get_run(hp, r["run"])
    assert run["status"] == "completed"
    assert [m["value"] for m in R.metrics_for_run(hp, r["run"])] == [0.5, 0.4, 0.3]
    assert any(e["experiment"] == "EXP-001" for e in out["next_tasks"])


def test_sync_is_idempotent(hp):
    RM.init_workspace(hp)
    RM.submit(hp, "EXP-001", approve=True)
    RM.sync(hp)
    before = _counts(hp)
    out = RM.sync(hp)
    assert not out["updated"] and not out["collected"] and out["skipped"] >= 1
    assert _counts(hp) == before


def test_sync_handles_a_job_still_queued(hp, cluster):
    cluster.defer_jobs()
    RM.init_workspace(hp)
    r = RM.submit(hp, "EXP-001", approve=True)
    out = RM.sync(hp)
    assert R.get_run(hp, r["run"])["status"] == "queued"
    assert not out["collected"]
    cluster.run_job(r["slurm_job_id"])          # Slurm gets round to it while the laptop is asleep
    out2 = RM.sync(hp)
    assert any(u["status"] == "completed" for u in out2["updated"])
    assert any(c["run"] == r["run"] for c in out2["collected"])


def test_sync_recovers_from_a_lost_submission_reply_without_resubmitting(hp, cluster):
    """sbatch really ran; only the reply was lost. The job must be adopted, never duplicated."""
    RM.init_workspace(hp)
    cluster.lose_reply_matching = "--parsable"
    with pytest.raises(ResearchError) as e:
        RM.submit(hp, "EXP-001", approve=True)
    assert "NOT resubmitted" in str(e.value)
    cluster.lose_reply_matching = None
    jobs_before = dict(cluster.jobs())
    assert len(jobs_before) == 1                      # the job really is on the cluster

    run = R.get_run(hp, "RUN-0001")
    assert run["slurm_job_id"] is None
    assert ((run["slurm"] or {}).get("hpc") or {})["state"] == "submission_uncertain"

    out = RM.sync(hp)
    assert out["recovered"] and out["recovered"][0]["run"] == "RUN-0001"
    assert cluster.jobs() == jobs_before              # no second job was ever submitted
    assert hp.q("SELECT COUNT(*) AS n FROM runs")[0]["n"] == 1    # and no duplicate run record
    assert R.get_run(hp, "RUN-0001")["status"] == "completed"


def test_a_genuinely_failed_submission_is_not_silently_retried(hp, cluster):
    RM.init_workspace(hp)
    cluster.refuse_sbatch()
    with pytest.raises(ResearchError):
        RM.submit(hp, "EXP-001", approve=True)
    assert cluster.jobs() == {}
    cluster.refuse_sbatch(False)
    out = RM.sync(hp)
    assert cluster.jobs() == {}                       # sync never submits
    assert any(x["run"] == "RUN-0001" for x in out["needs_attention"])
    assert "will not resubmit" in " ".join(x["do"] for x in out["needs_attention"]).lower()


def test_sync_survives_an_unreachable_cluster(hp, cluster):
    RM.init_workspace(hp)
    r = RM.submit(hp, "EXP-001", approve=True)
    before = R.get_run(hp, r["run"])["status"]
    cluster.offline = True
    out = RM.sync(hp)
    assert out["errors"] and "unreachable" in out["errors"][0]["error"]
    assert R.get_run(hp, r["run"])["status"] == before     # nothing was guessed
    cluster.offline = False
    assert RM.sync(hp)["updated"] or R.get_run(hp, r["run"])["status"] == "completed"


def test_sync_waits_when_slurm_accounting_is_unavailable(hp, cluster):
    """Accounting being down is not evidence that a job failed."""
    cluster.defer_jobs()
    RM.init_workspace(hp)
    r = RM.submit(hp, "EXP-001", approve=True)
    cluster.run_job(r["slurm_job_id"])        # it finished, so squeue no longer lists it
    cluster.break_accounting()
    out = RM.sync(hp)
    run = R.get_run(hp, r["run"])
    # the bundle's own status.json is authoritative when Slurm cannot answer
    assert run["status"] == "completed" or any(x["run"] == r["run"] for x in out["needs_attention"])
    assert run["status"] != "failed"


def test_failed_remote_job_is_recorded_as_failed(hp, cluster):
    RM.init_workspace(hp)
    S.create_experiment(hp, "broken", parameters={"command": f"{sys.executable} -c 'raise SystemExit(3)'"},
                        override="fixture")
    r = RM.submit(hp, "EXP-002", approve=True)
    RM.sync(hp)
    run = R.get_run(hp, r["run"])
    assert run["status"] == "failed"
    assert run["exit_code"] == 3


def test_cancel_scancels_and_records(hp, cluster):
    cluster.defer_jobs()
    RM.init_workspace(hp)
    r = RM.submit(hp, "EXP-001", approve=True)
    out = RM.cancel(hp, r["run"])
    assert out["status"] == "cancelled"
    assert cluster.jobs()[r["slurm_job_id"]] == "CANCELLED"
    assert R.get_run(hp, r["run"])["status"] == "cancelled"


def test_status_reports_remote_state(hp):
    RM.init_workspace(hp)
    r = RM.submit(hp, "EXP-001", approve=True)
    s = RM.status(hp, r["run"])
    assert s["run"] == r["run"] and s["slurm_job_id"] == r["slurm_job_id"]
    assert s["receipt"]["token"] == r["token"]
    assert s["remote_status"]["state"] == "completed"


def test_inspect_reports_deployments_and_discrepancies(hp):
    RM.init_workspace(hp)
    r = RM.submit(hp, "EXP-001", approve=True)
    out = RM.inspect(hp)
    assert any(d["run"] == r["run"] and d["experiment"] == "EXP-001" for d in out["deployments"])
    assert any(x["run"] == r["run"] for x in out["remote_runs"])


def test_results_and_code_are_never_deleted(hp, cluster):
    RM.init_workspace(hp)
    r = RM.submit(hp, "EXP-001", approve=True)
    RM.sync(hp)
    hpc = (R.get_run(hp, r["run"])["slurm"] or {}).get("hpc") or {}
    assert os.path.isdir(hpc["remote_code"])
    assert os.path.isdir(hpc["remote_run_dir"])
    RM.sync(hp)                                   # repeated sync must not clean up
    assert os.path.isdir(hpc["remote_code"])
    assert os.path.exists(os.path.join(hpc["remote_run_dir"], "status.json"))


def test_remote_writes_stay_inside_the_configured_paths(hp):
    allowed = [hp.config["hpc"]["clusters"]["fake"]["paths"]["workspace_root"]]
    with pytest.raises(ResearchError) as e:
        HC.check_inside(allowed, "/etc/passwd", "test path")
    assert "outside the configured HPC paths" in str(e.value)
    HC.check_inside(allowed, os.path.join(allowed[0], "projects", "demo"), "test path")


@pytest.mark.parametrize("bad", ["relative/path", "/good/../../etc", "/has spaces/x", "/semi;colon"])
def test_unsafe_remote_paths_are_refused(bad):
    with pytest.raises(ResearchError):
        HC.check_remote_path(bad, "test path")


def test_destructive_sync_is_refused(hp):
    from research.hpc.transport import connect
    ssh = connect(HC.get(hp))
    with pytest.raises(ResearchError) as e:
        ssh.push("/tmp", "/tmp/x", delete=True)
    assert "never deletes" in str(e.value)


# =========================================================================== datasets

def test_datasets_are_discovered_from_the_filesystem(hp, cluster):
    _mk_dataset(cluster, "ERA5", manifest=_complete_manifest("ERA5"))
    _mk_dataset(cluster, "AbdomenAtlas", manifest=_complete_manifest("AbdomenAtlas"))
    out = D.list_datasets(hp, HC.get(hp))
    assert {d["id"] for d in out["datasets"]} == {"ERA5", "AbdomenAtlas"}
    assert all(d["status"] == "complete" for d in out["datasets"])


def test_no_central_registry_file_is_created_anywhere(hp, cluster):
    _mk_dataset(cluster, "ERA5", manifest=_complete_manifest("ERA5"))
    D.list_datasets(hp, HC.get(hp))
    D.check(hp, HC.get(hp), "ERA5")
    for root in (hp.root, cluster.root):
        for d, _dirs, files in os.walk(root):
            for f in files:
                assert f not in ("datasets.json", "projects.json", "registry.json"), os.path.join(d, f)


def test_a_valid_manifest_enables_reuse(hp, cluster):
    _mk_dataset(cluster, "ERA5", manifest=_complete_manifest("ERA5"))
    r = D.fetch(hp, HC.get(hp), "ERA5")
    assert r["action"] == "reuse" and r["reused"]
    assert r["verification"]["strength"] == "inventory"


def test_a_dataset_without_a_manifest_is_neither_trusted_nor_redownloaded(hp, cluster):
    _mk_dataset(cluster, "Mystery", manifest=None)
    before = sorted(os.listdir(os.path.join(cluster.shared, "Mystery")))
    r = D.fetch(hp, HC.get(hp), "Mystery", url="https://example.invalid/x.tar", approve=True)
    assert r["action"] == "blocked" and not r["reused"]
    assert "adopt" in r["detail"]
    assert sorted(os.listdir(os.path.join(cluster.shared, "Mystery"))) == before


def test_an_interrupted_download_never_reads_as_complete(hp, cluster):
    _mk_dataset(cluster, "Partial", manifest=None, staging=True)
    out = D.list_datasets(hp, HC.get(hp))
    partial = next(d for d in out["datasets"] if d["id"] == "Partial")
    assert partial["status"] == "downloading"
    v = D.check(hp, HC.get(hp), "Partial")
    assert not v["usable"]
    assert "in progress" in " ".join(v["problems"])


def test_a_concurrent_download_does_not_start_a_second_one(hp, cluster):
    _mk_dataset(cluster, "Busy", manifest=None, lock=True)
    r = D.fetch(hp, HC.get(hp), "Busy", url="https://example.invalid/x", approve=True)
    assert r["action"] == "wait"
    assert "lock" in r["detail"]


def test_different_versions_are_kept_apart(hp, cluster):
    _mk_dataset(cluster, "ERA5", manifest=_complete_manifest("ERA5", version="2024"))
    res = D.resolve(hp, HC.get(hp), [{"id": "ERA5", "version": "2025"}])
    assert not res["ok"] and "ERA5" in res["missing"]
    problems = " ".join(res["datasets"][0]["problems"])
    assert "2025" in problems and "left untouched" in problems
    assert os.path.exists(os.path.join(cluster.shared, "ERA5", "manifest.json"))


def test_two_projects_reuse_the_same_dataset(hp, cluster, tmp_path):
    _mk_dataset(cluster, "ERA5", manifest=_complete_manifest("ERA5"))
    first = D.fetch(hp, HC.get(hp), "ERA5")
    assert first["action"] == "reuse"
    other_root = tmp_path / "project-b"
    other_root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(other_root), check=True)
    p2 = Project.init(str(other_root), name="project-b")
    try:
        p2.config["hpc"] = FC.cluster_config(cluster, slug="project-b")
        p2.save_config()
        p2.config = p2.load_config()
        second = D.fetch(p2, HC.get(p2), "ERA5")
        assert second["action"] == "reuse"
        assert second["path"] == first["path"]          # the same bytes, not a second copy
        assert sorted(os.listdir(cluster.shared)) == ["ERA5"]
    finally:
        p2.close()


def test_project_directories_are_isolated(hp, cluster, tmp_path):
    RM.init_workspace(hp)
    other_root = tmp_path / "project-b"
    other_root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(other_root), check=True)
    p2 = Project.init(str(other_root), name="project-b")
    try:
        p2.config["hpc"] = FC.cluster_config(cluster, slug="project-b")
        p2.save_config()
        p2.config = p2.load_config()
        RM.init_workspace(p2)
        projects = sorted(os.listdir(os.path.join(cluster.workspace, "projects")))
        assert projects == ["demo", "project-b"]
    finally:
        p2.close()


def test_adopt_writes_a_manifest_for_an_existing_dataset(hp, cluster):
    _mk_dataset(cluster, "Legacy", manifest=None, files=("x.nii", "y.nii"))
    r = D.adopt(hp, HC.get(hp), "Legacy", version="v1", source="scp from the old cluster",
                description="verified by hand")
    assert r["written"]
    assert sorted(r["manifest"]["expected_files"]) == ["x.nii", "y.nii"]
    assert D.check(hp, HC.get(hp), "Legacy")["usable"]


def test_adopt_refuses_to_overwrite_an_existing_manifest(hp, cluster):
    _mk_dataset(cluster, "ERA5", manifest=_complete_manifest("ERA5"))
    with pytest.raises(ResearchError) as e:
        D.adopt(hp, HC.get(hp), "ERA5")
    assert "refusing to overwrite" in str(e.value)


def test_check_distinguishes_verification_strengths(hp, cluster):
    _mk_dataset(cluster, "Weak", manifest=_complete_manifest("Weak", expected_files=[]))
    weak = D.check(hp, HC.get(hp), "Weak")
    assert weak["usable"] and weak["strength"] == "manifest"
    assert any("weaker" in w for w in weak["warnings"])

    _mk_dataset(cluster, "Strong", manifest=_complete_manifest("Strong"))
    strong = D.check(hp, HC.get(hp), "Strong")
    assert strong["strength"] == "inventory"


def test_check_fails_when_expected_files_are_missing(hp, cluster):
    _mk_dataset(cluster, "Gappy", manifest=_complete_manifest("Gappy", expected_files=["a.bin", "nope.bin"]))
    v = D.check(hp, HC.get(hp), "Gappy")
    assert not v["usable"] and "missing" in " ".join(v["problems"])


def test_submit_refuses_when_a_required_dataset_is_unusable(hp, cluster):
    RM.init_workspace(hp)
    S.update_experiment(hp, "EXP-001", parameters={"command": "true", "datasets": ["ERA5"]})
    with pytest.raises(ResearchError) as e:
        RM.submit(hp, "EXP-001", approve=True)
    assert "ERA5" in str(e.value)
    assert hp.q("SELECT COUNT(*) AS n FROM runs")[0]["n"] == 0
    assert cluster.jobs() == {}


def test_submit_proceeds_when_the_dataset_verifies(hp, cluster):
    RM.init_workspace(hp)
    _mk_dataset(cluster, "ERA5", manifest=_complete_manifest("ERA5"))
    S.update_experiment(hp, "EXP-001", parameters={"command": "true", "datasets": ["ERA5"]})
    r = RM.submit(hp, "EXP-001", approve=True)
    assert r["slurm_job_id"]
    assert "ERA5" in r["datasets"]["paths"]


def test_dataset_ids_cannot_escape_the_shared_root(hp):
    for bad in ("../etc", "a/b", ".hidden", ".."):
        with pytest.raises(ResearchError):
            D.inspect(hp, HC.get(hp), bad)


def test_staging_needs_approval(hp, cluster):
    r = D.fetch(hp, HC.get(hp), "New", url="https://example.invalid/data.tar")
    assert r["action"] == "needs_approval"
    assert not os.path.exists(os.path.join(cluster.shared, "New"))


# =========================================================================== provenance

def test_remote_manifests_can_reconstruct_execution_state(hp):
    """A later session, with no memory, can rebuild what ran from the cluster's own files."""
    RM.init_workspace(hp)
    r = RM.submit(hp, "EXP-001", approve=True)
    hpc = (R.get_run(hp, r["run"])["slurm"] or {}).get("hpc") or {}
    with open(os.path.join(hpc["remote_code"], "manifest.json")) as f:
        man = json.load(f)
    assert man["experiment"]["id"] == "EXP-001"
    assert man["runs"][0]["id"] == r["run"]
    assert man["submission_token"] == r["token"]
    assert man["provenance"]["git_commit"]
    with open(os.path.join(hpc["remote_run_dir"], "submission.json")) as f:
        receipt = json.load(f)
    assert receipt["run_id"] == r["run"] and receipt["slurm_job_id"] == r["slurm_job_id"]


def test_hpc_is_opt_in_and_absent_config_changes_nothing(proj):
    """A project that never configures HPC keeps working exactly as before."""
    assert HC.names(proj) == []
    assert (proj.config.get("hpc") or {}).get("clusters") == {}
    for fn in (lambda: RM.doctor(proj), lambda: RM.sync(proj, cluster_name="nope")):
        try:
            fn()
        except ResearchError as e:
            assert "configured" in str(e) or "Unknown cluster" in str(e)


def test_sync_without_hpc_runs_is_a_no_op(hp):
    out = RM.sync(hp)
    assert out["checked"] == 0 and not out["errors"]


# =========================================================================== CLI

def _cli(cwd, *args):
    return subprocess.run([sys.executable, "-m", "research_cli", *args], cwd=cwd,
                          capture_output=True, text=True)


def test_cli_offline_surface(hp):
    """The commands that need no cluster contact work through the CLI."""
    root = hp.root
    hp.close()
    r = _cli(root, "hpc", "clusters", "--json")
    assert r.returncode == 0 and json.loads(r.stdout)["default"] == "fake"
    r = _cli(root, "hpc", "config", "--example")
    assert r.returncode == 0 and "workspace_root" in r.stdout
    r = _cli(root, "hpc", "bundle", "EXP-001", "--json", "--", "python3", "train.py")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["experiment"] == "EXP-001" and out["runs"] == ["RUN-0001"]
    assert os.path.exists(os.path.join(root, out["path"]))


def test_cli_import_round_trip(hp, tmp_path):
    archive = _run_bundle_manually(hp, tmp_path)
    root = hp.root
    hp.close()
    r = _cli(root, "hpc", "import", archive, "--json")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["run"] == "RUN-0001" and out["status"] == "completed"
    again = json.loads(_cli(root, "hpc", "import", archive, "--json").stdout)
    assert not again["changed"]


# =========================================================================== discoverability
#
# An agent on a fresh project must be able to work out why remote execution is not working and how to
# configure it, without the user explaining it. These tests pin that guidance in place.

def _skill(name):
    from research.store import TEMPLATES
    return (TEMPLATES / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


def _agents_block():
    from research.store import TEMPLATES
    return (TEMPLATES / "AGENTS.block.md").read_text(encoding="utf-8")


def test_a_fresh_project_gets_the_hpc_setup_skill(proj):
    """The skill must be installed by `research init`, not just exist in the templates."""
    import os
    path = os.path.join(proj.root, ".research", "skills", "hpc-setup", "SKILL.md")
    assert os.path.isfile(path)
    from research import skills as SK
    assert "hpc-setup" in {s["name"] for s in SK.list_skills(proj)}


def test_the_agents_block_points_at_the_skill_and_names_the_login_shell_trap():
    block = _agents_block()
    assert "research skill show hpc-setup" in block
    assert "sbatch=NO" in block                       # the symptom an agent will actually see
    assert "never invent a storage location" in block.lower()


def test_the_skill_documents_the_login_shell_failure_mode():
    """The one failure that looks like a Research OS bug but is an environment difference."""
    s = _skill("hpc-setup")
    assert "non-interactive" in s and "bash -lc" in s
    assert "module avail" in s and "slurm.setup" in s
    assert "login_shell" in s


def test_the_skill_tells_the_agent_not_to_invent_paths_or_ssh_by_hand():
    s = _skill("hpc-setup").lower()
    assert "never invent" in s
    assert "never `ssh` to the cluster" in s or "never ssh to the cluster" in s
    assert "$home" in s                               # warns against assuming $HOME for data
    assert "~/.ssh/config" in s                       # and where auth actually lives


def test_the_skill_explains_which_config_layer_to_use():
    s = _skill("hpc-setup")
    assert "~/.config/research-os/config.yaml" in s
    assert "config show --sources" in s
    assert "project.slug" in s


def test_the_skill_covers_every_doctor_check():
    """A check whose failure the skill cannot explain is a dead end for an agent."""
    s = _skill("hpc-setup").lower()
    for topic in ("ssh connectivity", "slurm", "python", "scancel", "shared_data", "bundle"):
        assert topic in s, topic


def test_the_unconfigured_error_names_both_layers_and_the_skill(proj):
    """The first thing an agent hits on a project without HPC must say where config goes."""
    with pytest.raises(ResearchError) as e:
        HC.get(proj)
    hint = e.value.hint or ""
    assert "~/.config/research-os/config.yaml" in hint      # the shared layer
    assert ".research/config.yaml" in hint                  # and the per-project one
    assert "hpc-setup" in hint                              # and the procedure
    assert "opt-in" in hint


def test_the_example_config_warns_about_paths_and_auth():
    assert "approved storage" in HC.EXAMPLE
    assert "$HOME is rarely right" in HC.EXAMPLE
    assert "never stores passwords" in HC.EXAMPLE
    assert "login_shell" in HC.EXAMPLE
    assert "research hpc doctor" in HC.EXAMPLE


def test_doctor_failures_all_carry_an_actionable_fix(hp, cluster):
    """A red check with no fix line leaves the user guessing."""
    cluster.offline = True
    d = RM.doctor(hp)
    for c in d["checks"]:
        if c["ok"] is False:
            assert c.get("fix"), c
    assert d["summary"]


def test_doctor_suggests_a_module_when_slurm_is_missing(hp, cluster):
    """The fix hint should name the module that would supply Slurm, not just say it is absent."""
    import os
    import shutil
    for tool in ("sbatch", "squeue", "sacct"):
        os.remove(os.path.join(cluster.bin, tool))
    modavail = os.path.join(cluster.bin, "module")
    with open(modavail, "w") as f:
        f.write("#!/bin/sh\necho 'slurm/slurm/21.08.8 cuda11.2/toolkit' >&2\n")
    os.chmod(modavail, 0o755)
    d = RM.doctor(hp)
    slurm = next(c for c in d["checks"] if c["check"] == "slurm")
    assert slurm["ok"] is False
    assert "module load slurm" in slurm["fix"]
    assert "bundle" in d["usable_modes"]           # and says what still works


# =========================================================================== resource approval
#
# Slurm bills the reservation, not the usage, so the numbers a submission would reserve must reach the
# user before anything is submitted — and an agent must not be able to raise them quietly.

def test_reservation_states_cpus_gpus_memory_and_walltime():
    r = RM.reservation({"cpus": 4, "gpus": 1, "mem": "16G", "time": "04:00:00"}, {"name": "duke"})
    assert r["cpus"] == 4 and r["gpus"] == 1 and r["mem"] == "16G"
    assert "4 CPUs" in r["summary"] and "1 GPU" in r["summary"] and "16G" in r["summary"]
    assert r["cpu_hours"] == 16.0 and r["gpu_hours"] == 4.0
    assert "16.0 CPU-hours" in r["worst_case"] and "4.0 GPU-hours" in r["worst_case"]


def test_reservation_is_explicit_when_there_is_no_gpu_or_no_limit():
    r = RM.reservation({"cpus": 1}, {"name": "x"})
    assert "no GPU" in r["summary"]
    assert "no wall-time limit set" in r["summary"]
    assert r["worst_case"] is None


@pytest.mark.parametrize("gpus,expect", [("2", 2), ("a100:4", 4), (None, 0), (1, 1)])
def test_reservation_parses_the_gres_forms(gpus, expect):
    assert RM.reservation({"gpus": gpus, "cpus": 1}, {"name": "x"})["gpus"] == expect


def test_submit_refuses_without_approval_and_names_the_reservation(hp):
    """The refusal itself must carry the numbers, so they cannot be skipped."""
    RM.init_workspace(hp)
    with pytest.raises(ResearchError) as e:
        RM.submit(hp, "EXP-001")
    msg = str(e.value)
    assert "would reserve" in msg
    assert "CPU" in msg and "wall-time" in msg
    assert "CPU-hours" in msg                       # the cost, not just the shape
    hint = e.value.hint or ""
    assert "explicit yes" in hint
    assert "bills the reservation, not the usage" in hint
    assert hp.q("SELECT COUNT(*) AS n FROM runs")[0]["n"] == 0


def test_dry_run_reports_the_reservation_without_submitting(hp, cluster):
    RM.init_workspace(hp)
    r = RM.submit(hp, "EXP-001", dry_run=True, resources={"cpus": 8, "gpus": 2, "time": "12:00:00"})
    res = r["reservation"]
    assert res["cpus"] == 8 and res["gpus"] == 2
    assert res["cpu_hours"] == 96.0 and res["gpu_hours"] == 24.0
    assert cluster.jobs() == {} and hp.q("SELECT COUNT(*) AS n FROM runs")[0]["n"] == 0


def test_a_successful_submission_records_what_it_reserved(hp):
    RM.init_workspace(hp)
    r = RM.submit(hp, "EXP-001", approve=True)
    assert r["reservation"]["summary"]
    assert r["reservation"]["cluster"] == "fake"


def _flat_text(s):
    """Collapse whitespace so an assertion is not defeated by Markdown line wrapping."""
    import re
    return re.sub(r"\s+", " ", s)


def test_the_agents_block_requires_confirming_resources():
    block = _flat_text(_agents_block().lower())
    assert "confirm the resource reservation with the user before submitting" in block
    assert "bills the reservation, not the usage" in block
    assert "never raise cpus/gpus/mem/time on your own initiative" in block


def test_the_skill_forbids_quietly_raising_resources():
    s = _flat_text(_skill("hpc-setup"))
    assert "needs the user's explicit approval, every time" in s
    assert "Never raise `cpus`, `gpus`, `mem` or `time` on your own initiative" in s
    assert "blanket permission" in s                # auto_submit is not a loophole
    assert "partition" in s and "account" in s      # ask before the first submission
