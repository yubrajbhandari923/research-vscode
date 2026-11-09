"""WP8: the regrouped CLI. Old command names stay as hidden aliases; `research --help` shows only the new surface."""
import argparse
import re

import pytest

from research import providers as PV
from research_cli.main import build_parser, main

OLD = ["resume", "context", "dispatch", "execution", "exec", "provider", "project-init", "agents", "agents-md", "setup", "usage",
       "rpc", "rebuild", "bin"]
NEW_VISIBLE = ["init", "status", "current", "agent", "doctor", "promote"]
REGROUPED_AWAY = ["resume", "context", "dispatch", "execution", "provider", "project-init", "agents", "agents-md", "setup", "usage"]


def _top():
    sp = [a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)][0]
    return sp, {c.dest for c in sp._choices_actions}


def _run(argv, capsys):
    with pytest.raises(SystemExit) as e:
        main(argv)
    return e.value.code, capsys.readouterr().out


def test_every_hidden_alias_answers_help(capsys):
    sp, visible = _top()
    hidden = sorted(n for n in sp.choices if n not in visible and not any(n in (c.option_strings or []) for c in sp._choices_actions))
    # aliases of visible commands (q, exp, ws...) are listed next to them; hidden ones must include every old name
    assert set(OLD) <= set(hidden), set(OLD) - set(hidden)
    for name in OLD:
        code, out = _run([name, "--help"], capsys)
        assert code == 0 and out.startswith("usage: research " + name), name
    assert _run(["setup", "agents", "--help"], capsys)[0] == 0


def test_help_lists_the_regrouped_commands_only(capsys):
    _, out = _run(["--help"], capsys)
    listed = set(re.findall(r"^    ([a-z][a-z-]*)\b", out, re.M))
    assert set(NEW_VISIBLE) <= listed
    assert not (set(REGROUPED_AWAY) & listed), set(REGROUPED_AWAY) & listed
    assert "==SUPPRESS==" not in out and len(listed) <= 51        # 51: `research site` (3.3.0) is a top-level group on purpose
    code, agent_help = _run(["agent", "--help"], capsys)
    for sub in ("dispatch", "profiles", "providers", "executions", "instructions", "setup", "usage"):
        assert re.search(r"^    " + sub + r"\b", agent_help, re.M), sub


def test_agent_setup_takes_its_providers_from_the_registry(capsys):
    agent = [a for a in _top()[0].choices["agent"]._actions if isinstance(a, argparse._SubParsersAction)][0]
    setup = agent.choices["setup"]
    prov = [a for a in setup._actions if a.dest == "provider"][0]
    assert tuple(prov.choices) == PV.KNOWN and prov.default == PV.KNOWN[0]


def test_old_and_new_spellings_do_the_same(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["init", ".", "--name", "T", "--json"]) == 0

    def out(argv):
        capsys.readouterr()
        assert main(argv) == 0
        return capsys.readouterr().out

    assert out(["resume"]) == out(["status", "--full"]) != out(["status"])
    assert out(["context", "--print"]) == out(["current"])
    assert out(["context", "--json"]) == out(["current", "--quiet", "--json"])
    assert out(["context"]) == out(["current", "--quiet"])
    assert out(["agents", "--json"]) == out(["agent", "profiles", "--json"])
    assert out(["provider", "list", "--json"]) == out(["agent", "providers", "list", "--json"])
    assert out(["execution", "list", "--json"]) == out(["agent", "executions", "list", "--json"]) == out(["exec", "list", "--json"])
    assert out(["usage", "--json"]) == out(["agent", "usage", "--json"])
    assert out(["agents-md", "--preview"]) == out(["agent", "instructions", "--preview"])
    assert out(["setup", "agents", "--provider", PV.KNOWN[0], "--json"]) == out(["agent", "setup", PV.KNOWN[0], "--json"])
