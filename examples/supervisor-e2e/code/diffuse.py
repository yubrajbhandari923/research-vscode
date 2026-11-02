"""2D heat diffusion, explicit FTCS, periodic boundaries, dx = 1 (conventions: RESEARCH.md)."""
import argparse
import json
import os
import time

import numpy as np


def _variance(u):
    # sum over both axes of the second central moment (mass-normalised)
    x = np.arange(u.shape[0], dtype=float)
    px, py = u.sum(axis=1), u.sum(axis=0)
    m = u.sum()
    return sum(((x - (x * p).sum() / m) ** 2 * p).sum() / m for p in (px, py))


def simulate(D, n=64, steps=400, sigma0=4.0):
    dt = 0.2 / (4 * D)  # D*dt/dx^2 = 0.05
    r = D * dt
    x = np.arange(n) - n // 2
    u = np.exp(-(x[:, None] ** 2 + x[None, :] ** 2) / (2 * sigma0 ** 2))
    u /= u.sum()
    u0 = u.copy()
    var = [_variance(u)]
    for _ in range(steps):
        u = u + r * (np.roll(u, 1, 0) + np.roll(u, -1, 0) + np.roll(u, 1, 1) + np.roll(u, -1, 1) - 4 * u)
        var.append(_variance(u))
    return u0, u, dt * np.arange(steps + 1), np.array(var)


def write_outputs(d, D, u0, uf, t, var, metrics):
    os.makedirs(d, exist_ok=True)
    f1, f2 = os.path.join(d, "fields.npz"), os.path.join(d, "metrics.csv")
    np.savez(f1, u0=u0, u_final=uf, times=t, variance=var)
    with open(f2, "w") as f:
        f.write("name,value\n")
        for k, v in metrics.items():
            f.write("%s,%.17g\n" % (k, v))
    return [os.path.abspath(f1), os.path.abspath(f2)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--D", type=float, default=0.1)
    ap.add_argument("--n", type=int, default=64)
    ap.add_argument("--steps", type=int, default=400)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.D <= 0 or a.n < 3 or a.steps < 1:
        ap.error("need D > 0, n >= 3, steps >= 1")
    t0 = time.time()
    u0, uf, t, var = simulate(a.D, a.n, a.steps)
    metrics = {
        "final_variance": var[-1],
        "variance_growth_rate": np.polyfit(t, var, 1)[0],
        "total_mass_drift": abs(uf.sum() - u0.sum()),
        "runtime_s": time.time() - t0,
    }
    write_outputs(a.out, a.D, u0, uf, t, var, metrics)
    run = os.environ.get("RESEARCH_RUN_DIR")
    if run:
        paths = write_outputs(os.path.join(run, "outputs"), a.D, u0, uf, t, var, metrics)
        with open(os.path.join(run, "metrics.jsonl"), "a") as f:
            for k, v in dict(metrics, D=a.D).items():
                f.write(json.dumps({"name": k, "value": float(v), "step": 0}) + "\n")
        with open(os.path.join(run, "artifacts.jsonl"), "a") as f:
            for p in paths:
                f.write(json.dumps({"path": p}) + "\n")
    print({k: float(v) for k, v in metrics.items()})


if __name__ == "__main__":
    main()
