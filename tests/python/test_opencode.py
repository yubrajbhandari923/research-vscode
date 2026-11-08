"""WP13 (D.11): the opencode adapter against a scripted fake binary (tests/python/fakes/opencode). No model is ever called."""
import json
import os
import subprocess
import sys
import time

import pytest

from research import agents, doctor, execution as EX, isolation, mcp, providers as PR, usage as U
from research.providers import opencode as OC
from research.store import Project
from research.util import ResearchError
from test_providers import _task  # noqa: F401
from test_workspace import shared  # noqa: F401  (fixture)

FAKE = os.path.join(os.path.dirname(__file__), "fakes", "opencode")
BRIEF = "/ledger/cache/dispatch/T-1-implementer.md"
PROMPT = f"You are the implementer on this research project. Read the brief at {BRIEF} and follow it exactly."


@pytest.fixture(autouse=True)
def _fresh_version_cache():
    OC._VERSION_CACHE.clear()
    OC._HELP_CACHE.clear()
    yield
    OC._VERSION_CACHE.clear()
    OC._HELP_CACHE.clear()


def _build(prof=None, model=None, effort=None, headless=True, prompt=PROMPT):
    return PR.build_argv({"provider": "opencode", "command": FAKE, **(prof or {})}, "opencode", model, prompt, "/w", [], headless, effort)


def test_headless_argv_golden():
    assert _build(model="anthropic/claude-sonnet-5-5", effort="high") == [
        FAKE, "run", "--format", "json", "-m", "anthropic/claude-sonnet-5-5", "--variant", "high", PROMPT, "-f", BRIEF]   # -f last: it swallows a following positional
    assert _build() == [FAKE, "run", "--format", "json", PROMPT, "-f", BRIEF]
    assert _build(prompt="no brief here") == [FAKE, "run", "--format", "json", "no brief here"]


def test_interactive_argv_passes_prompt_only_when_the_cli_lists_it():
    assert _build(model="a/b", headless=False) == [FAKE, "-m", "a/b", "--prompt", PROMPT]


def test_policy_refusals():
    for bad in ("--auto", "--yolo", "--dangerously-skip-permissions"):
        with pytest.raises(ResearchError, match="permission checks"):
            _build({"args": [bad]})
    with pytest.raises(ResearchError, match="provider/model"):
        _build(model="claude-sonnet-5-5")


@pytest.mark.parametrize("version,major", [("opencode v2.1.4", 2), ("1.19.4", 1)])
def test_version_probe_and_config_shape_per_major(monkeypatch, version, major):
    monkeypatch.setenv("FAKE_OPENCODE_VERSION", version)
    vi = OC.version_info(FAKE)
    assert vi["major"] == major and vi["line"] == f"v{major}"
    env = {"RESEARCH_AGENT": "opencode", "RESEARCH_AGENT_ROLE": "implementer", "RESEARCH_ROOT": "/w"}
    cfg = json.loads(OC.extra_env({"command": FAKE}, env, True)["OPENCODE_CONFIG_CONTENT"])
    assert cfg["mcp"]["research"]["environment"]["RESEARCH_AGENT"] == "opencode" and cfg["mcp"]["research"]["command"] == ["research", "mcp"]
    if major == 2:
        assert "permission" not in cfg and {"action": "shell", "resource": "research *", "effect": "allow"} in cfg["permissions"]
        assert not any(r["effect"] == "deny" or r["resource"] == "*" and r["action"] == "shell" for r in cfg["permissions"])   # a deny would also block `research`
    else:
        assert "permissions" not in cfg and cfg["permission"]["bash"]["*"] == "ask" and cfg["permission"]["bash"]["research *"] == "allow"
    inter = json.loads(OC.extra_env({"command": FAKE}, env, False)["OPENCODE_CONFIG_CONTENT"])
    assert set(inter) == {"mcp"}   # interactive sessions keep the user's own permissions


def test_unreadable_version_refuses_headless_only(monkeypatch):
    monkeypatch.setenv("FAKE_OPENCODE_VERSION", "banana")
    assert OC.version_info(FAKE)["major"] is None
    with pytest.raises(ResearchError, match="opencode version"):
        OC.extra_env({"command": FAKE}, {}, True)
    assert "OPENCODE_CONFIG_CONTENT" in OC.extra_env({"command": FAKE}, {}, False)


REAL = "\n".join(json.dumps(x) for x in [   # shapes captured from opencode 1.19.5
    {"type": "step_start", "sessionID": "ses_a", "part": {"type": "step-start"}},
    {"type": "step_finish", "sessionID": "ses_a", "part": {"reason": "tool-calls", "tokens": {"total": 8034, "input": 6071, "output": 51, "reasoning": 120, "cache": {"write": 0, "read": 1792}}, "cost": 0.5}},
    {"type": "text", "sessionID": "ses_a", "part": {"text": "hello"}},
    {"type": "step_finish", "sessionID": "ses_a", "part": {"reason": "stop", "tokens": {"input": 10, "output": 5, "cache": {"write": 1, "read": 2}}, "cost": 0.25}}])


def test_usage_parser_sums_steps_and_flags_partial():
    r = U.parse_opencode_jsonl(REAL + "\nnot json\n")
    assert (r["input_tokens"], r["output_tokens"], r["cache_read_tokens"], r["cache_write_tokens"], r["cost_usd"]) == (6081, 56, 1794, 1, 0.75)
    assert r["complete"] and r["session_id"] == "ses_a" and r["result_text"] == "hello" and r["malformed_lines"] == 1 and r["final_reason"] == "stop"
    cut = U.parse_opencode_jsonl("\n".join(REAL.splitlines()[:2]))
    assert cut["complete"] is False and cut["input_tokens"] == 6071
    err = U.parse_opencode_jsonl(json.dumps({"type": "error", "sessionID": "s", "error": {"name": "X", "data": {"message": "boom"}}}))
    assert err["errors"] == ["boom"] and "input_tokens" not in err


def test_session_id_and_final_event():
    assert PR.session_id_of("opencode", REAL) == "ses_a" and PR.session_id_of("opencode", "") is None
    lines = REAL.splitlines()
    assert [OC.is_final_event(l) for l in lines] == [False, False, False, True] and not OC.is_final_event("garbage")


def test_resume_argv_and_probe_fallback(monkeypatch):
    argv = _build(model="a/b")
    assert PR.resume_argv("opencode", argv, "ses_x") == [FAKE, "run", "--session", "ses_x", "--format", "json", "-m", "a/b", PROMPT, "-f", BRIEF]
    assert PR.native_resume_supported("opencode", FAKE)
    monkeypatch.setenv("FAKE_OPENCODE_NO_RESUME", "1")
    OC._HELP_CACHE.clear()
    assert not OC.resume_probe(FAKE)   # unprobeable -> the existing context handoff


def test_mcp_merge_jsonc_and_registered(tmp_path):
    root = str(tmp_path)
    assert OC.mcp_registered(root) is False
    (tmp_path / "opencode.json").write_text(json.dumps({"model": "x/y", "mcp": {"other": {"type": "remote", "url": "https://e"}}}))
    r = mcp.install(root, ["opencode"])
    assert r["installed"]["opencode"] == "opencode.json"
    data = json.loads((tmp_path / "opencode.json").read_text())
    assert data["model"] == "x/y" and "other" in data["mcp"] and data["mcp"]["research"]["environment"]["RESEARCH_AGENT"] == "opencode"
    assert OC.mcp_registered(root) is True
    j = tmp_path / "j"
    j.mkdir()
    text = '{\n // keep me\n "model": "a/b", /* c */ "url": "https://x//y"\n}\n'
    (j / "opencode.jsonc").write_text(text)
    out = OC.mcp_install(str(j))
    assert out["where"] == "opencode.jsonc" and "research" in out["manual"] and (j / "opencode.jsonc").read_text() == text and not (j / "opencode.json").exists()
    assert OC.mcp_registered(str(j)) is False


def test_secret_dir_is_never_mountable():
    assert ".local/share/opencode" in isolation.SECRET_DIRS


def test_doctor_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_OPENCODE_VERSION", "1.19.4")
    monkeypatch.setenv("OPENCODE_DISABLE_CLAUDE_CODE_SKILLS", "1")
    (tmp_path / ".claude" / "skills" / "Bad_Name").mkdir(parents=True)
    ids = {r["id"] for r in OC.checks(str(tmp_path), FAKE)}
    assert {"agents.opencode.eol", "agents.opencode.agents_md", "agents.opencode.skills_env", "agents.opencode.skill_names", "agents.opencode.attribution"} <= ids


# ---------------------------------------------------------------------------------- watchdog, end to end on the fake
def _dispatch_and_run(repo, monkeypatch, scenario, **prof):
    monkeypatch.setenv("FAKE_OPENCODE_SCENARIO", scenario)
    with Project(str(repo)) as p:
        t, ws = _task(p)
        p.config.setdefault("agents", {}).setdefault("profiles", {})["opencode"] = {"provider": "opencode", "command": FAKE, **prof}
        d = agents.dispatch(p, "implementer", t["id"], profile="opencode", model="opencode/big-pickle", headless=True)
        assert d["usage_parser"] == "opencode_jsonl" and json.loads(d["env"]["OPENCODE_CONFIG_CONTENT"])["mcp"]["research"]
        t0 = time.time()
        rc = subprocess.run(d["argv"], env={**os.environ, **d["env"]}, cwd=str(repo), timeout=60).returncode
        rec = json.load(open(EX.path(p, d["execution_id"])))
        return rc, rec, U.records(p), time.time() - t0, p.q("SELECT summary FROM events WHERE action = 'execution_watchdog'")


def test_ok_run_records_usage_and_session(shared, monkeypatch):  # noqa: F811
    rc, rec, usage, _, _ = _dispatch_and_run(shared[0], monkeypatch, "ok")
    assert rc == 0 and rec["state"] == "completed" and "watchdog" not in rec and "provider_warning" not in rec
    assert rec["usage_recorded"] and usage[-1]["input_tokens"] == 200 and usage[-1]["cost_usd"] == 0.002 and "partial" not in usage[-1]


def test_hang_after_final_event_is_stopped_within_grace(shared, monkeypatch):  # noqa: F811
    rc, rec, usage, secs, ev = _dispatch_and_run(shared[0], monkeypatch, "hang_after_stop", exit_grace_seconds=1)
    assert rc == 0 and rec["state"] == "completed" and rec["exit_code"] == 0 and "did not exit" in rec["watchdog"] and secs < 30 and len(ev) == 1
    assert usage[-1]["input_tokens"] == 200 and "partial" not in usage[-1]


def test_missing_final_event_is_completed_with_a_warning_and_partial_usage(shared, monkeypatch):  # noqa: F811
    rc, rec, usage, _, _ = _dispatch_and_run(shared[0], monkeypatch, "no_final")
    assert rc == 0 and rec["state"] == "completed" and "no final event" in rec["provider_warning"]
    assert usage[-1]["partial"] is True and usage[-1]["input_tokens"] == 100   # recorded, never zero, never dropped


def test_tool_calls_are_not_final(shared, monkeypatch):  # noqa: F811
    rc, rec, usage, _, _ = _dispatch_and_run(shared[0], monkeypatch, "tool_calls_then_stop", exit_grace_seconds=1)
    assert rc == 0 and "watchdog" not in rec and usage[-1]["input_tokens"] == 300


def test_silent_run_is_stopped_by_the_idle_timeout(shared, monkeypatch):  # noqa: F811
    rc, rec, usage, secs, _ = _dispatch_and_run(shared[0], monkeypatch, "slow_silent", idle_timeout_seconds=2)
    assert rc == 124 and rec["state"] == "failed" and rec["watchdog"] == "no output for 2 s" and secs < 30


def test_error_run_fails_normally(shared, monkeypatch):  # noqa: F811
    rc, rec, usage, _, _ = _dispatch_and_run(shared[0], monkeypatch, "error")
    assert rc == 1 and rec["state"] == "failed" and "watchdog" not in rec
