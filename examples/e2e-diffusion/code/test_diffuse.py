"""Plain-assert tests for diffuse.py (no pytest). Run: python3 test_diffuse.py"""
import csv
import os
import subprocess
import sys
import tempfile

import numpy as np

import diffuse

HERE = os.path.dirname(os.path.abspath(__file__))


def test_mass_conserved():
    for D in (0.05, 0.1, 0.2):
        r = diffuse.run(D, n=64, steps=400)
        assert r["total_mass_drift"] < 1e-9, (D, r["total_mass_drift"])


def test_variance_growth_matches_4D():
    # Both axes together: d(var)/dt = 2 * (2D) = 4D.
    for D in (0.05, 0.1, 0.2):
        r = diffuse.run(D, n=64, steps=400)
        rate = r["variance_growth_rate"]
        assert abs(rate - 4 * D) / (4 * D) < 0.05, (D, rate)


def test_variance_grows_linearly_in_time():
    r = diffuse.run(0.1, n=64, steps=400)
    growth = r["variance"][-1] - r["variance"][0]
    assert abs(growth - 4 * 0.1 * r["times"][-1]) / growth < 0.01, growth


def test_cli_writes_outputs():
    with tempfile.TemporaryDirectory() as out:
        subprocess.check_call([sys.executable, os.path.join(HERE, "diffuse.py"),
                               "--D", "0.1", "--n", "64", "--steps", "400", "--out", out])
        z = np.load(os.path.join(out, "fields.npz"))
        for key in ("u0", "u_final", "times", "variance"):
            assert key in z.files, key
        assert z["u_final"].shape == (64, 64)
        assert len(z["variance"]) == 401
        with open(os.path.join(out, "metrics.csv")) as f:
            rows = {row["name"]: float(row["value"]) for row in csv.DictReader(f)}
        for key in ("final_variance", "variance_growth_rate", "total_mass_drift", "runtime_s"):
            assert key in rows, key
        assert rows["total_mass_drift"] < 1e-9


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all tests passed")
