"""H.2: `research init` + `agent setup <provider>` ends in the same working orchestration for every provider (fake binaries only)."""
import os

import pytest

from research import doctor, initproject as IP, providers, setup_agents as SA, strategy as S
from research.store import Project
from test_providers_registry import FAKES, _fake_path, _strip


@pytest.mark.parametrize("prov", providers.KNOWN)
def test_init_then_agent_setup_is_green_and_identical_in_shape(tmp_path, monkeypatch, prov):
    _fake_path(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_OPENCODE_VERSION", "opencode v2.1.4")   # v1 is end-of-life and warns by design
    root = tmp_path / "w"
    root.mkdir()
    assert IP.apply(str(root), {"name": "demo", "goal": "g", "agents": {"enabled": [prov], "default": prov}, "create_workspace": False,
                                "tools": {"link_skills": False, "starter_hooks": False}, "guide": {"enabled": False}})["ok"]
    main = str(root / "main")
    with Project.find(main) as p:
        before = _strip(p.raw_config, prov)
        dry = SA.setup(p, prov)
        assert not dry["applied"] and dry["provider"] == prov and dry["cli"]["available"]
        r = SA.setup(p, prov, apply=True)
        again = SA.setup(p, prov, apply=True)
        assert r["applied"] and all(v is True for v in r["routing_verified"].values()), r["routing_verified"]
        assert not r["unavailable"], r["unavailable"]
        assert again["mcp_has_research"] is True and r["instructions"]["file"] == providers.ADAPTERS[prov].caps["instructions"]
        ag = p.config["agents"]
        assert all(f"{prov}-{lv}" in ag["profiles"] for lv in providers.LEVELS)
        assert ag["roles"]["implementer"] == f"{prov}-balanced" and ag["roles"]["analyst"] == f"{prov}-deep"
        for n in S.BUILTIN:
            assert S.validate(p, n)["ok"], (n, S.validate(p, n))
        assert _strip(p.raw_config, prov) == before    # setup re-applies what init wrote; it never reshapes the config
    bad = [c for c in doctor.run(main)["checks"] if c["level"] in ("error", "warn")]
    assert bad == [], bad


def test_setup_rejects_an_unknown_provider_and_a_bad_opencode_model(tmp_path, monkeypatch):
    _fake_path(monkeypatch, tmp_path)
    root = tmp_path / "w"
    root.mkdir()
    IP.apply(str(root), {"name": "demo", "goal": "g", "agents": {"enabled": ["opencode"], "default": "opencode"}, "create_workspace": False,
                         "tools": {"link_skills": False, "starter_hooks": False}, "guide": {"enabled": False}})
    with Project.find(str(root / "main")) as p:
        with pytest.raises(Exception, match="Unknown provider"):
            SA.setup(p, "nope")
        with pytest.raises(Exception, match="provider/model"):
            SA.setup(p, "opencode", model="sonnet", apply=True)
        SA.setup(p, "opencode", model="opencode/big-pickle", apply=True)
        assert p.load_config()["agents"]["profiles"]["opencode"]["model"] == "opencode/big-pickle"
