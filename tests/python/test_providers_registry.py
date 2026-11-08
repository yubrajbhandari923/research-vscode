"""WP5a: one provider registry, provider-neutral strategies, init parity across providers."""
import os
import re
import subprocess
import sys

import pytest

from research import doctor, initproject as IP, providers, strategy as S
from research.store import DEFAULT_CONFIG, Project

FAKES = os.path.join(os.path.dirname(__file__), "fakes")


@pytest.mark.parametrize("name", list(providers.ADAPTERS))
def test_every_adapter_has_a_complete_level_pack(name):
    a = providers.ADAPTERS[name]
    assert set(a.levels) == set(providers.LEVELS) and a.build and a.caps and a.label
    assert providers.CAPABILITIES[name] is a.caps


def test_default_profiles_are_generated_for_every_known_provider():
    profs = DEFAULT_CONFIG["agents"]["profiles"]
    for n in providers.KNOWN:
        assert n in profs and all(f"{n}-{lv}" in profs for lv in providers.LEVELS)
    assert profs["claude-balanced"]["model"] == "claude-sonnet-5-5" and profs["codex-deep"]["effort"] == "high"
    assert DEFAULT_CONFIG["agents"]["handoff"]["fallback_order"] == list(providers.KNOWN)


def test_old_strategy_names_are_aliases_that_behave_identically(proj):
    for old, new in S.ALIASES.items():
        assert S.definition(proj, old) == S.definition(proj, new) and S.fingerprint(proj, old) == S.fingerprint(proj, new)
        assert S.validate(proj, old)["ok"] and old not in S.NAMES and new in S.NAMES
    assert {r["name"]: r["alias_of"] for r in S.listing(proj)}["sonnet_first"] == "balanced_first"


def test_strategy_use_activates_validates_and_dry_runs(proj):
    r = S.use(proj, "fast_max", dry_run=True)
    assert not r["applied"] and S.active_name(proj) != "fast_max"
    assert S.use(proj, "fast_max")["applied"] and S.active_name(proj) == "fast_max"
    with pytest.raises(Exception, match="Unknown strategy"):
        S.use(proj, "nope")


def _fake_path(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))   # `codex mcp add` is a fake, but keep the real home out of reach anyway
    monkeypatch.setenv("PATH", FAKES + os.pathsep + os.environ["PATH"])


def _strip(cfg, prov):
    """Config as flat dotted keys with the provider name erased, so providers can be compared."""
    flat = {}
    def walk(x, path):
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, path + [str(k).replace(prov, "<P>")])
        else:
            flat[".".join(path)] = x.replace(prov, "<P>") if isinstance(x, str) else x
    walk({k: v for k, v in cfg.items() if k != "project"}, [])
    return flat


def test_init_writes_a_working_orchestration_identical_in_shape_for_each_default_provider(tmp_path, monkeypatch):
    _fake_path(monkeypatch, tmp_path)
    shapes = {}
    for prov in providers.KNOWN:
        root = tmp_path / f"work-{prov}"
        root.mkdir()
        res = IP.apply(str(root), {"name": "demo", "goal": "g", "agents": {"enabled": [prov], "default": prov}, "create_workspace": False,
                                   "tools": {"link_skills": False, "starter_hooks": False}, "guide": {"enabled": False}})
        assert res["ok"]
        with Project.find(str(root / "main")) as p:
            ag, orch = p.config["agents"], p.config["orchestration"]
            assert ag["default_profile"] == prov and orch["active_strategy"] == "balanced_first" and orch["enabled"]
            assert ag["roles"] == {"planner": f"{prov}-balanced", "implementer": f"{prov}-balanced", "verifier": f"{prov}-balanced", "analyst": f"{prov}-deep"}
            assert S.definition(p, "balanced_first")["levels"] == providers.levels_for(prov)
            for n in S.BUILTIN:   # every built-in strategy resolves for this provider
                assert S.validate(p, n)["ok"], (n, S.validate(p, n))
            assert all(r["installed"] for r in S.show(p)["resolved_levels"].values())
            shapes[prov] = _strip(p.raw_config, prov)
        rep = doctor.run(str(root / "main"))
        assert rep["summary"]["errors"] == 0, [c for c in rep["checks"] if c["severity"] == "error"]
    first = shapes[providers.KNOWN[0]]
    assert all(s == first for s in shapes.values()), {k: set(v) ^ set(first) for k, v in shapes.items()}


def test_no_hard_coded_provider_list_outside_the_registry():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    pat = re.compile(r"""\(\s*["']claude["']\s*,\s*["']codex["']|["']claude["']\s*,\s*["']codex["']""")
    hits = []
    for base, _, files in os.walk(os.path.join(root, "packages")):
        if "node_modules" in base or ".vscode-test" in base or "_vendor" in base:
            continue
        for f in files:
            if f.endswith((".py", ".ts", ".tsx")):
                fp = os.path.join(base, f)
                if pat.search(open(fp, encoding="utf-8", errors="ignore").read()):
                    hits.append(fp)
    assert hits == []
