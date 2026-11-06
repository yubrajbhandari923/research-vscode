"""The supervisor features driven ONLY through the real `research` launcher in subprocesses (and a real pty for prompts): instruct, intervention
approve with the PIN, debug, status honesty, reload/restart, bin link and Telegram (setup/test/poll and the daemon's alerts and chat commands).
Stand-ins: the planning/debug agent is a real subprocess that prints a scripted JSON reply, and Telegram is a local HTTP server."""
import json
import os
import pty
import select
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import git
from research import localbin, planspec as PS, workspace as W
from research.store import Project
from test_supervisor import MOCK
from test_supervisor_fixes import plan, t

pytestmark = pytest.mark.skipif(os.name == "nt", reason="pty and POSIX launchers")
TOKEN = "123456789:AAH-fake_token_for_tests_0123456789abc"
ASSISTANT = "import os\nprint(open(os.environ['FAKE_REPLY_FILE']).read())\n"
WRONG = [("e1", [t("w", "implementer", "echo wrong > out.txt", checks=["file:out.txt", "cmd:grep -q right out.txt"], attempts=1)])]


class FakeTelegram:
    def __init__(self):
        self.sent, self.updates, outer = [], [], self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = urllib.parse.parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
                method = self.path.rsplit("/", 1)[-1]
                assert TOKEN in self.path
                if method == "sendMessage":
                    outer.sent.append(body["text"][0])
                    out = {"ok": True, "result": {}}
                elif method == "getUpdates":
                    off = int(body.get("offset", ["0"])[0])
                    out = {"ok": True, "result": [u for u in outer.updates if u["update_id"] >= off]}
                else:
                    out = {"ok": True}
                data = json.dumps(out).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def say(self, text, chat=777):
        self.updates.append({"update_id": len(self.updates) + 1, "message": {"chat": {"id": chat}, "text": text}})

    def wait_for(self, needle, timeout=60):
        t0 = time.time()
        while time.time() - t0 < timeout:
            hit = [m for m in self.sent if needle in m]
            if hit:
                return hit[-1]
            time.sleep(0.3)
        raise AssertionError(f"no Telegram message containing {needle!r}; got {self.sent}")


@pytest.fixture
def world(repo, tmp_path):
    Project.init(str(repo), name="demo", goal="g").close()
    git(repo, "add", "train.py")
    W.enable(str(repo), str(tmp_path / "ledger"))
    (tmp_path / "mock_agent.py").write_text(MOCK)
    (tmp_path / "assistant.py").write_text(ASSISTANT)
    p = Project(str(repo))
    p.config["agents"]["profiles"]["fake"] = {"command": sys.executable, "args": [str(tmp_path / "mock_agent.py")], "headless_args": ["--headless"]}
    p.config["agents"]["profiles"]["fakeai"] = {"command": sys.executable, "args": [str(tmp_path / "assistant.py")], "headless_args": ["--headless"]}
    p.config["supervisor"] = {**(p.config.get("supervisor") or {}), "poll_seconds": 1, "launch_grace_seconds": 5}
    p.config["supervisor_assistant"] = {"profile": "fakeai", "model": "test-model", "timeout_seconds": 60}
    p.config["promotion"] = {"require_supported_finding": False}
    p.save_config()
    PS.apply(p, plan(WRONG))
    p.close()
    tg = FakeTelegram()
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    reply = tmp_path / "reply.json"
    localbin.install(str(repo))
    w = type("W", (), {})()
    w.repo, w.tg, w.xdg, w.reply, w.bin = str(repo), tg, xdg, reply, localbin.launcher(str(repo))
    w.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "RESEARCH_"))}, "XDG_CONFIG_HOME": str(xdg), "FAKE_REPLY_FILE": str(reply),
             "RESEARCH_TELEGRAM_API": tg.url, "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
    yield w
    subprocess.run([w.bin, "supervisor", "stop"], cwd=w.repo, env=w.env, capture_output=True)
    tg.srv.shutdown()


def cli(w, *args, stdin=None, timeout=120):
    r = subprocess.run([w.bin, *args], cwd=w.repo, env=w.env, capture_output=True, text=True, input=stdin, timeout=timeout,
                       stdin=None if stdin is not None else subprocess.DEVNULL, start_new_session=True)
    return r.returncode, r.stdout + r.stderr


def jcli(w, *args, **kw):
    code, out = cli(w, *args, "--json", **kw)
    assert code == 0, out
    return json.loads(out)


def pty_cli(w, args, answers):
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(w.repo)
        os.execve(w.bin, [w.bin, *args], w.env)
    buf, prompts, answers, t0 = "", 0, list(answers), time.time()
    while time.time() - t0 < 90:
        r, _, _ = select.select([fd], [], [], 0.5)
        if not r:
            continue
        try:
            data = os.read(fd, 4096).decode(errors="replace")
        except OSError:
            break
        if not data:
            break
        buf += data
        tail = buf[-120:].rstrip()
        if tail.endswith(":") and any(k in tail for k in ("PIN", "token")) and prompts < len(answers):
            os.write(fd, (answers[prompts] + "\n").encode())
            prompts += 1
            buf += "\x00"
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status), prompts, buf.replace("\x00", "")


def wait_status(w, pred, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = jcli(w, "plan", "status", "fx")
        if pred(s):
            return s
        time.sleep(1)
    raise AssertionError(json.dumps(s, indent=1)[:1500])


def task_phase(s, key="w"):
    return next(x["phase"] for x in s["tasks"] if x["key"] == key)


def start_and_block(w):
    assert cli(w, "plan", "run", "fx", "--no-daemon")[0] == 0
    for _ in range(40):
        cli(w, "supervisor", "tick")
        if task_phase(jcli(w, "plan", "status", "fx")) == "blocked":
            return
        time.sleep(1)
    raise AssertionError("task never blocked")


# ------------------------------------------------------------------------------------------------ status honesty, bin link, reload/restart
def test_cli_status_says_halted_and_the_link_runs_from_main(world):
    start_and_block(world)
    code, out = cli(world, "plan", "status", "fx")
    assert code == 0 and "HALTED" in out and "nothing is running" in out and "research supervisor instruct" in out and "parallel agents: 1" in out
    assert jcli(world, "plan", "status", "fx")["health"]["state"] == "halted"
    r = subprocess.run(["./research", "plan", "status", "fx"], cwd=world.repo, env=world.env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0 and "HALTED" in r.stdout, "./research works from the main checkout"


def test_cli_reload_and_restart_leave_the_daemon_healthy(world):
    start_and_block(world)
    assert cli(world, "supervisor", "start")[0] == 0
    pid0 = jcli(world, "supervisor", "status")["daemon"]["pid"]
    code, out = cli(world, "supervisor", "reload")
    assert code == 0 and "Applied" in out
    assert jcli(world, "supervisor", "status")["daemon"]["pid"] == pid0
    code, out = cli(world, "supervisor", "restart", "--timeout", "60")
    assert code == 0 and "Restarted" in out and jcli(world, "supervisor", "status")["daemon"]["pid"] != pid0


# ------------------------------------------------------------------------------------------------ instruct / intervention / debug
def test_cli_instruct_multiline_unprotected_resumes_the_plan(world):
    start_and_block(world)
    world.reply.write_text(json.dumps({"summary": "Guide and retry.", "actions": [{"type": "guide", "text": "Print right."}, {"type": "unblock"}]}))
    assert cli(world, "supervisor", "start")[0] == 0
    code, out = cli(world, "supervisor", "instruct", "T-001", stdin="Print the word right\nnot wrong.\n\n")
    assert code == 0 and "INT-001" in out and "approved" in out and "[operational]" in out, out
    it = jcli(world, "intervention", "show", "INT-001")
    assert it["instruction"] == "Print the word right\nnot wrong." and it["state"] in ("approved", "implementing", "verifying", "resolved")
    code, out = cli(world, "intervention", "list")
    assert "INT-001" in out and "T-001" in out
    # the unprotected change only retries; the agent still prints "wrong", so the supervisor must NOT claim success
    for _ in range(90):
        if jcli(world, "intervention", "show", "INT-001")["state"] in ("resolved", "failed"):
            break
        time.sleep(1)
    assert jcli(world, "intervention", "show", "INT-001")["state"] == "failed", "never resolved without the supervisor verifying the task"


def test_cli_protected_instruction_needs_the_pin_at_the_prompt_then_runs_to_completion(world):
    start_and_block(world)
    assert pty_cli(world, ["approval", "set-pin"], ["4321", "4321"])[0] == 0
    world.reply.write_text(json.dumps({"summary": "Drop the content check.", "rationale": "Researcher: only existence matters.",
                                       "actions": [{"type": "amend_task", "fields": {"checks": ["file:out.txt"]}}, {"type": "reverify"}]}))
    assert cli(world, "supervisor", "start")[0] == 0
    code, out = cli(world, "supervisor", "instruct", "T-001", "--message", "The output only has to exist.")
    assert code == 0 and "[scientific*]" in out and "needs your approval (scientific): research intervention approve INT-001" in out, out
    code, prompts, out = pty_cli(world, ["intervention", "approve", "INT-001", "--note", "agreed"], ["0000"])
    assert code != 0 and prompts == 1 and "Incorrect PIN" in out
    assert jcli(world, "intervention", "show", "INT-001")["state"] == "proposed"
    code, prompts, out = pty_cli(world, ["intervention", "approve", "INT-001", "--note", "agreed, existence is the criterion"], ["4321"])
    assert code == 0 and prompts == 1 and "4321" not in out
    s = wait_status(world, lambda s: task_phase(s) == "verified" or s["state"] == "completed")
    it = jcli(world, "intervention", "show", "INT-001")
    for _ in range(30):
        if it["state"] == "resolved":
            break
        time.sleep(1)
        it = jcli(world, "intervention", "show", "INT-001")
    assert it["state"] == "resolved" and it["approval"]["via"].startswith("human approval with PIN")
    assert jcli(world, "plan", "status", "fx")["health"]["state"] == "completed"


def test_cli_reject_and_agent_shells_cannot_instruct(world):
    start_and_block(world)
    world.reply.write_text(json.dumps({"summary": "s", "actions": [{"type": "amend_task", "fields": {"checks": ["file:out.txt"]}}]}))
    assert cli(world, "supervisor", "instruct", "T-001", "-m", "loosen it")[0] == 0
    assert cli(world, "intervention", "reject", "INT-001", "--note", "no")[0] == 0
    assert jcli(world, "intervention", "show", "INT-001")["state"] == "rejected"
    world.env["CLAUDECODE"] = "1"
    code, out = cli(world, "supervisor", "instruct", "T-001", "-m", "agent says so")
    assert code != 0 and "human decision" in out and "CLAUDECODE" in out


def test_cli_debug_diagnoses_and_choose_starts_an_intervention(world):
    code, out = cli(world, "supervisor", "debug")
    assert code != 0 and "Nothing is blocked" in out
    start_and_block(world)
    world.reply.write_text(json.dumps({"summary": "The check wants the word right but the task prints wrong.", "recommended": 0,
                                       "options": [{"label": "Fix the task", "instruction": "Make the task print right.", "class": "implementation"},
                                                   {"label": "Relax the check", "instruction": "Only require the file.", "class": "scientific"}]}))
    code, out = cli(world, "supervisor", "debug")
    assert code == 0 and "task prints wrong" in out and "1. Fix the task [implementation] (recommended)" in out and "2. Relax the check [scientific]" in out and "model test-model" in out
    world.reply.write_text(json.dumps({"summary": "Guide.", "actions": [{"type": "guide", "text": "Print right."}, {"type": "unblock"}]}))
    code, out = cli(world, "supervisor", "debug", "--choose", "1")
    assert code == 0 and "INT-001" in out
    assert jcli(world, "intervention", "show", "INT-001")["instruction"] == "Make the task print right."


# ------------------------------------------------------------------------------------------------ Telegram through the CLI and the daemon
def test_cli_telegram_setup_status_test_and_remove(world):
    code, out = cli(world, "telegram", "status")
    assert code == 0 and "no token" in out
    code, prompts, out = pty_cli(world, ["telegram", "setup", "--chat-id", "777"], [TOKEN])
    assert code == 0 and prompts == 1 and TOKEN not in out, out
    assert any("connected" in m for m in world.tg.sent)
    assert jcli(world, "supervisor", "status")["daemon"]["running"], "setup also starts the supervisor, which is what answers the chat"
    f = world.xdg / "research-os" / "telegram.json"
    assert oct(os.stat(f).st_mode & 0o777) == "0o600"
    ledger_cfg = Project(world.repo)
    try:
        assert TOKEN not in open(os.path.join(ledger_cfg.rdir, "config.yaml")).read(), "the token never reaches the project"
    finally:
        ledger_cfg.close()
    out = cli(world, "telegram", "status")[1]
    assert "token saved" in out and "chat linked" in out and "enabled" in out and TOKEN not in out
    assert cli(world, "telegram", "test")[0] == 0 and "test message" in world.tg.sent[-1]
    assert cli(world, "telegram", "remove")[0] == 0 and not f.exists()
    assert "no token" in cli(world, "telegram", "status")[1]
    code, out = cli(world, "telegram", "setup", "--token", "garbage", "--chat-id", "1")
    assert code != 0 and "bot token" in out


def test_daemon_alerts_on_halt_answers_chat_and_never_approves_protected_changes(world):
    start_and_block(world)
    assert pty_cli(world, ["telegram", "setup", "--chat-id", "777"], [TOKEN])[0] == 0
    cfgp = Project(world.repo)
    cfgp.config["notifications"] = {**cfgp.config["notifications"], "halt_grace_seconds": 0, "auto_debug": True}
    cfgp.save_config()
    cfgp.close()
    world.reply.write_text(json.dumps({"summary": "The check wants right; the task prints wrong.", "recommended": 1,
                                       "options": [{"label": "Relax the check", "instruction": "Only require the file.", "class": "scientific"},
                                                   {"label": "Fix the task", "instruction": "Make the task print right.", "class": "implementation"}]}))
    assert cli(world, "supervisor", "start")[0] == 0
    msg = world.tg.wait_for("HALTED", 90)
    assert "T-001" in msg and "The check wants right" in msg and "1. Relax the check [scientific]" in msg and "2. Fix the task [implementation] (recommended)" in msg and TOKEN not in msg
    time.sleep(4)
    assert len([m for m in world.tg.sent if "HALTED" in m and "Options" in m]) == 1, "one alert per halt"
    world.tg.say("/status")
    assert "HALTED" in world.tg.wait_for("fx:", 30)
    world.tg.say("/blocked")
    assert "T-001" in world.tg.wait_for("T-001 ", 30)
    world.reply.write_text(json.dumps({"summary": "Relax.", "actions": [{"type": "amend_task", "fields": {"checks": ["file:out.txt"]}}, {"type": "reverify"}]}))
    world.tg.say("/say loosen the check, only the file matters")
    reply = world.tg.wait_for("needs your approval with the PIN", 60)
    assert "research intervention approve INT-001" in reply
    assert jcli(world, "intervention", "show", "INT-001")["state"] == "proposed", "chat cannot approve a protected change"
    world.tg.say("/status", chat=999)                                     # a stranger gets nothing
    time.sleep(4)
    n = len(world.tg.sent)
    time.sleep(3)
    assert len(world.tg.sent) == n
    # approving at the terminal (PIN) finishes the job and Telegram says the plan moves again
    assert pty_cli(world, ["approval", "set-pin"], ["4321", "4321"])[0] == 0
    assert pty_cli(world, ["intervention", "approve", "INT-001", "--note", "agreed"], ["4321"])[0] == 0
    assert "moving again" in world.tg.wait_for("moving again", 90)
