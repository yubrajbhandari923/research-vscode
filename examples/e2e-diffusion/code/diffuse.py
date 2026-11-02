"""Explicit FTCS solver for u_t = D (u_xx + u_yy) on an n x n grid, dx = 1, periodic. numpy only."""
import argparse
import csv
import json
import os
import shutil
import time

import numpy as np

SIGMA0 = 4.0  # initial Gaussian width (grid cells)


def laplacian(u):
    return np.roll(u, 1, 0) + np.roll(u, -1, 0) + np.roll(u, 1, 1) + np.roll(u, -1, 1) - 4.0 * u


def variance(u):
    # Sum of second moments about the centroid, both axes; grows as 4*D*t.
    n = u.shape[0]
    x = np.arange(n, dtype=float)
    m = u.sum()
    px = u.sum(axis=0) / m
    py = u.sum(axis=1) / m
    cx = (px * x).sum()
    cy = (py * x).sum()
    return float((px * (x - cx) ** 2).sum() + (py * (x - cy) ** 2).sum())


def run(D, n=64, steps=400):
    dx = 1.0
    dt = 0.2 * dx * dx / (4.0 * D)  # stability-safe: D*dt/dx^2 = 0.05 << 0.25
    c = n // 2
    y, x = np.meshgrid(np.arange(n, dtype=float), np.arange(n, dtype=float), indexing="ij")
    u0 = np.exp(-((x - c) ** 2 + (y - c) ** 2) / (2.0 * SIGMA0 ** 2))
    u = u0.copy()
    times = np.arange(steps + 1) * dt
    var = np.empty(steps + 1)
    var[0] = variance(u)
    t0 = time.time()
    for k in range(steps):
        u = u + dt * D * laplacian(u) / (dx * dx)
        var[k + 1] = variance(u)
    runtime = time.time() - t0
    slope = np.polyfit(times, var, 1)[0]
    drift = abs(u.sum() - u0.sum()) / u0.sum()
    return {
        "u0": u0, "u_final": u, "times": times, "variance": var,
        "final_variance": var[-1], "variance_growth_rate": slope,
        "total_mass_drift": drift, "runtime_s": runtime,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--D", type=float, required=True)
    p.add_argument("--n", type=int, default=64)
    p.add_argument("--steps", type=int, default=400)
    p.add_argument("--out", default="out")
    a = p.parse_args()
    if a.D <= 0 or a.n < 8 or a.steps < 1:
        p.error("need D > 0, n >= 8, steps >= 1")
    r = run(a.D, a.n, a.steps)
    os.makedirs(a.out, exist_ok=True)
    np.savez(os.path.join(a.out, "fields.npz"), u0=r["u0"], u_final=r["u_final"],
             times=r["times"], variance=r["variance"])
    with open(os.path.join(a.out, "metrics.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "value"])
        for key in ("final_variance", "variance_growth_rate", "total_mass_drift", "runtime_s"):
            w.writerow([key, repr(float(r[key]))])
    print("final_variance=%.6g growth_rate=%.6g (4D=%.6g) mass_drift=%.3g"
          % (r["final_variance"], r["variance_growth_rate"], 4 * a.D, r["total_mass_drift"]))

    run_dir = os.environ.get("RESEARCH_RUN_DIR")
    if run_dir:  # HPC collection: copy outputs and metrics into the run directory
        outd = os.path.join(run_dir, "outputs")
        os.makedirs(outd, exist_ok=True)
        if os.path.abspath(a.out) != os.path.abspath(outd):  # avoid copying a file onto itself
            for fn in ("fields.npz", "metrics.csv"):
                shutil.copy(os.path.join(a.out, fn), outd)
        metrics = {k: float(r[k]) for k in ("final_variance", "variance_growth_rate",
                                            "total_mass_drift", "runtime_s")}
        metrics["D"] = a.D
        with open(os.path.join(run_dir, "metrics.jsonl"), "a") as f:
            for name, value in metrics.items():
                f.write(json.dumps({"name": name, "value": value, "step": 0}) + "\n")
        # Research OS collects these artifacts when the job finishes.
        with open(os.path.join(run_dir, "artifacts.jsonl"), "a") as f:
            for fn in ("fields.npz", "metrics.csv"):
                path = os.path.abspath(os.path.join(outd, fn))
                f.write(json.dumps({"path": path, "kind": "data",
                                    "description": "%s D=%g" % (fn, a.D)}) + "\n")


if __name__ == "__main__":
    main()
