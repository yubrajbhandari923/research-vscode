import hashlib
import json
import os
import subprocess
import sys

from research import selfupdate as SU
from research.compat import core_satisfies
from research.store import Project

WHEEL = b"wheel-bytes"


def _fake(latest, sums_hash=None):
    h = sums_hash or hashlib.sha256(WHEEL).hexdigest()
    rel = {"tag_name": "v" + latest, "assets": [
        {"name": "x-1-py3-none-any.whl", "browser_download_url": "u/w"},
        {"name": "SHA256SUMS", "browser_download_url": "u/s"}]}
    data = {"u/w": WHEEL, "u/s": f"{h}  x-1-py3-none-any.whl\n".encode()}
    return lambda url: data[url] if url in data else json.dumps(rel).encode()


def _run(latest, **kw):
    calls, msgs = [], []
    kw.setdefault("fetch", _fake(latest))
    rc = SU.run(current="2.6.0", install=lambda w: calls.append(w) or 0, out=msgs.append, **kw)
    return rc, calls, msgs


def test_install_ok():
    rc, calls, _ = _run("2.7.0")
    assert rc == 0 and len(calls) == 1


def test_checksum_mismatch_leaves_install_untouched():
    rc, calls, msgs = _run("2.7.0", fetch=_fake("2.7.0", "0" * 64))
    assert rc == 1 and not calls and "checksum" in msgs[-1]


def test_check_only_and_up_to_date():
    rc, calls, msgs = _run("2.7.0", check=True)
    assert rc == 0 and not calls and "latest 2.7.0" in msgs[0]
    assert _run("2.6.0")[1] == []


def test_major_gating():
    rc, calls, msgs = _run("3.0.0")
    assert rc == 1 and not calls and "--major" in msgs[-1]
    assert _run("3.0.0", check=True)[2][0].count("breaking") == 1
    assert _run("3.0.0", major=True)[0] == 0


def test_from_file_checks_sibling_sums(tmp_path):
    w = tmp_path / "a.whl"
    w.write_bytes(WHEEL)
    (tmp_path / "SHA256SUMS").write_text("0" * 64 + "  a.whl\n")
    rc, calls, _ = _run("x", from_file=str(w))
    assert rc == 1 and not calls
    (tmp_path / "SHA256SUMS").write_text(hashlib.sha256(WHEEL).hexdigest() + "  a.whl\n")
    assert _run("x", from_file=str(w))[0] == 0


def test_compat():
    assert core_satisfies("3.1.0", "3.0") and core_satisfies("3.0.2", "3.0")
    assert not core_satisfies("3.0.0", "3.1") and not core_satisfies("2.9.0", "3.0") and not core_satisfies("4.0.0", "3.0")


def test_version_json_and_ping_from_subdir(tmp_path):
    Project.init(str(tmp_path), name="p")
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    env = os.environ
    out = subprocess.run([sys.executable, "-m", "research_cli.main", "version", "--json"], capture_output=True, text=True, env=env)
    v = json.loads(out.stdout)
    assert v["protocol"] == 1 and v["version"]
    p = subprocess.run([sys.executable, "-m", "research_cli.main", "rpc"], input='{"id":1,"method":"ping"}\n', cwd=sub,
                       capture_output=True, text=True, env=env, timeout=60)
    lines = [json.loads(x) for x in p.stdout.splitlines() if x.strip()]
    ready = next(x for x in lines if x.get("event") == "ready")
    assert ready["protocol"] == 1 and ready["initialized"] is True and os.path.realpath(ready["root"]) == os.path.realpath(str(tmp_path))
    pong = next(x for x in lines if x.get("id") == 1)["result"]
    assert pong["initialized"] is True and pong["protocol"] == 1
