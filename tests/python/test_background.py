"""Work keeps running after logout: the facts that decide it are checked, and the service that guarantees it is written correctly."""
import os

import pytest

from research import background as BG, supervisor as SV
from research.store import Project


@pytest.fixture
def p(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    root = tmp_path / "truect-phantom-recovery"
    root.mkdir()
    pr = Project.init(str(root), name="x", goal="g")
    yield pr
    pr.close()


def facts(monkeypatch, kup=False, linger=False, systemd=True, running=True, loginctl=True):
    monkeypatch.setattr(BG, "kill_user_processes", lambda: kup)
    monkeypatch.setattr(BG, "lingering", lambda: linger)
    monkeypatch.setattr(BG, "has_systemd_user", lambda: systemd)
    monkeypatch.setattr(BG.shutil, "which", lambda name: "/usr/bin/" + name if loginctl else None)
    monkeypatch.setattr(SV, "daemon_status", lambda p: {"running": running, "pid": os.getpid()})
    monkeypatch.setattr(BG, "_proc_env", lambda pid: {"PATH": "/usr/bin"})
    for k in ("SSH_AUTH_SOCK", "SSH_CONNECTION", "SSH_CLIENT"):
        monkeypatch.delenv(k, raising=False)


def row(c, what):
    return next(r for r in c["rows"] if r["what"] == what)


def test_a_detached_supervisor_survives_logout_on_an_ordinary_machine(p, monkeypatch):
    facts(monkeypatch)
    c = BG.check(p)
    assert c["survives_logout"] and row(c, "logout")["status"] == "ok" and "reboot" in row(c, "logout")["detail"]
    assert row(c, "cluster access")["status"] == "ok"


def test_logind_killing_user_processes_is_reported_as_a_failure_with_the_fix(p, monkeypatch):
    facts(monkeypatch, kup=True)
    c = BG.check(p)
    assert not c["survives_logout"] and row(c, "logout")["status"] == "fail" and row(c, "logout")["fix"] == "research background install"


def test_a_forwarded_ssh_agent_is_named_because_cluster_access_dies_with_the_session(p, monkeypatch):
    facts(monkeypatch)
    monkeypatch.setattr(BG, "_proc_env", lambda pid: {"SSH_AUTH_SOCK": "/tmp/ssh-XXXXabcd12/agent.4242", "SSH_CONNECTION": "10.0.0.1 5 10.0.0.2 22"})
    r = row(BG.check(p), "cluster access")
    assert r["status"] == "warn" and "ends with your SSH session" in r["detail"] and "ssh-copy-id" in r["fix"]
    monkeypatch.setattr(BG, "_proc_env", lambda pid: {"SSH_AUTH_SOCK": "/run/user/1000/keyring/ssh"})
    assert row(BG.check(p), "cluster access")["status"] == "ok", "a local agent is not a forwarded one"


def test_the_service_restarts_on_failure_and_never_kills_agents(p):
    text = BG.unit_text(p.root)
    assert f"ExecStart=" in text and f"-m research.supervisord run --root {p.root}" in text
    assert "Restart=on-failure" in text and "KillMode=process" in text and "WantedBy=default.target" in text
    assert f"WorkingDirectory={p.root}" in text and 'Environment="PATH=' in text and "PYTHONPATH=" in text
    assert BG.unit_name(p.root).startswith("research-os-truect-phantom-recovery-") and BG.unit_name(p.root) != BG.unit_name(p.root + "-2"), "one unit per project"


def test_install_writes_the_unit_enables_it_and_turns_lingering_on(p, monkeypatch):
    facts(monkeypatch, kup=True, running=False)
    calls, state = [], {"linger": False}
    monkeypatch.setattr(BG, "lingering", lambda: state["linger"])

    class R:
        returncode, stdout, stderr = 0, "", ""

    def run(*argv, timeout=15.0):
        calls.append(list(argv))
        if argv[:2] == ("loginctl", "enable-linger"):
            state["linger"] = True
        return R()
    monkeypatch.setattr(BG, "_run", run)
    r = BG.install(p)
    unit = BG.unit_name(p.root)
    assert os.path.exists(os.path.join(BG.unit_dir(), unit)) and r["units"] == [unit] and r["started"] == {unit: True}
    assert ["systemctl", "--user", "enable", "--now", unit] in calls and any(c[:2] == ["loginctl", "enable-linger"] for c in calls)
    assert r["lingering"] and r["linger_command"] is None
    c = BG.check(p)
    assert c["installed"] and row(c, "logout")["status"] == "ok" and "survives logout and starts again after a reboot" in row(c, "logout")["detail"]
    assert BG.uninstall(p)["removed"] and not os.path.exists(os.path.join(BG.unit_dir(), unit))


def test_logind_configuration_is_read_with_drop_ins(tmp_path, monkeypatch):
    main = tmp_path / "logind.conf"
    main.write_text("[Login]\n#KillUserProcesses=no\nKillUserProcesses=yes\n")
    real_open = open

    def fake_open(path, *a, **kw):
        return real_open(str(main) if path == "/etc/systemd/logind.conf" else path, *a, **kw)
    monkeypatch.setattr("builtins.open", fake_open)
    monkeypatch.setattr(BG.glob, "glob", lambda pat: [])
    assert BG.kill_user_processes() is True
