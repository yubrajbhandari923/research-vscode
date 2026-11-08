"""Usage accounting for headless agent runs."""
import json
import sys

import pytest
from research import agents
from research import orchestrate as O
from research import plans
from research import usage as U
from research.util import ResearchError


# ------------------------------------------------------------------ usage
CLAUDE_JSON = json.dumps({"type": "result", "result": "done", "total_cost_usd": 0.0123, "duration_ms": 900, "session_id": "s1",
                          "usage": {"input_tokens": 100, "output_tokens": 40, "cache_read_input_tokens": 500}})


def use(p, name="sonnet_first"):
    p.save_config({"orchestration.active_strategy": name})
    p.config = p.load_config()


@pytest.fixture
def plan(proj):
    return plans.create_plan(proj, "p", objective="o")


def mk(p, plan, **kw):
    return p.get("task", plans.create_task(p, plan["id"], "t", **kw)["id"])


def test_usage_parse_never_invents(proj):
    r = U.parse_claude_json(CLAUDE_JSON)
    assert r["input_tokens"] == 100 and r["cache_read_tokens"] == 500 and "cache_write_tokens" not in r and r["cost_usd"] == 0.0123
    assert U.parse_claude_json("not json") == {}
    assert U.record(proj, "x", {}, task="T-1") is None and U.records(proj) == []


def test_usage_summary_counts_reported_not_zero(proj):
    U.record(proj, "claude_json", {"input_tokens": 100, "output_tokens": 10, "cost_usd": 0.5}, task="T-1", strategy="a", level="fast")
    U.record(proj, "manual", {"input_tokens": 300}, task="T-2", strategy="a", level="fast")
    U.record(proj, "manual", {"input_tokens": 50, "cost_usd": 1.0}, task="T-3", strategy="b", level="deep")
    g = U.summary(proj, "strategy")["groups"]
    assert g["a"]["input_tokens"] == {"total": 400, "reported": 2} and g["a"]["cost_usd"] == {"total": 0.5, "reported": 1}
    assert g["a"]["cache_read_tokens"] == {"total": None, "reported": 0} and g["a"]["mean_per_task"]["cache_read_tokens"] is None
    assert g["b"]["tasks"] == 1 and U.summary(proj, "level")["groups"]["deep"]["records"] == 1


def test_headless_run_records_usage_and_manual_fallback(proj, plan, repo):
    script = repo / "fake_claude.py"
    script.write_text(f"import sys\nprint({CLAUDE_JSON!r})\n")
    proj.config["agents"]["profiles"]["mock"] = {"command": sys.executable, "args": [str(script)], "headless_args": ["--json"],
                                                 "usage_parser": "claude_json", "model": "m-mock", "model_flag": "--model"}
    d = agents.dispatch(proj, "analyst", None, "x", profile="mock", headless=True)
    assert d["argv"][:3] == [sys.executable, str(script), "--json"]
    h = agents.run_headless(proj, d)
    assert h["exit_code"] == 0 and h["result"] == "done" and h["usage"]["input_tokens"] == 100 and h["usage"]["model"] == "m-mock"
    proj.config["agents"]["profiles"]["plain"] = {"command": sys.executable, "model": None}
    with pytest.raises(ResearchError, match="headless_args"):
        agents.dispatch(proj, "analyst", None, "x", profile="plain", headless=True)
    use(proj)
    t = mk(proj, plan, acceptance_criteria="a", checks=["cmd:true"])
    O.record_attempt(proj, t["id"], "balanced", "fail", "incorrect_implementation", usage={"input_tokens": 7})
    assert U.summary(proj, "level")["groups"]["balanced"]["input_tokens"]["total"] == 7
    assert O.status(proj, t["id"])["usage"]["input_tokens"] == 7        # the manual ledger copy is preserved
