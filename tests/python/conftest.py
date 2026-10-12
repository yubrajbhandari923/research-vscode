import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "packages", "core"))
sys.path.insert(0, os.path.join(ROOT, "packages", "cli"))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path_factory):
    for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "RESEARCH_AGENT", "RESEARCH_AGENT_MODEL", "RESEARCH_AUTHOR_TYPE",
              "RESEARCH_ROOT", "RESEARCH_RUN_DIR", "RESEARCH_RUN_ID", "RESEARCH_IN_JOB", "CODEX_SANDBOX",
              "CODEX_SANDBOX_NETWORK_DISABLED", "RESEARCH_CONFIG"):
        monkeypatch.delenv(k, raising=False)
    # Point the machine-level config layer at an empty directory. Without this, the developer's own
    # ~/.config/research-os/config.yaml leaks into every test — a real cluster profile there makes
    # "no HPC configured" tests fail. A test that wants the layer sets XDG_CONFIG_HOME itself.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg-empty")))
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


# --------------------------------------------------------------------------- shared project templates
#
# Building a realistic project costs seconds (git calls, SQLite writes, mirror rendering), and the big
# fixtures were being rebuilt for every test that used them: ~14s x 26 tests in test_fullreport alone.
# A template is built once per session and copied per test instead, which is a directory copy.

def clone_project(template: str, dest: str):
    """Copy a built project template to `dest` and open it. Each test gets its own mutable copy."""
    import shutil
    from research.store import Project
    shutil.copytree(template, dest, symlinks=True)
    # research.db records absolute paths in `mirrors`; rebuilding from the text files is the supported
    # way to relocate a project, and is far cheaper than recreating the records.
    p = Project(dest, sync=False)
    p.rebuild_index(backup=False)
    return p


@pytest.fixture(scope="session")
def _template_root(tmp_path_factory):
    return tmp_path_factory.mktemp("templates")


@pytest.fixture(scope="session")
def built_template(_template_root):
    """Build a named project template once per session.

    Usage in a test module:

        @pytest.fixture
        def rich(built_template, tmp_path):
            return clone_project(built_template("rich", _build_rich), str(tmp_path / "proj"))
    """
    import subprocess as _sp
    cache = {}

    def build(name, builder):
        if name in cache:
            return cache[name]
        d = os.path.join(str(_template_root), name)
        os.makedirs(d)
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        _sp.run(["git", "init", "-q", "-b", "main"], cwd=d, check=True, env=env)
        with open(os.path.join(d, "train.py"), "w") as f:
            f.write("print('hi')\n")
        _sp.run(["git", "add", "."], cwd=d, check=True, env=env)
        _sp.run(["git", "commit", "-qm", "init"], cwd=d, check=True, env=env)
        cwd = os.getcwd()
        os.chdir(d)
        try:
            from research.store import Project
            p = Project.init(d, name=name, goal=f"template: {name}")
            try:
                builder(p)
            finally:
                p.close()
        finally:
            os.chdir(cwd)
        cache[name] = d
        return d
    return build
