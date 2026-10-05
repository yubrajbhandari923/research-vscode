"""Toy numerical experiment (stdlib only): damped fixed-point iteration x_{k+1} = x_k - alpha * grad(x_k)
on an ill-conditioned quadratic. Small alpha converges slowly, large alpha oscillates/diverges.
Reports final error and runtime; writes results.npy + metrics.csv into --out."""
import argparse
import csv
import json
import math
import os
import random
import struct
import time


def write_npy(path, values):
    header = "{'descr': '<f8', 'fortran_order': False, 'shape': (%d,), }" % len(values)
    header += " " * (63 - (len(header) + 10) % 64) + "\n"
    with open(path, "wb") as f:
        f.write(b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header.encode("latin1"))
        f.write(struct.pack("<%dd" % len(values), *values))


def run(alpha, steps=60, seed=0):
    rnd = random.Random(seed)
    eig = [0.2 + 1.6 * i / 9 for i in range(10)]  # curvatures in [0.2, 1.8]
    x = [rnd.uniform(-1, 1) for _ in eig]
    hist, flips = [], 0
    for _ in range(steps):
        new = [xi - alpha * lam * xi for xi, lam in zip(x, eig)]
        flips += sum(1 for a, b in zip(x, new) if a * b < 0)  # overshoot = sign flip
        x = new
        err = math.sqrt(sum(v * v for v in x))
        hist.append(err if math.isfinite(err) else float("inf"))
        time.sleep(0.004)  # pretend work
    return hist, flips / (steps * len(eig))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--alpha", type=float, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()
    hist, osc = run(a.alpha)  # osc = fraction of coordinate updates that overshoot (sign flips)
    runtime = time.time() - t0
    final = hist[-1]
    write_npy(os.path.join(a.out, "results.npy"), hist)
    with open(os.path.join(a.out, "metrics.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["step", "error"])
        for i, e in enumerate(hist):
            w.writerow([i, f"{e:.6g}"])
    print(f"alpha={a.alpha} final_error={final:.4g} oscillation={osc:.3f} runtime={runtime:.2f}s")
    try:  # attach metrics to the active research run (no-op outside `research run exec`)
        import research
        research.log_metric("final_error", final)
        research.log_metric("oscillation", round(osc, 4))
        research.log_metric("runtime", round(runtime, 3), unit="s")
        research.register_artifact(os.path.join(a.out, "results.npy"), description=f"error history, alpha={a.alpha}")
        research.register_artifact(os.path.join(a.out, "metrics.csv"), description=f"per-step error, alpha={a.alpha}")
    except Exception as e:  # pragma: no cover
        print("research logging skipped:", e)
    json.dump({"alpha": a.alpha, "final_error": final}, open(os.path.join(a.out, "summary.json"), "w"))
