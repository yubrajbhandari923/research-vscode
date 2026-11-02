"""Initialize Project: empty folder -> main/ + ledger/ + experiments/ + workspace file, one git repository."""
import json
import os
import subprocess

import pytest

from research import initproject as IP, plans, providers, services
from research import workspace as W
from research.store import Project

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def g(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, env={**os.environ, **GIT_ENV}).stdout


@pytest.fixture
def made(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "sparse-ct"
    root.mkdir()
    (root / "stray.txt").write_text("pre-existing, must not be committed")
    spec = {"name": "sparse-ct", "goal": "find alpha", "agents": {"enabled": ["claude", "agy"], "default": "claude"}}
    pl = IP.plan(str(root), spec)
    assert pl["ok"], pl["problems"]
    return root, spec, IP.apply(str(root), spec)


def test_layout_one_repo_and_root_of_main(made):
    root, _, r = made
    assert sorted(os.listdir(root)) == ["experiments", "ledger", "main", "sparse-ct.code-workspace", "stray.txt"]
    assert os.listdir(root / "experiments") == []
    common = {os.path.realpath(g(d, "rev-parse", "--git-common-dir").strip() if os.path.isabs(g(d, "rev-parse", "--git-common-dir").strip())
                               else os.path.join(d, g(d, "rev-parse", "--git-common-dir").strip())) for d in (root / "main", root / "ledger")}
    assert len(common) == 1
    files = g(root / "main", "ls-tree", "-r", "--name-only", "main").split()
    assert {"README.md", ".gitignore", "AGENTS.md", "CLAUDE.md"} <= set(files) and not any(f.startswith("main/") for f in files)
    assert "stray.txt" not in files
    assert (root / "ledger" / ".research" / "config.yaml").exists() and not (root / "main" / ".research").exists()
    assert (root / "main" / "CLAUDE.md").read_text().strip() == "@AGENTS.md"


def test_workspace_file_and_project_resolves_from_every_root(made):
    root, _, r = made
    ws = json.load(open(r["workspace_file"]))
    assert [f["path"] for f in ws["folders"]] == ["main", "ledger", "experiments"] and ws["settings"]["research.projectRoot"] == "."
    for d in ("main", "ledger"):
        with Project(str(root / d)) as p:
            assert p.name == "sparse-ct" and p.config["layout"]["experiments_dir"] == "experiments"
            assert p.config["agents"]["default_profile"] == "claude"


def test_new_experiments_use_configured_root_and_mcp_reachable_from_worktree(made):
    root, _, _ = made
    with Project(str(root / "main")) as p:
        e = services.create_experiment(p, "e", hypothesis="h", question=services.create_question(p, "q")["id"])
        ws = W.create(p, e["id"])
    assert os.path.dirname(ws["path"]) == str(root / "experiments")
    assert (root / "main" / ".agents" / "mcp_config.json").exists()
    v = providers.mcp_verify(str(root / "main"), ws["path"])
    assert v["ok"], v


def test_refuses_nonempty_targets_and_bad_input(tmp_path):
    root = tmp_path / "p"
    (root / "main").mkdir(parents=True)
    (root / "main" / "x").write_text("1")
    assert any("not empty" in x for x in IP.plan(str(root), {"name": "p"})["problems"])
    assert not IP.plan(str(tmp_path), {"name": ""})["ok"]
    assert not IP.plan(str(tmp_path / "q"), {"name": "q", "agents": {"enabled": ["claude"], "default": "codex"}})["ok"]
    assert not IP.plan(str(tmp_path / "q"), {"name": "q", "hpc": {"enabled": True}})["ok"]


def test_hpc_job_environment_is_saved_as_slurm_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "hp"
    root.mkdir()
    spec = {"name": "hp", "hpc": {"enabled": True, "ssh_host": "clu", "workspace_root": "/w", "setup": "module load python/3.11\n\n source ~/v/bin/activate "}}
    assert IP.plan(str(root), spec)["ok"]
    IP.apply(str(root), spec)
    with Project(str(root / "main")) as p:
        assert p.config["hpc"]["clusters"]["clu"]["slurm"]["setup"] == ["module load python/3.11", "source ~/v/bin/activate"]
