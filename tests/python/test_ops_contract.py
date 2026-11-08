"""The op layer's contracts: what the extension calls exists, MCP schemas fit their ops, mutating ops are flagged."""
import io
import json
import os
import re

import pytest

from research import api
from research.mcp import TOOLS, _tools

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "packages", "vscode-extension", "src")
# Allow-list entries in panels.ts that name no RPC method (dead strings; remove them from CONTROL_RPC/MUTATING_RPC, then from here).
STALE_IN_EXTENSION = {"setup_environment", "setup_preview", "setup_apply"}

# The methods that used to be listed in rpc.MUTATING (frozen: removing one from an op is a behaviour change).
OLD_MUTATING = set("""pack_set skill_install skill_toggle skills_upgrade hook_install hook_install_starters skills_link hook_approve
hook_toggle hook_run guide_init guide_build guide_highlight guide_log guide_draft guide_draft_done guide_cite_add plan_promote plan_yaml_apply
plan_control supervisor_task proposal_decide approval_set_pin init_yaml_apply supervisor_daemon intervention_request intervention_action
debug_run telegram_set telegram_test telegram_clear hpc_submit hpc_init hpc_sync freeze_tests create update set_status set_baseline
synthesize supersede_finding run_attach run_start run_cancel register_artifact log_metric init update_project sync rebuild release_task
task_note verify_task review_finding report dispatch skills_add skills_remove skills_update skills_set strategy_use setup_agents task_pin
experiment_check""".split())
# MCP tools that were read-only (readOnlyHint) before the op layer.
READ_ONLY_TOOLS = set("""research_current research_search research_show task_next task_context plan_status run_status run_logs runs_compare
skills_list skill_show report_full guide_status guide_highlights guide_draft_check guide_validate doctor workspace_list hpc_doctor""".split())


def _extension_ops():
    found = set()
    for dp, _, fs in os.walk(SRC):
        for f in fs:
            if not f.endswith((".ts", ".tsx")):
                continue
            with open(os.path.join(dp, f), encoding="utf-8") as fh:
                t = fh.read()
            found |= set(re.findall(r"\b(?:rpc|call)(?:<[^>(]*>)?\(\s*['\"]([a-z_]+)['\"]", t))
            if f == "panels.ts":
                for name in ("CONTROL_RPC", "MUTATING_RPC"):
                    found |= set(re.findall(r"'([a-z_]+)'", re.search(name + r" = new Set\(\[(.*?)\]\)", t, re.S).group(1)))
    return found


def test_every_op_the_extension_calls_exists():
    missing = _extension_ops() - set(api.OPS)
    assert missing == STALE_IN_EXTENSION, f"extension names an RPC method that does not exist: {sorted(missing - STALE_IN_EXTENSION)}; stale list now: {sorted(missing)}"


def test_mcp_schemas_fit_their_ops():
    tools = _tools()
    assert [t.name for t in tools] == [n for n, *_ in TOOLS]
    by_tool = [o.mcp for o in api.OPS.values() if o.mcp]
    assert sorted(by_tool) == sorted(set(by_tool)) == sorted(n for n, *_ in TOOLS), "every tool is exactly one op's `mcp=`"
    for t in tools:
        props, req = t.schema["properties"], t.schema["required"]
        o = t.op
        assert o.open or set(props) <= set(o.params), f"{t.name}: schema keys {sorted(set(props) - set(o.params))} are not parameters of op {o.name}"
        assert set(req) <= set(props)
        assert all(o.params.get(k, True) for k in req), f"{t.name}: required key with a default in op {o.name}"
        assert all(not needed or k in props or o.open for k, needed in o.params.items()), f"{t.name}: op {o.name} requires {[k for k, n in o.params.items() if n and k not in props]} that the schema cannot pass"


def test_mutating_ops_are_flagged():
    assert OLD_MUTATING <= {n for n, o in api.OPS.items() if o.mutates}, sorted(OLD_MUTATING - {n for n, o in api.OPS.items() if o.mutates})
    assert {t.name for t in _tools() if not t.mutating} == READ_ONLY_TOOLS


def test_changed_flag_follows_database_commits(proj, repo):
    s = api.Session(str(repo))
    api.call("create", s, {"type": "question", "title": "Q"})
    assert s.changed is True
    api.call("show", s, {"id": "Q-001"})
    assert s.changed is False
    api.call("ping", s)
    assert s.changed is False


def test_author_override_is_per_request(proj, repo):
    s = api.Session(str(repo))
    api.call("create", s, {"type": "question", "title": "A"}, author={"agent": "bot", "author_type": "agent"})
    api.call("create", s, {"type": "question", "title": "B"})
    got = {r["title"]: r["author_type"] for r in api.call("tree", s)["questions"]}
    assert got == {"A": "agent", "B": "human"}


def test_bad_shape_is_bad_params_but_an_inner_type_error_is_not(proj, repo, monkeypatch):
    s = api.Session(str(repo))
    with pytest.raises(api.BadParams):
        api.call("show", s, {"id": "Q-1", "bogus": 1})
    with pytest.raises(api.UnknownOp):
        api.call("nope", s)
    monkeypatch.setattr("research.views.show", lambda *a, **k: (_ for _ in ()).throw(TypeError("inner")))
    with pytest.raises(TypeError):
        api.call("show", s, {"id": "Q-1"})


def test_ready_event_lists_ops(repo):
    from research.rpc import serve
    out = io.StringIO()
    serve(str(repo), io.StringIO(""), out)
    ready = json.loads(out.getvalue().splitlines()[0])
    assert ready["event"] == "ready" and ready["ops"] == sorted(api.OPS) and "tree" in ready["ops"]


def test_chat_surface_refuses_ops_without_a_chat_policy(proj, repo):
    s = api.Session(str(repo))
    with pytest.raises(api.PolicyBlocked):
        api.call("tree", s, surface="chat")
