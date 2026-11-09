"""The project's Python environment: one setting used by agents, checks and `{python}`; created with uv, never guessed."""
import os
import shutil
import subprocess
import sys

import pytest

from research import agents, envcheck as EC, initproject as IP, plans, verification
from research.store import Project

needs_uv = pytest.mark.skipif(not shutil.which("uv"), reason="uv not installed")


@pytest.fixture
def venv(tmp_path):
    d = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(d)], check=True)
    return d


def test_unset_environment_is_reported_not_guessed(proj):
    r = EC.check(proj)
    assert r["python"] is None and not r["ok"] and "No project Python" in r["problems"][0]
    assert EC.shim_dir(proj) is None and EC.path_with_python(proj, "/a:/b") == "/a:/b"


def test_configured_python_drives_the_shim_checks_and_agent_path(proj, venv, tmp_path):
    proj.config["environment"] = {"python": str(venv), "packages": ["sys", "nonexistent_pkg_xyz"]}
    proj.save_config()
    r = EC.check(proj)
    assert r["python"].endswith("python") and r["packages"] == {"sys": True, "nonexistent_pkg_xyz": False} and not r["ok"]
    d = EC.shim_dir(proj)
    assert str(venv / "bin" / "python") in open(os.path.join(d, "python3")).read()
    assert EC.path_with_python(proj, "/x").startswith(d + os.pathsep)
    # `{python}` and PATH in a task check
    pl = plans.create_plan(proj, "p")
    t = plans.create_task(proj, pl["id"], "t", goal="g", checks=["cmd:{python} -c 'import sys; sys.exit(0 if \"venv\" in sys.prefix else 1)'",
                                                                "cmd:python3 -c 'import sys; sys.exit(0 if \"venv\" in sys.prefix else 1)'"])
    res = verification._task_checks(proj, t["id"], transition=False, log=False)
    assert all(x["ok"] for x in res["results"]), res["results"]
    # agent sessions get the same interpreter first on PATH
    proj.config["agents"]["profiles"]["fake"] = {"command": sys.executable, "args": ["-c", "pass"], "headless_args": ["--x"]}
    d2 = agents.dispatch(proj, "implementer", t["id"], profile="fake", allow_unfrozen="test")
    assert d2["env"]["PATH"].split(os.pathsep)[0] == d and d2["env"]["RESEARCH_PYTHON"] == r["python"]


@needs_uv
def test_create_with_uv_sets_the_config_and_refuses_to_overwrite(tmp_path):
    made = EC.create(str(tmp_path / "v"), [])
    assert os.path.isfile(made["python"])
    with pytest.raises(Exception):
        EC.create(str(tmp_path / "v"), [])


@needs_uv
def test_wizard_plans_and_creates_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    spec = {"name": "p", "python": {"mode": "create", "packages": ""}}
    pl = IP.plan(str(root), spec)
    assert pl["ok"] and any("Create a Python environment" in a for a in pl["actions"]) and any("environment.python" in a for a in pl["actions"])
    IP.apply(str(root), spec)
    with Project(str(root / "main")) as p:
        assert EC.resolve_python(p) == str(root / ".venv" / "bin" / "python")
    bad = IP.plan(str(tmp_path / "other"), {"name": "p", "python": {"mode": "existing", "path": "/no/such/python"}})
    assert not bad["ok"] and any("Existing Python" in x for x in bad["problems"])


@pytest.mark.parametrize("spec,mod", [("PyYAML", "yaml"), ("pyyaml>=6.0", "yaml"), ("PyYAML[extra]==6.0.1 ; python_version >= '3.8'", "yaml"),
                                      ("scikit-learn~=1.4", "sklearn"), ("Pillow", "PIL"), ("opencv-python-headless", "cv2"),
                                      ("numpy", "numpy"), ("torch>=2", "torch"), ("my-own-pkg", "my_own_pkg"), ("ruamel.yaml", "ruamel.yaml")])
def test_import_name_maps_the_distribution_to_its_module(spec, mod):
    assert EC.import_name(spec) == mod


def test_check_imports_pyyaml_as_yaml(proj, venv):
    site = subprocess.run([str(venv / "bin" / "python"), "-c", "import sysconfig;print(sysconfig.get_paths()['purelib'])"], capture_output=True, text=True).stdout.strip()
    open(os.path.join(site, "yaml.py"), "w").close()      # what a PyYAML install provides
    proj.config["environment"] = {"python": str(venv), "packages": ["PyYAML>=6"]}
    proj.save_config()
    r = EC.check(proj)
    assert r["packages"] == {"PyYAML>=6": True} and r["ok"] and not r["problems"]
    r = EC.check(proj, ["PyYAML>=6", "nonexistent-dist-xyz"])
    assert r["packages"]["nonexistent-dist-xyz"] is False and "nonexistent-dist-xyz" in r["problems"][0]
