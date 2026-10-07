"""Project-local CLI at .research/bin/research: created by init, runnable without install or PATH, refreshed by stamp."""
import json
import os
import subprocess
import sys

import pytest

from research import localbin
from research import mcp as MCP

from conftest import git


def _bare_env(tmp_path):
    # no `research` on PATH, no PYTHONPATH: what an agent sandbox (e.g. Claude Code's Bash tool) looks like
    return {"HOME": str(tmp_path), "PATH": os.path.dirname(sys.executable) + os.pathsep + "/usr/bin:/bin"}


@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher")
def test_init_creates_runnable_gitignored_launcher(proj, tmp_path):
    exe = os.path.join(proj.root, ".research", "bin", "research")
    assert os.access(exe, os.X_OK)
    assert os.path.exists(os.path.join(proj.root, ".research", "bin", "research.cmd"))
    assert os.path.exists(os.path.join(proj.root, ".research", "bin", "lib", "research_cli", "__main__.py"))
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


def test_rpc_install_bin_refreshes_outdated_agents_block(proj):
    from research.rpc import Server
    path = os.path.join(proj.root, "AGENTS.md")
    with open(path, "w") as f:
        f.write("# mine\n\n<!-- research:begin — old -->\nold block\n<!-- research:end -->\n\nkeep me\n")
    r = Server(proj.root).m_install_bin()
    text = open(path).read()
    assert ".research/bin/research" in text and "keep me" in text and "# mine" in text
    assert r.get("agents_md")
