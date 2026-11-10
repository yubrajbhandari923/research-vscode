"""The TUI's Git health page: dirty and untracked files with the reason each matters, worktrees, promotions, and a reviewed commit that asks first."""
import os
import subprocess

import pytest

pytest.importorskip("textual")
import research
from research_cli.tui import ResearchTUI
from research_cli.tui.sections import SECTIONS
from test_tui import BIG, go, text


@pytest.fixture
def gp(tmp_path, monkeypatch):
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    (tmp_path / "train.py").write_text("print(1)\n")
    subprocess.run(["git", "add", "train.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    p = research.init(str(tmp_path), name="G")
    (tmp_path / "train.py").write_text("print(2)\n")
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n")
    (tmp_path / ".env").write_text("TOKEN=1\n")
    yield p, str(tmp_path)
    p.close()


def test_git_health_page_lists_files_reasons_and_never_offers_to_commit_a_secret(gp):
    p, root = gp
    app = ResearchTUI(root)

    async def then(pilot):
        await pilot.press("h")
        await pilot.pause()
        sec = SECTIONS["git"]
        keys = [r[0] for r in sec.rows(app)]
        assert "scaffold" in keys and "f:train.py" in keys and "f:.env" in keys
        assert "experiment worktrees start from the last commit" in sec.detail(app, "f:train.py").plain or "source code" in sec.detail(app, "f:train.py").plain
        assert "will not commit this" in sec.detail(app, "f:.env").plain
        assert [a for a, _ in sec.actions(app, "f:.env")] == ["ignore_secret"]               # a secret can only be ignored, never committed
        assert [a for a, _ in sec.actions(app, "scaffold")] == ["review", "commit"]
    go(app, then)


def test_reviewed_commit_shows_the_diff_asks_and_commits_only_the_file(gp):
    p, root = gp
    app = ResearchTUI(root)

    async def then(pilot):
        await pilot.press("h")
        await pilot.pause()
        sec = SECTIONS["git"]
        sec.rows(app)
        sec.act(app, "f:train.py", "review")
        await pilot.pause()
        assert "print(2)" in text(app, "#git-d")                                          # the staged diff is on screen
        assert subprocess.run(["git", "log", "--oneline"], cwd=root, capture_output=True, text=True).stdout.count("\n") == 1
        sec.act(app, "f:train.py", "commit")
        await pilot.pause()
        await pilot.press("n")                                                            # declined: nothing is committed
        await pilot.pause()
        assert subprocess.run(["git", "log", "--oneline"], cwd=root, capture_output=True, text=True).stdout.count("\n") == 1
        sec.act(app, "f:train.py", "commit")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        log = subprocess.run(["git", "log", "--name-only", "--format=%s", "-1"], cwd=root, capture_output=True, text=True).stdout
        assert "train.py" in log and "CLAUDE.md" not in log and ".env" not in log
        assert "CLAUDE.md" in subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout
    go(app, then)
