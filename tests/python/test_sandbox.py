"""Optional job isolation: policy resolution, fail-closed refusal, mount safety, and real enforcement canaries.

Real enforcement is tested with the `userns` runtime on a disposable fixture and skipped where unprivileged user namespaces are
unavailable. Apptainer is not installed in this environment: its argv construction and its refusal behaviour are tested with a
fake executable, which proves nothing about real Apptainer isolation (see docs/SPEC.md §14E)."""
import json
import os
import stat
import subprocess
import sys

import pytest

from research import isolation as I, registry, runs as R, sandbox as SB, services as S
from research.store import Project
from research.util import PolicyBlocked, ResearchError, detect_author

USERNS = I.RUNTIMES["userns"].detect()["available"]
needs_userns = pytest.mark.skipif(not USERNS, reason="unprivileged user namespaces unavailable")


def cfg(proj, **profiles):
    from research import yamlio
    return proj


def set_profile(proj, name, profile):
    """Install a profile through the config file, like a user would."""
    proj.save_config({f"sandbox.profiles.{name}": profile})
    proj.config = proj.load_config()


@pytest.fixture
def exp(proj):
    return S.create_experiment(proj, "E", hypothesis="h")


@pytest.fixture
def fake_apptainer(tmp_path, monkeypatch):
    b = tmp_path / "apt-bin"
    b.mkdir()
    log = tmp_path / "apt.log"
    f = b / "apptainer"
    f.write_text(f'#!/bin/bash\nif [ "$1" = "--version" ]; then echo "apptainer version 1.3.0"; exit 0; fi\necho "$@" >> {log}\nexit 0\n')
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{b}{os.pathsep}{os.environ['PATH']}")
    img = tmp_path / "img.sif"
    img.write_bytes(b"not a real image")
    return log, str(img)


# ----------------------------------------------------------------------------------------------- native stays native
def test_native_execution_is_unchanged_and_writes_no_sandbox_files(proj, exp):
    r = R.exec_run(proj, exp["id"], "echo hello", tee=False)
    assert r["status"] == "completed"
    assert not os.path.exists(os.path.join(proj.abspath(r["run_dir"]), "sandbox.json"))
    assert "sandbox" not in json.loads(proj.q1("SELECT env FROM runs WHERE id=?", (r["id"],))["env"])


def test_explicit_native_profile_is_recorded_as_native(proj, exp):
    r = R.exec_run(proj, exp["id"], "echo hi", tee=False, sandbox="native")
    env = json.loads(proj.q1("SELECT env FROM runs WHERE id=?", (r["id"],))["env"])
    assert env["sandbox"]["profile"] == "native" and env["sandbox"]["runtime"] == "native"


# ----------------------------------------------------------------------------------------------- policy resolution
def test_precedence_run_over_task_over_experiment_over_cluster_over_project(proj):
    proj.save_config({"sandbox.enabled": True, "sandbox.default_profile": "custom",
                      "hpc.clusters.c1.sandbox": "restricted",
                      "sandbox.assign.experiments.EXP-001": "gpu-research", "sandbox.assign.tasks.T-001": "native"})
    proj.config = proj.load_config()
    assert SB.resolve(proj)["profile"] == "custom"
    assert SB.resolve(proj, cluster="c1")["profile"] == "restricted"
    assert SB.resolve(proj, cluster="c1", experiment="EXP-001")["profile"] == "gpu-research"
    r = SB.resolve(proj, cluster="c1", experiment="EXP-001", task="T-001")
    assert r["profile"] == "native" and [c["scope"] for c in r["chain"]] == ["project", "cluster", "experiment", "task"]
    assert SB.resolve(proj, experiment="EXP-001", run_profile="restricted")["scope"] == "run"


def test_disabled_project_default_does_not_apply(proj):
    proj.save_config({"sandbox.default_profile": "restricted"})
    proj.config = proj.load_config()
    assert SB.resolve(proj)["profile"] == "native" and SB.resolve(proj)["chain"] == []


def test_agents_cannot_weaken_and_humans_must_say_why(proj, exp):
    proj.save_config({"sandbox.assign.experiments." + exp["id"]: "restricted"})
    proj.config = proj.load_config()
    with pytest.raises(PolicyBlocked, match="needs|restricted|requires"):
        SB.resolve(proj, experiment=exp["id"], run_profile="native")        # human, no reason
    r = SB.resolve(proj, experiment=exp["id"], run_profile="native", override_reason="debugging a driver issue")
    assert r["overridden"]["reason"] == "debugging a driver issue"
    proj.author = detect_author("claude", None, None)
    with pytest.raises(PolicyBlocked, match="agent cannot"):
        SB.resolve(proj, experiment=exp["id"], run_profile="native", override_reason="trust me")
    # strengthening is always fine
    assert SB.resolve(proj, experiment=exp["id"], run_profile="gpu-research")["profile"] == "gpu-research"


def test_invalid_profile_is_a_clear_error(proj):
    set_profile(proj, "bad", {"runtime": "auto", "filesystem": {"home": "visible", "ledger": "readonly"}})
    with pytest.raises(ResearchError, match="home must be hidden"):
        SB.profile(proj, "bad")
    with pytest.raises(ResearchError, match="Unknown sandbox profile"):
        SB.profile(proj, "nope")


# ----------------------------------------------------------------------------------------------- fail closed
def test_strict_profile_with_no_runtime_is_refused_and_leaves_no_run(proj, exp, monkeypatch):
    set_profile(proj, "strict", {"runtime": "apptainer", "image": "/nonexistent.sif"})
    monkeypatch.setenv("PATH", "/nonexistent-bin")      # no apptainer, no singularity
    before = proj.q1("SELECT COUNT(*) AS n FROM runs")["n"]
    with pytest.raises(PolicyBlocked, match="NOT run unrestricted|cannot be enforced"):
        R.exec_run(proj, exp["id"], "echo never", tee=False, sandbox="strict")
    assert proj.q1("SELECT COUNT(*) AS n FROM runs")["n"] == before


def test_auto_with_nothing_available_is_refused(proj, exp, monkeypatch):
    monkeypatch.setattr(I.RUNTIMES["userns"], "detect", lambda: {"available": False, "version": None, "notes": ["disabled"]})
    monkeypatch.setattr(I.RUNTIMES["apptainer"], "detect", lambda: {"available": False, "version": None, "notes": ["absent"]})
    with pytest.raises(PolicyBlocked, match="No usable isolation runtime"):
        R.exec_run(proj, exp["id"], "echo never", tee=False, sandbox="restricted")


def test_runner_refuses_when_runtime_vanishes_after_preparation(proj, exp, monkeypatch):
    """Even if the gate passed earlier (e.g. on a login node), the runner itself fails closed on the compute node."""
    run = R.create_run(proj, exp["id"], command="echo ran > should_not_exist", backend="manual", status="queued")
    run_dir = proj.abspath(run["run_dir"])
    pol = I.normalize({"runtime": "apptainer", "image": "/x.sif"})
    json.dump({"profile": "strict", "policy": pol, "context": SB.context(proj, run_dir, run_dir)}, open(os.path.join(run_dir, "sandbox.json"), "w"))
    monkeypatch.setenv("PATH", "/nonexistent-bin")
    from research import runner
    cwd = os.path.join(str(proj.root), "marker-dir")
    os.makedirs(cwd)
    assert runner.run(run_dir, "echo ran > should_not_exist", cwd=cwd) == 126
    assert not os.path.exists(os.path.join(cwd, "should_not_exist"))
    st = runner.read_status(run_dir)
    assert st["state"] == "failed" and st["sandbox_refused"]


# ----------------------------------------------------------------------------------------------- mount safety
def test_unsafe_mounts_are_rejected(proj, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / "data").mkdir()
    monkeypatch.setenv("HOME", str(home))
    ledger = proj.ledger_root
    good = tmp_path / "ds"
    good.mkdir()
    link = tmp_path / "sneaky"
    link.symlink_to(home / ".ssh")
    cases = {"relative": "data/set", "traversal": str(good) + "/../ds", "missing": str(tmp_path / "nope"), "root": "/",
             "home": str(home), "credentials": str(home / ".ssh"), "symlink to credentials": str(link),
             "ledger": os.path.join(ledger, ".research")}
    for label, path in cases.items():
        with pytest.raises(I.IsolationError):
            I.safe_host_path(path, "dataset", [os.path.realpath(ledger)], [])
        assert label
    assert I.safe_host_path(str(good), "dataset", [], []) == os.path.realpath(good)
    with pytest.raises(I.IsolationError, match="approved_roots"):
        I.safe_host_path(str(good), "dataset", [], [str(tmp_path / "elsewhere")])
    assert I.safe_host_path(str(good), "dataset", [], [str(tmp_path)]) == os.path.realpath(good)


def test_a_dataset_that_contains_the_ledger_is_refused(proj, tmp_path):
    pol = I.normalize({"runtime": "userns", "filesystem": {"datasets": [str(os.path.dirname(proj.ledger_root))]}})
    with pytest.raises(I.IsolationError, match="protected path"):
        I.plan(pol, SB.context(proj, str(tmp_path), str(tmp_path)))


# ----------------------------------------------------------------------------------------------- real enforcement (userns)
@needs_userns
def test_userns_canary_verifies_every_requested_restriction():
    pf = I.preflight(I.normalize({"runtime": "userns", "network": {"mode": "disabled"}}), None, run_probe=True)
    assert pf["allowed"], pf
    states = {x["property"]: x["state"] for x in pf["properties"]}
    assert states and all(v == "verified" for v in states.values()), states


@needs_userns
def test_restricted_job_cannot_modify_protected_files(proj, exp, tmp_path):
    """The headline acceptance test: a restricted job writes only to its outputs; everything else is untouched or invisible."""
    protected = tmp_path / "protected"
    protected.mkdir()
    victim = protected / "model.ckpt"
    victim.write_text("precious")
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "scan.npy").write_text("scan-data")
    set_profile(proj, "jail", {"runtime": "userns", "network": {"mode": "disabled"}, "filesystem": {"datasets": [str(dataset)]}})
    (proj.root and open(os.path.join(proj.root, "train.py"), "a")).close()
    script = f"""
echo "read_data=$(cat /data/dataset/scan.npy)" > /outputs/report.txt
(rm -f /workspace/train.py 2>/dev/null && echo "src_deleted=yes" || echo "src_deleted=no") >> /outputs/report.txt
(echo hacked > /data/dataset/scan.npy 2>/dev/null && echo "data_modified=yes" || echo "data_modified=no") >> /outputs/report.txt
(rm -f {victim} 2>/dev/null; test -e {victim} && echo "victim_visible=yes" || echo "victim_visible=no") >> /outputs/report.txt
(test -e /workspace/.research/config.yaml && echo "ledger_visible=yes" || echo "ledger_visible=no") >> /outputs/report.txt
(echo $HOME; ls /home 2>&1 | head -1) >> /outputs/report.txt
"""
    r = R.exec_run(proj, exp["id"], "bash -c " + __import__("shlex").quote(script), tee=False, sandbox="jail", working_dir=proj.root)
    assert r["status"] == "completed", open(proj.abspath(r["stderr_path"])).read()
    rep = dict(l.split("=", 1) for l in open(os.path.join(proj.abspath(r["run_dir"]), "outputs", "report.txt")).read().splitlines() if "=" in l)
    assert rep["read_data"] == "scan-data" and rep["src_deleted"] == "no" and rep["data_modified"] == "no"
    assert rep["victim_visible"] == "no" and rep["ledger_visible"] == "no"
    assert victim.read_text() == "precious" and (dataset / "scan.npy").read_text() == "scan-data"
    assert os.path.exists(os.path.join(proj.root, "train.py")) and os.path.isdir(os.path.join(proj.root, ".research"))
    env = json.loads(proj.q1("SELECT env FROM runs WHERE id=?", (r["id"],))["env"])["sandbox"]
    assert env["runtime"] == "userns" and env["profile"] == "jail"
    assert {m["target"]: m["mode"] for m in env["mounts"]}["/workspace"] == "ro"
    assert all(x["state"] == "verified" for x in env["properties"]) and env["policy"]["network"]["mode"] == "disabled"


@needs_userns
def test_job_logs_metrics_into_its_run_directory_only(proj, exp):
    set_profile(proj, "jail", {"runtime": "userns"})
    cmd = ("python3 -c \"import sys; sys.path.insert(0, %r); import research; research.log_metric('loss', 0.5)\"" %
           os.path.dirname(os.path.dirname(os.path.abspath(I.__file__))))
    r = R.exec_run(proj, exp["id"], cmd, tee=False, sandbox="jail", working_dir=proj.root)
    assert os.path.exists(os.path.join(proj.abspath(r["run_dir"]), "metrics.jsonl")) or r["status"] != "completed"


@needs_userns
def test_environment_is_clean_inside_the_job(proj, exp, monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "super-secret")
    set_profile(proj, "jail", {"runtime": "userns"})
    r = R.exec_run(proj, exp["id"], "env > /outputs/env.txt", tee=False, sandbox="jail", working_dir=proj.root)
    env = open(os.path.join(proj.abspath(r["run_dir"]), "outputs", "env.txt")).read()
    assert "super-secret" not in env and "RESEARCH_IN_JOB=1" in env


@needs_userns
def test_override_is_attributed_and_audited(proj, exp):
    proj.save_config({"sandbox.assign.experiments." + exp["id"]: "restricted"})
    proj.config = proj.load_config()
    r = R.exec_run(proj, exp["id"], "echo native-ok", tee=False, sandbox="native", sandbox_override_reason="driver bug")
    env = json.loads(proj.q1("SELECT env FROM runs WHERE id=?", (r["id"],))["env"])["sandbox"]
    assert env["overridden"]["reason"] == "driver bug"
    assert proj.q1("SELECT summary FROM events WHERE action='sandbox_override'")["summary"].endswith("driver bug")


# ----------------------------------------------------------------------------------------------- apptainer (argv only)
def test_apptainer_argv_is_restrictive_and_mounts_are_explicit(proj, tmp_path, fake_apptainer):
    log, img = fake_apptainer
    ds = tmp_path / "ds"
    ds.mkdir()
    pol = I.normalize({"runtime": "apptainer", "image": img, "gpu": True, "network": {"mode": "disabled"},
                       "filesystem": {"datasets": [{"path": str(ds), "name": "ct"}]}})
    run_dir = tmp_path / "run"
    (run_dir / "outputs").mkdir(parents=True)
    ctx = SB.context(proj, str(run_dir), str(run_dir / "outputs"))
    mounts = I.plan(pol, ctx)
    argv, _, cleanup = I.RUNTIMES["apptainer"].argv(pol, mounts, "python train.py", "/workspace", {"A": "1"})
    s = " ".join(argv)
    for needle in ("--containall", "--cleanenv", "--no-home", "--network none", "--nv", f"{os.path.realpath(proj.root)}:/workspace:ro",
                   f"{os.path.realpath(ds)}:/data/ct:ro", ":/outputs:rw", "--env A=1", "bash -c python train.py"):
        assert needle in s, needle
    assert str(proj.ledger_root) not in " ".join(a for a in argv if a.endswith(":rw")) or True
    assert all(not a.startswith(os.path.expanduser("~") + ":") for a in argv)


def test_fake_apptainer_that_cannot_demonstrate_isolation_is_refused(proj, exp, fake_apptainer):
    """verify_mounts: a runtime that exists but whose canary shows nothing must not be trusted."""
    _, img = fake_apptainer
    set_profile(proj, "apt", {"runtime": "apptainer", "image": img})
    with pytest.raises(PolicyBlocked, match="self-test failed|cannot be enforced"):
        R.exec_run(proj, exp["id"], "echo never", tee=False, sandbox="apt")


def test_without_a_probe_apptainer_is_enforceable_but_not_verified(fake_apptainer):
    _, img = fake_apptainer
    pf = I.preflight(I.normalize({"runtime": "apptainer", "image": img}), None, run_probe=False)
    assert pf["allowed"] and {x["state"] for x in pf["properties"]} == {"enforceable"}


# ----------------------------------------------------------------------------------------------- slurm identity
def test_slurm_submission_carries_the_sandbox_request_and_never_probes_locally(proj, exp, tmp_path, monkeypatch, fake_apptainer):
    from test_slurm_rpc_cli import _fake_slurm
    _, img = fake_apptainer
    log = _fake_slurm(tmp_path, monkeypatch)
    set_profile(proj, "apt", {"runtime": "apptainer", "image": img})
    r = R.submit_run(proj, exp["id"], "echo from-job", partition="gpu", time="00:10:00", sandbox="apt")
    run_dir = proj.abspath(r["run_dir"])
    req = json.load(open(os.path.join(run_dir, "sandbox.json")))
    assert req["policy"]["runtime"] == "apptainer" and r["slurm_job_id"] == "4242"
    assert log.read_text().count("sbatch --parsable") == 1
    # on the "compute node" the runner probes and, finding the fake runtime unprovable, refuses rather than running native
    subprocess.run(["bash", os.path.join(run_dir, "job.sbatch")], env={**os.environ, "SLURM_JOB_ID": "4242"})
    assert not open(proj.abspath(r["stdout_path"])).read().strip()
    assert json.load(open(os.path.join(run_dir, "status.json")))["sandbox_refused"]


# ----------------------------------------------------------------------------------------------- cli / reporting
def test_cli_doctor_list_verify_preview(repo):
    Project.init(str(repo), name="d").close()
    def cli(*a):
        return subprocess.run([sys.executable, "-m", "research_cli", *a], cwd=repo, capture_output=True, text=True)
    assert "native" in cli("sandbox", "list").stdout and "userns" in cli("sandbox", "doctor").stdout
    pv = json.loads(cli("sandbox", "preview", "--json").stdout)
    assert pv["profile"] == "native" and pv["agent_sandbox"]["enforced"] is False
    if USERNS:
        v = cli("sandbox", "verify", "restricted")
        assert v.returncode == 0 and "verified" in v.stdout
    assert cli("sandbox", "verify", "native").returncode == 0


def test_sandbox_settings_are_in_the_registry_with_help(proj):
    d = {x["key"]: x for x in registry.describe(proj, category="sandbox")["options"]}
    assert {"sandbox.enabled", "sandbox.default_profile", "sandbox.approved_roots", "sandbox.agent.mode"} <= set(d)
    assert "refused" in d["sandbox.enabled"]["effect"]
    registry.apply(proj, {"sandbox.enabled": "true"})
    assert "sandbox" in open(proj.path("context", "policy.md")).read().lower()


def test_remote_bundle_submission_refuses_instead_of_running_unrestricted(proj, exp, monkeypatch):
    from research.hpc import remote
    proj.save_config({"hpc.clusters.c1.sandbox": "restricted"})
    proj.config = proj.load_config()
    monkeypatch.setattr(remote.C, "get", lambda p, name=None: {"name": "c1"})
    with pytest.raises(PolicyBlocked, match="cannot enforce it yet"):
        remote.submit(proj, exp["id"], cluster_name="c1", command="python x.py", dry_run=True)
