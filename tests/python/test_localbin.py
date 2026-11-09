"""Project-local CLI at .research/bin/research: created by init, runnable without install or PATH, refreshed by stamp."""
import json
import os
import subprocess
import sys

import pytest

from research import localbin
from research import mcp as MCP

from conftest import git
from conftest import env_for
from research import workspace as W
from research.store import Project
from test_approval import cli  # noqa: F401  (fixture)


def _bare_env(tmp_path):
    # no `research` on PATH, no PYTHONPATH: what an agent sandbox (e.g. Claude Code's Bash tool) looks like
    return {"HOME": str(tmp_path), "PATH": os.path.dirname(sys.executable) + os.pathsep + "/usr/bin:/bin"}


@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher")
def test_init_creates_runnable_gitignored_launcher(proj, tmp_path):
    exe = os.path.join(proj.root, ".research", "bin", "research")
    assert os.access(exe, os.X_OK)
    assert os.path.exists(os.path.join(proj.root, ".research", "bin", "research.cmd"))
    assert not os.path.exists(os.path.join(proj.root, ".research", "bin", "lib"))     # nothing vendored
    git(proj.root, "check-ignore", ".research/bin/research")  # raises if not ignored
    sub = os.path.join(proj.root, "sub")
    os.makedirs(sub)
    r = subprocess.run([exe, "--json", "question", "create", "From the local CLI?"], cwd=sub, env=_bare_env(tmp_path),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["id"] == "Q-001"


@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher")
def test_run_exec_through_launcher_can_import_research(proj, tmp_path):
    exe = os.path.join(proj.root, ".research", "bin", "research")
    env = _bare_env(tmp_path)
    with open(os.path.join(proj.root, "uses_api.py"), "w") as f:
        f.write("import research\nresearch.log_metric('loss', 0.5)\n")
    assert subprocess.run([exe, "experiment", "create", "smoke"], cwd=proj.root, env=env, capture_output=True).returncode == 0
    r = subprocess.run([exe, "run", "exec", "EXP-001", "--", sys.executable, "uses_api.py"], cwd=proj.root, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_install_is_noop_when_current_and_refreshes_when_forced(proj):
    first = localbin.install(proj.root)
    assert first["updated"] is False  # init already installed it
    assert localbin.install(proj.root, force=True)["updated"] is True
    assert localbin.status(proj.root)["stamp"] == first["stamp"]


def test_existing_gitignore_gets_bin_line_once(proj):
    gi = os.path.join(proj.root, ".research", ".gitignore")
    with open(gi, "w") as f:
        f.write("research.db\n")
    localbin.install(proj.root, force=True)
    localbin.install(proj.root, force=True)
    assert open(gi).read().count("bin/") == 1


def test_mcp_install_uses_launcher_when_research_not_on_path(proj, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    r = MCP.install(proj.root, ["claude"])
    cfg = json.load(open(os.path.join(proj.root, ".mcp.json")))
    assert cfg["mcpServers"]["research"]["command"] == localbin.launcher(proj.root)
    assert r["command"].endswith("mcp")


def test_write_agents_md_refreshes_outdated_block(proj):
    path = os.path.join(proj.root, "AGENTS.md")
    with open(path, "w") as f:
        f.write("# mine\n\n<!-- research:begin — old -->\nold block\n<!-- research:end -->\n\nkeep me\n")
    assert proj.write_agents_md()
    text = open(path).read()
    assert ".research/bin/research" in text and "keep me" in text and "# mine" in text and "old block" not in text


@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher")
def test_launcher_execs_the_recorded_interpreter_and_replaces_old_vendored_copy(proj, tmp_path):
    """The launcher runs whatever interpreter was recorded (so a self-update is followed), and a missing one fails clearly."""
    bd = os.path.join(proj.root, ".research", "bin")
    os.makedirs(os.path.join(bd, "lib"), exist_ok=True)           # a 3.0-style vendored copy
    fake = tmp_path / "py"
    fake.write_text('#!/bin/sh\necho "ran:$@"\n')
    fake.chmod(0o755)
    assert localbin.install(proj.root, python=str(fake))["updated"] is True
    assert not os.path.exists(os.path.join(bd, "lib"))
    r = subprocess.run([os.path.join(bd, "research"), "version"], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
    assert r.stdout.strip() == "ran:-m research_cli version"
    assert localbin.install(proj.root, python=str(fake))["updated"] is False
    fake.unlink()
    r = subprocess.run([os.path.join(bd, "research"), "version"], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 127 and "is gone" in r.stderr
    from research import machine
    assert os.path.realpath(proj.root) in machine.known_projects()


# ------------------------------------------------------------------------------------------------ ./research in main
def test_install_links_research_into_main_runs_from_there_and_stays_out_of_git(cli):
    link = os.path.join(cli["repo"], "research")
    assert os.path.islink(link) and os.path.realpath(link) == os.path.realpath(cli["bin"])
    r = subprocess.run(["./research", "approval", "status"], cwd=cli["repo"], env=env_for(cli["xdg"]), capture_output=True, text=True)
    assert r.returncode == 0 and "PIN" in r.stdout, r.stderr
    r = subprocess.run(["./research", "plan", "status", "demo-plan"], cwd=cli["repo"], env=env_for(cli["xdg"]), capture_output=True, text=True)
    assert r.returncode == 0 and "demo-plan" in r.stdout, "it resolves the project from main, through the link"
    status = subprocess.run(["git", "status", "--porcelain"], cwd=cli["repo"], capture_output=True, text=True).stdout
    assert "research\n" not in status.replace("research.pre-ledger", ""), "the link is git-excluded, never committed"


def test_link_never_replaces_real_files_and_unlink_removes_only_its_own_link(cli):
    os.remove(os.path.join(cli["repo"], "research"))
    os.mkdir(os.path.join(cli["repo"], "research"))                     # the project has its own `research/` package
    r = localbin.install(cli["repo"], force=True)                       # installing must still succeed; only the convenience link is skipped
    assert r["link"]["created"] is False and "already exists" in r["link"]["error"] and os.path.isdir(os.path.join(cli["repo"], "research"))
    r = subprocess.run([cli["bin"], "bin", "link"], cwd=cli["repo"], env=env_for(cli["xdg"]), capture_output=True, text=True)
    assert r.returncode != 0 and "already exists" in r.stdout + r.stderr and os.path.isdir(os.path.join(cli["repo"], "research"))
    r = subprocess.run([cli["bin"], "bin", "link", "--name", "rs"], cwd=cli["repo"], env=env_for(cli["xdg"]), capture_output=True, text=True)
    assert r.returncode == 0 and os.path.islink(os.path.join(cli["repo"], "rs"))
    subprocess.run([cli["bin"], "bin", "unlink", "--name", "rs"], cwd=cli["repo"], env=env_for(cli["xdg"]), capture_output=True)
    assert not os.path.lexists(os.path.join(cli["repo"], "rs")) and os.path.isdir(os.path.join(cli["repo"], "research")), "a real directory is untouched"


def test_no_link_inside_experiment_worktrees_and_the_launcher_template_is_symlink_safe(cli, tmp_path):
    p = Project(cli["repo"])
    e = p.list("experiment")[0] if p.list("experiment") else None
    try:
        if e:
            ws = W.ensure_for_experiment(p, e["id"])
            assert localbin.link(ws["path"]) is None
    finally:
        p.close()
    assert "$0" not in open(cli["bin"]).read()                          # never locates itself by path, so a symlink cannot confuse it
    os.symlink(cli["bin"], tmp_path / "elsewhere")                      # a link from any directory, relative cwd
    r = subprocess.run([str(tmp_path / "elsewhere"), "approval", "status"], cwd=cli["repo"], env=env_for(cli["xdg"]), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_opening_a_project_without_a_launcher_restores_it(proj, monkeypatch):
    root = proj.root
    from research import daemonlib, notify, rpc
    monkeypatch.setattr(daemonlib, "ensure_current", lambda root: None)
    monkeypatch.setattr(notify, "ensure_running", lambda p: None)
    path = localbin.launcher(root)
    os.remove(path)
    assert localbin.status(root)["exists"] is False
    rpc._on_open(root)
    assert localbin.status(root)["exists"] is True
