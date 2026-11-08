"""RPC server: hostile input and the agent-setup sections."""
import io
import json

import pytest
from research import hooks
from research import plans
from research import rpc
from research import services


def use(p, name="sonnet_first"):
    p.save_config({"orchestration.active_strategy": name})
    p.config = p.load_config()


@pytest.fixture
def plan(proj):
    return plans.create_plan(proj, "p", objective="o")


def mk(p, plan, **kw):
    return p.get("task", plans.create_task(p, plan["id"], "t", **kw)["id"])


def test_rpc_agent_setup_has_new_sections(proj, plan, repo):
    from research import api
    use(proj)
    t = mk(proj, plan, task_type="implementation", acceptance_criteria="a", checks=["cmd:true"])
    e = services.create_experiment(proj, "e", hypothesis="h")
    sess = api.Session(str(repo))
    c = lambda op_, **kw: api.call(op_, sess, kw)
    c("task_pin", id=t["id"], level="deep")
    hooks.add(proj, "h1", "preflight", "true")
    d = c("agent_setup")
    row = next(x for x in d["tasks"] if x["id"] == t["id"])
    assert row["override"] == {"level": "deep"} and row["route"]["level"] == "deep" and row["checks"] == 1
    assert d["hooks"][0]["name"] == "h1" and not d["hooks"][0]["approved"] and d["usage"]["groups"] == {}
    assert d["experiments"][0]["id"] == e["id"]
    assert c("hook_toggle", name="h1", enabled=False)["enabled"] is False
    assert c("task_pin", id=t["id"], clear=True)["override"] is None


@pytest.mark.parametrize('message', [None, 1, [], 'text', {'method': 1}, {'method': 'ping', 'params': []},
                                      {'method': 'ping', 'params': {'author': []}},
                                      {'method': 'ping', 'params': {'author': {'author_type': 'invalid'}}}])
def test_rpc_survives_bad_request(repo, message):
    output = io.StringIO()
    rpc.serve(str(repo), io.StringIO(json.dumps(message) + '\n' + json.dumps({'id': 42, 'method': 'ping'}) + '\n'), output)
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert 'error' in rows[1]
    assert rows[-1]['id'] == 42 and 'result' in rows[-1]
