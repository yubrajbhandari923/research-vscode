import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "packages", "core"))
sys.path.insert(0, os.path.join(ROOT, "packages", "cli"))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "RESEARCH_AGENT", "RESEARCH_AGENT_MODEL", "RESEARCH_AUTHOR_TYPE",
              "RESEARCH_ROOT", "RESEARCH_RUN_DIR", "RESEARCH_RUN_ID", "RESEARCH_IN_JOB", "CODEX_SANDBOX",
              "CODEX_SANDBOX_NETWORK_DISABLED"):
        monkeypatch.delenv(k, raising=False)
    env_pp = os.pathsep.join([os.path.join(ROOT, "packages", "core"), os.path.join(ROOT, "packages", "cli")])
    monkeypatch.setenv("PYTHONPATH", env_pp + os.pathsep + os.environ.get("PYTHONPATH", ""))


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


@pytest.fixture
def repo(tmp_path, monkeypatch):
    d = tmp_path / "proj"
    d.mkdir()
    git(d, "init", "-q", "-b", "main")
    (d / "train.py").write_text("print('hi')\n")
    git(d, "add", ".")
    git(d, "commit", "-qm", "init")
    monkeypatch.chdir(d)
    return d


@pytest.fixture
def proj(repo):
    from research.store import Project
    p = Project.init(str(repo), name="demo", goal="Understand alpha")
    yield p
    p.close()
