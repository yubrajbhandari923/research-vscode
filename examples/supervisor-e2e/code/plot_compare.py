"""Comparison figure for the D sweep. Usage: plot_compare.py LEDGER_RUNS_DIR OUT_DIR RUN:D [RUN:D ...]
Reads outputs/fields.npz and outputs/metrics.csv from <LEDGER_RUNS_DIR>/<RUN>/collected/."""
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

runs_dir, out_dir = sys.argv[1], sys.argv[2]
specs = [(a.split(":")[0], float(a.split(":")[1])) for a in sys.argv[3:]]
cols = ["#0072B2", "#E69F00", "#009E73"]  # Okabe-Ito
plt.rcParams.update({"font.family": "serif", "mathtext.fontset": "dejavuserif", "font.size": 10})

fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.5, 3.2), constrained_layout=True)
Ds, rates, fitted = [], [], []
for (run, D), c in zip(specs, cols):
    base = os.path.join(runs_dir, run, "collected", "outputs")
    z = np.load(os.path.join(base, "fields.npz"))
    m = dict(l.strip().split(",") for l in open(os.path.join(base, "metrics.csv")).readlines()[1:])
    t, v = z["times"], z["variance"]
    a1.plot(t, v, color=c, lw=1.8, label="$D=%.2f$" % D)
    a1.plot(t, v[0] + 4 * D * t, "k--", lw=0.9)
    rate = float(m["variance_growth_rate"])
    Ds.append(D); rates.append(rate); fitted.append(np.polyfit(t, v, 1)[0])
    print("%s D=%.2f rate(metrics.csv)=%.10g rate(refit)=%.10g 4D=%.4g rel.dev=%.2e var0=%.6g varN=%.6g tN=%.4g"
          % (run, D, rate, fitted[-1], 4 * D, rate / (4 * D) - 1, v[0], v[-1], t[-1]))
a1.plot([], [], "k--", lw=0.9, label=r"$\sigma_0^2$-offset $+\,4Dt$")
a1.set_xlabel("time $t$ (simulation time units, $\\Delta x=1$ cell)")
a1.set_ylabel("variance $\\sigma_x^2+\\sigma_y^2$ (cells$^2$)")
a1.legend(frameon=False)
a1.set_title("(a) Variance growth")

Dl = np.linspace(0, 0.22, 50)
a2.plot(Dl, 4 * Dl, "k--", lw=0.9, label="$4D$")
a2.plot(Ds, rates, "o", color="#D55E00", ms=7, label="measured (linear fit)")
a2.set_xlabel("diffusion coefficient $D$ (cells$^2$ / time)")
a2.set_ylabel("growth rate $d\\sigma^2/dt$ (cells$^2$ / time)")
a2.legend(frameon=False)
a2.set_title("(b) Growth rate vs $D$")

os.makedirs(out_dir, exist_ok=True)
for ext in ("png", "pdf"):
    fig.savefig(os.path.join(out_dir, "diffusion_comparison." + ext), dpi=300)
