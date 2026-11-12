"""`research logs`: what an agent is doing is readable while it runs, from the provider's own live record (never from a model)."""
import json
import os
import time

import pytest

from research import agentlog as AL, execution as E
from research.store import Project
from research.util import now_iso


@pytest.fixture
def p(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    pr = Project.init(str(root), name="x", goal="g")
    yield pr
    pr.close()


def execution(p, ident="abc12345", argv=("claude", "-p", "--output-format", "json", "do it"), state="running", **kw):
    path = E.path(p, ident)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rec = {"id": ident, "task": "T-001", "state": state, "prepared_at": now_iso(), "launched_at": now_iso(), "argv": list(argv), "cwd": p.root, "pid": os.getpid(),
           "launcher_pid": os.getpid(), "mode": "headless", "env": {}, **kw}
    with open(path, "w") as f:
        json.dump(rec, f)
    return ident


def transcript(p, lines, name="sess-1"):
    d = AL.claude_dir(p.root)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name + ".jsonl")
    with open(path, "a") as f:
        for l in lines:
            f.write(json.dumps(l) + "\n")
    return path


def say(text):
    return {"type": "assistant", "timestamp": "2026-11-12T01:00:00Z", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def tool(name, **inp):
    return {"type": "assistant", "timestamp": "2026-11-12T01:00:05Z", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": name, "input": inp}]}}


def result(text, error=False):
    return {"type": "user", "timestamp": "2026-11-12T01:00:09Z", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": text, "is_error": error}]}}


def test_a_running_claude_agent_is_readable_step_by_step_before_it_prints_anything(p):
    ident = execution(p)
    transcript(p, [{"type": "user", "message": {"role": "user", "content": "Read the brief at /x/brief.md " + "and follow it. " * 60}},
                   say("I will start by reading the solver."), tool("Read", file_path="src/solver.py"), result("def step():\n    pass\n" + "x = 1\n" * 30),
                   tool("Bash", command="python -m pytest -q tests/test_solver.py", description="run tests"), result("E   AssertionError: mass not conserved", error=True)])
    assert not os.path.exists(E.path(p, ident)[:-5] + ".out"), "stdout is still empty: Claude prints one JSON object when it exits"
    d = AL.events(p, ident)
    assert d["source"].endswith("sess-1.jsonl") and [e["kind"] for e in d["events"]] == ["prompt", "say", "tool", "result", "tool", "error"]
    text = "\n".join(AL.render(d["events"]))
    assert "I will start by reading the solver." in text and "▸ Read  src/solver.py" in text and "▸ Bash  python -m pytest -q tests/test_solver.py" in text
    assert "… 29 more line(s)" in text, "a long tool result is shortened"
    assert "✗ E   AssertionError: mass not conserved" in text, "an error never is"
    assert "task prompt" in text and "follow it. follow it." not in text
    assert "x = 1\n" * 3 in "\n".join(l.strip() + "\n" for l in AL.render(d["events"], full=True)) or "x = 1" in "\n".join(AL.render(d["events"], full=True))
    act = AL.last_activity(p, ident)
    assert act["kind"] == "error" and "mass not conserved" in act["text"]


def test_follow_returns_only_what_is_new_and_ends_with_the_final_result(p):
    ident = execution(p)
    transcript(p, [say("first")])
    r = AL.Reader(p, ident)
    assert [e["text"] for e in r.more()] == ["first"] and r.more() == []
    transcript(p, [tool("Edit", file_path="src/a.py"), say("second")])
    assert [e["kind"] for e in r.more()] == ["tool", "say"]
    with open(E.path(p, ident)[:-5] + ".out", "w") as f:
        f.write(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "Done: 64 tests pass.", "num_turns": 12, "total_cost_usd": 0.4213, "session_id": "sess-1"}) + "\n")
    ev = r.more()
    assert [e["kind"] for e in ev] == ["done"] and ev[0]["text"] == "Done: 64 tests pass.  (12 turns, $0.42)"


def test_an_older_session_in_the_same_worktree_is_not_mistaken_for_this_attempt(p):
    old = transcript(p, [say("from an earlier attempt")], name="old")
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    ident = execution(p)
    assert AL.events(p, ident)["events"] == [] and AL.last_activity(p, ident) is None
    transcript(p, [say("this attempt")], name="new")
    assert [e["text"] for e in AL.events(p, ident)["events"]] == ["this attempt"]
    transcript(p, [say("a named session wins")], name="named")
    assert [e["text"] for e in AL.events(p, ident, session_id="new")["events"]] == ["this attempt"]


def test_a_json_event_stream_and_plain_output_are_both_readable(p):
    ident = execution(p, "c0de0001", argv=("codex", "exec", "--json", "do it"), state="completed", exit_code=0, ended_at=now_iso())
    base = E.path(p, ident)[:-5]
    with open(base + ".out", "w") as f:
        f.write(json.dumps({"type": "item.started", "item": {"type": "command_execution", "command": "pytest -q"}}) + "\n")
        f.write(json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": "pytest -q", "aggregated_output": "3 passed", "exit_code": 0}}) + "\n")
        f.write(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "All tests pass."}}) + "\n")
        f.write("a plain line from some other provider\n")
    with open(base + ".err", "w") as f:
        f.write("warning: something on stderr\n")
    ev = AL.events(p, ident)["events"]
    assert [(e["kind"], e["text"]) for e in ev] == [("tool", "shell  pytest -q"), ("result", "3 passed"), ("say", "All tests pass."), ("raw", "a plain line from some other provider"),
                                                    ("stderr", "warning: something on stderr")]


def test_a_launch_that_never_started_says_why(p):
    ident = execution(p, "dead0001", state="failed", exit_code=127, error="could not start 'claude': [Errno 2] No such file or directory", ended_at=now_iso())
    ev = AL.events(p, ident)["events"]
    assert ev and ev[-1]["kind"] == "error" and "could not start" in ev[-1]["text"]
