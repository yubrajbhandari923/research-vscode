"""Plain-assert tests for diffuse.py (no pytest). Run: python3 test_diffuse.py

Contract: simulate(D, n=64, steps=400) -> (u0, u_final, times, variance)
  - u0, u_final: (n, n) float arrays, total mass 1 at start; periodic FTCS
  - times: (steps+1,) with dt = 0.2/(4D); variance: (steps+1,) sum of both axes' second central moments
CLI: python3 diffuse.py --D --n --steps --out DIR -> fields.npz, metrics.csv (name,value)
"""
import csv
import json
import os
import subprocess
import sys
import tempfile

import numpy as np

import diffuse

HERE = os.path.dirname(os.path.abspath(__file__))
METRICS = {"final_variance", "variance_growth_rate", "total_mass_drift", "runtime_s"}


def test_mass_and_shapes():
    u0, uf, t, var = diffuse.simulate(0.1)
    assert u0.shape == uf.shape == (64, 64)
    assert abs(u0.sum() - 1.0) < 1e-9
    assert abs(uf.sum() - u0.sum()) < 1e-9
    assert len(t) == len(var) == 401
    assert abs(t[1] - 0.2 / (4 * 0.1)) < 1e-12
    assert abs(var[0] - 32.0) < 0.5  # two axes, sigma0 = 4 -> 2*16


def test_growth_rate():
    for D in (0.05, 0.1, 0.2):
        _, _, t, var = diffuse.simulate(D)
        rate = np.polyfit(t, var, 1)[0]
        assert abs(rate - 4 * D) / (4 * D) < 0.01, (D, rate)


def test_cli():
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "o")
        subprocess.run([sys.executable, os.path.join(HERE, "diffuse.py"), "--D", "0.1", "--n", "64",
                        "--steps", "400", "--out", out], check=True, env={k: v for k, v in os.environ.items()
                                                                         if k != "RESEARCH_RUN_DIR"})
        z = np.load(os.path.join(out, "fields.npz"))
        assert {"u0", "u_final", "times", "variance"} <= set(z.files)
        with open(os.path.join(out, "metrics.csv")) as f:
            m = {r[0]: float(r[1]) for r in csv.reader(f) if r and r[0] != "name"}
        assert METRICS <= set(m), m
        assert abs(m["variance_growth_rate"] - 0.4) / 0.4 < 0.01
        assert m["total_mass_drift"] < 1e-9


def test_cli_hpc_contract():
    with tempfile.TemporaryDirectory() as d:
        run = os.path.join(d, "run")
        os.makedirs(run)
        env = dict(os.environ, RESEARCH_RUN_DIR=run)
        subprocess.run([sys.executable, os.path.join(HERE, "diffuse.py"), "--D", "0.05", "--n", "32",
                        "--steps", "50", "--out", os.path.join(d, "o")], check=True, env=env)
        names = {json.loads(l)["name"] for l in open(os.path.join(run, "metrics.jsonl"))}
        assert (METRICS | {"D"}) <= names, names
        arts = [json.loads(l)["path"] for l in open(os.path.join(run, "artifacts.jsonl"))]
        assert len(arts) >= 2
        for p in arts:
            assert os.path.isabs(p) and os.path.exists(p) and p.startswith(os.path.join(run, "outputs")), p


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
