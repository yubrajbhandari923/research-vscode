"""MCP server: protocol, tools, install, hostile input."""
import io
import json
import subprocess
import sys

import pytest
from research import mcp
from research import mcp as MCP
from research import plans as P
from research import runs as R
from research import services as S


def _exp_with_runs(p, repo, n=2):
    q = S.create_question(p, "Does alpha matter?")
    e = S.create_experiment(p, "alpha sweep", question=q["id"], parameters={"alpha": [0.1, 0.5]}, planned_runs=6)
    (repo / "sim.py").write_text(
        "import research,sys\na=float(sys.argv[1])\nresearch.log_metric('err', abs(a-0.5))\n"
        "open(f'out_{a}.png','wb').write(b'\\x89PNG\\r\\n')\nresearch.register_artifact(f'out_{a}.png', description='plot')\n")
    runs = [R.exec_run(p, e["id"], f"{sys.executable} sim.py {a}", parameters={"alpha": a}, tee=False)
            for a in (0.1, 0.5)[:n]]
    return q, e, runs


def _mcp(repo, *msgs, author=None):
    out = io.StringIO()
    MCP.serve(str(repo), io.StringIO("\n".join(json.dumps(m) for m in msgs) + "\n"), out, author=author)
    return [json.loads(l) for l in out.getvalue().splitlines()]


@pytest.mark.parametrize('message', [None, 1, [], 'text', {'method': 'ping'},
    {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'clientInfo': 1}},
    {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': [], 'arguments': []}},
    {'jsonrpc': '2.0', 'id': 1, 'method': 'ping', 'params': []}])
def test_mcp_survives_bad_request(repo, message):
    output = io.StringIO()
    ping = {'jsonrpc': '2.0', 'id': 42, 'method': 'ping'}
    mcp.serve(str(repo), io.StringIO(json.dumps(message) + '\n' + json.dumps(ping) + '\n'), output)
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert 'error' in rows[0]
    assert rows[-1] == {**{'jsonrpc': '2.0', 'id': 42}, 'result': {}}


def test_mcp_protocol_and_tools(proj, repo):
    q, e, runs = _exp_with_runs(proj, repo)
    plan = P.create_plan(proj, "P")
    P.create_task(proj, plan["id"], "do it", checks=["file:out_0.5.png"])
    call = lambda i, name, args: {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}
    res = _mcp(repo,
               {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "Claude Code"}}},
               {"jsonrpc": "2.0", "method": "notifications/initialized"},
               {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
               call(3, "task_next", {}), call(4, "task_start", {"id": "T-001"}),
               call(5, "task_note", {"id": "T-001", "text": "via mcp"}),
               call(6, "task_done", {"id": "T-001", "result": "ok"}),
               call(7, "finding_create", {"title": "mcp claim", "supports": [runs[0]["id"]]}),
               call(8, "finding_review", {"id": "F-001", "verdict": "supported"}),
               call(9, "runs_compare", {"a": runs[0]["id"], "b": runs[1]["id"]}),
               call(10, "research_current", {}), call(11, "task_start", {"bad": 1}),
               {"jsonrpc": "2.0", "id": 12, "method": "ping"},
               {"jsonrpc": "2.0", "id": 13, "method": "nope"})
    by = {r["id"]: r for r in res}
    assert sorted(by) == list(range(1, 14))  # the notification got no response
    assert by[1]["result"]["protocolVersion"] == "2025-06-18" and by[1]["result"]["capabilities"]["tools"]
    names = {t["name"] for t in by[2]["result"]["tools"]}
    assert {"research_current", "task_context", "task_done", "finding_review", "run_start", "skill_show"} <= names
    assert all(t["inputSchema"]["type"] == "object" for t in by[2]["result"]["tools"])
    text = lambda i: by[i]["result"]["content"][0]["text"]
    assert json.loads(text(3))["id"] == "T-001"
    assert json.loads(text(4))["claimed_by"] == "claude-code"  # authorship from clientInfo
    assert json.loads(text(6))["status"] == "done"
    assert by[8]["result"]["isError"] and "independent reviewer" in text(8)
    assert json.loads(text(9))["changed_parameters"] == ["alpha"]
    assert text(10).startswith("# Research context")
    assert by[11]["result"]["isError"] and "Bad arguments" in text(11)
    assert by[12]["result"] == {} and by[13]["error"]["code"] == -32601


def test_mcp_install_merges_configs(repo, proj):
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    r = MCP.install(str(repo), ["claude", "vscode", "cursor", "gemini", "codex"])
    data = json.loads((repo / ".mcp.json").read_text())
    assert data["mcpServers"]["other"] == {"command": "x"} and data["mcpServers"]["research"]["args"] == ["mcp"]
    assert json.loads((repo / ".vscode/mcp.json").read_text())["servers"]["research"]["type"] == "stdio"
    assert (repo / ".cursor/mcp.json").exists() and (repo / ".gemini/settings.json").exists()
    assert "codex mcp add research" in r["installed"]["codex"]


def test_mcp_server_as_subprocess(proj, repo):
    """The real stdio entry point: `python -m research_cli mcp`."""
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2099-01-01"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "research_search", "arguments": {"query": "x"}}}]
    p = subprocess.run([sys.executable, "-m", "research_cli", "mcp"], cwd=repo, capture_output=True, text=True,
                       input="\n".join(json.dumps(m) for m in msgs) + "\n", timeout=60)
    lines = [json.loads(l) for l in p.stdout.splitlines()]
    assert lines[0]["result"]["protocolVersion"] in MCP.PROTOCOL_VERSIONS  # unknown version → one we support
    assert lines[1]["result"]["isError"] is False
