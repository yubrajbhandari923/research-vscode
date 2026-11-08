"""Approval PIN through a real pseudo-terminal."""
import json
import os
import subprocess
import sys

import pytest

from conftest import env_for, git, pty_run
from research import localbin, planspec as PS, supervisor as SV, workspace as W
from research.store import Project
from test_supervisor import MOCK, task_yaml, yaml_plan


pytestmark = pytest.mark.skipif(os.name == "nt", reason="pty and symlinks are POSIX")


@pytest.fixture
def cli(repo, tmp_path):
    """Shared-ledger project with a running plan and the project launcher, as a person would have after `research ledger enable`."""
    if os.name == "nt":
        pytest.skip("pty and symlinks are POSIX")
    Project.init(str(repo), name="demo", goal="g").close()
    git(repo, "add", "train.py")
    W.enable(str(repo), str(tmp_path / "ledger"))
    p = Project(str(repo))
    script = tmp_path / "mock_agent.py"
    script.write_text(MOCK)
    p.config["agents"]["profiles"]["fake"] = {"command": sys.executable, "args": [str(script)], "headless_args": ["--headless"]}
    p.config["agents"]["profiles"]["rev"] = {"command": sys.executable}
    p.config["auto_promotion"] = {"test_command": "true", "reviewer_profile": "rev"}
    p.save_config()
    PS.apply(p, yaml_plan([task_yaml("a", "A", "echo 1 > a.txt", checks=["file:a.txt"])], extra_exec=", auto_promotion: true"))
    SV.plan_run(p, "demo-plan", start_daemon=False)
    p.close()
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    localbin.install(str(repo))
    return {"bin": str(tmp_path / "ledger" / ".research" / "bin" / "research"), "repo": str(repo), "xdg": xdg, "tmp": tmp_path}


def authorize(cli, answers, **env):
    return pty_run([cli["bin"], "plan", "autopromote", "demo-plan", "authorize", "--note", "overnight"], answers, env_for(cli["xdg"], **env), cli["repo"])


def mode(cli):
    r = subprocess.run([cli["bin"], "plan", "autopromote", "demo-plan", "status", "--json"], cwd=cli["repo"], env=env_for(cli["xdg"]), capture_output=True, text=True)
    return json.loads(r.stdout)["mode"]


def pinfile(cli):
    f = cli["xdg"] / "research-os" / "approval.json"
    return json.loads(f.read_text()) if f.exists() else None


# ------------------------------------------------------------------------------------------------ the PIN prompt
def test_set_pin_prompts_twice_without_echo_then_every_protected_action_asks(cli):
    code, prompts, out = pty_run([cli["bin"], "approval", "set-pin"], ["4321", "4321"], env_for(cli["xdg"]), cli["repo"])
    assert code == 0 and len(prompts) == 2 and "4321" not in out and pinfile(cli)["hash"], out
    code, prompts, out = authorize(cli, ["0000"])
    assert code != 0 and len(prompts) == 1 and "Incorrect PIN" in out and mode(cli) == "manual", "a wrong PIN authorizes nothing"
    code, prompts, out = authorize(cli, ["4321"])
    assert code == 0 and len(prompts) == 1 and mode(cli) == "active", out


def test_first_protected_action_without_a_pin_creates_one_instead_of_silently_passing(cli):
    assert pinfile(cli) is None
    code, prompts, out = authorize(cli, ["7777", "7777"])
    assert code == 0 and len(prompts) == 2 and "No approval PIN is set" in out and pinfile(cli)["hash"] and mode(cli) == "active", out
    # now it is enforced
    subprocess.run([cli["bin"], "plan", "autopromote", "demo-plan", "revoke"], cwd=cli["repo"], env=env_for(cli["xdg"]), capture_output=True)
    code, prompts, out = authorize(cli, ["1"])
    assert code != 0 and len(prompts) == 1 and mode(cli) == "manual"


def test_mismatched_new_pin_creates_nothing_and_authorizes_nothing(cli):
    code, prompts, out = authorize(cli, ["7777", "8888"])
    assert code != 0 and pinfile(cli) is None and mode(cli) == "manual"


def test_agent_shells_get_a_clear_message_and_are_never_locked_out(cli):
    pty_run([cli["bin"], "approval", "set-pin"], ["4321", "4321"], env_for(cli["xdg"]), cli["repo"])
    code, prompts, out = authorize(cli, [], CLAUDE_CODE_ENTRYPOINT="cli")
    assert code != 0 and prompts == [] and "CLAUDE_CODE_ENTRYPOINT" in out and "own terminal" in out and mode(cli) == "manual"
    assert pinfile(cli).get("fails", 0) == 0


def test_no_terminal_fails_closed_without_counting_a_wrong_attempt(cli):
    pty_run([cli["bin"], "approval", "set-pin"], ["4321", "4321"], env_for(cli["xdg"]), cli["repo"])
    for _ in range(7):   # more than MAX_FAILS: must never lock
        r = subprocess.run([cli["bin"], "plan", "autopromote", "demo-plan", "authorize", "--note", "x"], cwd=cli["repo"], env=env_for(cli["xdg"]),
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, start_new_session=True)
        assert r.returncode != 0 and "PIN" in (r.stdout + r.stderr)
    assert pinfile(cli).get("fails", 0) == 0 and pinfile(cli).get("locked_until", 0) == 0
    assert authorize(cli, ["4321"])[0] == 0, "the right PIN still works"
