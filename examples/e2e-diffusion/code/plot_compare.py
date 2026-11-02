"""Compare diffusion runs: one figure with variance growth, final fields and growth rate vs D.

Usage: python plot_compare.py DIR [DIR ...] --out FIGDIR
Each DIR holds fields.npz and metrics.csv from diffuse.py. D is read from a 'D' row in
metrics.csv if present, otherwise from the directory name (e.g. 'D0.05' or 'D=0.05').
"""
import argparse
import csv
import os
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.gridspec import GridSpec

# Okabe-Ito palette (colourblind-safe); cividis is perceptually uniform and colourblind-safe.
COLOURS = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7"]
plt.rcParams.update({
    "font.family": "serif", "font.size": 9, "mathtext.fontset": "stix",
    "axes.linewidth": 0.8, "savefig.dpi": 300,
})


def load(d):
    z = np.load(os.path.join(d, "fields.npz"))
    D = None
    with open(os.path.join(d, "metrics.csv")) as f:
        for row in csv.DictReader(f):
            if row["name"] == "D":
                D = float(row["value"])
    if D is None:
        m = re.search(r"D[=_-]?(\d+(?:\.\d+)?)", os.path.basename(os.path.normpath(d)))
        if not m:
            raise SystemExit("cannot determine D for %s (no 'D' row, name has no D<value>)" % d)
        D = float(m.group(1))
    rate = np.polyfit(z["times"], z["variance"], 1)[0]
    return D, z["times"], z["variance"], z["u_final"], rate


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dirs", nargs="+")
    p.add_argument("--out", default=".")
    a = p.parse_args()

    runs = sorted((load(d) for d in a.dirs), key=lambda r: r[0])
    n = len(runs)
    colour = {r[0]: COLOURS[i % len(COLOURS)] for i, r in enumerate(runs)}
    vmin = min(r[3].min() for r in runs)
    vmax = max(r[3].max() for r in runs)

    fig = plt.figure(figsize=(7.0, 2.8), constrained_layout=True)
    gs = GridSpec(1, n + 2, figure=fig, width_ratios=[1.5] + [1.0] * n + [1.5])
    ax1 = fig.add_subplot(gs[0, 0])
    ax3 = fig.add_subplot(gs[0, n + 1])

    # (1) variance vs time; theory sigma^2 = sigma^2(0) + 4 D t (sigma^2 = sum of both axes).
    for D, t, var, _, _ in runs:
        ax1.plot(t, var, color=colour[D], lw=1.4, label=r"$D=%g$" % D)
        ax1.plot(t, var[0] + 4 * D * t, color=colour[D], ls="--", lw=1.0)
    ax1.set_xlabel(r"time $t$ (time units)")
    ax1.set_ylabel(r"variance $\sigma^2(t)$ (cell$^2$)")
    ax1.legend(frameon=False, fontsize=8)
    ax1.text(0.02, 0.97, "dashed: $\\sigma^2(0)+4Dt$", transform=ax1.transAxes,
             va="top", fontsize=7)

    # (2) final fields on a shared colour scale.
    im = None
    for i, (D, _, _, u, _) in enumerate(runs):
        ax = fig.add_subplot(gs[0, i + 1])
        im = ax.imshow(u, cmap="cividis", vmin=vmin, vmax=vmax, origin="lower")
        ax.set_title(r"$D=%g$" % D, fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_xlabel("x (cells)", fontsize=8)
        if i == 0:
            ax.set_ylabel("y (cells)", fontsize=8)
    fig.colorbar(im, ax=fig.axes[1:n + 1], label=r"temperature $u_{\rm final}$ (dimensionless)",
                 shrink=0.9)

    # (3) fitted growth rate vs D, with 4D.
    dense = np.linspace(0, runs[-1][0] * 1.1, 50)
    ax3.plot(dense, 4 * dense, color="0.3", ls="--", lw=1.0, label=r"$4D$")
    for D, _, _, _, rate in runs:
        ax3.plot(D, rate, "o", color=colour[D], ms=5)
    ax3.set_xlabel(r"diffusion coefficient $D$ (cell$^2$/time unit)")
    ax3.set_ylabel(r"growth rate $d\sigma^2/dt$ (cell$^2$/time unit)")
    ax3.set_xlim(0, dense[-1])
    ax3.set_ylim(0, None)
    ax3.legend(frameon=False, fontsize=8)

    os.makedirs(a.out, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(a.out, "diffusion_comparison." + ext), dpi=300)
    print("wrote %s/diffusion_comparison.{png,pdf}" % a.out)


if __name__ == "__main__":
    main()
