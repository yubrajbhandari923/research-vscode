"""Real CLI behaviour through a real pseudo-terminal: the approval PIN prompt, the `./research` link in the main checkout, and the
supervisor's escalation after an incomplete deliverable. Subprocesses use the project launcher exactly as a person would."""
import json
import os
import pty
import select
import subprocess
import sys
import time

import pytest

from conftest import git
from research import autopromote as AP, localbin, planspec as PS, supervisor as SV, workspace as W
from research.store import Project
from test_supervisor import MOCK, lproj, run_until, task_yaml, yaml_plan  # noqa: F401  (lproj is a fixture)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="pty and symlinks are POSIX")
AGENT_VARS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_SANDBOX", "RESEARCH_AGENT", "RESEARCH_EXECUTION")


def env_for(xdg, **extra):
    env = {k: v for k, v in os.environ.items() if k not in AGENT_VARS and not k.startswith("RESEARCH_")}
    env["XDG_CONFIG_HOME"] = str(xdg)
    env.update(extra)
    return env


def pty_run(argv, answers, env, cwd, timeout=60):
    """Run argv with a controlling terminal; type `answers` at each PIN prompt. Returns (exit code, prompts seen, transcript)."""
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(cwd)
        os.execve(argv[0], argv, env)
    buf, prompts, answers, t0 = "", [], list(answers), time.time()
    while time.time() - t0 < timeout:
        r, _, _ = select.select([fd], [], [], 0.5)
        if r:
            try:
                data = os.read(fd, 4096).decode(errors="replace")
            except OSError:
                break
            if not data:
                break
            buf += data
            tail = buf[-80:]
            if tail.rstrip().endswith(":") and "PIN" in tail and len(prompts) < len(answers) + 8 and (not prompts or buf.rfind("PIN") > buf.rfind(prompts[-1] + "\x00")):
                prompts.append(tail.strip().splitlines()[-1])
                os.write(fd, ((answers.pop(0) if answers else "") + "\n").encode())
                buf += "\x00"
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status), prompts, buf.replace("\x00", "")


@pytest.fixture
def cli(repo, tmp_path):
    """Shared-ledger project with a running plan and the project launcher, as a person would have after `research ledger enable`."""
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


# ------------------------------------------------------------------------------------------------ escalation
def attempts(p, ref="demo-plan"):
    st = PS.load_state(p, ref)
    return [a for e in SV.load_run(p, st["plan_id"])["tasks"].values() for a in e["attempts"]]


ESC = ", escalation: {model: big-model, effort: high}"


def incomplete_plan(extra):
    # attempt 1 does nothing (deliverable never written); attempt 2 writes it
    return yaml_plan([task_yaml("a", "A", "echo done > out.txt", checks=["file:out.txt"], attempt="2")], extra_exec=extra)


def test_incomplete_deliverable_escalates_the_repair_attempt_to_the_stronger_model(lproj):
    PS.apply(lproj, incomplete_plan(ESC))
    SV.plan_run(lproj, "demo-plan", start_daemon=False)
    assert run_until(lproj, lambda: {t["key"]: t["phase"] for t in SV.status(lproj, "demo-plan")["tasks"]}["a"] == "verified", 90)
    a1, a2 = attempts(lproj)
    assert a1["category"] == "incomplete_deliverable" and not a1.get("escalated") and a1.get("model") != "big-model"
    assert a2["escalated"] == {"model": "big-model", "effort": "high"} and a2["model"] == "big-model" and a2["state"] == "verified"
    from research import execution as E
    assert E.show(lproj, a2["execution"])["request"]["effort"] == "high" and E.show(lproj, a1["execution"])["request"].get("effort") is None
    assert "escalated" in " ".join(e["summary"] for e in lproj.q("SELECT summary FROM events WHERE action='supervisor_escalated'"))


def test_no_escalation_unless_configured_or_for_other_failures(lproj):
    PS.apply(lproj, incomplete_plan(""))
    SV.plan_run(lproj, "demo-plan", start_daemon=False)
    assert run_until(lproj, lambda: {t["key"]: t["phase"] for t in SV.status(lproj, "demo-plan")["tasks"]}["a"] == "verified", 90)
    assert not any(a.get("escalated") for a in attempts(lproj)), "default: never escalates"


def test_a_wrong_result_is_not_an_incomplete_deliverable_and_escalation_is_bounded(lproj):
    # the file exists but a command check fails on attempt 1: incorrect_implementation, so no escalation
    PS.apply(lproj, yaml_plan([task_yaml("a", "A", "echo wrong > out.txt", checks=["file:out.txt", "cmd:grep -q right out.txt"], attempt="1")], extra_exec=ESC))
    SV.plan_run(lproj, "demo-plan", start_daemon=False)
    run_until(lproj, lambda: len(attempts(lproj)) >= 2 and attempts(lproj)[-1]["state"] in ("failed", "verified", "done"), 90)
    a1 = attempts(lproj)[0]
    assert a1["category"] == "incorrect_implementation" and not any(a.get("escalated") for a in attempts(lproj))


def test_plan_validation_rejects_bad_escalation():
    for bad in (", escalation: {color: red}", ", escalation: {on: [incomplete_deliverable]}", ", escalation: {model: x, on: [scientific_assumption]}"):
        with pytest.raises(PS.PlanSpecError, match="escalation"):
            PS.validate(incomplete_plan(bad))
    assert PS.validate(incomplete_plan(ESC))["ok"]
