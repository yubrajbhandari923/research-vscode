import json
import os

from research import daemonlib, machine
from research.store import Project


def test_machine_registry_tolerates_missing_and_corrupt(tmp_path):
    assert machine.known_projects() == []
    Project.init(str(tmp_path / "a"), name="a")
    machine.remember(str(tmp_path / "a"))
    machine.remember(str(tmp_path / "gone"))                      # not a project: filtered on read
    assert machine.known_projects() == [os.path.realpath(str(tmp_path / "a"))]
    if os.name == "posix":
        assert os.stat(machine.path()).st_mode & 0o777 == 0o600
    open(machine.path(), "w").write("{not json")
    assert machine.known_projects() == []
    machine.remember(str(tmp_path / "a"))                         # corrupt file is rewritten
    assert len(machine.known_projects()) == 1


def test_heartbeat_and_stale_detection(tmp_path):
    hb = str(tmp_path / "hb.json")
    assert daemonlib.read_heartbeat(hb)["alive"] is False
    daemonlib.write_heartbeat(hb, root="r")
    d = daemonlib.read_heartbeat(hb)
    assert d["alive"] and not d["stale"] and d["root"] == "r"
    json.dump({"pid": os.getpid(), "version": "0.0.0"}, open(hb, "w"))
    assert daemonlib.read_heartbeat(hb)["stale"] is True


def test_ensure_current_is_a_noop_without_a_daemon_and_never_raises(tmp_path):
    Project.init(str(tmp_path / "a"), name="a")
    assert daemonlib.ensure_current(str(tmp_path / "a"))["action"] == "none"
    assert daemonlib.ensure_current(str(tmp_path / "missing"))["action"] == "none"


def test_ensure_current_restarts_a_stale_supervisor(tmp_path, monkeypatch):
    from research import supervisor as SV
    Project.init(str(tmp_path / "a"), name="a")
    monkeypatch.setattr(SV, "daemon_status", lambda p: {"running": True, "restart_required": True})
    monkeypatch.setattr(SV, "daemon_restart", lambda p, timeout=0: {"restarted": True})
    assert daemonlib.ensure_current(str(tmp_path / "a")) == {"action": "restart", "restarted": True}
