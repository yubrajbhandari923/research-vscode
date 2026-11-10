"""Git hygiene: classification, the three-way report, preflight, managed ignores, the reviewed scoped commit, non-interactive failures, dirty-tree recovery."""
import os
import subprocess

import pytest

from conftest import git
from research import githygiene as GH, promotion as PR, services, workspace as W
from research.store import Project
from research.util import PolicyBlocked, ResearchError

HUMAN = {"author_type": "human", "author_name": "alice"}
AGENT = {"author_type": "agent", "author_name": "claude"}


def write(root, name, text="x\n"):
    path = os.path.join(str(root), name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return path


def out(root, *a):
    return subprocess.run(["git", *a], cwd=str(root), capture_output=True, text=True).stdout


@pytest.fixture
def hp(repo):
    """A project in a git repo, with a human author."""
    p = Project.init(str(repo), name="d")
    p.author = dict(HUMAN)
    p.save_config({"governance.approval.require_pin": False})
    p.config = p.load_config()
    yield p
    p.close()


# ------------------------------------------------------------------------------------------------ classification
@pytest.mark.parametrize("path,kind,matters", [
    (".env", "secret", True), ("config/id_rsa", "secret", True), (".env.example", "other", True), ("creds/credentials.json", "secret", True),
    ("__pycache__/x.cpython-312.pyc", "generated", False), ("pkg.egg-info/PKG-INFO", "generated", False), (".venv/bin/python", "generated", False),
    (".research/db.sqlite", "runtime", False), ("AGENTS.md", "scaffold", True), (".claude/settings.json", "scaffold", True),
    ("plans/main.yaml", "plan", True), ("src/model.py", "source", True), ("docs/notes.md", "doc", True), ("reports/final.qmd", "report", True),
    ("outputs/weights.bin", "output", False), ("results/run1/loss.csv", "output", False), ("fig1.png", "figure", True), ("outputs/fig1.png", "output", False),
])
def test_classification(repo, path, kind, matters):
    c = GH.classify(str(repo), path)
    assert (c["kind"], c["matters"]) == (kind, matters) and c["why"]


def test_a_yaml_that_declares_plans_is_a_plan_wherever_it_lives(repo):
    write(repo, "experiments/x.yaml", "schema_version: 1\nplans:\n  - key: a\n")
    write(repo, "experiments/settings.yaml", "lr: 0.1\n")
    assert GH.classify(str(repo), "experiments/x.yaml")["kind"] == "plan"
    assert GH.classify(str(repo), "experiments/settings.yaml")["kind"] == "source"


# ------------------------------------------------------------------------------------------------ report: main, worktrees, ledger stay apart
@pytest.fixture
def world(repo, tmp_path):
    Project.init(str(repo), name="d").close()
    W.enable(str(repo), str(tmp_path / "ledger"))
    p = Project(str(repo))
    p.author = dict(HUMAN)
    p.save_config({"governance.approval.require_pin": False, "promotion.require_supported_finding": False})
    p.config = p.load_config()
    e = services.create_experiment(p, "faster solver", question=services.create_question(p, "q")["id"], hypothesis="h")
    ws = W.create(p, e["id"], name="solver")
    yield p, ws, str(repo)
    p.close()


def test_report_keeps_main_worktrees_and_ledger_separate(world):
    p, ws, repo = world
    write(repo, "plans/p.yaml", "schema_version: 1\nplans:\n  - key: a\n")
    write(repo, "train.py", "print('changed')\n")
    write(ws["path"], "solver.py", "x = 1\n")                                    # uncommitted work in the experiment worktree
    git(ws["path"], "add", "solver.py")
    git(ws["path"], "commit", "-qm", "solver")
    write(ws["path"], "scratch.py", "tmp\n")
    r = GH.report(p)
    m = r["main"]
    assert [e["path"] for e in m["tracked_changes"]] == ["train.py"]
    assert any(e["path"] == "plans/p.yaml" and e["kind"] == "plan" for e in m["untracked_meaningful"])
    assert not any(e["path"] in ("solver.py", "scratch.py") for e in m["untracked"] + m["tracked_changes"])   # worktree files never leak into main's list
    wt = r["worktrees"][0]
    assert wt["unpromoted_commits"] == 1 and wt["untracked"] == 1 and wt["dirty"] == 0
    assert r["ledger"]["shared"] and r["ledger"]["path"] != repo
    assert r["summary"]["unpromoted_commits"] == 1 and r["summary"]["tracked_changes"] == 1


def test_pending_promotions_are_reported(world):
    p, ws, repo = world
    write(ws["path"], "solver.py", "x = 1\n")
    git(ws["path"], "add", "solver.py")
    git(ws["path"], "commit", "-qm", "solver")
    PR.prepare(p, ws["id"], paths=["solver.py"])
    assert [x["state"] for x in GH.report(p)["promotions"]] and GH.report(p)["summary"]["pending_promotions"] == 1


# ------------------------------------------------------------------------------------------------ preflight
def test_preflight_explains_what_matters_and_why_and_continues_by_default(hp, repo):
    write(repo, "train.py", "print('edited')\n")
    write(repo, "plans/p.yaml", "schema_version: 1\nplans:\n  - key: a\n")
    write(repo, ".env", "TOKEN=abc\n")
    write(repo, "__pycache__/a.pyc", "x")
    r = GH.preflight(hp, "plan")
    text = " ".join(i["what"] + " " + i["why"] for i in r["issues"])
    assert r["ok"] and r["mode"] == "warn"
    assert "will NOT see these edits" in text and "uncommitted plan file is not in them" in text
    assert any("secrets" in i["what"] and ".env" in i["files"] for i in r["issues"])
    assert any("generated or internal" in i["what"] for i in r["issues"])
    assert all(i["do"] for i in r["issues"] if i["severity"] != "notice" or i["files"])
    assert GH.lines(r)[0].startswith("Git preflight (plan)")


def test_preflight_blocks_only_when_configured_and_names_the_way_out(hp, repo):
    write(repo, "train.py", "print('edited')\n")
    GH.require_preflight(hp, "plan")                                              # default warn: no exception
    hp.save_config({"git.preflight": "block"})
    hp.config = hp.load_config()
    with pytest.raises(PolicyBlocked) as e:
        GH.require_preflight(hp, "plan")
    assert "research git commit" in str(e.value) and "git.preflight" in (getattr(e.value, "hint", "") or "")
    hp.save_config({"git.preflight": "off"})
    hp.config = hp.load_config()
    assert GH.preflight(hp)["issues"] == []


def test_dirty_main_blocks_promotion_until_reviewed_and_committed(hp, repo):
    write(repo, "train.py", "print('edited')\n")
    r = GH.preflight(hp, "promotion")
    assert not r["ok"] and r["blocking"][0]["severity"] == "blocking" and "fast-forwards main" in r["blocking"][0]["why"]
    pv = GH.commit(hp, paths=["train.py"])
    assert pv["committed"] is False and "confirm" in pv["needs"] and "edited" in pv["diff"]
    GH.commit(hp, paths=["train.py"], confirm=True)
    assert GH.preflight(hp, "promotion")["ok"] and not GH.preflight(hp, "promotion")["blocking"]


def test_approving_a_promotion_is_refused_up_front_when_block_is_set_and_main_is_dirty(world):
    p, ws, repo = world
    write(ws["path"], "solver.py", "x = 1\n")
    git(ws["path"], "add", "solver.py")
    git(ws["path"], "commit", "-qm", "solver")
    pid = PR.prepare(p, ws["id"], paths=["solver.py"])["id"]
    write(repo, "train.py", "dirty\n")
    p.save_config({"git.preflight": "block"})
    p.config = p.load_config()
    with pytest.raises(PolicyBlocked) as e:
        PR.approve(p, pid, "ship")
    assert "uncommitted" in str(e.value)


# ------------------------------------------------------------------------------------------------ managed ignores
def test_ignore_block_is_previewed_written_idempotent_and_leaves_your_lines(hp, repo):
    write(repo, ".gitignore", "my-secret-notes/\n*.tmp\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-qm", "gi")
    pl = GH.ignore_plan(hp)
    assert "__pycache__/" in pl["add"] and pl["changes"]
    assert open(os.path.join(repo, ".gitignore")).read() == "my-secret-notes/\n*.tmp\n"          # preview writes nothing
    r = GH.ignore_apply(hp)
    text = open(os.path.join(repo, ".gitignore")).read()
    assert r["written"] and text.startswith("my-secret-notes/\n*.tmp\n") and GH.BLOCK_BEGIN in text and ".research/" in text
    assert not GH.ignore_apply(hp)["written"] and open(os.path.join(repo, ".gitignore")).read() == text
    write(repo, "__pycache__/a.pyc", "x")
    assert "__pycache__" not in out(repo, "status", "--porcelain")


def test_ignore_never_hides_important_files(hp, repo):
    write(repo, "src/notes.md", "docs\n")
    write(repo, "plans/p.yaml", "schema_version: 1\nplans:\n  - key: a\n")
    pl = GH.ignore_plan(hp, ["*.md", "plans/", "*.py"])
    assert {x["pattern"] for x in pl["refused"]} == {"*.md", "plans/", "*.py"} and not set(pl["add"]) & {"*.md", "plans/", "*.py"}
    GH.ignore_apply(hp, ["*.md", "plans/", "*.py"])
    assert "*.md" not in open(os.path.join(repo, ".gitignore")).read()
    write(repo, ".gitignore", "*.md\n")                                                          # the user's own rule hides a document: flagged, never added by us
    write(repo, "README.md", "x\n")
    assert any(e["path"] == "README.md" for e in GH.main_report(hp)["ignored_important"])


def test_tracked_generated_files_are_reported_not_untracked(hp, repo):
    write(repo, "__pycache__/a.pyc", "x")
    git(repo, "add", "-f", "__pycache__/a.pyc")
    git(repo, "commit", "-qm", "oops")
    pl = GH.ignore_plan(hp)
    assert "__pycache__/a.pyc" in pl["tracked_but_generated"] and "never deletes or untracks" in pl["note"]
    GH.ignore_apply(hp)
    assert "__pycache__/a.pyc" in out(repo, "ls-files")                                           # still tracked: nothing was deleted


# ------------------------------------------------------------------------------------------------ the reviewed, scoped commit
def test_commit_is_scoped_confirmed_and_leaves_everything_else_alone(hp, repo):
    write(repo, "AGENTS.md", "# rules\n")
    write(repo, "CLAUDE.md", "@AGENTS.md\n")
    write(repo, "notes.txt", "mine\n")
    write(repo, "train.py", "print('wip')\n")
    write(repo, "staged.txt", "staged by me\n")
    git(repo, "add", "staged.txt")
    pv = GH.commit(hp, scaffold=True)
    assert sorted(pv["files"]) == ["AGENTS.md", "CLAUDE.md"] and not pv["committed"] and "rules" in pv["diff"]
    assert "AGENTS.md" not in out(repo, "ls-files")                                               # preview built in a throwaway index
    assert out(repo, "diff", "--cached", "--name-only").split() == ["staged.txt"]
    r = GH.commit(hp, scaffold=True, confirm=True)
    assert r["committed"] and sorted(out(repo, "show", "--name-only", "--format=", "HEAD").split()) == ["AGENTS.md", "CLAUDE.md"]
    assert out(repo, "diff", "--cached", "--name-only").split() == ["staged.txt"]                  # your staged file is still staged, not committed
    assert " M train.py" in out(repo, "status", "--porcelain")                                        # your other edit is still just modified
    assert "?? notes.txt" in out(repo, "status", "--porcelain")
    assert out(repo, "remote") == ""                                                              # nothing pushed, nothing configured


def test_commit_refuses_secrets_caches_runtime_state_and_unnamed_files(hp, repo):
    write(repo, ".env", "TOKEN=abc\n")
    write(repo, "__pycache__/a.pyc", "x")
    write(repo, "ok.py", "x = 1\n")
    pv = GH.commit(hp, paths=[".env", "__pycache__/a.pyc", ".research/config.yaml", "ok.py"])
    assert pv["files"] == ["ok.py"] and {x["path"] for x in pv["refused"]} >= {".env", "__pycache__"}
    assert "TOKEN" not in pv["diff"]
    with pytest.raises(ResearchError):
        GH.commit(hp)                                                                             # no paths and no --scaffold: nothing is guessed
    with pytest.raises(ResearchError):
        GH.commit(hp, paths=["../outside.txt"])


def test_agents_cannot_commit_on_main(hp, repo):
    write(repo, "ok.py", "x = 1\n")
    hp.author = dict(AGENT)
    with pytest.raises(PolicyBlocked):
        GH.commit(hp, paths=["ok.py"], confirm=True)
    assert "ok.py" not in out(repo, "ls-files")


def test_files_inside_an_experiment_worktree_are_not_committed_on_main(world):
    p, ws, repo = world
    write(ws["path"], "solver.py", "x = 1\n")
    rel = os.path.relpath(os.path.join(ws["path"], "solver.py"), repo)
    with pytest.raises(ResearchError):
        GH.commit(p, paths=[rel], confirm=True)


# ------------------------------------------------------------------------------------------------ headless: never blocks on a prompt
def test_a_failing_hook_is_an_actionable_error_not_a_hang(hp, repo):
    hook = os.path.join(repo, ".git", "hooks", "pre-commit")
    write(os.path.join(repo, ".git", "hooks"), "pre-commit", "#!/bin/sh\necho 'lint failed' >&2\nexit 1\n")
    os.chmod(hook, 0o755)
    write(repo, "ok.py", "x = 1\n")
    with pytest.raises(ResearchError) as e:
        GH.commit(hp, paths=["ok.py"], confirm=True)
    assert "lint failed" in str(e.value) and "commit hook" in (getattr(e.value, "hint", "") or "")
    assert "ok.py" not in out(repo, "log", "--name-only", "--format=")


def test_git_never_waits_for_a_person(hp, repo):
    hook = os.path.join(repo, ".git", "hooks", "pre-commit")
    write(os.path.join(repo, ".git", "hooks"), "pre-commit", "#!/bin/sh\nsleep 10\n")
    os.chmod(hook, 0o755)
    write(repo, "ok.py", "x = 1\n")
    git(repo, "add", "ok.py")
    with pytest.raises(ResearchError) as e:
        GH.git(str(repo), "commit", "-m", "x", timeout=1)
    assert "did not finish" in str(e.value) and "Nothing was changed" in getattr(e.value, "hint", "")
    assert {"GIT_TERMINAL_PROMPT", "GIT_EDITOR", "GIT_PAGER"} <= set(GH.NONINTERACTIVE)


def test_missing_identity_says_how_to_fix_it(hp, repo, monkeypatch, tmp_path):
    for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    for k in ("user.name", "user.email"):
        subprocess.run(["git", "config", "--unset-all", k], cwd=repo)
    subprocess.run(["git", "config", "user.useConfigOnly", "true"], cwd=repo)
    write(repo, "ok.py", "x = 1\n")
    with pytest.raises(ResearchError) as e:
        GH.commit(hp, paths=["ok.py"], confirm=True)
    assert "git config user.name" in (getattr(e.value, "hint", "") or "")


# ------------------------------------------------------------------------------------------------ which files verified work puts on the experiment branch
def test_selection_for_verified_commits(repo):
    big = b"0" * (6 * 1024 * 1024)
    write(repo, "mod.py", "x = 1\n")
    write(repo, "tests/test_mod.py", "def test(): pass\n")
    write(repo, "reports/summary.md", "# result\n")
    write(repo, "outputs/fig.png", "png")
    write(repo, "outputs/data.bin", "bin")
    write(repo, "outputs/ckpt.pt", "w")
    with open(os.path.join(repo, "outputs", "huge.png"), "wb") as f:
        f.write(big)
    write(repo, "__pycache__/m.pyc", "c")
    write(repo, ".env", "TOKEN=1\n")
    write(repo, "patch1.yaml", "x: 1\n")
    os.remove(os.path.join(repo, "train.py"))
    sel = GH.select_for_commit(str(repo), ["outputs/", "results/", "patch*.yaml", "*.pyc", "__pycache__/", ".venv/"], ["*.png", "*.md"], 5 * 1024 * 1024)
    assert set(sel["add"]) == {"mod.py", "tests/test_mod.py", "reports/summary.md", "outputs/fig.png", "train.py"}      # deletion of a tracked file included
    why = {s["path"]: s["why"] for s in sel["skipped"]}
    assert why["outputs/data.bin"].startswith("excluded") and why["outputs/ckpt.pt"].startswith("excluded") and why["patch1.yaml"].startswith("excluded")
    assert why["outputs/huge.png"].startswith("larger than 5 MB") and why[".env"] == "secret" and why["__pycache__/m.pyc"] == "generated"


# ------------------------------------------------------------------------------------------------ init
def test_init_writes_a_gitignore_with_only_disposable_and_internal_files(tmp_path, monkeypatch):
    from research import initproject as IP
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    r = IP.apply(str(root), {"name": "proj", "goal": "g", "agents": {"enabled": ["claude"], "default": "claude"}})
    gi = open(os.path.join(root, "main", ".gitignore")).read()
    assert GH.BLOCK_BEGIN in gi and ".research/" in gi and "__pycache__/" in gi
    assert not any(l.strip() in ("*.md", "*.py", "*.yaml", "docs/", "plans/", "reports/", "src/") for l in gi.splitlines())
    assert r


# ------------------------------------------------------------------------------------------------ the CLI, headless
def cli(repo, *args):
    import sys
    env = {k: v for k, v in os.environ.items() if not k.startswith("RESEARCH_") and k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    return subprocess.run([sys.executable, "-m", "research_cli", *args], cwd=str(repo), capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env, timeout=120)


def test_cli_commit_without_a_terminal_needs_yes_and_never_prompts(hp, repo):
    write(repo, "ok.py", "x = 1\n")
    r = cli(repo, "git", "commit", "--paths", "ok.py")
    assert r.returncode == 2 and "repeat with --yes" in r.stdout and "ok.py" not in out(repo, "ls-files")
    r = cli(repo, "git", "commit", "--paths", "ok.py", "-m", "Add ok", "--yes")
    assert r.returncode == 0 and "Not pushed" in r.stdout and "ok.py" in out(repo, "ls-files")
    r = cli(repo, "git", "status", "--json")
    assert r.returncode == 0 and '"attention"' in r.stdout
    r = cli(repo, "git", "ignore")
    assert r.returncode == 0 and "Would add" in r.stdout and not os.path.exists(os.path.join(repo, ".gitignore"))
